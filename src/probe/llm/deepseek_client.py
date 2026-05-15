"""DeepSeek backend.

DeepSeek serves an OpenAI-compatible API at ``https://api.deepseek.com``.
Structured output is achieved via OpenAI-style function calling: declare
a ``tools`` entry with the JSON schema as ``parameters`` and force the
choice via ``tool_choice``.
"""

from __future__ import annotations

import json
from typing import Any

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekClient:
    """DeepSeek via the OpenAI SDK with a custom base_url."""

    def __init__(self, api_key: str, model: str = "deepseek-chat") -> None:
        if not api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is not set. "
                "Set it with: export DEEPSEEK_API_KEY=your-key"
            )
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        self._model = model

    def call_with_schema(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        tool_name: str,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": "Structured output for the Probe hypothesis engine",
                        "parameters": schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": tool_name}},
        )

        message = response.choices[0].message

        if message.tool_calls:
            args_str = message.tool_calls[0].function.arguments
            try:
                return json.loads(args_str)
            except json.JSONDecodeError:
                return {}

        # Some models leak the JSON into message.content even with a forced
        # tool_choice. Try to recover.
        if message.content:
            try:
                return json.loads(message.content)
            except json.JSONDecodeError:
                pass

        return {}

    def call_text(self, prompt: str, max_tokens: int = 1024) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
