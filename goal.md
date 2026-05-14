Build an open-source AI debugging agent called "Probe" — an IDE-agnostic, MCP-native, hypothesis-driven debugging agent that uses DAP (Debug Adapter Protocol) to drive language runtimes and automatically diagnose bug root causes through a ReAct loop.

## What Probe Does
When given a failing test or bug description, Probe autonomously:
1. **Hypothesize**: Generates 2-3 structured, falsifiable root-cause hypotheses (each with a verification plan and falsification criteria)
2. **Instrument**: Sets breakpoints/watchpoints via DAP at strategic locations
3. **Execute**: Runs the test, collects runtime state (variable values, stack traces, execution paths)
4. **Analyze**: Compares hypotheses against actual runtime data, refutes or confirms each
5. **Iterate**: If all hypotheses are refuted, generates new ones based on accumulated evidence
6. **Fix**: Once root cause is confirmed, generates a patch and verifies it by running tests in a sandbox

**Every step above MUST be recorded into a structured trace for real-time observation and post-hoc review.**

## Observability & Visualization

Probe's debugging process must be fully transparent and observable. Users should see exactly what the agent is thinking and doing at every moment.

### Trace Engine (tracer.py)
A dedicated trace engine records every action atomically as a **TraceEvent** — a timestamped JSON object written to an append-only trace log. Each event has:
```json
{
  "timestamp": "2026-05-14T10:30:01.123Z",
  "step_type": "hypothesize | instrument | execute | analyze | iterate | fix",
  "event_id": "uuid",
  "session_id": "uuid",
  "data": { ... }
}
```

Event types and their data payloads:
- **hypothesize**: the full LLM prompt sent, raw response received, and parsed hypothesis list
- **instrument**: breakpoint locations set (file, line, condition), DAP request/response pairs
- **execute**: test stdout/stderr, exit code, execution duration, breakpoint hit order, thread states
- **analyze**: each hypothesis evaluated against falsification_criteria, the runtime evidence used, verdict (confirmed / refuted / inconclusive)
- **iterate**: the new evidence that triggered re-hypothesizing, the updated hypothesis list
- **fix**: the generated patch diff, sandbox test result (pass/fail), final verdict

The trace log is written to `probe_traces/<session_id>.jsonl` — one JSON object per line, append-only, so it survives crashes and can be tailed in real time.

### Live Console Observer
When running `probe debug`, the terminal shows a live dashboard (using Rich library):
- A spinner with current step name (e.g., "🔍 Generating hypotheses...")
- A scrolling panel showing the latest 3 trace events
- A hypothesis status table: each hypothesis shown with a colored status badge (🟡 pending / 🟢 confirmed / 🔴 refuted)
- When a breakpoint is hit: print the file:line, the local variables, and which hypothesis this breakpoint is testing
- When analysis completes: print a verdict block with evidence for each hypothesis

The console mode is the default. Use `probe debug --quiet` to suppress live output and only write the trace file.

### HTML Visualization Report
After the debug session completes, Probe generates a self-contained HTML report at `probe_traces/<session_id>.html` alongside the JSONL trace. The HTML report contains:

1. **Timeline View**: A horizontal scrollable timeline showing each step as a colored block (green=hypothesize, blue=instrument, orange=execute, purple=analyze, red=fix). Clicking a block expands it to show full event details.
2. **Hypothesis Decision Tree**: A tree diagram showing:
   - Root node: bug description / failing test
   - First generation: initial 2-3 hypotheses with confidence scores
   - Branches: what evidence was collected for each, whether it confirmed or refuted the hypothesis
   - Leaf nodes: final verdict (root cause found, or inconclusive)
   - Refuted hypotheses shown in red with the specific evidence that killed them
3. **Evidence Gallery**: A table listing every piece of runtime evidence collected (variable values, stack frames, expression evaluations) and which hypothesis it supported or refuted.
4. **Patch Review**: If a fix was generated, show the diff with syntax highlighting and the sandbox test result.

The HTML report is self-contained (no external CSS/JS dependencies). It uses minimal vanilla JavaScript for interactivity (expand/collapse, filter). It must render correctly in any modern browser by opening the file directly (no server needed).

