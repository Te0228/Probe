"""Anthropic Claude backend.

Uses Claude's ``tool_use`` mechanism with forced ``tool_choice`` to
guarantee structured output that conforms to the provided JSON schema.
"""

from __future__ import annotations

from typing import Any


class AnthropicClient:
    """Claude via Anthropic SDK with forced tool_use for structured output."""

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Set it with: export ANTHROPIC_API_KEY=your-key"
            )
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def call_with_schema(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        tool_name: str,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": tool_name,
                    "description": "Structured output for the Probe hypothesis engine",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )

        for block in response.content:
            if block.type == "tool_use":
                return dict(block.input)
        return {}

    def call_text(
        self,
        prompt: str,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = self._client.messages.create(**kwargs)
        if not response.content:
            return ""
        return response.content[0].text
