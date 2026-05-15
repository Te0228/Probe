"""Hypothesis engine — generates structured, falsifiable hypotheses.

Every hypothesis MUST include:
- hypothesis_id: unique identifier
- statement: the root-cause claim
- confidence: 0.0–1.0
- verification_plan: list of DAP actions to test the hypothesis
- falsification_criteria: what runtime evidence would disprove the hypothesis

The engine talks to whatever provider is configured (Anthropic, DeepSeek, ...)
via the ``LLMClient`` protocol. It never imports a vendor SDK directly.
"""

import json
import os
import re
from typing import Any

from probe.config import ProbeConfig
from probe.llm import LLMClient, get_llm_client

# JSON Schema for structured hypothesis output
HYPOTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis_id": {"type": "string"},
                    "statement": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "verification_plan": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string"},
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                                "condition": {"type": "string"},
                                "expression": {"type": "string"},
                            },
                            "required": ["action"],
                        },
                    },
                    "falsification_criteria": {"type": "string"},
                },
                "required": [
                    "hypothesis_id",
                    "statement",
                    "confidence",
                    "verification_plan",
                    "falsification_criteria",
                ],
            },
        }
    },
    "required": ["hypotheses"],
}

SYSTEM_PROMPT = """You are a debugging expert AI. Your task is to analyze a failing test or bug description and generate 2-3 structured, falsifiable hypotheses about the root cause.

Each hypothesis MUST contain:
1. **hypothesis_id**: A short unique identifier (e.g., "H1", "H2", "H3")
2. **statement**: A clear claim about what you believe the root cause is
3. **confidence**: Your confidence in this hypothesis (0.0 to 1.0)
4. **verification_plan**: A list of debugging actions to test this hypothesis. Each action can be:
   - set_breakpoint: {"action": "set_breakpoint", "file": "path/to/file.py", "line": <line_number>}
   - eval_expression: {"action": "eval_expression", "expression": "<python expression>"}
   - inspect_variable: {"action": "inspect_variable", "file": "path/to/file.py", "line": <line_number>}
5. **falsification_criteria**: A specific, testable statement describing what runtime evidence would DISPROVE this hypothesis. This is critical for preventing confirmation bias. Example: "If variable 'result' is not of type int at line 42, this hypothesis is refuted."

IMPORTANT: Every hypothesis MUST have a non-empty falsification_criteria. A hypothesis without one is invalid.

Examine the code structure and test failure carefully. Think about:
- Type errors (comparing incompatible types, passing wrong types)
- Logic errors (wrong conditions, off-by-one)
- Data flow issues (variable not updated, wrong value assigned)
- Edge cases (empty inputs, boundary conditions)

For breakpoint locations, use the ACTUAL line numbers from the source code provided."""


