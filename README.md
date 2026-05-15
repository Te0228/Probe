# Probe

**A hypothesis-driven AI debugging agent. Investigates bugs the way a senior engineer would, instead of guessing the way an LLM does.**

Probe diagnoses bug root causes by generating *falsifiable* hypotheses, setting real breakpoints via the Debug Adapter Protocol (DAP), reading live runtime state, and ruling alternatives out with evidence — producing an auditable investigation trail, not a one-shot guess.

---

## The problem with "AI debugging" today

Paste an error into Cursor, Copilot Chat, or Claude, and the model does pattern-matching on the traceback and the surrounding code. For textbook bugs (`int + str`, a missing import, an obvious typo) this works. For anything that depends on **runtime state** — what was this dict actually holding? was that object `None` at the moment of the crash? did the upstream caller pass what we assumed? — the model guesses, often confidently, often wrong.

The root issue is methodology. LLMs default to **defending a conclusion**: they read the error, form a hypothesis, then look for evidence that supports it. That is confirmation bias, automated. The result is the now-familiar pattern: a polished, plausible-sounding root-cause story that is simply not true.

## How Probe is different

Probe forces the **scientific method** onto the LLM:

1. **Hypothesize** — Generate 2-3 competing hypotheses. Every hypothesis must declare in advance, *as a required schema field*, what runtime evidence would refute it. No falsification criteria, no hypothesis.
2. **Instrument** — Set DAP breakpoints at the locations each hypothesis predicts will be informative.
3. **Execute** — Actually run the code under the debugger. Collect runtime state — variable values, stack frames, expression evaluations — from the live process.
4. **Analyse** — Compare the collected evidence against each hypothesis's falsification criteria. **At most one hypothesis can survive.** The rest are explicitly refuted, with the specific evidence that killed them.
5. **Iterate** — If all hypotheses are refuted, regenerate them informed by what we now know.
6. **Fix** — Once a root cause survives, generate a patch and verify it in a sandbox before suggesting it.

Every step writes a `TraceEvent` to an append-only JSONL log. The result is an **auditable investigation trail** — a reviewer can read exactly why Probe believed what it believed, and which evidence ruled out the alternatives.

## What a falsifiable hypothesis looks like

The hypothesis engine is JSON-schema-constrained. The LLM cannot produce an invalid hypothesis:

```json
{
  "hypothesis_id": "H2",
  "statement": "update_status() returns None for unknown task IDs, and server.py dereferences it without a null check.",
  "confidence": 0.85,
  "verification_plan": [
    {"action": "set_breakpoint", "file": "service.py", "line": 47},
    {"action": "eval_expression", "expression": "type(task)"}
  ],
  "falsification_criteria":
    "If `task` at line 47 is not None, OR if a null-guard already precedes the .id access, this hypothesis is refuted."
}
```

`falsification_criteria` is a required schema field — schema-level enforcement of Popper's principle that an unfalsifiable claim is not a scientific claim. The evaluator then enforces **at-most-one-confirmed**: if the LLM tries to confirm two hypotheses, a heuristic discriminator takes over and picks the single best match, refuting the others with explicit evidence. Both safeguards exist because LLMs, given the chance, will confirm everything.

## Worked example

A task-management service crashes when asked to complete a non-existent task:

```bash
$ python -m tests.demo_projects.task_service.server complete TASK-999
AttributeError: 'NoneType' object has no attribute 'id'
```

A typical LLM-chat suggestion: *"add `if task is None: return` at line 47."* That is a symptom patch, not a root cause — it hides the failure without explaining why `update_status()` returned `None`.

Probe's investigation:

