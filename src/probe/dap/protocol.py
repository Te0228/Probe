"""Debug Adapter Protocol (DAP) type definitions.

Defines the message structures used to communicate with DAP-compliant
debug adapters (e.g., debugpy) via JSON-RPC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── JSON-RPC protocol messages ──────────────────────────────────────────────────


@dataclass
class Request:
    """A DAP request message sent to the debug adapter."""

    seq: int
    command: str
    arguments: dict[str, Any] = field(default_factory=dict)
    type: str = "request"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "command": self.command,
            "arguments": self.arguments,
        }


@dataclass
class Response:
    """A DAP response message received from the debug adapter."""

    seq: int
    request_seq: int
    success: bool
    command: str
    body: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    type: str = "response"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Response":
        return cls(
            seq=d.get("seq", 0),
            request_seq=d.get("request_seq", 0),
            success=d.get("success", False),
            command=d.get("command", ""),
            body=d.get("body", {}),
            message=d.get("message", ""),
            type=d.get("type", "response"),
        )


@dataclass
class Event:
    """A DAP event message received from the debug adapter (e.g., stopped, output)."""

    seq: int
    event: str
    body: dict[str, Any] = field(default_factory=dict)
    type: str = "event"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(
            seq=d.get("seq", 0),
            event=d.get("event", ""),
            body=d.get("body", {}),
            type=d.get("type", "event"),
        )


# ── DAP domain types ────────────────────────────────────────────────────────────


@dataclass
class Source:
    """A source file reference."""

    name: str = ""
    path: str = ""
    source_reference: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Source":
        return cls(
            name=d.get("name", ""),
            path=d.get("path", ""),
            source_reference=d.get("sourceReference", 0),
        )


@dataclass
class StackFrame:
    """A stack frame from a stopped thread."""

    id: int
    name: str
    source: Source | None = None
    line: int = 0
    column: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StackFrame":
        src = d.get("source")
        return cls(
            id=d.get("id", 0),
            name=d.get("name", ""),
            source=Source.from_dict(src) if src else None,
            line=d.get("line", 0),
            column=d.get("column", 0),
        )


@dataclass
class Variable:
    """A variable from a stack frame or evaluate response."""

    name: str
    value: str
    type: str = ""
    variables_reference: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Variable":
        return cls(
            name=d.get("name", ""),
            value=d.get("value", ""),
            type=d.get("type", ""),
            variables_reference=d.get("variablesReference", 0),
        )


@dataclass
class Breakpoint:
    """A breakpoint location."""

    id: int = 0
    verified: bool = False
    source: Source | None = None
    line: int = 0
    message: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Breakpoint":
        src = d.get("source")
        return cls(
            id=d.get("id", 0),
            verified=d.get("verified", False),
            source=Source.from_dict(src) if src else None,
            line=d.get("line", 0),
            message=d.get("message", ""),
        )


@dataclass
class Thread:
    """A thread in the debuggee."""

    id: int
    name: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Thread":
        return cls(id=d.get("id", 0), name=d.get("name", ""))
