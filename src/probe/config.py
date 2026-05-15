"""Configuration management for Probe — reads from environment variables and defaults."""

import os
from dataclasses import dataclass, field


@dataclass
class ProbeConfig:
    """Probe configuration loaded from environment variables with sensible defaults.

    LLM backend selection:
      - ``LLM_BACKEND``: ``"deepseek"`` (default) or ``"anthropic"``
      - ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_MODEL``: used when backend = deepseek
      - ``ANTHROPIC_API_KEY`` / ``PROBE_MODEL``: used when backend = anthropic (opt-in extra)
    """

    # ── LLM backend selection ──────────────────────────────────────────────────
    llm_backend: str = field(
        default_factory=lambda: os.environ.get("LLM_BACKEND", "deepseek")
    )

    # ── Anthropic ──────────────────────────────────────────────────────────────
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    model: str = field(
        default_factory=lambda: os.environ.get("PROBE_MODEL", "claude-sonnet-4-20250514")
    )

    # ── DeepSeek ───────────────────────────────────────────────────────────────
    deepseek_api_key: str = field(
        default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY", "")
    )
    deepseek_model: str = field(
        default_factory=lambda: os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    )

    # ── Run-time options ───────────────────────────────────────────────────────
    max_iterations: int = 3
    output_dir: str = "probe_traces"
    quiet: bool = False
    debugpy_path: str = "debugpy"
    test_command_prefix: str = "pytest"
    timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "ProbeConfig":
        """Create a config from environment variables."""
        return cls()