### Key Design Decisions for Observability:
- **Append-only trace log**: Events are written immediately as they happen, so `tail -f probe_traces/<session_id>.jsonl` works in real time. If the agent crashes, partial traces are preserved.
- **Machine-readable + Human-readable**: JSONL is the canonical format (for programmatic consumption), HTML is the human-friendly view.
- **No external services**: All visualization is local — no data leaves the machine. The HTML file is fully self-contained.
- **Rich is the only new dependency**: Use Python Rich for terminal live display. HTML uses no frameworks.

## Technical Architecture

### 5 Core Modules:
1. **Orchestrator** (orchestrator.py) — ReAct main loop: observe runtime state → generate hypotheses → execute investigation plan → judge verdict → iterate or fix
2. **Hypothesis Engine** (hypothesis.py) — Uses Claude API with structured output to generate JSON hypotheses, each containing: hypothesis_id, statement, confidence, verification_plan (list of tool calls), and falsification_criteria
3. **DAP Bridge** (dap/) — DAP protocol client that communicates with debugpy (Python) via JSON-RPC. Implements: start_debug_session, set_breakpoint, remove_breakpoint, continue_execution, eval_expression, get_stack_trace, get_variables, run_test
4. **Trace Engine** (tracer.py) — Records every step as a TraceEvent to JSONL + powers live Rich console display + generates self-contained HTML visualization report (timeline, decision tree, evidence gallery, patch review)
5. **Report Builder** (report.py) — Generates the final structured JSON + Markdown investigation summary (the static deliverables; distinct from the interactive trace visualization)

### Tech Stack:
- Python 3.12+, using hatchling for build
- debugpy as DAP adapter for Python debugging
- Anthropic Claude API for LLM reasoning (tool use / structured output)
- MCP Python SDK (mcp) for MCP Server mode
- Typer for CLI
- Rich for live terminal dashboard
- SQLite for session persistence
- pytest for testing

### Project Structure:
```
probe/
├── README.md
├── DESIGN.md
├── pyproject.toml
├── src/probe/
│   ├── __init__.py
│   ├── cli.py                  # CLI entry (Typer)
│   ├── mcp_server.py           # MCP Server entry
│   ├── orchestrator.py         # ReAct main loop
│   ├── hypothesis.py           # Hypothesis engine (Claude API)
│   ├── tracer.py               # Trace engine (JSONL + Rich live display + HTML visualization)
│   ├── fix_generator.py        # Patch generation + sandbox verification
│   ├── report.py               # Final investigation report (JSON + Markdown)
│   ├── config.py               # Configuration management
│   ├── dap/
│   │   ├── __init__.py
│   │   ├── client.py           # DAP protocol client
│   │   ├── protocol.py         # DAP message type definitions
│   │   └── adapters/
│   │       ├── __init__.py
│   │       ├── base.py         # Adapter base class (Protocol/ABC)
│   │       └── python.py       # debugpy adapter
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py         # Tool registry
│   │   ├── debug_tools.py      # DAP-related tools
│   │   ├── source_tools.py     # Source code reading tools
│   │   ├── test_tools.py       # Test runner tools
│   │   └── git_tools.py        # Git-related tools
│   └── memory/
│       ├── __init__.py
│       └── session_store.py    # SQLite session storage
├── tests/
│   ├── conftest.py
│   ├── test_orchestrator.py
│   ├── test_hypothesis.py
│   ├── test_tracer.py
│   ├── test_dap_client.py
│   └── fixtures/               # Intentionally buggy Python projects for testing
│       ├── type_mismatch/
│       ├── null_reference/
│       ├── off_by_one/
│       ├── wrong_return_value/
│       └── import_error/
├── benchmarks/
│   ├── README.md
│   ├── bug_suite.json
│   └── run_benchmark.py
├── examples/
│   ├── basic_usage.py
│   └── mcp_integration.md
└── docs/
    ├── architecture.md
    ├── adding_languages.md
    └── hypothesis_engine.md
```

