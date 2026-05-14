"""Configuration management for Probe — reads from environment variables and defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProbeConfig:
    """Probe configuration loaded from environment variables with sensible defaults."""

    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    model: str = field(
        default_factory=lambda: os.environ.get("PROBE_MODEL", "claude-sonnet-4-20250514")
    )
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
