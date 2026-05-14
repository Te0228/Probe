"""MCP Server — exposes Probe's debugging tools via the Model Context Protocol.

Provides 8 tools:
  start_debug_session, set_breakpoint, remove_breakpoint,
  continue_execution, eval_expression, get_stack_trace,
  get_variables, run_test

Each tool invocation produces a TraceEvent for full observability.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from probe.config import ProbeConfig
from probe.tracer import SessionManager, Tracer


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOL_START_DEBUG_SESSION = Tool(
    name="start_debug_session",
    description="Start a new DAP debug session for a Python program or test.",
    inputSchema={
        "type": "object",
        "properties": {
            "test_command": {
                "type": "string",
                "description": "The pytest command to run (e.g., 'pytest tests/fixtures/type_mismatch/test_calculator.py').",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the debug session (defaults to current directory).",
            },
        },
        "required": ["test_command"],
    },
)

TOOL_SET_BREAKPOINT = Tool(
    name="set_breakpoint",
    description="Set a breakpoint at a given file and line number.",
    inputSchema={
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "Absolute or relative path to the source file.",
            },
            "line": {
                "type": "integer",
                "description": "Line number where the breakpoint should be set.",
            },
            "condition": {
                "type": "string",
                "description": "Optional conditional expression for the breakpoint.",
            },
        },
        "required": ["file", "line"],
    },
)

TOOL_REMOVE_BREAKPOINT = Tool(
    name="remove_breakpoint",
    description="Remove all breakpoints from a given source file.",
    inputSchema={
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "Absolute or relative path to the source file.",
            },
            "line": {
                "type": "integer",
                "description": "Line number of the breakpoint to remove.",
            },
        },
        "required": ["file", "line"],
    },
)

TOOL_CONTINUE_EXECUTION = Tool(
    name="continue_execution",
    description="Continue execution of the debuggee until the next breakpoint or program exit.",
    inputSchema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)

TOOL_EVAL_EXPRESSION = Tool(
    name="eval_expression",
    description="Evaluate a Python expression in the context of the debuggee's current stack frame.",
    inputSchema={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Python expression to evaluate (e.g., 'type(result)', 'x + y').",
            },
            "frame_id": {
                "type": "integer",
                "description": "Stack frame ID to evaluate in (0 = current frame).",
                "default": 0,
            },
        },
        "required": ["expression"],
    },
)

TOOL_GET_STACK_TRACE = Tool(
    name="get_stack_trace",
    description="Retrieve the current stack trace from the debuggee.",
    inputSchema={
        "type": "object",
        "properties": {
            "thread_id": {
                "type": "integer",
                "description": "Thread ID to get the stack trace for (defaults to 1).",
                "default": 1,
            },
        },
        "required": [],
    },
)

TOOL_GET_VARIABLES = Tool(
    name="get_variables",
    description="Retrieve local variables from a specific stack frame.",
    inputSchema={
        "type": "object",
        "properties": {
            "frame_id": {
                "type": "integer",
                "description": "Stack frame ID to retrieve variables from.",
            },
        },
        "required": ["frame_id"],
    },
)

TOOL_RUN_TEST = Tool(
    name="run_test",
    description="Run a pytest command and return the test output.",
    inputSchema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The test command to execute (e.g., 'pytest tests/fixtures/type_mismatch/test_calculator.py').",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the test execution.",
            },
        },
        "required": ["command"],
    },
)


ALL_TOOLS = [
    TOOL_START_DEBUG_SESSION,
    TOOL_SET_BREAKPOINT,
    TOOL_REMOVE_BREAKPOINT,
    TOOL_CONTINUE_EXECUTION,
    TOOL_EVAL_EXPRESSION,
    TOOL_GET_STACK_TRACE,
    TOOL_GET_VARIABLES,
    TOOL_RUN_TEST,
]


# ── MCP Server ────────────────────────────────────────────────────────────────


class ProbeMCPServer:
    """MCP Server that exposes Probe's debugging tools.

    Each tool invocation creates a TraceEvent, maintaining full observability
    even when used through an MCP client (e.g., Claude Code, Cursor).
    """

    def __init__(
        self,
        config: ProbeConfig | None = None,
        output_dir: str = "probe_traces",
    ) -> None:
        self._config = config or ProbeConfig.from_env()
        self._output_dir = output_dir
        self._session_mgr: SessionManager | None = None
        self._tracer: Tracer | None = None
        self._dap_adapter: Any = None
        self._dap_client: Any = None
        self._server = Server(
            name="probe-mcp-server",
            version="0.1.0",
            instructions="Probe Debug Agent — hypothesis-driven debugging via MCP.",
        )
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register tool list and tool call handlers with the MCP server."""
        server = self._server

        @server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            return ALL_TOOLS

        @server.call_tool()
        async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            result = await self._dispatch(name, arguments)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call to the appropriate handler."""
        self._ensure_session()

        if name == "start_debug_session":
            return await self._handle_start_debug_session(arguments)
        elif name == "set_breakpoint":
            return await self._handle_set_breakpoint(arguments)
        elif name == "remove_breakpoint":
            return await self._handle_remove_breakpoint(arguments)
        elif name == "continue_execution":
            return await self._handle_continue_execution(arguments)
        elif name == "eval_expression":
            return await self._handle_eval_expression(arguments)
        elif name == "get_stack_trace":
            return await self._handle_get_stack_trace(arguments)
        elif name == "get_variables":
            return await self._handle_get_variables(arguments)
        elif name == "run_test":
            return await self._handle_run_test(arguments)
        else:
            return {"error": f"Unknown tool: {name}"}

    def _ensure_session(self) -> None:
        """Ensure a tracer session exists for trace event recording."""
        if self._tracer is None:
            self._session_mgr = SessionManager(output_dir=self._output_dir)
            self._tracer = Tracer(
                session_mgr=self._session_mgr,
                output_dir=self._output_dir,
                console_mode=False,
            )

    # ── Tool handlers ─────────────────────────────────────────────────────────

    async def _handle_start_debug_session(
        self, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Start a DAP debug session for a test command."""
        test_command = args.get("test_command", "")
        cwd = args.get("cwd", os.getcwd())

        if not test_command:
            return {"error": "test_command is required"}

        try:
            from probe.dap.adapters.python import PythonAdapter
            from probe.dap.client import DAPClient

            adapter = PythonAdapter()
            self._dap_adapter = adapter

            # Parse the test command
            parts = test_command.strip().split()
            module = ""
            program = ""
            dap_args: list[str] = []
            if parts and parts[0] == "pytest":
                module = "pytest"
                dap_args = parts[1:]
            else:
                program = parts[0] if parts else ""
                dap_args = parts[1:]

            await adapter.start(
                program=program,
                args=dap_args,
                cwd=cwd,
                module=module,
            )

            self._dap_client = DAPClient(adapter=adapter, tracer=self._tracer)

            if self._tracer:
                self._tracer.emit("instrument", {
                    "tool": "start_debug_session",
                    "test_command": test_command,
                    "cwd": cwd,
                    "module": module,
                    "status": "started",
                })

            return {
                "status": "started",
                "test_command": test_command,
                "session_id": self._session_mgr.session_id if self._session_mgr else "",
            }
        except Exception as e:
            if self._tracer:
                self._tracer.emit("execute", {
                    "tool": "start_debug_session",
                    "error": str(e),
                })
            return {"error": f"Failed to start debug session: {e}"}

    async def _handle_set_breakpoint(
        self, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Set a breakpoint via DAP."""
        file_path = args.get("file", "")
        line = args.get("line", 0)
        condition = args.get("condition")

        if not file_path or line <= 0:
            return {"error": "file and line are required"}

        if self._dap_client is None:
            # Simulated response when no DAP session is active
            if self._tracer:
                self._tracer.emit("instrument", {
                    "tool": "set_breakpoint",
                    "file": file_path,
                    "line": line,
                    "condition": condition,
                    "simulated": True,
                })
            return {
                "file": file_path,
                "line": line,
                "breakpoint_id": 0,
                "verified": False,
                "note": "(simulated — no active DAP session)",
            }

        try:
            bp = await self._dap_client.set_breakpoint(file_path, line, condition)
            return {
                "file": file_path,
                "line": line,
                "breakpoint_id": bp.id,
                "verified": bp.verified,
            }
        except Exception as e:
            return {"error": str(e)}

    async def _handle_remove_breakpoint(
        self, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Remove all breakpoints from a file."""
        file_path = args.get("file", "")
        line = args.get("line", 0)

        if not file_path or line <= 0:
            return {"error": "file and line are required"}

        if self._dap_client is None:
            if self._tracer:
                self._tracer.emit("instrument", {
                    "tool": "remove_breakpoint",
                    "file": file_path,
                    "line": line,
                    "simulated": True,
                })
            return {"status": "removed", "file": file_path, "line": line, "note": "(simulated)"}

        try:
            await self._dap_client.remove_breakpoint(file_path, line)
            return {"status": "removed", "file": file_path, "line": line}
        except Exception as e:
            return {"error": str(e)}

    async def _handle_continue_execution(
        self, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Continue debuggee execution."""
        if self._dap_client is None:
            if self._tracer:
                self._tracer.emit("execute", {
                    "tool": "continue_execution",
                    "simulated": True,
                })
            return {"status": "continued", "note": "(simulated — no active DAP session)"}

        try:
            stopped = await self._dap_client.continue_execution()
            return {
                "status": "stopped",
                "reason": stopped.get("body", stopped).get("reason", "unknown"),
            }
        except Exception as e:
            return {"error": str(e)}

    async def _handle_eval_expression(
        self, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Evaluate an expression in the debuggee."""
        expression = args.get("expression", "")
        frame_id = args.get("frame_id", 0)

        if not expression:
            return {"error": "expression is required"}

        if self._dap_client is None:
            if self._tracer:
                self._tracer.emit("execute", {
                    "tool": "eval_expression",
                    "expression": expression,
                    "simulated": True,
                })
            return {
                "expression": expression,
                "result": "<simulated — no active DAP session>",
            }

        try:
            result = await self._dap_client.eval_expression(expression, frame_id)
            return {"expression": expression, "result": result}
        except Exception as e:
            return {"error": str(e)}

    async def _handle_get_stack_trace(
        self, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Get the current stack trace."""
        thread_id = args.get("thread_id", 1)

        if self._dap_client is None:
            if self._tracer:
                self._tracer.emit("execute", {
                    "tool": "get_stack_trace",
                    "simulated": True,
                })
            return {"frames": [], "note": "(simulated — no active DAP session)"}

        try:
            frames = await self._dap_client.get_stack_trace(thread_id)
            return {
                "frames": [
                    {
                        "name": f.name,
                        "file": f.source.path if f.source else "",
                        "line": f.line,
                    }
                    for f in frames
                ]
            }
        except Exception as e:
            return {"error": str(e)}

    async def _handle_get_variables(
        self, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Get local variables from a stack frame."""
        frame_id = args.get("frame_id", 0)

        if frame_id <= 0:
            return {"error": "frame_id is required"}

        if self._dap_client is None:
            if self._tracer:
                self._tracer.emit("execute", {
                    "tool": "get_variables",
                    "frame_id": frame_id,
                    "simulated": True,
                })
            return {"variables": {}, "note": "(simulated — no active DAP session)"}

        try:
            variables = await self._dap_client.get_variables(frame_id)
            return {"variables": {v.name: v.value for v in variables}}
        except Exception as e:
            return {"error": str(e)}

    async def _handle_run_test(self, args: dict[str, Any]) -> dict[str, Any]:
        """Run a test command and return output."""
        command = args.get("command", "")
        cwd = args.get("cwd", os.getcwd())

        if not command:
            return {"error": "command is required"}

        start_time = time.monotonic()
        try:
            result = subprocess.run(
                command.split(),
                capture_output=True,
                text=True,
                timeout=60,
                cwd=cwd,
            )
            elapsed = time.monotonic() - start_time

            if self._tracer:
                self._tracer.emit("execute", {
                    "tool": "run_test",
                    "command": command,
                    "exit_code": result.returncode,
                    "elapsed": round(elapsed, 3),
                })

            return {
                "exit_code": result.returncode,
                "stdout": result.stdout[-3000:] if result.stdout else "",
                "stderr": result.stderr[-3000:] if result.stderr else "",
                "elapsed": round(elapsed, 3),
            }
        except subprocess.TimeoutExpired:
            return {"error": "Test timed out after 60s"}
        except Exception as e:
            return {"error": str(e)}

    # ── Run the server ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start the MCP server over stdio transport."""
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream=read_stream,
                write_stream=write_stream,
                initialization_options=self._server.create_initialization_options(),
            )

    def run_blocking(self) -> None:
        """Run the MCP server (blocking call for CLI usage)."""
        asyncio.run(self.run())


# ── CLI entry ─────────────────────────────────────────────────────────────────


def start_mcp_server(config: ProbeConfig | None = None) -> None:
    """Start the Probe MCP server. Called from the CLI (``probe serve --mcp``)."""
    server = ProbeMCPServer(config=config)
    server.run_blocking()