```bash
$ probe debug --run "python -m tests.demo_projects.task_service.server complete TASK-999"

  observe       AttributeError: 'NoneType' object has no attribute 'id'
  hypothesize   H1 task is None because of a race condition       (refuted)
                H2 update_status returns None for unknown IDs     (confirmed)
                H3 the server caches a stale task reference       (refuted)
  instrument    breakpoints set at service.py:47, server.py:23
  execute       at server.py:23  ->  task = None,  task_id = 'TASK-999'
                at service.py:47 ->  returns early, no match
  analyse       H1 refuted: no concurrent access in trace
                H2 confirmed: service.py:47 returns None when id not in _tasks;
                              server.py:23 dereferences without check
                H3 refuted: only one task lookup in trace, no caching layer
  fix           service.py:47: raise TaskNotFound(task_id) instead of return None
                server.py:23: catch TaskNotFound and exit cleanly
                sandbox: test_complete_nonexistent_task passes
```

The HTML report at `probe_traces/<session_id>/report.html` shows the full timeline, the decision tree (which hypotheses survived, which died and why), the evidence gallery, and the proposed patch. Every claim is backed by a `TraceEvent` you can audit.

## Architecture

```
                    ┌──────────────────────┐
                    │   CLI / MCP Server   │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │     Orchestrator     │   ReAct loop:
                    │                      │   observe → hypothesize
                    │                      │   → instrument → execute
                    │                      │   → analyse → iterate / fix
                    └──┬─────────┬────────┬┘
                       │         │        │
              ┌────────▼──┐ ┌────▼─────┐ ┌▼──────────┐
              │ Hypothesis│ │ DAP      │ │ Trace +   │
              │ Engine    │ │ Bridge   │ │ Report    │
              │           │ │          │ │ Builder   │
              │ LLM-      │ │ debugpy  │ │ JSONL +   │
              │ backend   │ │ over TCP,│ │ HTML +    │
              │ adapter,  │ │ adapter  │ │ Markdown  │
              │ falsifi-  │ │ pattern  │ │ summary   │
              │ ability   │ │          │ │           │
              │ schema    │ │          │ │           │
              └───────────┘ └──────────┘ └───────────┘
```

The four modules and the design decisions behind them:

**Orchestrator** ([src/probe/orchestrator.py](src/probe/orchestrator.py)) — A custom ~900-line ReAct loop, no agent framework. Generic frameworks (LangChain, etc.) assume a "thought-action-observation" cycle that does not map cleanly to "hypothesize-instrument-execute-analyse". A custom loop is fully deterministic, has no black-box behavior, and pulls in zero transitive dependencies.

**Hypothesis Engine** ([src/probe/hypothesis.py](src/probe/hypothesis.py)) — A JSON schema marks `falsification_criteria` as required; the LLM is forced to emit it via the provider's structured-output mechanism (Anthropic `tool_use` or OpenAI-style `function` calling). `evaluate_all()` enforces at-most-one-confirmed; if the LLM violates it, a heuristic discriminator takes over. A heuristic fallback for the entire engine kicks in when the API is unavailable — Probe still produces useful output without an API key.

The engine talks to whichever provider is configured via the [`LLMClient`](src/probe/llm/base.py) protocol — the same adapter pattern Probe uses for DAP. **DeepSeek is the default** (cost-efficient, fast); Anthropic is supported as an opt-in extra. Adding a new provider is implementing one ~70-line class against the protocol.

**DAP Bridge** ([src/probe/dap/](src/probe/dap/)) — DAP was chosen over `pdb`/`ipdb` deliberately. DAP is the standard protocol behind every major IDE and is implemented for 50+ languages by their vendors. The orchestrator depends on a `DAPAdapter` abstraction; the Python adapter (debugpy over TCP) is implemented today, and adding a new language is an adapter implementation, not an orchestrator change. See [docs/adding_languages.md](docs/adding_languages.md).

**Trace + Report Builder** ([src/probe/tracer.py](src/probe/tracer.py), [src/probe/report.py](src/probe/report.py)) — Every step emits a `TraceEvent` written immediately to an append-only JSONL file. If the agent crashes, the partial trace is preserved. `tail -f` works in real time. After the run, the same events build a self-contained HTML report (no server, no framework, no external dependencies) plus a Markdown summary suitable for pasting into a PR.

Full rationale and trade-offs for every decision: [DESIGN.md](DESIGN.md).

## End-to-end flow

