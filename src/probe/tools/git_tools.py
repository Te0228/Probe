"""Git-related debugging tools — inspect recent changes, blame, and history.

Useful for debugging regressions: identify which commit introduced a bug,
view recent changes to a file, or inspect the code at a specific revision.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


class GitDiffTool:
    """Tool: show the working-tree diff for a file or directory."""

    name = "git_diff"

    def __init__(self, tracer: Any = None) -> None:
        self._tracer = tracer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        file_path = kwargs.get("file", "")
        staged = kwargs.get("staged", False)

        cwd = self._repo_root()
        if not cwd:
            return {"error": "Not in a git repository"}

        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")
        if file_path:
            cmd.append("--")
            cmd.append(file_path)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=cwd,
            )
            diff = result.stdout
            self._emit("observe", {
                "tool": "git_diff",
                "file": file_path or cwd,
                "staged": staged,
                "hunks": diff.count("@@"),
            })
            return {"diff": diff, "file": file_path or cwd, "staged": staged}
        except subprocess.TimeoutExpired:
            return {"error": "git diff timed out"}
        except Exception as e:
            return {"error": str(e)}

    def _repo_root(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() or None
        except Exception:
            return None

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)


class GitBlameTool:
    """Tool: show line-by-line authorship for a file (useful for identifying
    which commit introduced a specific line)."""

    name = "git_blame"

    def __init__(self, tracer: Any = None) -> None:
        self._tracer = tracer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        file_path = kwargs.get("file", "")
        if not file_path:
            return {"error": "file is required"}
        start_line = kwargs.get("start_line", 1)
        end_line = kwargs.get("end_line", 0)

        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"}

        cmd = ["git", "blame", "--line-porcelain"]
        if end_line and end_line >= start_line:
            cmd.extend(["-L", f"{start_line},{end_line}"])
        cmd.extend(["--", file_path])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=p.parent,
            )
            # Parse the porcelain output into structured data
            blames: list[dict[str, str]] = []
            for line in result.stdout.splitlines():
                if line.startswith("author "):
                    blames.append({"author": line[7:]})
                elif line.startswith("author-time "):
                    if blames:
                        blames[-1]["time"] = line[12:]
                elif line.startswith("summary "):
                    if blames:
                        blames[-1]["summary"] = line[8:]
                elif line.startswith("\t"):
                    if blames:
                        blames[-1]["code"] = line[1:]

            self._emit("observe", {
                "tool": "git_blame",
                "file": file_path,
                "lines": len(blames),
            })
            return {"file": file_path, "blame": blames}
        except subprocess.TimeoutExpired:
            return {"error": "git blame timed out"}
        except Exception as e:
            return {"error": str(e)}

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)


class GitLogTool:
    """Tool: show recent commit history for a file or directory."""

    name = "git_log"

    def __init__(self, tracer: Any = None) -> None:
        self._tracer = tracer

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        file_path = kwargs.get("file", "")
        max_count = kwargs.get("max_count", 10)

        cwd = self._repo_root()
        if not cwd:
            return {"error": "Not in a git repository"}

        cmd = [
            "git", "log",
            f"--max-count={max_count}",
            "--format=%h %ad %s",
            "--date=short",
        ]
        if file_path:
            cmd.extend(["--", file_path])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=cwd,
            )
            commits: list[dict[str, str]] = []
            for line in result.stdout.strip().splitlines():
                if line:
                    parts = line.split(" ", 2)
                    if len(parts) >= 3:
                        commits.append({
                            "hash": parts[0],
                            "date": parts[1],
                            "message": parts[2],
                        })

            self._emit("observe", {
                "tool": "git_log",
                "file": file_path or cwd,
                "commits": len(commits),
            })
            return {"commits": commits, "file": file_path or cwd}
        except subprocess.TimeoutExpired:
            return {"error": "git log timed out"}
        except Exception as e:
            return {"error": str(e)}

    def _repo_root(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() or None
        except Exception:
            return None

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        if self._tracer:
            self._tracer.emit(step_type, data)
