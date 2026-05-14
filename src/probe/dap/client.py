"""High-level DAP client wrapping the debug adapter.

Provides convenience methods: set_breakpoint, continue_execution,
eval_expression, get_stack_trace, get_variables, run_test.

Every DAP operation emits a TraceEvent via the tracer.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from probe.dap.protocol import Breakpoint, StackFrame, Variable


class DAPClient:
    """High-level DAP client for controlling a debug session.

    Wraps a DAP adapter and provides convenient methods for debugging
    operations. Each operation emits a TraceEvent for observability.
    """

    def __init__(self, adapter, tracer=None) -> None:
        self._adapter = adapter
        self._tracer = tracer
        self._breakpoints: dict[str, list[int]] = {}  # file_path -> [id, ...]

    # -- breakpoints -------------------------------------------------------------

    async def set_breakpoint(
        self, file_path: str, line: int, condition: str | None = None
    ) -> Breakpoint:
        """Set a breakpoint at the given file and line."""
        args: dict[str, Any] = {
            "source": {"name": os.path.basename(file_path), "path": file_path},
            "breakpoints": [{"line": line}],
        }
        if condition:
            args["breakpoints"][0]["condition"] = condition

        body = await self._adapter.send_request("setBreakpoints", args)
        bp_list = body.get("breakpoints", [])

        bp = Breakpoint.from_dict(bp_list[0]) if bp_list else Breakpoint()
        if bp.verified:
            self._breakpoints.setdefault(file_path, []).append(bp.id)

        self._emit("instrument", {
            "action": "set_breakpoint",
            "file": file_path,
            "line": line,
            "condition": condition,
            "breakpoint_id": bp.id,
            "verified": bp.verified,
        })

        return bp

    async def remove_breakpoint(self, file_path: str, line: int) -> None:
        """Remove a breakpoint at the given file and line."""
        await self._adapter.send_request("setBreakpoints", {
            "source": {"name": os.path.basename(file_path), "path": file_path},
            "breakpoints": [],
        })
        self._breakpoints.pop(file_path, None)

        self._emit("instrument", {
            "action": "remove_breakpoint",
            "file": file_path,
            "line": line,
        })

    # -- execution control -------------------------------------------------------

    async def continue_execution(self) -> dict[str, Any]:
        """Continue execution and wait for the next stopped event."""
        await self._adapter.send_request("continue", {})
        stopped = await self._adapter.wait_for_stopped()

        self._emit("execute", {
            "action": "continue",
            "stopped_reason": stopped.get("body", stopped).get("reason", "unknown"),
            "thread_id": stopped.get("body", stopped).get("threadId"),
        })

        return stopped

    async def step_in(self) -> dict[str, Any]:
        """Step into the next line."""
        body = await self._adapter.send_request("stepIn", {})
        stopped = await self._adapter.wait_for_stopped()
        return stopped

    async def step_out(self) -> dict[str, Any]:
        """Step out of the current function."""
        body = await self._adapter.send_request("stepOut", {})
        stopped = await self._adapter.wait_for_stopped()
        return stopped

    async def step_over(self) -> dict[str, Any]:
        """Step over the next line."""
        body = await self._adapter.send_request("next", {})
        stopped = await self._adapter.wait_for_stopped()
        return stopped

    # -- state inspection --------------------------------------------------------

    async def get_stack_trace(self, thread_id: int = 1) -> list[StackFrame]:
        """Get the stack trace for a thread."""
        body = await self._adapter.send_request("stackTrace", {"threadId": thread_id})
        frames = [StackFrame.from_dict(f) for f in body.get("stackFrames", [])]

        self._emit("execute", {
            "action": "get_stack_trace",
            "thread_id": thread_id,
            "frame_count": len(frames),
        })

        return frames

    async def get_variables(self, frame_id: int) -> list[Variable]:
        """Get variables (locals) for a stack frame."""
        body = await self._adapter.send_request("scopes", {"frameId": frame_id})
        scopes = body.get("scopes", [])

        variables: list[Variable] = []
        for scope in scopes:
            scope_vars_ref = scope.get("variablesReference", 0)
            if scope_vars_ref > 0:
                vars_body = await self._adapter.send_request("variables", {
                    "variablesReference": scope_vars_ref,
                })
                for v in vars_body.get("variables", []):
                    variables.append(Variable.from_dict(v))

        self._emit("execute", {
            "action": "get_variables",
            "frame_id": frame_id,
            "variable_count": len(variables),
        })

        return variables

    async def eval_expression(self, expression: str, frame_id: int = 0) -> str:
        """Evaluate an expression in the context of a stack frame."""
        body = await self._adapter.send_request("evaluate", {
            "expression": expression,
            "frameId": frame_id,
            "context": "repl",
        })

        result = body.get("result", "")

        self._emit("execute", {
            "action": "eval_expression",
            "expression": expression,
            "result": result,
        })

        return result

    # -- convenience: run a test from start to finish ----------------------------

    async def run_to_breakpoints(
        self, breakpoints: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Set breakpoints, signal the debuggee to start, and collect state
        when the first breakpoint or unhandled exception is hit.

        The debuggee (started via ``debugpy --wait-for-client``) is paused
        waiting for ``configurationDone``.  We set line breakpoints AND
        exception breakpoints (for uncaught exceptions), send
        *configurationDone* to start execution, and then wait for a stop or
        process exit.

        Args:
            breakpoints: List of dicts with 'file' and 'line' keys.

        Returns:
            Collected state from the stop event, or an empty dict if the
            process exited without hitting a breakpoint.
        """
        if not breakpoints:
            return {
                "variables": {},
                "frames": [],
                "stopped_reason": "no_breakpoints",
            }

        # Set line breakpoints
        verified_count = 0
        for bp_spec in breakpoints:
            bp = await self.set_breakpoint(
                bp_spec["file"],
                bp_spec["line"],
                bp_spec.get("condition"),
            )
            if bp.verified:
                verified_count += 1

        # Set exception breakpoints so we catch unhandled crashes even when
        # line breakpoints fail to verify (e.g. due to path resolution issues).
        try:
            await self._adapter.send_request("setExceptionBreakpoints", {
                "filters": ["uncaught"],
            })
        except Exception:
            pass  # Best-effort; not all adapters may support this

        # Tell the debuggee to start running
        await self._adapter.send_request("configurationDone", {})

        # Wait for the first stop event or process exit
        start_time = time.monotonic()

        try:
            event_msg = await self._adapter.wait_for_stop_or_terminated(
                timeout=60.0
            )
        except TimeoutError:
            return {
                "variables": {},
                "frames": [],
                "stopped_reason": "timeout",
                "breakpoints_verified": verified_count,
            }

        elapsed = time.monotonic() - start_time

        event_type = event_msg.get("event", "")
        body = event_msg.get("body", event_msg)

        # Handle graceful exit / termination without a stop
        if event_type in ("exited", "terminated"):
            return {
                "variables": {},
                "frames": [],
                "stopped_reason": event_type,
                "exit_code": body.get("exitCode"),
                "breakpoints_verified": verified_count,
                "elapsed_seconds": round(elapsed, 3),
            }

        # Must be a "stopped" event — collect state
        thread_id = body.get("threadId", 1)
        frames = await self.get_stack_trace(thread_id)
        all_vars: dict[str, str] = {}
        if frames:
            try:
                variables = await self.get_variables(frames[0].id)
                for v in variables:
                    all_vars[v.name] = v.value
            except Exception:
                pass

        collected = {
            "stopped_reason": body.get("reason"),
            "thread_id": thread_id,
            "frames": [
                {
                    "name": f.name,
                    "file": f.source.path if f.source else "",
                    "line": f.line,
                }
                for f in frames
            ],
            "variables": all_vars,
            "elapsed_seconds": round(elapsed, 3),
            "breakpoints_verified": verified_count,
        }

        self._emit("execute", {
            "action": "run_to_breakpoints",
            "breakpoints_set": len(breakpoints),
            "breakpoints_verified": verified_count,
            "stopped_at": collected,
        })

        return collected

    # -- helpers -----------------------------------------------------------------

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)