```
  user
   │
   │ probe debug --run "python -m myapp serve"
   ▼
  Orchestrator
   │
   │ 1. observe       run the command, capture stderr / traceback
   │ 2. hypothesize   call Claude with schema → 2–3 hypotheses,
   │                  each with falsification_criteria
   │ 3. instrument    convert verification_plan → DAP breakpoints
   │ 4. execute       run again under DAP, hit a breakpoint,
   │                  read frames + locals + watch expressions
   │ 5. analyse       evaluate each hypothesis against the
   │                  evidence; enforce at-most-one-confirmed
   │ 6. iterate       if all refuted, feed evidence back into step 2
   │ 7. conclude      pick the confirmed hypothesis, locate the
   │                  faulty line, generate a heuristic patch
   ▼
  probe_traces/<session_id>/
     ├── trace.jsonl     append-only event log
     ├── report.html     interactive timeline + decision tree
     ├── report.md       PR-ready investigation summary
     └── report.json     machine-readable summary
```

Every numbered step writes a `TraceEvent`. You can `tail -f` the JSONL during the run, or open `report.html` after — both views render the same events.

## Quick start

```bash
pip install -e .

# Default backend: DeepSeek (cheap, fast, OpenAI-compatible function calling)
export DEEPSEEK_API_KEY=sk-...

# Or run without any key — heuristic mode still produces a useful investigation
probe debug --run "python -m tests.demo_projects.data_cli.cli bestseller /tmp/empty.csv"
probe debug --test "pytest tests/fixtures/type_mismatch/"
probe debug --script broken.py

# Or integrate into pytest
pytest tests/ --probe
```

Want to use Anthropic Claude instead?

```bash
pip install -e ".[anthropic]"
export LLM_BACKEND=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

Open `probe_traces/<session_id>/report.html` in any browser to review the investigation.

## Demo projects

Two multi-file Python projects with realistic bugs — these are not toy assertions, they are bugs that require following data flow across files.

### Data CLI — [`tests/demo_projects/data_cli/`](tests/demo_projects/data_cli/)

A CSV-processing CLI. Works on normal input. Crashes on the empty-input edge case:

```bash
# Works:
python -m tests.demo_projects.data_cli.cli bestseller \
    tests/demo_projects/data_cli/sample_data.csv

# Crashes — Probe diagnoses:
echo "# empty" > /tmp/empty.csv
probe debug --run "python -m tests.demo_projects.data_cli.cli bestseller /tmp/empty.csv"
```

**Root cause:** `find_best_seller()` calls `max()` on an empty dict — `ValueError: max() arg is an empty sequence`. The error message points at `max()`; the actual fix is a guard clause two functions upstream.

### Task Service — [`tests/demo_projects/task_service/`](tests/demo_projects/task_service/)

A task management service. Crashes when operating on a task that doesn't exist:

```bash
# Works:
python -m tests.demo_projects.task_service.server create "Fix login" --assignee alice

