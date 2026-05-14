"""Probe tools — debugging actions exposed to the hypothesis engine.

Each tool communicates via Protocol/ABC interfaces and emits TraceEvents.
"""

from probe.tools.registry import ToolRegistry

__all__ = ["ToolRegistry"]
