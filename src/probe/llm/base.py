"""LLMClient protocol + factory.

The hypothesis engine needs two things from any LLM provider:
  1. Schema-constrained structured output (for hypothesis generation).
  2. Free-form text completion (for per-hypothesis evaluation).

Every provider adapter implements both methods. The factory dispatches
on ``ProbeConfig.llm_backend``.
"""

from __future__ import annotations

from typing import Any, Protocol


class LLMClient(Protocol):
    """Provider-agnostic LLM interface."""

    def call_with_schema(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        tool_name: str,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Call the model forcing JSON output that matches ``schema``.

        Returns the parsed dict. Implementations must enforce structured
        output via the provider's tool-use / function-calling mechanism.
        """
        ...

    def call_text(
        self,
        prompt: str,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> str:
        """Call the model with a user prompt (and optional system prompt), return raw text."""
        ...


def get_llm_client(config) -> LLMClient:
    """Build the configured LLM backend.

    Reads ``config.llm_backend``: ``"deepseek"`` (default) or ``"anthropic"``.
    The actual SDK is imported lazily so users without a given provider
    installed are not forced to install it.
    """
    backend = (getattr(config, "llm_backend", "") or "deepseek").lower()

    if backend == "anthropic":
        from probe.llm.anthropic_client import AnthropicClient
        return AnthropicClient(
            api_key=config.anthropic_api_key,
            model=config.model,
        )

    if backend == "deepseek":
        from probe.llm.deepseek_client import DeepSeekClient
        return DeepSeekClient(
            api_key=config.deepseek_api_key,
            model=config.deepseek_model,
        )

    raise ValueError(
        f"Unknown LLM backend: {backend!r}. Expected 'anthropic' or 'deepseek'."
    )
