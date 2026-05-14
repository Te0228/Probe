"""Shared test fixtures for Probe's test suite."""

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

# Ensure the source is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))


# ── Source code fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def sample_source_code() -> dict[str, str]:
    """A simple Python module with a type-mismatch bug."""
    return {
        "calculator.py": (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def is_valid_total(value):\n"
            "    # BUG: comparing int to str\n"
            "    return value == \"100\"\n"
        ),
        "test_calc.py": (
            "from calculator import is_valid_total\n"
            "\n"
            "def test_is_valid_total():\n"
            "    assert is_valid_total(100) == True\n"
        ),
    }


@pytest.fixture
def temp_dir() -> Generator[str, None, None]:
    """A temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def mock_tracer():
    """A mock tracer that collects events in memory (no file I/O)."""
    from probe.tracer import TraceEvent

    class MockTracer:
        def __init__(self):
            self.events: list[TraceEvent] = []
            self.session_id_value = "test-session-001"

        def emit(self, step_type: str, data: dict[str, Any]) -> str:
            event = TraceEvent(
                step_type=step_type,
                data=data,
                session_id=self.session_id_value,
            )
            self.events.append(event)
            return event.event_id

        @property
        def session_id(self) -> str:
            return self.session_id_value

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    return MockTracer()


@pytest.fixture
def mock_dap_client():
    """A mock DAP client that returns realistic but fake DAP data."""
    from dataclasses import dataclass
    from typing import Any

    @dataclass
    class MockSource:
        name: str = ""
        path: str = ""

    @dataclass
    class MockStackFrame:
        id: int = 1
        name: str = "test_function"
        source: Any = None
        line: int = 4

        def __post_init__(self):
            if self.source is None:
                self.source = MockSource(name="test.py", path="/tmp/test.py")

    @dataclass
    class MockVariable:
        name: str = "value"
        value: str = "100"
        type: str = ""
        variables_reference: int = 0

    @dataclass
    class MockBreakpoint:
        id: int = 1
        verified: bool = True
        source: Any = None
        line: int = 4
        message: str = ""

        def __post_init__(self):
            if self.source is None:
                self.source = MockSource()

    class MockDAPClient:
        def __init__(self):
            self.breakpoints_set: list[dict[str, Any]] = []
            self.breakpoints_removed: list[tuple[str, int]] = []
            self.expressions_evaluated: list[str] = []

        async def set_breakpoint(self, file_path: str, line: int, condition: str | None = None):
            self.breakpoints_set.append({
                "file": file_path,
                "line": line,
                "condition": condition,
            })
            return MockBreakpoint(id=len(self.breakpoints_set), line=line)

        async def remove_breakpoint(self, file_path: str, line: int):
            self.breakpoints_removed.append((file_path, line))

        async def continue_execution(self) -> dict[str, Any]:
            return {"body": {"reason": "breakpoint"}, "event": "stopped"}

        async def get_stack_trace(self, thread_id: int = 1) -> list[Any]:
            return [
                MockStackFrame(id=1, name="is_valid_total", source=MockSource(name="calculator.py", path="/tmp/calculator.py"), line=4),
                MockStackFrame(id=2, name="test_is_valid_total", source=MockSource(name="test_calc.py", path="/tmp/test_calc.py"), line=3),
            ]

        async def get_variables(self, frame_id: int) -> list[Any]:
            return [
                MockVariable(name="value", value="100"),
                MockVariable(name="a", value="50"),
                MockVariable(name="b", value="50"),
            ]

        async def eval_expression(self, expression: str, frame_id: int = 0) -> str:
            self.expressions_evaluated.append(expression)
            if expression == "type(value)":
                return "<class 'int'>"
            if expression == "type('100')":
                return "<class 'str'>"
            if expression == "value":
                return "100"
            return f"<eval: {expression}>"

        async def run_to_breakpoints(self, breakpoints: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "variables": {"value": "100", "a": "50", "b": "50"},
                "frames": [
                    {"name": "is_valid_total", "file": "/tmp/calculator.py", "line": 4},
                    {"name": "test_is_valid_total", "file": "/tmp/test_calc.py", "line": 3},
                ],
                "stopped_reason": "breakpoint",
                "dap_used": True,
            }

    return MockDAPClient()


@pytest.fixture
def probe_config():
    """A ProbeConfig with no Anthropic API key (triggers heuristics)."""
    from probe.config import ProbeConfig

    return ProbeConfig(
        max_iterations=2,
        quiet=True,
        timeout_seconds=30,
    )