# Crashes — Probe diagnoses:
probe debug --run "python -m tests.demo_projects.task_service.server complete TASK-999"
```

**Root cause:** `update_status()` returns `None` for unknown IDs (a silent contract violation), and `server.py` dereferences `.id` immediately. The crash site (`server.py`) and the root cause (`service.py`) are in different files — exactly the upstream-vs-downstream confusion that static AI tools systematically miss.

### Fixture suite — [`tests/fixtures/`](tests/fixtures/)

Five smaller fixtures used by the benchmark suite, one bug pattern each:

| Fixture | Bug pattern | Run with |
|---|---|---|
| `type_mismatch/` | `int + str` concatenation | `pytest tests/fixtures/type_mismatch/ --probe` |
| `null_reference/` | `None` attribute access | `pytest tests/fixtures/null_reference/ --probe` |
| `off_by_one/` | Loop boundary error | `pytest tests/fixtures/off_by_one/ --probe` |
| `wrong_return_value/` | `>` instead of `>=` | `pytest tests/fixtures/wrong_return_value/ --probe` |
| `import_error/` | Missing module import | `pytest tests/fixtures/import_error/ --probe` |

## MCP server mode

```bash
probe serve --mcp
```

Exposes Probe's debugging primitives as MCP tools to any MCP-compatible client — Claude Code, Cursor, or your own agent:

```
start_debug_session    set_breakpoint        remove_breakpoint
continue_execution     eval_expression       get_stack_trace
get_variables          run_test
```

The MCP surface is intentionally low-level. Higher-level orchestration — hypothesis generation, falsification, multi-iteration debugging — happens inside Probe; the MCP tools are the building blocks an external agent can compose with.

## Benchmarks

The suite measures two things:

1. **Diagnostic accuracy.** For each fixture in `tests/fixtures/`, does Probe's confirmed hypothesis match the known bug category?
2. **Trace completeness.** Were all expected event types emitted? Are the JSONL, JSON, Markdown, and HTML reports all produced?

```bash
python benchmarks/run_benchmark.py
```

### Current results

Per-fixture pass/fail across two backends. Raw logs in [`benchmarks/results.txt`](benchmarks/results.txt) (heuristic) and [`benchmarks/results-deepseek.txt`](benchmarks/results-deepseek.txt) (DeepSeek).

| Fixture | Bug pattern | Heuristic mode | DeepSeek (`deepseek-chat`) |
|---|---|---|---|
| `type_mismatch` | `int + str` | PASS (42s) | PASS (58s) |
| `null_reference` | `None.attr` | PASS (42s) | PASS (61s) |
| `off_by_one` | loop boundary | PASS (42s) | PASS (63s) |
| `wrong_return_value` | `>` vs `>=` | PASS (42s) | PASS (57s) |
| `import_error` | missing module | PASS (42s) | PASS (56s) |
| **Total** | | **5 / 5** | **5 / 5** |

Both modes correctly diagnosed all five fixture categories. Heuristic mode is faster (no network round-trips); the LLM backend adds ~15s/fixture but exercises the structured-hypothesis path end to end.

See [benchmarks/README.md](benchmarks/README.md) for the scoring methodology.

**What's deliberately not in the benchmark yet:**

- A head-to-head against Cursor / Copilot / direct LLM prompting on the same bug suite. This is the comparison that would best validate Probe's premise, but it requires bug categories that meaningfully differentiate — specifically **runtime-state-dependent** bugs and **cross-file root cause** bugs (where the symptom and cause live in different files). The two demo projects above are early examples of the second category. Expanding the fixture set to cover more of both is the next benchmarking milestone.

## Supported languages

| Language | DAP adapter | Status |
|---|---|---|
| Python | `debugpy` | Implemented (v0.1) |
| Go | `delve` (dlv-dap) | Adapter interface ready, not yet implemented |
| Node / TypeScript | `vscode-js-debug` | Adapter interface ready, not yet implemented |
| Rust / C / C++ | `codelldb` | Adapter interface ready, not yet implemented |

The orchestrator depends on the `DAPAdapter` interface ([adapters/base.py](src/probe/dap/adapters/base.py)), not on `debugpy` directly. Adding a new language is implementing one ~250-line adapter; the hypothesis engine, trace engine, and reporting layer are language-agnostic. This is the architectural payoff for choosing DAP over `pdb`: the broader debugger ecosystem already speaks DAP, so Probe inherits coverage of 50+ languages by adapter, not by re-implementation. See [docs/adding_languages.md](docs/adding_languages.md) for the adapter contract.

## Design decisions

Full rationale for every architectural choice lives in [DESIGN.md](DESIGN.md). The four most important:

**DAP over `pdb`.** DAP is a structured, multi-language, IDE-standard protocol with machine-parseable responses. `pdb` is Python-only, single-threaded, and built for interactive humans, not programmatic agents. Choosing DAP buys forward compatibility with every major language.

**Custom ReAct loop, no agent framework.** Generic frameworks assume a thought-action-observation cycle that does not match hypothesize-instrument-execute-analyse. The custom loop is ~900 lines, fully deterministic, and has no black-box behavior. We trade away framework ergonomics for full control over execution order and trace fidelity.

**Append-only JSONL traces.** Crashes preserve partial traces. `tail -f` works in real time. Any line-oriented tool (`jq`, `grep`, log aggregators) can post-process the file. The trace format is the contract between the agent and its observers; the same JSONL drives the live console, the HTML report, and the Markdown summary.

**At-most-one-confirmed.** LLMs, asked to judge their own hypotheses, tend to mark everything plausible as confirmed. Probe rejects multi-confirm verdicts at the engine level and falls back to a deterministic discriminator that scores hypotheses against exception types, variable names present at runtime, and keyword overlap with the test output. This is a small but load-bearing guard against the LLM agreeing with itself.

## Project structure

```
probe/
├── README.md
├── DESIGN.md
├── pyproject.toml
├── src/probe/
│   ├── cli.py                 # CLI entry point (Typer)
│   ├── mcp_server.py          # MCP Server entry point
│   ├── orchestrator.py        # ReAct main loop
│   ├── hypothesis.py          # Hypothesis engine + falsification schema
│   ├── tracer.py              # Trace engine: JSONL + Rich + HTML
│   ├── fix_generator.py       # Heuristic patch generation
│   ├── report.py              # JSON + Markdown investigation summary
│   ├── config.py              # Environment-driven configuration
│   ├── dap/
│   │   ├── client.py          # High-level DAP client
│   │   ├── protocol.py        # DAP message dataclasses
│   │   └── adapters/
│   │       ├── base.py        # Language-agnostic adapter contract
│   │       └── python.py      # debugpy adapter (TCP)
│   ├── llm/
│   │   ├── base.py            # LLMClient protocol + factory
│   │   ├── anthropic_client.py  # Claude (tool_use)
│   │   └── deepseek_client.py   # DeepSeek (OpenAI-compatible function calling)
│   ├── tools/                 # Tool registry + debugging primitives
│   └── memory/                # SQLite session storage
├── tests/
│   ├── test_orchestrator.py
│   ├── test_hypothesis.py
│   ├── test_tracer.py
│   ├── test_dap_client.py
│   ├── demo_projects/         # Multi-file realistic apps with bugs
│   └── fixtures/              # Single-bug pytest fixtures
├── benchmarks/
│   ├── bug_suite.json
│   └── run_benchmark.py
└── docs/
    ├── architecture.md
    ├── adding_languages.md
    └── hypothesis_engine.md
