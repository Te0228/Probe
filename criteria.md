The project is considered successfully complete when ALL criteria in the current phase are met. Master agent: evaluate against these phase-by-phase. Subagent: report which phase you are working on.

## 1. Core Functionality + Observability — Phase 1 Gate (Must pass before Phase 2)
- [ ] `pyproject.toml` exists with all 6 dependencies and `pip install -e .` works on Python 3.12+
- [ ] `probe debug --test "pytest tests/fixtures/type_mismatch/test_bug.py"` runs end-to-end and outputs a root cause diagnosis
- [ ] **Live console dashboard**: Running the command shows a Rich-powered live display with spinner, event log, and hypothesis status table with colored badges
- [ ] **JSONL trace**: After completion, `probe_traces/<session_id>/trace.jsonl` exists and contains all 6 event types (hypothesize, instrument, execute, analyze, iterate, fix)
- [ ] **HTML report**: After completion, `probe_traces/<session_id>/report.html` opens in a browser and shows all 4 visualization components:
  - Timeline view with expandable step blocks
  - Hypothesis decision tree (root → hypotheses → evidence → verdicts)
  - Evidence gallery table (variable values, stack frames, evaluations with hypothesis linkage)
  - Patch review with syntax-highlighted diff (if fix was generated)
- [ ] The full ReAct loop completes: hypothesize → instrument (DAP breakpoints) → execute (run test, collect state) → analyze (compare against falsification_criteria) → output diagnosis
- [ ] Each hypothesis is valid structured JSON with all 5 required fields: hypothesis_id, statement, confidence, verification_plan, falsification_criteria
- [ ] DAP Bridge communicates with debugpy: can start a session, set/remove breakpoints, continue, eval expressions, get stack traces, get variables
- [ ] At least 1 hypothesis is confirmed or refuted with specific runtime evidence visible in the trace
- [ ] `probe debug --quiet` suppresses console output but still writes the trace files
- [ ] JSONL trace is append-only — if the process is killed mid-run, partial traces exist and are readable

## 2. Bug Coverage — Phase 2 Gate (Must pass before Phase 3)
- [ ] All 5 bug fixtures exist as self-contained Python projects with failing pytest tests
- [ ] Fixtures cover: type_mismatch, null_reference, off_by_one, wrong_return_value, import_error
- [ ] `benchmarks/bug_suite.json` defines all 5 fixtures with name, test_command, and expected_root_cause_category
- [ ] `python benchmarks/run_benchmark.py` executes and prints a scored summary table
- [ ] ≥3 out of 5 bugs are correctly diagnosed (root cause category matches expected)
- [ ] Every benchmarked run produces a complete JSONL trace + HTML report
- [ ] `probe debug --describe "<natural language bug description>"` also works (not just --test mode)
- [ ] Report builder generates both JSON and Markdown output for each investigation
- [ ] HTML reports from different fixture runs can be opened side-by-side in browser tabs for comparison

## 3. MCP Server — Phase 3 Gate
- [ ] `probe serve --mcp` starts an MCP server without errors
- [ ] All 8 MCP tools are callable via MCP protocol: start_debug_session, set_breakpoint, remove_breakpoint, continue_execution, eval_expression, get_stack_trace, get_variables, run_test
- [ ] Each MCP tool invocation produces a TraceEvent in the session's trace log
- [ ] MCP Server follows MCP Python SDK conventions (can be registered in Claude Code / Cursor)
- [ ] Fix Generator produces a valid patch and verifies it by running the test in a subprocess sandbox

## 4. Code Quality — Phase 3 Gate
- [ ] All modules use type hints on public functions and method signatures
- [ ] All public functions have docstrings (one-liner is acceptable)
- [ ] Modules communicate via interfaces (Protocol/ABC), not concrete implementations
- [ ] `pytest tests/` passes with 0 exit code (tests exist for orchestrator, hypothesis engine, tracer, DAP client)
- [ ] `pip install -e .` works cleanly on Python 3.12+

## 5. Documentation — Phase 3 Gate
- [ ] `README.md` includes: project description, quick start (≤3 commands), architecture overview with observability diagram, supported languages table, "Adding a Language" link
- [ ] `DESIGN.md` explains every major architectural decision with trade-off analysis, including the observability design decisions
- [ ] `docs/architecture.md` provides detailed module descriptions, data flow diagram, and trace event lifecycle
- [ ] `docs/adding_languages.md` explains how to add a new DAP adapter step-by-step
- [ ] `docs/hypothesis_engine.md` explains the hypothesis generation and falsification approach

## 6. Distribution — Phase 3 Gate
- [ ] `pyproject.toml` is properly configured with hatchling (name, version, dependencies incl. rich, CLI entry point)
- [ ] `pip install -e .` installs the package and the `probe` CLI entry point is registered

## 7. Explicit Non-Goals (Do NOT build these — will be rejected)
- ❌ Multi-language support beyond Python (Python/debugpy only for v0.1)
- ❌ VSCode extension or any IDE plugin
- ❌ Web UI or GUI (CLI + self-contained HTML reports only)
- ❌ Real-time streaming to external services or dashboards (local-only)
- ❌ CI/CD pipeline configuration
- ❌ Remote debugging (all debugging is local)
- ❌ Concurrent multi-session management (single-session SQLite persistence is fine)
- ❌ Any agent framework dependency (LangChain, CrewAI, AutoGen, etc.)
