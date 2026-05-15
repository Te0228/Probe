"""LLM backend abstraction.

Probe's hypothesis engine depends on the ``LLMClient`` protocol, not on
any specific provider SDK. Adding a new provider is implementing one
class against this protocol — the same pattern Probe uses for DAP
adapters.
"""

from probe.llm.base import LLMClient, get_llm_client

__all__ = ["LLMClient", "get_llm_client"]