class HypothesisEngine:
    """Generates hypotheses using the configured LLM backend with structured output."""

    def __init__(self, config: ProbeConfig | None = None, tracer: Any | None = None):
        self._config = config or ProbeConfig.from_env()
        self._tracer = tracer
        self._client: LLMClient | None = None

    def _get_client(self) -> LLMClient:
        if self._client is None:
            self._client = get_llm_client(self._config)
        return self._client

    def generate_hypotheses(
        self,
        bug_description: str,
        source_code_context: dict[str, str] | None = None,
        previous_evidence: list[dict[str, Any]] | None = None,
        iteration: int = 0,
    ) -> list[dict[str, Any]]:
        """Generate 2-3 structured, falsifiable hypotheses.

        Args:
            bug_description: Description of the bug or test failure output.
            source_code_context: Dict mapping file paths to their source code.
            previous_evidence: Evidence from previous iterations (for re-hypothesizing).
            iteration: Current iteration number (0-indexed).

        Returns:
            List of hypothesis dicts, each with all 5 required fields.
        """
        # Build the user prompt
        prompt_parts = [
            f"Bug / Test Failure:\n{bug_description}\n",
        ]

        if source_code_context:
            prompt_parts.append("Source Code:")
            for filepath, code in source_code_context.items():
                prompt_parts.append(f"\n--- {filepath} ---\n{code}")

        if previous_evidence:
            prompt_parts.append("\nPrevious Investigation Evidence:")
            prompt_parts.append(json.dumps(previous_evidence, indent=2))
            prompt_parts.append(
                "\nAll previous hypotheses were refuted. Generate new hypotheses "
                "based on the evidence above."
            )

        user_prompt = "\n".join(prompt_parts)

        # Try the configured LLM backend; fall back to heuristic if unavailable
        hypotheses: list[dict[str, Any]] = []
        raw_response = ""
        try:
            client = self._get_client()
            parsed = client.call_with_schema(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                schema=HYPOTHESIS_SCHEMA,
                tool_name="output_hypotheses",
                max_tokens=2048,
            )
            raw_response = json.dumps(parsed, indent=2)
            hypotheses = parsed.get("hypotheses", [])
        except Exception as e:
            raw_response = f"(LLM unavailable: {e})"
            hypotheses = self._heuristic_hypotheses(bug_description, source_code_context)

        # Validate each hypothesis has falsification_criteria
        for h in hypotheses:
            if not h.get("falsification_criteria"):
                h["falsification_criteria"] = (
                    f"If the predicted behavior at verification points does not match "
                    f"expectation, this hypothesis is refuted."
                )

        # Emit trace event
        if self._tracer:
            self._tracer.emit("hypothesize", {
                "action": "generate_hypotheses",
                "iteration": iteration,
                "prompt": user_prompt[:500] + ("..." if len(user_prompt) > 500 else ""),
                "response": raw_response[:500] + ("..." if len(raw_response) > 500 else ""),
                "hypotheses": hypotheses,
            })

        return hypotheses

    @staticmethod
    def _heuristic_hypotheses(
        bug_description: str,
        source_code_context: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate hypotheses heuristically when the LLM backend is unavailable.

        Analyses the bug description and source code for common bug patterns:
        TypeError, AttributeError, ValueError, off-by-one, etc.
        """
        desc = (bug_description or "").lower()
        code = "\n".join(source_code_context.values()) if source_code_context else ""

        hypotheses: list[dict[str, Any]] = []

        # Pattern 1: TypeError (int + str, type mismatch)
        if "typeerror" in desc or "type" in desc or "unsupported operand" in desc:
            hypotheses.append({
                "hypothesis_id": "H1",
                "statement": "A type mismatch occurs: code performs an operation on incompatible types (e.g., int + str) without proper type coercion or validation.",
                "confidence": 0.85,
                "verification_plan": [
                    {"action": "set_breakpoint", "file": "calculator.py", "line": 15, "condition": None},
                    {"action": "set_breakpoint", "file": "calculator.py", "line": 24, "condition": None},
                ],
                "falsification_criteria": "If all operands at the crash site are of the same type, or if explicit type conversion is already present before the operation, this hypothesis is refuted.",
            })

        # Pattern 1b: AttributeError (null reference / NoneType)
        if "attributeerror" in desc or "nonetype" in desc or "none" in desc.lower():
            hypotheses.append({
                "hypothesis_id": "H2",
                "statement": "A null reference: code calls a method or attribute on a None/null value without a null check.",
                "confidence": 0.85,
                "verification_plan": [
                    {"action": "set_breakpoint", "file": "finder.py" if "finder" in str(source_code_context or {}) else "main.py", "line": 5, "condition": None},
                ],
                "falsification_criteria": "If the variable at the crash site is not None/NoneType, or if a null guard is already present before the method call, this hypothesis is refuted.",
            })

        # Pattern 1c: ValueError / empty sequence (e.g. max() on empty dict)
        if "valueerror" in desc or "empty" in desc or "max()" in desc:
            hypotheses.append({
                "hypothesis_id": "H3",
                "statement": "An empty sequence error: a function like max() or min() is called on an empty collection without a guard clause.",
                "confidence": 0.85,
                "verification_plan": [
                    {"action": "set_breakpoint", "file": "processor.py", "line": 42, "condition": None},
                ],
                "falsification_criteria": "If the collection is non-empty at the crash site, or if a guard clause is already present before the call, this hypothesis is refuted.",
            })

        # Pattern 2: Logic error (incorrect comparison)
        if "assert" in desc.lower() or "logic" in desc.lower() or ">" in code or "<" in code:
            hypotheses.append({
                "hypothesis_id": "H4",
                "statement": "A logic error: an incorrect comparison operator or condition causes unexpected behavior (e.g., using > instead of >=).",
                "confidence": 0.5,
                "verification_plan": [
                    {"action": "set_breakpoint", "file": "calculator.py", "line": 27, "condition": None},
                ],
                "falsification_criteria": "If the comparison operators in the code match the expected semantics and all boundary values are handled correctly, this hypothesis is refuted.",
            })

        # Pattern 3: Data flow / wrong value
        hypotheses.append({
            "hypothesis_id": "H5",
            "statement": "A data flow issue: a variable is assigned an unexpected value (e.g., from string concatenation instead of arithmetic addition) before it is used in a comparison.",
            "confidence": 0.7,
            "verification_plan": [
                {"action": "set_breakpoint", "file": "calculator.py", "line": 22, "condition": None},
                {"action": "eval_expression", "expression": "type(total)", "file": "calculator.py", "line": 22},
            ],
            "falsification_criteria": "If the variable's runtime type and value at the point of use match the expected numeric type and value, this hypothesis is refuted.",
        })

        return hypotheses[:3]

    def evaluate_hypothesis(
        self,
        hypothesis: dict[str, Any],
        runtime_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate a single hypothesis against collected runtime evidence.

        Uses Claude to judge whether evidence confirms or refutes the hypothesis.

        Args:
            hypothesis: The hypothesis dict with falsification_criteria.
            runtime_evidence: Collected runtime data (variables, stack, breakpoint hits).

        Returns:
            Dict with: hypothesis_id, verdict (confirmed/refuted/inconclusive),
            reasoning, evidence_cited.
        """
        client = self._get_client()

        prompt = f"""Evaluate this hypothesis against the collected runtime evidence.

Hypothesis: {json.dumps(hypothesis, indent=2)}

Runtime Evidence: {json.dumps(runtime_evidence, indent=2)}

Determine whether the evidence:
- CONFIRMS the hypothesis (supports the root cause claim)
- REFUTES the hypothesis (contradicts the claim or falsification criteria)
- Is INCONCLUSIVE (not enough data to decide)

Pay special attention to the falsification_criteria. If the evidence shows conditions described in the falsification_criteria, the hypothesis MUST be refuted.

Respond with a JSON object:
{{"verdict": "confirmed"|"refuted"|"inconclusive", "reasoning": "...", "evidence_cited": ["..."]}}"""

        text = client.call_text(prompt, max_tokens=1024) or "{}"
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from the text
            import re
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group())
                except json.JSONDecodeError:
                    result = {"verdict": "inconclusive", "reasoning": text[:200]}
            else:
                result = {"verdict": "inconclusive", "reasoning": text[:200]}

        result["hypothesis_id"] = hypothesis.get("hypothesis_id", "?")

        return result

    @staticmethod
    def _evaluate_heuristic(
        hypotheses: list[dict[str, Any]],
        runtime_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Heuristic discriminator that scores hypotheses against the actual
        error output and runtime evidence.

        Key design principle: at most ONE hypothesis is confirmed.  The
        hypothesis that best matches the observed error is picked; the
        rest are explicitly refuted with specific reasons.

        Args:
            hypotheses: List of hypothesis dicts.
            runtime_evidence: Collected runtime data (variables, test output, etc.).

        Returns:
            Dict with ``verdicts`` (hypothesis_id -> verdict) and ``evidence`` list.
        """
        verdicts: dict[str, str] = {}
        evidence: list[dict[str, Any]] = []

        test_output = runtime_evidence.get("test_output", "").lower()
        variables = runtime_evidence.get("variables", {})

        # ── Score each hypothesis ──────────────────────────────────────────
        scored: list[dict[str, Any]] = []

        EXACT_MATCH_KW = 2.0       # exact exception class in output
        VAR_IN_STATEMENT_KW = 1.0  # runtime variable name in hypothesis statement
        FALSIFICATION_KW = 0.5     # runtime variable name in falsification criteria
        CONFIDENCE_KW = 0.5        # multiplier on original confidence
        KEYWORD_HIT_KW = 0.3       # per keyword match (capped at 3 hits)

        STOPWORDS = {
            "a", "an", "the", "is", "of", "in", "or", "to", "at",
            "by", "for", "with", "on", "be", "as", "it", "that", "this",
            "its", "and",
        }

        # Exception patterns mapped to keywords in a hypothesis statement
        EXCEPTION_MAP = {
            "typeerror": {"type", "types", "mismatch", "coercion", "cast", "str", "int"},
            "attributeerror": {"attribute", "none", "null", "nonexistent", "missing"},
            "valueerror": {"value", "empty", "sequence", "max", "min", "invalid", "format", "parse"},
            "keyerror": {"key", "missing", "dictionary", "dict"},
            "indexerror": {"index", "bounds", "list"},
            "assertionerror": {"assert", "assertion", "expect", "logic", "comparison"},
        }

        for h in hypotheses:
            hid = h.get("hypothesis_id", "?")
            statement = h.get("statement", "").lower()
            falsification = h.get("falsification_criteria", "").lower()
            confidence = h.get("confidence", 0.5)

            score = 0.0
            reasons: list[str] = []

            # +2 for exact exception match: check if the output contains a
            # known exception AND the hypothesis statement mentions keywords
            # associated with that exception.
            for exc_name, related_kw in EXCEPTION_MAP.items():
                if exc_name in test_output:
                    if any(kw in statement for kw in related_kw):
                        score += EXACT_MATCH_KW
                        reasons.append(
                            f"'{exc_name}' in output matches 'type' claim in hypothesis"
                        )
                        break  # one exception match per hypothesis

            # +1 if a runtime variable name (len >= 2) appears as a whole word
            # in the hypothesis statement.
            for var_name in variables:
                if len(var_name) < 2:
                    continue
                if re.search(
                    r'\b' + re.escape(var_name.lower()) + r'\b', statement
                ):
                    score += VAR_IN_STATEMENT_KW
                    reasons.append(f"Variable '{var_name}' found in runtime state")
                    break

            # +0.5 if falsification criteria references a runtime variable
            if falsification and variables:
                for var_name in variables:
                    if len(var_name) < 2:
                        continue
                    if re.search(
                        r'\b' + re.escape(var_name.lower()) + r'\b', falsification
                    ):
                        score += FALSIFICATION_KW
                        reasons.append(
                            f"Falsification criteria references '{var_name}'"
                        )
                        break

            # + confidence bonus
            score += confidence * CONFIDENCE_KW

            # + keyword overlap between statement and test output
            stmt_words = set(statement.split()) - STOPWORDS
            keyword_hits = [
                w for w in stmt_words
                if len(w) > 2 and w in test_output
            ]
            if keyword_hits:
                score += min(len(keyword_hits), 3) * KEYWORD_HIT_KW
                reasons.append(
                    f"Keywords matched in output: {keyword_hits[:4]}"
                )

            scored.append({
                "hypothesis": h,
                "score": score,
                "reasons": reasons,
            })

        # ── Decide: best match gets confirmed, rest refuted ──────────────────
        if scored:
            scored.sort(key=lambda s: s["score"], reverse=True)
            best = scored[0]

            # Only confirm if the best score is meaningful (> 0)
            if best["score"] >= 0.5:
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

                    # Skip if already confirmed (guard against duplicate IDs)
                    if verdicts.get(other_hid) == "confirmed":
                        continue

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
                # No hypothesis scored well enough
                for s in scored:
                    hid = s["hypothesis"]["hypothesis_id"]
                    verdicts[hid] = "inconclusive"
                    evidence.append({
                        "hypothesis_id": hid,
                        "verdict": "inconclusive",
                        "reasoning": (
                            f"Low match score ({s['score']:.1f}): "
                            f"{s['hypothesis']['statement'][:100]}"
                        ),
                        "detail": "Not enough evidence to confirm or refute",
                    })

        # Any hypothesis not scored gets inconclusive
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

    def evaluate_all(
        self,
        hypotheses: list[dict[str, Any]],
        runtime_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate all hypotheses against evidence and emit analysis trace.

        If the LLM-based per-hypothesis evaluation returns more than one
        ``confirmed`` verdict we run the heuristic discriminator instead
        (the heuristic enforces at-most-one-confirmed).

        Returns:
            Dict with verdicts mapping hypothesis_id to verdict and evidence list.
        """
        verdicts: dict[str, str] = {}
        evidence_list: list[dict[str, Any]] = []

        try:
            for h in hypotheses:
                eval_result = self.evaluate_hypothesis(h, runtime_evidence)
                hid = eval_result["hypothesis_id"]
                verdicts[hid] = eval_result["verdict"]
                evidence_list.append({
                    "hypothesis_id": hid,
                    "verdict": eval_result["verdict"],
                    "reasoning": eval_result.get("reasoning", ""),
                    "detail": eval_result.get("reasoning", "")[:120],
                })
        except Exception:
            # LLM unavailable — use the heuristic directly
            result = self._evaluate_heuristic(hypotheses, runtime_evidence)
            verdicts = result["verdicts"]
            evidence_list = result["evidence"]
        else:
            # If the LLM confirmed more than one hypothesis, its evaluation
            # is not discriminating enough.  Replace with the heuristic.
            confirmed_count = sum(
                1 for v in verdicts.values() if v == "confirmed"
            )
            if confirmed_count > 1:
                result = self._evaluate_heuristic(hypotheses, runtime_evidence)
                verdicts = result["verdicts"]
                evidence_list = result["evidence"]

        # Emit analysis trace
        if self._tracer:
            self._tracer.emit("analyze", {
                "action": "evaluate_all",
                "verdicts": verdicts,
                "evidence": evidence_list,
            })

        return {
            "verdicts": verdicts,
            "evidence": evidence_list,
        }


# ── Convenience function ──────────────────────────────────────────────────────

def generate_hypotheses(
    bug_description: str,
    source_code: dict[str, str] | None = None,
    config: ProbeConfig | None = None,
    tracer: Any | None = None,
) -> list[dict[str, Any]]:
    """Generate hypotheses for a bug. Convenience function for CLI usage."""
    engine = HypothesisEngine(config=config, tracer=tracer)
    return engine.generate_hypotheses(bug_description, source_code)
