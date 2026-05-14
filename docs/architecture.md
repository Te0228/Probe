# Architecture

## Module Descriptions

### 1. CLI (`cli.py`)

Entry point for the `probe` command. Uses Typer for argument parsing. Exposes two commands:

- **`probe debug`** -- Runs the hypothesis-driven debugging loop against a test or bug description. Supports `--test`, `--describe`, `--quiet`, and `--output` flags.
- **`probe serve --mcp`** -- Starts Probe as an MCP server over stdio, registering 8 debugging tools.

The CLI creates a `SessionManager`, `Tracer`, and `Orchestrator`, wires them together, and invokes the ReAct loop.

### 2. Orchestrator (`orchestrator.py`)

The central ReAct loop. Five-step cycle per iteration:

1. **Observe** -- Runs the test (or parses the bug description) to capture failure output
2. **Hypothesize** -- Calls the hypothesis engine to generate 2-3 falsifiable hypotheses
3. **Instrument** -- Plans breakpoint locations from hypothesis verification plans
4. **Execute** -- Sets breakpoints via DAP, runs the test, collects runtime state (variables, stack frames, breakpoint hit order)
5. **Analyze** -- Evaluates each hypothesis against falsification criteria using collected evidence
6. **Conclude** -- If one hypothesis is confirmed: diagnose root cause and attempt fix. If all refuted: re-hypothesize (max 3 iterations).

Every step emits a `TraceEvent`. The orchestrator depends on abstractions (`ITracer`, `HypothesisEngine`, `DAPClient`), not concrete implementations.

### 3. Hypothesis Engine (`hypothesis.py`)

Generates structured hypotheses via Claude API (with heuristic fallback). Each hypothesis must have:

| Field | Description |
|-------|-------------|
| `hypothesis_id` | Short unique identifier (e.g., H1, H2) |
| `statement` | Clear root-cause claim |
| `confidence` | 0.0 to 1.0 |
| `verification_plan` | List of DAP actions to test the hypothesis |
| `falsification_criteria` | Specific runtime evidence that would disprove the hypothesis |

Also provides `evaluate_all()` which judges each hypothesis against collected runtime evidence, producing a verdict (confirmed/refuted/inconclusive) and reasoning.

### 4. DAP Bridge (`dap/`)

Three-layer architecture:

- **`protocol.py`** -- Dataclass definitions for DAP types: `Request`, `Response`, `Event`, `StackFrame`, `Variable`, `Breakpoint`, `Thread`, `Source`.
- **`adapters/base.py`** -- Abstract `DAPAdapter` Protocol defining the interface: `start()`, `stop()`, `send_request()`, `wait_for_stopped()`, `read_event()`.
- **`adapters/python.py`** -- Concrete implementation using debugpy over TCP. Launches the debuggee with `--listen --wait-for-client`, connects via TCP, exchanges DAP JSON-RPC with Content-Length headers.
- **`client.py`** -- High-level `DAPClient` wrapping the adapter: `set_breakpoint()`, `remove_breakpoint()`, `continue_execution()`, `eval_expression()`, `get_stack_trace()`, `get_variables()`, `run_to_breakpoints()`. Every DAP operation emits a TraceEvent.

### 5. Trace Engine (`tracer.py`)

The observability backbone. Contains:

- **`TraceEvent`** -- Dataclass: `timestamp`, `step_type`, `event_id`, `session_id`, `data`. Serializes to JSONL.
- **`SessionManager`** -- Creates `probe_traces/<session_id>/` directory.
- **`Tracer`** -- Context manager. `emit()` appends a JSON line to the trace file (with `fsync`), returns `event_id`. Also feeds events to `ConsoleObserver` for live display.
- **`ConsoleObserver`** -- Rich-powered live dashboard showing spinner, recent events, and hypothesis status table with colored badges.
- **`HTMLReportBuilder`** -- Generates a self-contained HTML report from completed trace events. Includes: timeline (expandable colored blocks), hypothesis decision tree, evidence gallery table, and patch review with diff highlighting.

### 6. Fix Generator (`fix_generator.py`)

Takes a confirmed hypothesis and source code context, and:

1. Uses Claude API (with heuristic fallback) to generate a minimal patch as a unified diff
2. Applies the patch in a temporary sandbox directory
3. Runs the test command in the sandbox to verify the fix passes
4. Returns a `PatchResult` with the diff, application status, and sandbox test result
5. Emits a `fix` TraceEvent with the full patch and verification outcome

