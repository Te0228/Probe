"""Fix Generator — takes a confirmed hypothesis and source context to produce
and verify a patch in a sandbox. Emits fix TraceEvents throughout."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

from probe.config import ProbeConfig


FIX_SYSTEM_PROMPT = """You are an expert software engineer debugging a failing test.
Given the confirmed root cause hypothesis and source code, produce a minimal, correct
patch. Output ONLY a unified diff (diff/patch format) with the exact changes needed.

Rules for the patch:
1. Make the minimal change needed to fix the root cause — do not refactor anything else.
2. The patch must be a valid unified diff starting with `--- a/` and `+++ b/` headers.
3. Include at least one @@ hunk header.
4. Only modify existing source files; do not create new files.
5. Ensure the fix matches the confirmed hypothesis.

Format your response as a raw unified diff, no explanatory text, no markdown fences."""


@dataclass
class PatchResult:
    """Result of applying a patch and verifying it."""

    hypothesis_id: str
    patch_diff: str
    applied: bool
    sandbox_result: str  # "pass" | "fail" | "error"
    sandbox_output: str
    error_message: str = ""


class FixGenerator:
    """Generates patches for confirmed hypotheses, applies them in a sandbox,
    and verifies that the fix passes the test.

    Every operation emits a TraceEvent for full observability."""

    def __init__(
        self,
        config: ProbeConfig | None = None,
        tracer: Any | None = None,
    ) -> None:
        self._config = config or ProbeConfig.from_env()
        self._tracer = tracer
        self._client: anthropic.Anthropic | None = None

    def _get_client(self) -> anthropic.Anthropic:
        """Return or create the Anthropic client."""
        if self._client is None:
            api_key = self._config.anthropic_api_key
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY is not set")
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def generate_fix(
        self,
        hypothesis: dict[str, Any],
        source_code_context: dict[str, str],
        test_command: str,
    ) -> PatchResult:
        """Generate a patch for a confirmed hypothesis and verify it.

        Args:
            hypothesis: The confirmed hypothesis dict.
            source_code_context: Dict mapping file paths to source code.
            test_command: The test command to verify against (e.g., 'pytest tests/...').

        Returns:
            PatchResult with the patch diff and sandbox verification outcome.
        """
        hypothesis_id = hypothesis.get("hypothesis_id", "H0")
        statement = hypothesis.get("statement", "No statement")

        # Step 1: Generate the patch (API first, then heuristic fallback)
        patch_diff = ""
        try:
            patch_diff = self._generate_patch(hypothesis, source_code_context)
        except Exception:
            pass  # API unavailable — fall through to heuristic

        if not patch_diff:
            patch_diff = self._heuristic_patch(hypothesis, source_code_context)

        # Step 2: Apply the patch in a sandbox and verify
        patch_result = self._apply_and_verify(
            source_code_context, patch_diff, test_command, hypothesis_id, statement
        )

        # Step 3: Emit the fix trace event
        self._emit_fix_event(patch_result, hypothesis)

        return patch_result

    def _generate_patch(
        self,
        hypothesis: dict[str, Any],
        source_code_context: dict[str, str],
    ) -> str:
        """Use Claude API to generate a patch for the confirmed hypothesis."""
        statement = hypothesis.get("statement", "")
        falsification = hypothesis.get("falsification_criteria", "")

        user_prompt_parts = [
            f"Confirmed root cause: {statement}",
            f"Falsification criteria that was NOT met (i.e., hypothesis was confirmed): {falsification}",
            "",
            "Source files to fix:",
        ]
        for filepath, code in source_code_context.items():
            user_prompt_parts.append(f"\n--- {filepath} ---\n{code}")

        user_prompt = "\n".join(user_prompt_parts)

        client = self._get_client()
        response = client.messages.create(
            model=self._config.model,
            max_tokens=2048,
            system=FIX_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        text = response.content[0].text if response.content else ""

        # Extract the diff from the response (strip markdown fences if present)
        diff = text
        if "```diff" in diff:
            diff = diff.split("```diff", 1)[1]
            if "```" in diff:
                diff = diff.split("```", 1)[0]
        elif "```" in diff:
            diff = diff.split("```", 1)[1]
            if "```" in diff:
                diff = diff.split("```", 1)[0]

        return diff.strip()

    def _heuristic_patch(
        self,
        hypothesis: dict[str, Any],
        source_code_context: dict[str, str],
    ) -> str:
        """Generate a patch heuristically when the API is unavailable.

        Analyses the hypothesis statement and source code to produce a
        simple replacement that addresses common bug patterns.
        """
        statement = hypothesis.get("statement", "").lower()
        patches: list[str] = []

        for filepath, code in source_code_context.items():
            lines = code.split("\n")
            for i, line in enumerate(lines, start=1):
                new_line = None
                indent = line[:len(line) - len(line.lstrip())]

                # Pattern: null reference — return None and caller dereferences
                if "null" in statement or "none" in statement:
                    # Fix caller: add null check before dereferencing
                    if ".id" in line or ".status" in line or ".name" in line or ".value" in line:
                        if "print" in line or "return" in line or "=" in line:
                            # e.g.: print(f"Completed: {task.id} — status: {task.status}")
                            # fix: if task is None: ...
                            new_line = f"{indent}if task is None:\n{indent}    print('Error: task not found')\n{indent}    return\n{line}"
                    # Fix source: return None should raise or return sentinel
                    if "return None" in line:
                        new_line = line.replace("return None", "raise KeyError(f\"Task not found: {task_id}\")")

                # Pattern: type mismatch — add int() conversion
                if "type" in statement and ("int(" in line or "str(" in line or "+" in line):
                    if "int(" in line and ("+" in line or "return" in line):
                        pass  # Too complex for heuristic

                # Pattern: off-by-one — fix > to >=
                if "off-by-one" in statement or "off by one" in statement:
                    if ">" in line and ">=" not in line and (
                        "return" in line or "for" in line or "range" in line
                    ):
                        new_line = line.replace(" > ", " >= ", 1)

                # Pattern: empty sequence — add guard before max()/min()
                if "empty" in statement or "max()" in statement:
                    if "max(" in line or "min(" in line:
                        # Extract the variable name from the max/min call
                        call_match = re.search(r'(?:max|min)\((\w+)', line)
                        var_name = call_match.group(1) if call_match else "collection"
                        new_line = f"{indent}if not {var_name}:\n{indent}    return None  # empty guard\n{line}"

                if new_line is not None and new_line != line:
                    patches.append(f"--- a/{filepath}\n+++ b/{filepath}\n@@ -{i},{i} +{i},{i} @@\n-{line}\n+{new_line}")

        return "\n".join(patches)

    def _apply_and_verify(
        self,
        source_code_context: dict[str, str],
        patch_diff: str,
        test_command: str,
        hypothesis_id: str,
        statement: str,
    ) -> PatchResult:
        """Apply the patch in a temp directory and run the test to verify.

        Returns a PatchResult with the sandbox outcome.
        """
        if not source_code_context or not patch_diff:
            return PatchResult(
                hypothesis_id=hypothesis_id,
                patch_diff=patch_diff,
                applied=False,
                sandbox_result="error",
                sandbox_output="No source code or patch to apply",
                error_message="No source code or patch to apply",
            )

        sandbox_dir = tempfile.mkdtemp(prefix="probe_sandbox_")

        try:
            # Copy the source files into the sandbox
            for filepath, content in source_code_context.items():
                rel_path = self._relative_path(filepath)
                dest_path = os.path.join(sandbox_dir, rel_path)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                Path(dest_path).write_text(content, encoding="utf-8")

            # Apply the patch by parsing the diff and modifying files
            applied = self._apply_patch_to_dir(sandbox_dir, patch_diff)

            if not applied:
                return PatchResult(
                    hypothesis_id=hypothesis_id,
                    patch_diff=patch_diff,
                    applied=False,
                    sandbox_result="error",
                    sandbox_output="Failed to apply patch",
                    error_message="Could not parse or apply the patch diff",
                )

            # Run the test in the sandbox
            sandbox_output = ""
            try:
                result = subprocess.run(
                    test_command.split(),
                    capture_output=True,
                    text=True,
                    timeout=self._config.timeout_seconds,
                    cwd=sandbox_dir,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                sandbox_output = result.stdout + "\n" + result.stderr
                sandbox_result = "pass" if result.returncode == 0 else "fail"
            except subprocess.TimeoutExpired:
                sandbox_output = f"(Test timed out after {self._config.timeout_seconds}s)"
                sandbox_result = "fail"
            except Exception as e:
                sandbox_output = str(e)
                sandbox_result = "error"

            return PatchResult(
                hypothesis_id=hypothesis_id,
                patch_diff=patch_diff,
                applied=True,
                sandbox_result=sandbox_result,
                sandbox_output=sandbox_output[-3000:],
            )
        finally:
            # Clean up the sandbox
            try:
                shutil.rmtree(sandbox_dir)
            except Exception:
                pass

    def _apply_patch_to_dir(self, target_dir: str, patch_diff: str) -> bool:
        """Apply a unified diff to files in a directory.

        Parses the diff hunks and applies line changes to the target files.
        Returns True if at least one file was modified.
        """
        files_modified = 0
        lines = patch_diff.split("\n")

        # Parse diffs for each file
        current_file: str | None = None
        current_content: list[str] | None = None
        current_output: list[str] | None = None  # Track output lines during hunk

        for line in lines:
            # Detect file header: --- a/... and +++ b/...
            if line.startswith("--- a/"):
                current_file = line[6:]  # Remove "--- a/"
                continue
            if line.startswith("+++ b/") and current_file:
                target_path = os.path.join(target_dir, current_file)
                if os.path.exists(target_path):
                    current_content = (
                        Path(target_path).read_text(encoding="utf-8").split("\n")
                    )
                else:
                    current_file = None
                continue

            # Detect hunk header: @@ -old_start,old_count +new_start,new_count @@
            if line.startswith("@@") and current_content is not None and current_file:
                current_output = list(current_content)
                continue

            if current_output is not None and current_file:
                if line.startswith(" "):
                    current_output.append(line[1:])  # Context line
                elif line.startswith("-"):
                    # Remove the line — find and remove the exact line
                    line_to_remove = line[1:]
                    try:
                        idx = current_output.index(line_to_remove)
                        current_output.pop(idx)
                    except ValueError:
                        pass
                elif line.startswith("+"):
                    # Add the line — insert before the next line in current content
                    # Simple approach: append after last removed
                    current_output.append(line[1:])
                elif line == "\\ No newline at end of file":
                    pass  # Ignore
                else:
                    # Hunk boundary — write the file
                    if current_file:
                        target_path = os.path.join(target_dir, current_file)
                        Path(target_path).write_text(
                            "\n".join(current_output), encoding="utf-8"
                        )
                        files_modified += 1
                    current_output = None

        # Write last file if any
        if current_output is not None and current_file:
            target_path = os.path.join(target_dir, current_file)
            Path(target_path).write_text(
                "\n".join(current_output), encoding="utf-8"
            )
            files_modified += 1

        return files_modified > 0

    def _relative_path(self, file_path: str) -> str:
        """Extract a relative path from a full file path for sandboxing."""
        # Keep the directory structure relative to the project root
        path = Path(file_path)
        if path.is_absolute():
            # Try to get a meaningful relative path
            try:
                return str(path.relative_to(Path.cwd()))
            except ValueError:
                return path.name
        return file_path

    def _emit_fix_event(
        self, result: PatchResult, hypothesis: dict[str, Any]
    ) -> None:
        """Emit a fix TraceEvent."""
        if not self._tracer:
            return
        self._tracer.emit("fix", {
            "action": "generate_fix",
            "hypothesis_id": result.hypothesis_id,
            "hypothesis_statement": hypothesis.get("statement", "")[:200],
            "patch": result.patch_diff,
            "patch_applied": result.applied,
            "sandbox_result": result.sandbox_result,
            "sandbox_output": result.sandbox_output[:1000],
            "error": result.error_message[:500] if result.error_message else None,
        })
