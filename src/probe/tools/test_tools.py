"""Test runner tools — run tests and collect results."""

from __future__ import annotations

import subprocess
import time
from typing import Any


class RunTestTool:
    """Tool: run a test command and collect output."""

    name = "run_test"

    def __init__(self, tracer: Any = None, timeout: int = 60) -> None:
        self._tracer = tracer
        self._timeout = timeout

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        command = kwargs.get("command", "")
        cwd = kwargs.get("cwd", ".")
        if not command:
            return {"error": "command is required"}
        start = time.monotonic()
        try:
            result = subprocess.run(
                command.split(),
                capture_output=True,
                text=True,
                timeout=self._timeout,
                cwd=cwd,
            )
            elapsed = time.monotonic() - start
            self._emit("execute", {
                "tool": "run_test",
                "command": command,
                "exit_code": result.returncode,
                "elapsed": round(elapsed, 3),
            })
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "elapsed": round(elapsed, 3),
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Test timed out after {self._timeout}s"}
        except Exception as e:
            return {"error": str(e)}

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)
