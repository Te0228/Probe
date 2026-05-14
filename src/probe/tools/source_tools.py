"""Source code reading tools — read file contents, search for patterns."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


class ReadFileTool:
    """Tool: read the contents of a source file."""

    name = "read_file"

    def __init__(self, tracer: Any = None) -> None:
        self._tracer = tracer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        file_path = kwargs.get("file", "")
        if not file_path:
            return {"error": "file is required"}
        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"}
        try:
            content = p.read_text(encoding="utf-8")
            self._emit("observe", {"tool": "read_file", "file": str(p), "lines": len(content.splitlines())})
            return {"file": str(p), "content": content}
        except Exception as e:
            return {"error": str(e)}

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)


class SearchCodebaseTool:
    """Tool: search for a pattern across source files."""

    name = "search_codebase"

    def __init__(self, tracer: Any = None) -> None:
        self._tracer = tracer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        pattern = kwargs.get("pattern", "")
        directory = kwargs.get("directory", ".")
        if not pattern:
            return {"error": "pattern is required"}
        results: list[dict[str, Any]] = []
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return {"error": f"Invalid regex: {e}"}
        root = Path(directory)
        if not root.exists():
            return {"error": f"Directory not found: {directory}"}
        for py_file in root.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                for i, line in enumerate(content.splitlines(), start=1):
                    if compiled.search(line):
                        results.append({"file": str(py_file), "line": i, "text": line.strip()[:120]})
            except Exception:
                pass
        self._emit("observe", {"tool": "search_codebase", "pattern": pattern, "matches": len(results)})
        return {"pattern": pattern, "matches": results}

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)
