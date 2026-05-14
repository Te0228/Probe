"""Tests for the DAP client and protocol types."""

from __future__ import annotations

from typing import Any

import pytest


# ── Protocol types ─────────────────────────────────────────────────────────────


class TestDAPProtocol:
    """Tests for DAP protocol message types."""

    def test_request_to_dict(self):
        """A Request serialises correctly to a dict."""
        from probe.dap.protocol import Request

        req = Request(seq=1, command="initialize", arguments={"clientID": "probe"})
        d = req.to_dict()

        assert d["seq"] == 1
        assert d["type"] == "request"
        assert d["command"] == "initialize"
        assert d["arguments"]["clientID"] == "probe"

    def test_response_from_dict(self):
        """Response.from_dict parses a DAP response correctly."""
        from probe.dap.protocol import Response

        resp = Response.from_dict({
            "seq": 2,
            "request_seq": 1,
            "success": True,
            "command": "initialize",
            "body": {"supportsConditionalBreakpoints": True},
        })

        assert resp.seq == 2
        assert resp.request_seq == 1
        assert resp.success is True
        assert resp.body["supportsConditionalBreakpoints"] is True

    def test_event_from_dict(self):
        """Event.from_dict parses a DAP event correctly."""
        from probe.dap.protocol import Event

        evt = Event.from_dict({
            "seq": 3,
            "event": "stopped",
            "body": {"reason": "breakpoint", "threadId": 1},
        })

        assert evt.event == "stopped"
        assert evt.body["reason"] == "breakpoint"

    def test_stackframe_from_dict(self):
        """StackFrame.from_dict parses correctly."""
        from probe.dap.protocol import StackFrame

        frame = StackFrame.from_dict({
            "id": 1,
            "name": "test_func",
            "source": {"name": "test.py", "path": "/tmp/test.py"},
            "line": 42,
            "column": 5,
        })

        assert frame.id == 1
        assert frame.name == "test_func"
        assert frame.source is not None
        assert frame.source.name == "test.py"
        assert frame.source.path == "/tmp/test.py"
        assert frame.line == 42

    def test_variable_from_dict(self):
        """Variable.from_dict parses correctly."""
        from probe.dap.protocol import Variable

        var = Variable.from_dict({
            "name": "result",
            "value": "42",
            "type": "int",
            "variablesReference": 0,
        })

        assert var.name == "result"
        assert var.value == "42"
        assert var.type == "int"

    def test_breakpoint_from_dict(self):
        """Breakpoint.from_dict parses correctly."""
        from probe.dap.protocol import Breakpoint

        bp = Breakpoint.from_dict({
            "id": 101,
            "verified": True,
            "source": {"name": "calc.py", "path": "/tmp/calc.py"},
            "line": 10,
        })

        assert bp.id == 101
        assert bp.verified is True
        assert bp.line == 10


# ── DAPClient with mock adapter ────────────────────────────────────────────────


