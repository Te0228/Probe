# Hypothesis Engine

## How Hypotheses Are Generated

The hypothesis engine (`src/probe/hypothesis.py`) generates structured, falsifiable root-cause hypotheses using two paths:

### 1. Claude API (Primary)

When `ANTHROPIC_API_KEY` is set, hypotheses are generated using Claude with structured output (tool use). The flow:

1. **Prompt construction** -- The system prompt instructs Claude to act as a debugging expert, analyzing the failing test output and source code to generate 2-3 hypotheses. Each hypothesis must have all 5 required fields.

2. **Tool use** -- Claude is forced to use `tool_choice: {type: "tool", name: "output_hypotheses"}`. The tool's `input_schema` enforces the JSON structure:
   ```json
   {
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
             "verification_plan": { ... },
             "falsification_criteria": {"type": "string"}
           },
           "required": ["hypothesis_id", "statement", "confidence",
                        "verification_plan", "falsification_criteria"]
         }
       }
     },
     "required": ["hypotheses"]
   }
   ```

3. **Validation** -- After extraction, every hypothesis is checked for non-empty `falsification_criteria`. If missing, a default criteria is injected: "If the predicted behavior at verification points does not match expectation, this hypothesis is refuted."

4. **Trace** -- The full prompt (truncated to 500 chars), raw response, and parsed hypotheses are emitted as a `hypothesize` TraceEvent.

### 2. Heuristic Fallback

When the Claude API is unavailable (no key, network error, quota exceeded), the engine uses pattern matching against common Python bug categories:

| Bug Pattern | Detection Signal | Generated Hypothesis |
|-------------|-----------------|---------------------|
| Type Mismatch | "typeerror", "type", "unsupported operand" in error | Code operates on incompatible types without coercion |
| Null Reference | "attributeerror", "nonetype" in error | Code calls method/attribute on None without null check |
| Logic Error | "assert", "logic" in error, or comparison operators in source | Incorrect comparison operator or condition |
| Data Flow | Always generated (fallback H3) | Variable assigned unexpected value before use |

Each heuristic hypothesis includes a `verification_plan` with specific breakpoint locations and at least one non-empty `falsification_criteria`.

## Falsification Methodology

The core mechanism that prevents confirmation bias loops is the **falsification_criteria** field. Every hypothesis MUST include a specific, testable statement describing what runtime evidence would DISPROVE it.

### Why Falsification?

Traditional debugging (and naive AI debugging) works by finding evidence that "supports" a hypothesis. This is vulnerable to confirmation bias: the agent keeps looking for data that confirms its first guess, ignoring contradictory evidence.

Falsification inverts this: the agent actively looks for evidence that could **disprove** each hypothesis. If the falsification criteria is NOT met (i.e., the expected disconfirming evidence does not appear), then the hypothesis is confirmed. If the falsification criteria IS met, the hypothesis is eliminated and a new one is generated.

### Example

**Hypothesis:** "A type mismatch occurs: the `result` variable is a string being compared to an integer."

**Falsification Criteria:** "If `type(result)` evaluates to `<class 'int'>` at line 42, this hypothesis is refuted (the types are compatible, so the bug is something else)."

**Evidence Collected:** `type(result)` at line 42 evaluates to `<class 'str'>`.

**Verdict:** Confirmed -- the falsification criteria was NOT met (the type was str, not int).

### Evaluation Algorithm

`evaluate_all()` scores each hypothesis against runtime evidence:

1. **Exact exception match (+2.0):** The error type in the test output (TypeError, AttributeError, etc.) is associated with keywords in the hypothesis statement. If they match, the hypothesis gets a strong score boost.

2. **Runtime variable match (+1.0):** If a variable name from the runtime state appears as a whole word in the hypothesis statement, the hypothesis gets a medium boost.

3. **Falsification criteria match (+0.5):** If the falsification criteria references a variable in the runtime state, the hypothesis gets a small boost.

4. **Confidence bonus (+confidence * 0.5):** The original confidence score contributes to the final score.

5. **Keyword overlap (+0.3 per keyword, max 3):** Words from the hypothesis statement that appear in the test output give incremental boosts.

The hypothesis with the highest score is **confirmed** if and only if its score exceeds a threshold (0.5 for the heuristic, varies by context). All other hypotheses are explicitly **refuted** with specific reasoning about why they scored lower.

### At-Most-One-Confirmed Constraint

The evaluation algorithm enforces that **at most one hypothesis is confirmed per iteration**. If the LLM-based evaluation returns multiple "confirmed" verdicts, the heuristic discriminator is invoked to pick the single best match and refute the others. This prevents the agent from producing ambiguous results.

## Structured Output Format

Every hypothesis adheres to the following JSON structure:

```json
{
  "hypothesis_id": "H1",
  "statement": "A type mismatch occurs: the calculate_total function compares an int to a str because user input is not converted to int before comparison.",
  "confidence": 0.85,
  "verification_plan": [
    {
      "action": "set_breakpoint",
      "file": "calculator.py",
      "line": 15
    },
    {
      "action": "eval_expression",
      "expression": "type(result)"
    }
  ],
  "falsification_criteria": "If the variable 'result' has type int at line 15, or if explicit type conversion (int()) is already present before the comparison, this hypothesis is refuted."
}
```

### Field Requirements

| Field | Required | Type | Constraints |
|-------|----------|------|-------------|
| `hypothesis_id` | Yes | string | Unique within the generation batch |
| `statement` | Yes | string | Clear, specific root-cause claim |
| `confidence` | Yes | number | 0.0 to 1.0 (higher = more certain) |
| `verification_plan` | Yes | array | List of DAP actions (set_breakpoint, eval_expression, inspect_variable) |
| `falsification_criteria` | Yes | string | Non-empty. Specific, testable statement of what evidence would disprove the hypothesis |

### Verification Plan Actions

Each action in the verification plan can be:

- **`set_breakpoint`** -- `{"action": "set_breakpoint", "file": "path/to/file.py", "line": <line_number>, "condition": null}`
- **`eval_expression`** -- `{"action": "eval_expression", "expression": "<python expression>"}`
- **`inspect_variable`** -- `{"action": "inspect_variable", "file": "path/to/file.py", "line": <line_number>}`

### Iteration and Re-Hypothesizing

When all hypotheses are refuted, the orchestrator triggers a re-hypothesize step. The engine receives the accumulated evidence from previous iterations, which informs new hypothesis generation. This continues for up to 3 iterations. If after 3 iterations no hypothesis is confirmed, the best-confidence hypothesis is reported as "inconclusive" with supporting evidence.
