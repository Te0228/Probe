"""Abstract DAP adapter interface — defines the contract for language-specific
debug adapters."""

from __future__ import annotations

from typing import Any, Protocol

from probe.dap.protocol import Breakpoint, StackFrame, Thread, Variable


class DAPAdapter(Protocol):
    """Protocol defining the interface for a DAP debug adapter.

    Implementations wrap language-specific debug adapters (e.g., debugpy for
    Python) and communicate via stdin/stdout JSON-RPC.
    """

    async def start(self, program: str, args: list[str] | None = None, cwd: str | None = None) -> None:
        """Start a debug session with the given program and arguments."""
        ...

    async def stop(self) -> None:
        """Stop the debug session and clean up resources."""
        ...

    async def send_request(self, command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a DAP request and return the response body."""
        ...

    async def wait_for_stopped(self, timeout: float = 30.0) -> dict[str, Any]:
        """Wait for a 'stopped' event and return the event body."""
        ...

    async def read_event(self, event_type: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
        """Read the next DAP event, optionally filtered by type."""
        ...