class TestDAPClient:
    """Tests for the DAPClient using a mock adapter."""

    def test_client_initializes(self, mock_tracer):
        """DAPClient initialises with an adapter and tracer."""
        from probe.dap.client import DAPClient

        adapter = _MockAdapter()
        client = DAPClient(adapter=adapter, tracer=mock_tracer)
        assert client is not None

    def test_set_breakpoint_emits_trace(self, mock_tracer):
        """set_breakpoint emits an instrument TraceEvent."""
        from probe.dap.client import DAPClient
        import asyncio

        adapter = _MockAdapter()
        client = DAPClient(adapter=adapter, tracer=mock_tracer)

        bp = asyncio.run(
            client.set_breakpoint("/tmp/test.py", 42, None)
        )

        assert bp.line == 42
        assert bp.verified is True

        # Check trace event was emitted
        instrument_events = [e for e in mock_tracer.events if e.step_type == "instrument"]
        assert len(instrument_events) >= 1
        ev = instrument_events[0]
        assert ev.data["action"] == "set_breakpoint"
        assert ev.data["file"] == "/tmp/test.py"
        assert ev.data["line"] == 42

    def test_remove_breakpoint_clears_file(self, mock_tracer):
        """remove_breakpoint clears breakpoints for a file."""
        from probe.dap.client import DAPClient
        import asyncio

        adapter = _MockAdapter()
        client = DAPClient(adapter=adapter, tracer=mock_tracer)

        # Set then remove
        asyncio.run(client.set_breakpoint("/tmp/test.py", 42, None))
        asyncio.run(client.remove_breakpoint("/tmp/test.py", 42))

        # The client should have no breakpoints for that file
        assert "/tmp/test.py" not in client._breakpoints

    def test_eval_expression_returns_result(self, mock_tracer):
        """eval_expression returns the evaluated result string."""
        from probe.dap.client import DAPClient
        import asyncio

        adapter = _MockAdapter()
        client = DAPClient(adapter=adapter, tracer=mock_tracer)

        result = asyncio.run(
            client.eval_expression("1 + 1")
        )

        assert result is not None

    def test_get_stack_trace_returns_frames(self, mock_tracer):
        """get_stack_trace returns a list of StackFrame objects."""
        from probe.dap.client import DAPClient
        import asyncio

        adapter = _MockAdapter()
        client = DAPClient(adapter=adapter, tracer=mock_tracer)

        frames = asyncio.run(client.get_stack_trace(1))
        assert len(frames) > 0
        assert frames[0].name is not None

    def test_get_variables_returns_variables(self, mock_tracer):
        """get_variables returns a list of Variable objects."""
        from probe.dap.client import DAPClient
        import asyncio

        adapter = _MockAdapter()
        client = DAPClient(adapter=adapter, tracer=mock_tracer)

        variables = asyncio.run(client.get_variables(1))
        assert isinstance(variables, list)

    def test_run_to_breakpoints_collects_state(self, mock_tracer):
        """run_to_breakpoints sets breakpoints and collects runtime state."""
        from probe.dap.client import DAPClient
        import asyncio

        adapter = _MockAdapter()
        client = DAPClient(adapter=adapter, tracer=mock_tracer)

        breakpoints = [
            {"file": "/tmp/test.py", "line": 10},
            {"file": "/tmp/test.py", "line": 20},
        ]

        state = asyncio.run(client.run_to_breakpoints(breakpoints))
        assert "variables" in state or "stopped_reason" in state

    def test_run_to_breakpoints_empty_list(self, mock_tracer):
        """run_to_breakpoints with empty list returns immediately."""
        from probe.dap.client import DAPClient
        import asyncio

        adapter = _MockAdapter()
        client = DAPClient(adapter=adapter, tracer=mock_tracer)

        state = asyncio.run(client.run_to_breakpoints([]))
        assert state.get("stopped_reason") == "no_breakpoints"


# ── Mock adapter ───────────────────────────────────────────────────────────────


class _MockAdapter:
    """A mock DAP adapter for testing the DAPClient."""

    def __init__(self):
        self._pending: dict[int, Any] = {}
        self._seq = 0

    async def send_request(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        """Return realistic mock responses based on the command."""
        if command == "setBreakpoints":
            bp_list = args.get("breakpoints", [])
            if bp_list:
                return {
                    "breakpoints": [
                        {
                            "id": bp_list[0].get("line", 1),
                            "verified": True,
                            "source": args.get("source", {}),
                            "line": bp_list[0].get("line", 1),
                        }
                    ]
                }
            else:
                return {"breakpoints": []}
        elif command == "setExceptionBreakpoints":
            return {}
        elif command == "configurationDone":
            return {}
        elif command == "continue":
            return {}
        elif command == "disconnect":
            return {}
        elif command == "stackTrace":
            return {
                "stackFrames": [
                    {"id": 1, "name": "test_func", "source": {"name": "t.py", "path": "/tmp/t.py"}, "line": 10},
                    {"id": 2, "name": "caller", "source": {"name": "t.py", "path": "/tmp/t.py"}, "line": 5},
                ]
            }
        elif command == "scopes":
            return {"scopes": [{"name": "Locals", "variablesReference": 100, "expensive": False}]}
        elif command == "variables":
            return {
                "variables": [
                    {"name": "result", "value": "42", "type": "int", "variablesReference": 0},
                    {"name": "data", "value": "hello", "type": "str", "variablesReference": 0},
                ]
            }
        elif command == "evaluate":
            return {"result": f"eval({args.get('expression', '?')})", "variablesReference": 0}
        elif command == "stepIn" or command == "stepOut" or command == "next":
            return {}
        else:
            return {}

    async def wait_for_stopped(self, timeout: float = 30.0) -> dict[str, Any]:
        """Return a mock stopped event."""
        return {"body": {"reason": "breakpoint", "threadId": 1}}

    async def wait_for_stop_or_terminated(self, timeout: float = 60.0) -> dict[str, Any]:
        """Return a mock stopped event."""
        return {"event": "stopped", "body": {"reason": "breakpoint", "threadId": 1, "allThreadsStopped": True}}

    async def read_event(self, event_type: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
        """Return a mock event."""
        body = {"reason": "breakpoint"} if event_type == "stopped" else {}
        return {"event": event_type or "output", "body": body}

    async def start(self, **kwargs) -> None:
        """Mock start — no-op."""
        pass

    async def stop(self) -> None:
        """Mock stop — no-op."""
        pass
