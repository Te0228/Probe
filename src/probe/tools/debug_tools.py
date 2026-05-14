"""DAP-related debugging tools — set breakpoints, evaluate expressions,
inspect stack frames and variables.

Each tool wraps a DAP client call and emits a TraceEvent.
"""

from __future__ import annotations

from typing import Any


class SetBreakpointTool:
    """Tool: set a breakpoint at a given file and line via DAP."""

    name = "set_breakpoint"

    def __init__(self, dap_client: Any, tracer: Any = None) -> None:
        self._dap = dap_client
        self._tracer = tracer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        file_path = kwargs.get("file", "")
        line = kwargs.get("line", 0)
        condition = kwargs.get("condition")
        if not file_path or line <= 0:
            return {"error": "file and line are required"}
        bp = await self._dap.set_breakpoint(file_path, line, condition)
        return {
            "file": file_path,
            "line": line,
            "breakpoint_id": bp.id,
            "verified": bp.verified,
        }


class EvalExpressionTool:
    """Tool: evaluate a Python expression in the debuggee context."""

    name = "eval_expression"

    def __init__(self, dap_client: Any, tracer: Any = None) -> None:
        self._dap = dap_client
        self._tracer = tracer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        expression = kwargs.get("expression", "")
        frame_id = kwargs.get("frame_id", 0)
        if not expression:
            return {"error": "expression is required"}
        result = await self._dap.eval_expression(expression, frame_id)
        self._emit("execute", {"tool": "eval_expression", "expression": expression, "result": result})
        return {"expression": expression, "result": result}

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)


class GetStackTraceTool:
    """Tool: retrieve the current stack trace from the debuggee."""

    name = "get_stack_trace"

    def __init__(self, dap_client: Any, tracer: Any = None) -> None:
        self._dap = dap_client
        self._tracer = tracer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        thread_id = kwargs.get("thread_id", 1)
        frames = await self._dap.get_stack_trace(thread_id)
        self._emit("execute", {"tool": "get_stack_trace", "frame_count": len(frames)})
        return {
            "frames": [
                {"name": f.name, "file": f.source.path if f.source else "", "line": f.line}
                for f in frames
            ]
        }

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)


class GetVariablesTool:
    """Tool: retrieve local variables from a stack frame."""

    name = "get_variables"

    def __init__(self, dap_client: Any, tracer: Any = None) -> None:
        self._dap = dap_client
        self._tracer = tracer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        frame_id = kwargs.get("frame_id", 0)
        if frame_id <= 0:
            return {"error": "frame_id is required"}
        variables = await self._dap.get_variables(frame_id)
        self._emit("execute", {"tool": "get_variables", "variable_count": len(variables)})
        return {
            "variables": {v.name: v.value for v in variables}
        }

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)