### Hard Constraints (MUST follow — violating any of these breaks the project):
- **No agent framework** (no LangChain, no CrewAI, no AutoGen). Write a custom ~300-line ReAct orchestrator. The hypothesis-driven loop is too specialized for generic frameworks.
- **DAP over direct pdb**: All debug operations go through DAP JSON-RPC via debugpy. Never shell out to pdb/ipdb.
- **Every hypothesis MUST include a falsification_criteria field**. This is the core mechanism that prevents confirmation bias loops. A hypothesis without falsification_criteria is invalid.
- **Every action MUST produce a TraceEvent**: The orchestrator must not execute any step (hypothesize, instrument, execute, analyze, iterate, fix) without emitting a corresponding TraceEvent to the tracer. No step is invisible.
- **All modules communicate via interfaces** (Protocol/ABC). Orchestrator depends on abstractions, not concrete implementations. This is what enables future language adapters.

### Quality Standards (apply after the end-to-end logic is working — NOT during Phase 1):
- Type hints on all public functions and method signatures
- Docstrings on all public functions (one-liner is fine)
- pytest tests for orchestrator, hypothesis engine, tracer, and DAP client

---

## Execution Plan — READ THIS BEFORE WRITING ANY CODE

You MUST build this project in 3 phases. Do NOT move to the next phase until the current one is fully verified as working. Do NOT build all modules in isolation before integrating — vertical slice first, then horizontal expansion.

### Phase 1 — Vertical Slice: End-to-End Loop with One Bug Type + Full Observability
**Goal: `probe debug --test "pytest tests/fixtures/type_mismatch/test_bug.py"` runs, produces a live Rich dashboard, writes a JSONL trace, and outputs a root cause diagnosis.**

Build in this exact order:
1. Create `pyproject.toml` with hatchling config and dependencies (debugpy, anthropic, mcp, typer, rich)
2. Create `src/probe/__init__.py` and `src/probe/config.py`
3. Implement `src/probe/tracer.py` FIRST — before anything else, so every subsequent module can use it:
   - `TraceEvent` dataclass and `to_jsonl()` serialization
   - `Tracer` class: `emit(step_type, data)` → appends JSON line to trace file, returns event_id
   - `SessionManager`: creates session dir `probe_traces/<session_id>/`, manages session_id, exposes `get_trace_path()`, `get_html_path()`
   - `ConsoleObserver` class: wraps Rich Live display, receives events and renders the dashboard (spinner + event log + hypothesis table)
   - `HTMLReportBuilder` class: takes a completed JSONL trace file, generates self-contained HTML with timeline, decision tree, evidence gallery, patch review
   - The Tracer must work as a context manager: `with Tracer(session_id, console_mode=True) as tracer: ...`
4. Implement `src/probe/dap/protocol.py` — DAP message type definitions (Request, Response, Event, StackFrame, Variable, Breakpoint, etc.)
5. Implement `src/probe/dap/adapters/base.py` — Abstract adapter interface (Protocol)
6. Implement `src/probe/dap/adapters/python.py` — debugpy adapter: start/stop sessions, communicate via stdin/stdout JSON-RPC
7. Implement `src/probe/dap/client.py` — High-level DAP client wrapping the adapter: set_breakpoint, continue, eval_expression, get_stack_trace, get_variables. Each DAP operation emits a TraceEvent via the tracer.
8. Implement `src/probe/hypothesis.py` — Claude API call with structured output (JSON mode or tool use) that returns a list of hypotheses, each with: hypothesis_id, statement, confidence (0.0-1.0), verification_plan, falsification_criteria. Emits a hypothesize TraceEvent with the full prompt and response.
9. Implement `src/probe/orchestrator.py` — ReAct loop that takes a tracer instance and emits events at every step:
   - Observe: read the bug description / test failure output → emit TraceEvent(step_type="observe")
   - Hypothesize: call hypothesis engine → emit TraceEvent(step_type="hypothesize")
   - Plan: for each hypothesis, determine breakpoint locations → emit TraceEvent(step_type="instrument")
   - Execute: set breakpoints via DAP, run the test, collect variables/stack → emit TraceEvent(step_type="execute")
   - Analyze: compare collected state against falsification_criteria → emit TraceEvent(step_type="analyze")
   - Conclude: if confirmed → fix. If all refuted → re-hypothesize (max 3 iterations) → emit TraceEvent(step_type="iterate" or "fix")