### 7. MCP Server (`mcp_server.py`)

Exposes Probe as an MCP tool provider. Registers 8 tools:
`start_debug_session`, `set_breakpoint`, `remove_breakpoint`, `continue_execution`,
`eval_expression`, `get_stack_trace`, `get_variables`, `run_test`.

Each tool invocation creates a TraceEvent. The server communicates over stdio using the MCP Python SDK, making it compatible with any MCP-compatible client (Claude Code, Cursor).

### 8. Tools (`tools/`)

- **`registry.py`** -- `ToolRegistry`: register/lookup/execute tools by name. Tools implement the `Tool` Protocol (`name` property + `async execute(**kwargs)`).
- **`debug_tools.py`** -- `SetBreakpointTool`, `EvalExpressionTool`, `GetStackTraceTool`, `GetVariablesTool`. Each wraps a DAP client call and emits a TraceEvent.
- **`test_tools.py`** -- `RunTestTool`: runs a test command via subprocess, captures output.
- **`source_tools.py`** -- `ReadFileTool`, `SearchCodebaseTool`: file I/O and regex search for source code context.

### 9. Memory (`memory/`)

- **`session_store.py`** -- SQLite-backed `SessionStore` for session metadata. Provides CRUD (save, get, list, delete) plus indexed lookups (by trace path, by verdict). Schema: `sessions(session_id, created_at, verdict, root_cause, trace_path, html_path, iterations, events_count)`.

### 10. Report Builder (`report.py`)

Generates static JSON and Markdown investigation summaries from trace events. Distinct from the interactive HTML trace visualization -- this produces portable, shareable deliverables suitable for pasting into issues or PRs.

## Data Flow Diagram

```
User / MCP Client
    │
    ▼
┌──────────┐    observe     ┌──────────────┐
│   CLI    │ ─────────────► │ Orchestrator  │
│serve -mcp│                │  (ReAct Loop) │
└──────────┘                └──┬───┬───┬──┘
                               │   │   │
                   ┌───────────┘   │   └──────────┐
                   ▼               ▼              ▼
            ┌──────────┐   ┌──────────┐   ┌──────────┐
            │Hypothesis│   │DAPClient │   │  Tracer  │
            │ Engine   │   │          │   │          │
            └────┬─────┘   └────┬─────┘   └────┬─────┘
                 │              │              │
                 ▼              ▼              ▼
            ┌──────────┐   ┌──────────┐   ┌──────────┐
            │Claude API│   │ debugpy  │   │trace.jsonl
            │          │   │  (TCP)   │   │report.html
            └──────────┘   └────┬─────┘   │live Rich  │
                                │         └──────────┘
                                ▼
                         ┌──────────┐
                         │  Python  │
                         │  Process │
                         │ (debuggee)│
                         └──────────┘
```

## Trace Event Lifecycle

1. **Creation** -- An action occurs (hypothesis generated, breakpoint set, test executed). The module calls `tracer.emit(step_type, data)`.

2. **Serialization** -- `Tracer.emit()` constructs a `TraceEvent` with a UUID event_id, ISO 8601 timestamp, and the caller's data. The event is serialized to a JSON line with `json.dumps()`.

3. **Persistence** -- The JSON line is appended to `probe_traces/<session_id>/trace.jsonl`. `os.fsync()` is called to ensure the write reaches disk. This makes the trace crash-safe.

4. **Live Display** -- If console mode is active, the event is forwarded to `ConsoleObserver`, which updates the Rich dashboard (spinner, event log, hypothesis table).

5. **Post-hoc** -- After the session, `Tracer.build_html_report()` reads all events from memory and passes them to `HTMLReportBuilder`, which generates the self-contained HTML report.

### Event Types

| step_type | Produced by | data payload (key fields) |
|-----------|-------------|---------------------------|
| `observe` | Orchestrator._observe | bug_description, test_output |
| `hypothesize` | HypothesisEngine.generate_hypotheses | prompt, response, hypotheses[] |
| `instrument` | Orchestrator._plan_instrumentation, DAPClient | breakpoints[], breakpoint_id |
| `execute` | Orchestrator._execute, DAPClient | runtime_state, variables, frames, exit_code |
| `analyze` | Orchestrator._analyse, HypothesisEngine.evaluate_all | verdicts{}, evidence[] |
| `iterate` | Orchestrator (all refuted, re-hypothesize) | refuted_hypotheses[], new_evidence |
| `fix` | Orchestrator (conclusion), FixGenerator | root_cause, patch, sandbox_result |