```

## Status

| Component | Status |
|-----------|--------|
| Orchestrator (ReAct loop) | Implemented |
| Hypothesis Engine + falsification schema | Implemented |
| LLM Backend — DeepSeek (default) | Implemented |
| LLM Backend — Anthropic Claude (opt-in extra) | Implemented |
| LLM Backend — heuristic fallback (no API key) | Implemented |
| DAP Bridge — Python (debugpy) | Implemented |
| DAP Bridge — other languages | Adapter interface defined; not yet implemented |
| Trace Engine (JSONL + Rich + HTML) | Implemented |
| MCP server | Implemented |
| pytest plugin (`--probe`) | Implemented |
| Fix generator with sandbox verification | Implemented (heuristic patches) |

This is a v0.1 reference implementation of the hypothesis-driven debugging approach. The architecture is designed for extension; the implementation is intentionally narrow.

## Roadmap

- **Cross-file root-cause fixtures.** Bugs whose symptom and cause are in different files — the case where static AI tools systematically fail.
- **Head-to-head benchmark.** Probe vs Cursor vs raw Claude prompting on the same expanded fixture suite, with the runtime-state-dependent and cross-file categories that should differentiate Probe most.
- **Go adapter (`delve-dap`).** First non-Python language; concrete validation of the multi-language story.
- **Conditional breakpoint inference.** Let the hypothesis engine generate breakpoints with conditions (e.g., `task is None`) so the agent stops only at runs that match the hypothesis under test.
- **Trace replay.** Re-run analysis against an existing trace without re-executing the debuggee — useful for prompt iteration on the hypothesis engine itself.

## License

MIT
