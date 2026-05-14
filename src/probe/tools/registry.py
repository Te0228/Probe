"""Tool registry — central catalog of all available debugging tools.

Tools are registered by name and expose a Protocol/ABC interface so the
orchestrator depends on abstractions, not concrete implementations.
"""

from __future__ import annotations

from typing import Any, Protocol


class Tool(Protocol):
    """Protocol for a debugging tool."""

    @property
    def name(self) -> str:
        ...

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        ...


class ToolRegistry:
    """Registry of available debugging tools. Tools are callable by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool by its name."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return sorted(self._tools.keys())

    def execute(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """Execute a tool by name (synchronous wrapper)."""
        import asyncio
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"Unknown tool: {name}"}
        return asyncio.run(tool.execute(**kwargs))