10. Create `tests/fixtures/type_mismatch/` — a minimal self-contained Python project:
    - One module with a type-related bug (e.g., comparing int to str)
    - One failing pytest test
    - A README.md describing the bug (for reference, not consumed by Probe)
11. Implement `src/probe/cli.py` — Typer app with:
    - `probe debug --test <test_path>` — run against a specific pytest test
    - `probe debug --describe "<description>"` — run against a bug description
    - `probe debug --quiet` — suppress console display, write trace only
    - `probe debug --output <dir>` — custom output directory for traces
12. **VERIFY**: Run the full loop end-to-end:
    - `probe debug --test "pytest tests/fixtures/type_mismatch/test_bug.py"` must show a live Rich dashboard
    - After completion, `probe_traces/<session_id>/trace.jsonl` must exist and contain all 6 step events
    - After completion, `probe_traces/<session_id>/report.html` must open in a browser and show the timeline + decision tree + evidence gallery
    - The orchestrator must correctly diagnose the type_mismatch bug

### Phase 2 — Breadth: 5 Bug Types + Benchmark
**Goal: 5 fixture projects, benchmark suite, ≥3/5 bugs correctly diagnosed with full traces.**

1. Create the remaining 4 fixture projects under `tests/fixtures/`:
   - `null_reference/` — None/AttributeError bug
   - `off_by_one/` — loop boundary or index error
   - `wrong_return_value/` — function returns incorrect value under certain conditions
   - `import_error/` — missing or circular import
2. Run each fixture through `probe debug` and verify:
   - Root cause correctly identified
   - JSONL trace is complete (all 6 step types present)
   - HTML report renders without errors
3. Implement `src/probe/report.py` — generates the final JSON + Markdown investigation summary (distinct from the HTML trace visualization)
4. Implement `src/probe/tools/` modules (debug_tools.py, source_tools.py, test_tools.py)
5. Create `benchmarks/bug_suite.json` — JSON array defining all 5 bug fixtures
6. Create `benchmarks/run_benchmark.py` — runs each fixture through the orchestrator, scores pass/fail, prints summary table, also checks that traces exist for each run
7. **VERIFY**: `python benchmarks/run_benchmark.py` scores ≥3/5. Each scored run has a valid JSONL trace + HTML report.

### Phase 3 — MCP Server + Polish + Distribution
**Goal: MCP server works, tests pass, docs complete, package installable.**

1. Implement `src/probe/mcp_server.py` — MCP Server exposing all 8 tools: start_debug_session, set_breakpoint, remove_breakpoint, continue_execution, eval_expression, get_stack_trace, get_variables, run_test. Each tool invocation also produces a TraceEvent.
2. Implement `src/probe/fix_generator.py` — takes confirmed hypothesis + source context, generates a patch, applies it in a temp directory, runs the test to verify the fix passes. Emits fix TraceEvents.
3. Implement `src/probe/memory/session_store.py` — SQLite storage for session history (indexes trace paths for fast lookup)
4. Write `tests/conftest.py`, `tests/test_orchestrator.py`, `tests/test_hypothesis.py`, `tests/test_tracer.py`, `tests/test_dap_client.py`
5. Run `pytest tests/` — all tests must pass
6. Write docs:
   - `README.md`: project description, quick start (3 commands: install, debug a test, describe a bug), architecture overview with observability diagram, supported languages table, link to adding_languages.md
   - `DESIGN.md`: every major architectural decision with trade-off analysis (why DAP over pdb, why custom ReAct over LangChain, why hypothesis-driven over linear debugging, why append-only JSONL trace over in-memory logging, why self-contained HTML over server-rendered UI, why SQLite over Postgres)
   - `docs/architecture.md`: detailed module descriptions, data flow diagram, trace event lifecycle
   - `docs/adding_languages.md`: step-by-step guide for adding a new DAP adapter
   - `docs/hypothesis_engine.md`: hypothesis generation approach, falsification methodology, structured output format
7. **VERIFY**: `pip install -e .` works. `probe serve --mcp` starts and registers tools. Opening a trace HTML report in a browser shows all 4 visualization components.
