# Probe

An IDE-agnostic, MCP-native, hypothesis-driven AI debugging agent that uses DAP (Debug Adapter Protocol) to drive language runtimes and automatically diagnose bug root causes through a ReAct loop.

## What Probe Does

1. **Hypothesize** -- Generates structured, falsifiable root-cause hypotheses
2. **Instrument** -- Sets breakpoints via DAP at strategic locations
3. **Execute** -- Runs the test, collects runtime state (variables, stack traces)
4. **Analyze** -- Compares hypotheses against evidence, refutes or confirms each
5. **Iterate** -- If all refuted, generates new hypotheses from accumulated evidence
6. **Fix** -- Once root cause is confirmed, generates a patch and verifies it in a sandbox

Every step produces a **TraceEvent** written to an append-only JSONL trace log. A self-contained HTML report is generated at the end with timeline, decision tree, evidence gallery, and patch review.

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Set your API key
export ANTHROPIC_API_KEY=your-key

# 3. Debug a failing test
probe debug --test "pytest tests/fixtures/type_mismatch/test_calculator.py"

# Or describe a bug
probe debug --describe "Comparing str to int in the calculator module"
```

After the session completes, open the HTML report in any browser:

```bash
open probe_traces/<session_id>/report.html
```

## MCP Server Mode

```bash
probe serve --mcp
```

Registers 8 tools with any MCP-compatible client (Claude Code, Cursor, etc.):
`start_debug_session`, `set_breakpoint`, `remove_breakpoint`, `continue_execution`,
`eval_expression`, `get_stack_trace`, `get_variables`, `run_test`.

## Architecture Overview

```
                    ┌──────────────────────┐
                    │   CLI (cli.py)        │
                    │   Typer commands      │
                    └──────┬───────────────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
     ┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼─────┐
     │ Orchestrator │ │  MCP     │ │  Fix      │
     │ (ReAct loop) │ │  Server  │ │  Generator│
     └──────┬──────┘ └─────┬─────┘ └─────┬─────┘
            │              │              │
     ┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼─────┐
     │ Hypothesis  │ │  DAP      │ │  Trace    │
     │ Engine      │ │  Bridge   │ │  Engine   │
     └──────┬──────┘ └─────┬─────┘ └─────┬─────┘
            │              │              │
     ┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼─────┐
     │  Claude API │ │  debugpy  │ │ JSONL +   │
     │  (LLM)      │ │  (DAP)    │ │ HTML      │
     └─────────────┘ └───────────┘ └───────────┘
```

### Observability Diagram

```
   TraceEvent
   ┌─────────────────────────────────────────────┐
   │ timestamp    step_type    event_id           │
   │ session_id   data (payload)                  │
   └─────────────┬───────────────────────────────┘
                 │ emit() ──► append-only JSONL file
                 │ emit() ──► Rich console (live dashboard)
                 │ build_html_report() ──► self-contained HTML
```

## Supported Languages

| Language | DAP Adapter | Status |
|----------|-------------|--------|
| Python   | debugpy     | v0.1   |

[Adding a Language](docs/adding_languages.md)

## Project Structure

```
probe/
├── README.md
├── DESIGN.md
├── pyproject.toml
├── src/probe/
│   ├── cli.py                 # CLI entry (Typer)
│   ├── mcp_server.py          # MCP Server entry
│   ├── orchestrator.py        # ReAct main loop
│   ├── hypothesis.py          # Hypothesis engine (Claude API)
│   ├── tracer.py              # Trace engine (JSONL + Rich + HTML)
│   ├── fix_generator.py       # Patch generation + sandbox
│   ├── report.py              # JSON + Markdown investigation summary
│   ├── config.py              # Configuration management
│   ├── dap/                   # DAP protocol client + adapters
│   ├── tools/                 # Tool registry + debugging tools
│   └── memory/                # SQLite session storage
├── tests/
│   ├── conftest.py
│   ├── test_orchestrator.py
│   ├── test_hypothesis.py
│   ├── test_tracer.py
│   ├── test_dap_client.py
│   └── fixtures/              # Intentionally buggy test projects
├── benchmarks/
│   ├── bug_suite.json
│   └── run_benchmark.py
└── docs/
    ├── architecture.md
    ├── adding_languages.md
    └── hypothesis_engine.md
```

## License

MIT
