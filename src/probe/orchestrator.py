"""ReAct orchestrator — the main hypothesis-driven debugging loop.

Observes the bug, generates hypotheses, instruments via DAP, executes the
test, collects runtime state, analyses evidence against falsification
criteria, iterates if needed, and outputs a root cause diagnosis.

Every step emits a TraceEvent. No step is invisible.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from probe.config import ProbeConfig
from probe.dap.adapters.python import PythonAdapter
from probe.dap.client import DAPClient
from probe.hypothesis import HypothesisEngine


class Orchestrator:
    """ReAct main loop for hypothesis-driven debugging.

    Architecture:
        observe -> hypothesis -> instrument -> execute -> analyse -> (iterate | conclude)

    All modules communicate via interfaces (Protocol/ABC). The orchestrator
    depends on abstractions (tracer, hypothesis engine, DAP client), not
    concrete implementations.
    """

    def __init__(
        self,
        tracer,
        config: ProbeConfig | None = None,
        hypothesis_engine: HypothesisEngine | None = None,
    ) -> None:
        self._tracer = tracer
        self._config = config or ProbeConfig.from_env()
        self._hypothesis_engine = hypothesis_engine or HypothesisEngine(
            config=self._config, tracer=tracer
        )
        self._dap_client = None  # Set during run

    def run(
        self,
        test_command: str | None = None,
        bug_description: str | None = None,
        source_code: dict[str, str] | None = None,
        script: str | None = None,
        script_args: list[str] | None = None,
        run_command: str | None = None,
    ) -> dict[str, Any]:
        """Run the full ReAct loop synchronously.

        Args:
            test_command: A pytest command to run.
            bug_description: Natural language bug description.
            source_code: Dict mapping file paths to source code content.
            script: Path to a Python script to debug directly via DAP.
            script_args: Arguments to pass to the script.
            run_command: Arbitrary shell command to run under DAP (e.g. 'python -m myapp serve').

        Returns:
            Dict with keys: root_cause, verdict, iterations, evidence.
        """
        return asyncio.run(
            self._run_async(test_command, bug_description, source_code,
                            script=script, script_args=script_args,
                            run_command=run_command)
        )

    async def _run_async(
        self,
        test_command: str | None,
        bug_description: str | None,
        source_code: dict[str, str] | None,
        script: str | None = None,
        script_args: list[str] | None = None,
        run_command: str | None = None,
    ) -> dict[str, Any]:
        """Async implementation of the ReAct loop."""

        # ── Step 0: Observe ────────────────────────────────────────────────
        bug_info = await self._observe(test_command, bug_description, source_code,
                                       script=script, run_command=run_command)

        # ── Set up DAP bridge ──────────────────────────────────────────────
        adapter = PythonAdapter()
        self._dap_client = DAPClient(adapter=adapter, tracer=self._tracer)

        # Determine launch parameters for DAP
        module: str = ""
        program: str = ""
        dap_args: list[str] = []

        if script:
            program = script
            dap_args = script_args or []
        elif run_command:
            # Parse arbitrary command: "python -m myapp serve" -> program="python", args=["-m", "myapp", "serve"]
            # Or: "python script.py --flag" -> runs via debugpy as a program
            parts = run_command.strip().split()
            if parts:
                if parts[0] in ("python", "python3"):
                    parts = parts[1:]  # Strip python/python3 prefix
                # Handle "python -m myapp" -> module mode
                if len(parts) >= 2 and parts[0] == "-m":
                    module = parts[1]
                    dap_args = parts[2:]
                else:
                    program = parts[0]
                    dap_args = parts[1:]
        elif test_command:
            parts = test_command.strip().split()
            if parts and parts[0] == "pytest":
                module = "pytest"
                dap_args = parts[1:]
            else:
                program = parts[0] if parts else ""
                dap_args = parts[1:]

        try:
            await adapter.start(
                program=program,
                args=dap_args,
                cwd=os.getcwd(),
                module=module,
            )
        except Exception as e:
            self._emit("execute", {
                "action": "dap_start_failed",
                "error": str(e),
            })
            self._dap_client = None  # Prevent broken client from being used later

        # ── Main ReAct loop ────────────────────────────────────────────────
        all_evidence: list[dict[str, Any]] = []
        max_iterations = self._config.max_iterations

        try:
            for iteration in range(max_iterations):
                # ── Step 1: Hypothesize ────────────────────────────────────
                hypotheses = self._hypothesize(
                    bug_info["description"],
                    source_code or {},
                    previous_evidence=all_evidence,
                    iteration=iteration,
                )

                if not hypotheses:
                    # Fallback: create a basic hypothesis if LLM fails
                    hypotheses = [{
                        "hypothesis_id": "H1",
                        "statement": "The bug is caused by a type mismatch in the code path.",
                        "confidence": 0.5,
                        "verification_plan": [],
                        "falsification_criteria": "",
                    }]

                # ── Step 2: Instrument — determine breakpoint locations ────
                breakpoints = self._plan_instrumentation(
                    hypotheses, source_code or {}, bug_info
                )

                # ── Step 3: Execute — run test with DAP, collect state ────
                runtime_state = await self._execute(
                    test_command, bug_info, breakpoints, source_code
                )

                # ── Step 4: Analyse — compare state against falsification ──
                analysis = self._analyse(hypotheses, runtime_state)
                all_evidence.extend(analysis.get("evidence", []))

                # ── Step 5: Conclude ───────────────────────────────────────
                verdicts = analysis.get("verdicts", {})
                confirmed = [
                    hid for hid, v in verdicts.items() if v == "confirmed"
                ]

                if confirmed:
                    # Root cause found!
                    confirmed_h = next(
                        (h for h in hypotheses if h["hypothesis_id"] in confirmed),
                        hypotheses[0],
                    )
                    self._emit("fix", {
                        "action": "conclude",
                        "verdict": "confirmed",
                        "root_cause": confirmed_h.get("statement", ""),
                        "hypothesis_id": confirmed_h.get("hypothesis_id", ""),
                        "confidence": confirmed_h.get("confidence", 0),
                        "iterations": iteration + 1,
                        "all_evidence": all_evidence,
                    })
                    return {
                        "root_cause": confirmed_h.get("statement", ""),
                        "verdict": "confirmed",
                        "iterations": iteration + 1,
                        "evidence": all_evidence,
                    }

                all_refuted = all(
                    v == "refuted" for v in verdicts.values()
                ) if verdicts else False

                if all_refuted and iteration < max_iterations - 1:
                    # All refuted — re-hypothesize
                    self._emit("iterate", {
                        "action": "re_hypothesize",
                        "iteration": iteration + 1,
                        "refuted_hypotheses": [
                            h.get("hypothesis_id") for h in hypotheses
                        ],
                        "new_evidence": runtime_state,
                    })
                    continue

                if all_refuted:
                    # Max iterations reached with all refuted
                    self._emit("fix", {
                        "action": "conclude",
                        "verdict": "inconclusive",
                        "reason": f"All {len(hypotheses)} hypotheses refuted after {max_iterations} iterations.",
                        "iterations": iteration + 1,
                        "all_evidence": all_evidence,
                    })
                    return {
                        "root_cause": "",
                        "verdict": "inconclusive",
                        "iterations": iteration + 1,
                        "evidence": all_evidence,
                    }

                # Partial inconclusive — treat best-confidence as tentative
                best = max(hypotheses, key=lambda h: h.get("confidence", 0))
                self._emit("fix", {
                    "action": "conclude",
                    "verdict": "inconclusive",
                    "best_hypothesis": best.get("statement", ""),
                    "confidence": best.get("confidence", 0),
                    "iterations": iteration + 1,
                    "all_evidence": all_evidence,
                })
                return {
                    "root_cause": best.get("statement", ""),
                    "verdict": "inconclusive",
                    "iterations": iteration + 1,
                    "evidence": all_evidence,
                }

            # Shouldn't reach here, but just in case
            return {
                "root_cause": "",
                "verdict": "error",
                "iterations": max_iterations,
                "evidence": all_evidence,
            }
        finally:
            # Always tear down the DAP session
            try:
                await adapter.stop()
            except Exception:
                pass

    # ── Step implementations ─────────────────────────────────────────────────

    async def _observe(
        self,
        test_command: str | None,
        bug_description: str | None,
        source_code: dict[str, str] | None,
        script: str | None = None,
        run_command: str | None = None,
    ) -> dict[str, Any]:
        """Observe the bug — run the code to get failure output, or parse
        the bug description."""
        description = ""
        test_output = ""

        if script:
            import sys as _sys
            cmd = [_sys.executable, script]
            test_output = self._run_test_no_debug(" ".join(cmd))
            description = f"python {script}"

        if run_command:
            test_output = self._run_test_no_debug(run_command)
            if not description:
                description = run_command

        if test_command:
            test_output = self._run_test_no_debug(test_command)
            if not description:
                description = test_command

        if bug_description:
            description = bug_description

        self._emit("observe", {
            "action": "observe",
            "test_command": test_command,
            "script": script,
            "run_command": run_command,
            "bug_description": description,
            "test_output": test_output[:2000] if test_output else "",
        })

        return {
            "description": description,
            "test_output": test_output,
            "source_code": source_code or {},
        }

    def _hypothesize(
        self,
        bug_description: str,
        source_code: dict[str, str],
        previous_evidence: list[dict[str, Any]] | None = None,
        iteration: int = 0,
    ) -> list[dict[str, Any]]:
        """Generate hypotheses using the Claude API."""
        return self._hypothesis_engine.generate_hypotheses(
            bug_description=bug_description,
            source_code_context=source_code,
            previous_evidence=previous_evidence,
            iteration=iteration,
        )

    def _plan_instrumentation(
        self,
        hypotheses: list[dict[str, Any]],
        source_code: dict[str, str],
        bug_info: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Determine breakpoint locations from hypothesis verification plans.

        If the LLM didn't provide specific breakpoints, use heuristics:
        place breakpoints at likely error locations in the source code.
        """
        breakpoints: list[dict[str, Any]] = []

        for h in hypotheses:
            plan = h.get("verification_plan", [])
            for step in plan:
                if step.get("action") == "set_breakpoint":
                    file = step.get("file", "")
                    line = step.get("line", 0)
                    if file and line:
                        # Resolve file path against source_code to get absolute path
                        resolved_file = self._resolve_file_from_source(
                            file, source_code or {}
                        )
                        breakpoints.append({
                            "file": resolved_file or file,
                            "line": line,
                            "condition": step.get("condition"),
                            "hypothesis_id": h.get("hypothesis_id", "?"),
                        })

        # If no breakpoints from LLM, use heuristics
        if not breakpoints and source_code:
            breakpoints = self._heuristic_breakpoints(source_code, bug_info)

        self._emit("instrument", {
            "action": "plan_instrumentation",
            "breakpoints": breakpoints,
            "hypothesis_count": len(hypotheses),
        })

        return breakpoints

    def _heuristic_breakpoints(
        self, source_code: dict[str, str], bug_info: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Generate breakpoint locations using simple heuristics.

        Places breakpoints at lines likely to contain the bug:
        - Lines with comparison operators (==, !=, <, >, is, in)
        - Lines with type conversion (int(), str(), etc.)
        - Lines with function calls that return values
        - Exception handler lines
        """
        breakpoints: list[dict[str, Any]] = []
        patterns = ["==", "!=", "is not", " is ", "type(", "int(", "str(",
                     "return ", "except", "assert"]

        for filepath, code in source_code.items():
            abs_path = self._resolve_file_path(filepath)
            lines = code.split("\n")
            for i, line_text in enumerate(lines, start=1):
                for pat in patterns:
                    if pat in line_text and not line_text.strip().startswith("#"):
                        breakpoints.append({
                            "file": abs_path,
                            "line": i,
                            "condition": None,
                            "hypothesis_id": "heuristic",
                        })
                        break  # One breakpoint per line max

        return breakpoints[:10]  # Cap at 10 breakpoints

    async def _execute(
        self,
        test_command: str | None,
        bug_info: dict[str, Any],
        breakpoints: list[dict[str, Any]],
        source_code: dict[str, str] | None,
    ) -> dict[str, Any]:
        """Execute the test with DAP instrumentation.

        Uses DAP to set breakpoints and collect real runtime state.
        Falls back to static source inspection when DAP is unavailable or fails.
        """
        state: dict[str, Any] = {
            "variables": {},
            "stack_frames": [],
            "test_output": bug_info.get("test_output", ""),
            "breakpoints_set": len(breakpoints),
            "executed": False,
            "execution_error": None,
            "dap_used": False,
        }

        # Try to run via DAP if we have a client
        if self._dap_client and breakpoints:
            try:
                collected = await self._dap_client.run_to_breakpoints(breakpoints)
                state["variables"] = collected.get("variables", {})
                state["stack_frames"] = collected.get("frames", [])
                state["executed"] = True
                state["dap_used"] = True
            except Exception as e:
                state["execution_error"] = str(e)

        # Fallback: inspect source code statically at breakpoint locations
        if source_code and breakpoints:
            static_state = self._static_inspect(source_code, breakpoints)
            if not state.get("variables"):
                state["variables"] = static_state.get("variables", {})
            if not state.get("stack_frames"):
                state["stack_frames"] = static_state.get("stack_frames", [])

        # Re-run test to capture fresh output if needed
        if test_command and not state["test_output"]:
            state["test_output"] = self._run_test_no_debug(test_command)

        self._emit("execute", {
            "action": "execute",
            "breakpoints_set": len(breakpoints),
            "runtime_state": state,
        })

        return state

    def _static_inspect(
        self,
        source_code: dict[str, str],
        breakpoints: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Perform static inspection of source code at breakpoint locations.

        This is a fallback when DAP is not available. It extracts variable
        names and type hints from the source at breakpoint lines.
        """
        variables: dict[str, str] = {}
        stack_frames: list[dict[str, Any]] = []

        for bp in breakpoints:
            filepath = bp["file"]
            line_no = bp["line"]

            # Find the matching source code
            for src_path, code in source_code.items():
                abs_path = self._resolve_file_path(src_path)
                if abs_path == filepath or filepath.endswith(os.path.basename(src_path)):
                    lines = code.split("\n")
                    if line_no <= len(lines):
                        line_text = lines[line_no - 1].strip()
                        # Extract variable names from the line
                        # Simple heuristic: look for identifiers that look like variables
                        import re
                        # Extract words that look like variable assignments or usages
                        words = re.findall(r'\b([a-zA-Z_]\w*)\b', line_text)
                        for w in words:
                            if w not in ("def", "class", "return", "if", "else",
                                         "elif", "for", "while", "import", "from",
                                         "try", "except", "finally", "with", "as",
                                         "in", "not", "and", "or", "is", "True",
                                         "False", "None", "print", "assert", "raise",
                                         "pass", "break", "continue"):
                                variables[w] = f"<static: used at line {line_no}>"

                    stack_frames.append({
                        "name": f"{os.path.basename(src_path)}:{line_no}",
                        "file": filepath,
                        "line": line_no,
                    })
                    break

        return {"variables": variables, "stack_frames": stack_frames}

    def _analyse(
        self,
        hypotheses: list[dict[str, Any]],
        runtime_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Analyse collected runtime state against hypothesis falsification criteria.

        Uses the HypothesisEngine's evaluate_all for LLM-powered analysis,
        with a heuristic fallback for when the API is unavailable or for
        simple cases.
        """
        try:
            result = self._hypothesis_engine.evaluate_all(hypotheses, runtime_state)
        except Exception:
            # Heuristic fallback when LLM is unavailable
            result = self._heuristic_analyse(hypotheses, runtime_state)

        # Safety net: if the LLM confirmed more than one hypothesis, the
        # evaluation is not discriminating enough.  Fall back to the heuristic
        # which enforces at-most-one-confirmed.
        verdicts = result.get("verdicts", {})
        confirmed_count = sum(
            1 for v in verdicts.values() if v == "confirmed"
        )
        if confirmed_count > 1:
            result = self._heuristic_analyse(hypotheses, runtime_state)

        # Always emit analyze trace event (evaluate_all may have already done so,
        # but we ensure it's emitted even on heuristic path)
        self._emit("analyze", {
            "action": "analyse",
            "verdicts": result.get("verdicts", {}),
            "evidence": result.get("evidence", []),
        })

        return result

    def _heuristic_analyse(
        self,
        hypotheses: list[dict[str, Any]],
        runtime_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Heuristic analysis when LLM is unavailable.

        Scores each hypothesis against the test output and runtime variables,
        then picks the best match as confirmed and refutes the rest with
        specific evidence.  Never confirms more than one hypothesis.
        """
        verdicts: dict[str, str] = {}
        evidence: list[dict[str, Any]] = []

        test_output = runtime_state.get("test_output", "")
        variables = runtime_state.get("variables", {})

        # ── Score each hypothesis ──────────────────────────────────────────
        scored: list[dict[str, Any]] = []

        for h in hypotheses:
            hid = h.get("hypothesis_id", "?")
            statement = h.get("statement", "").lower()
            falsification = h.get("falsification_criteria", "").lower()
            confidence = h.get("confidence", 0.5)

            score = 0.0
            reasons: list[str] = []

            # +2 for exact exception match
            if "typeerror" in test_output.lower() and "type" in statement:
                score += 2.0
                reasons.append("TypeError in output matches type-mismatch claim")

            if "attributeerror" in test_output.lower() and ("none" in statement or "null" in statement or "attribute" in statement):
                score += 2.0
                reasons.append("AttributeError in output matches null-reference claim")

            # +1 if hypothesis mentions a variable that appears in runtime variables.
            # Use word-boundary matching to avoid single-char names (e.g. 'a')
            # matching in every common English word.
            for var_name in variables:
                if len(var_name) < 2:
                    continue  # skip single-character names
                if re.search(
                    r'\b' + re.escape(var_name.lower()) + r'\b', statement
                ):
                    score += 1.0
                    reasons.append(f"Variable '{var_name}' found in runtime state")
                    break

            # +0.5 if falsification criteria mentions something we can check
            if falsification and variables:
                for var_name in variables:
                    if len(var_name) < 2:
                        continue
                    if re.search(
                        r'\b' + re.escape(var_name.lower()) + r'\b', falsification
                    ):
                        score += 0.5
                        reasons.append(
                            f"Falsification criteria references variable '{var_name}'"
                        )
                        break

            # +0.5 for confidence bonus
            score += confidence * 0.5

            # +1 if test output contains keywords from the statement
            stmt_keywords = set(statement.split()) - {"a", "an", "the", "is", "of", "in", "or", "to", "at", "by", "for", "with", "on", "be", "as"}
            output_lower = test_output.lower()
            matched_kw = [kw for kw in stmt_keywords if len(kw) > 2 and kw in output_lower]
            if matched_kw:
                score += min(len(matched_kw) * 0.3, 1.0)
                reasons.append(f"Keywords matched in output: {matched_kw[:3]}")

            scored.append({
                "hypothesis": h,
                "score": score,
                "reasons": reasons,
            })

        # ── Decide: best match is confirmed, rest are refuted ──────────────
        if scored:
            scored.sort(key=lambda s: s["score"], reverse=True)
            best = scored[0]

            # Only confirm if the score is significantly above zero
            if best["score"] >= 1.0:
                hid = best["hypothesis"]["hypothesis_id"]
                verdicts[hid] = "confirmed"
                evidence.append({
                    "hypothesis_id": hid,
                    "verdict": "confirmed",
                    "reasoning": (
                        f"Best-matching hypothesis (score={best['score']:.1f}): "
                        + "; ".join(best["reasons"])
                        + f" — {best['hypothesis']['statement'][:100]}"
                    ),
                    "detail": "; ".join(best["reasons"]),
                })

                # Refute all others with specific evidence
                for s in scored[1:]:
                    other_hid = s["hypothesis"]["hypothesis_id"]
                    other_stmt = s["hypothesis"]["statement"]

                    # Build a specific refutation reason
                    refute_reasons: list[str] = []
                    if s["score"] < best["score"]:
                        refute_reasons.append(
                            f"Lower match score ({s['score']:.1f} vs {best['score']:.1f})"
                        )
                    if not s["reasons"]:
                        refute_reasons.append(
                            "No evidence in runtime state or test output supports this claim"
                        )
                    else:
                        refute_reasons.append(
                            f"Weak evidence: {'; '.join(s['reasons'])}"
                        )

                    verdicts[other_hid] = "refuted"
                    evidence.append({
                        "hypothesis_id": other_hid,
                        "verdict": "refuted",
                        "reasoning": (
                            f"Refuted: {other_stmt[:100]}. "
                            + " ".join(refute_reasons)
                        ),
                        "detail": " ".join(refute_reasons),
                    })
            else:
                # No hypothesis scored well enough — mark all inconclusive
                for s in scored:
                    hid = s["hypothesis"]["hypothesis_id"]
                    verdicts[hid] = "inconclusive"
                    evidence.append({
                        "hypothesis_id": hid,
                        "verdict": "inconclusive",
                        "reasoning": f"Low match score ({s['score']:.1f}): {s['hypothesis']['statement'][:100]}",
                        "detail": f"Not enough evidence to confirm or refute",
                    })

        # ── Any hypothesis not yet scored gets inconclusive ────────────────
        for h in hypotheses:
            hid = h.get("hypothesis_id", "?")
            if hid not in verdicts:
                verdicts[hid] = "inconclusive"
                evidence.append({
                    "hypothesis_id": hid,
                    "verdict": "inconclusive",
                    "reasoning": f"Could not evaluate: {h.get('statement', '')[:100]}",
                    "detail": "No scoring data available",
                })

        return {"verdicts": verdicts, "evidence": evidence}

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _run_test_no_debug(self, test_command: str) -> str:
        """Run a test command and capture its output."""
        try:
            result = subprocess.run(
                test_command.split(),
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
                cwd=os.getcwd(),
            )
            return result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            return f"(Test timed out after {self._config.timeout_seconds}s)"
        except Exception as e:
            return f"(Test execution error: {e})"

    def _resolve_file_path(self, path: str) -> str:
        """Resolve a file path to an absolute path if possible."""
        p = Path(path)
        if p.is_absolute():
            return str(p)

        # Try relative to cwd
        cwd_path = Path(os.getcwd()) / path
        if cwd_path.exists():
            return str(cwd_path.resolve())

        return path

    def _resolve_file_from_source(
        self, filename: str, source_code: dict[str, str]
    ) -> str | None:
        """Resolve a bare filename to the matching absolute path from source_code keys.

        The source_code dict maps file paths to content.  Filenames coming from
        LLM hypotheses (e.g. "calculator.py") need to be matched against those
        keys so debugpy receives an absolute path it can verify.
        """
        basename = os.path.basename(filename)
        for src_path in source_code:
            if os.path.basename(src_path) == basename:
                return self._resolve_file_path(src_path)
        # As a last resort, if the filename itself exists relative to cwd
        p = Path(filename)
        if p.exists():
            return str(p.resolve())
        return None

    def _emit(self, step_type: str, data: dict[str, Any]) -> None:
        """Emit a TraceEvent through the tracer."""
        if self._tracer:
            self._tracer.emit(step_type, data)
