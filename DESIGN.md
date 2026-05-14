# Design Decisions

Every major architectural decision in Probe is documented here with its rationale and trade-off analysis.

## 1. DAP over pdb

**Decision:** All debug operations go through DAP JSON-RPC via debugpy. Never shell out to pdb/ipdb.

**Rationale:**
- DAP is a standard protocol supported by every major IDE and language runtime. Using it means Probe can work across languages by swapping the adapter, without changing the orchestrator.
- pdb is Python-specific and single-threaded. It cannot be driven programmatically without fragile `pexpect` hacks.
- DAP gives structured, machine-parseable responses (stack frames, variables, breakpoints) that the hypothesis engine can reason about.
- debugpy is maintained by Microsoft and is the reference DAP implementation for Python. It handles threading, async, subprocesses, and edge cases that a hand-rolled pdb wrapper would miss.

**Trade-off:**
- DAP has more overhead than pdb (TCP connection, JSON serialization). For small scripts, the startup latency is noticeable (~1-2s). For the target use case (failing test suites), this overhead is negligible compared to test execution time.
- The DAP protocol has a steep learning curve. We abstract it behind `DAPClient` and adapter interfaces so that the orchestrator never touches raw DAP messages.

## 2. Custom ReAct over LangChain / Agent Frameworks

**Decision:** Write a custom ~300-line ReAct orchestrator. No agent framework dependencies.

**Rationale:**
- The hypothesis-driven debugging loop is too specialized for generic agent frameworks. LangChain's ReAct abstraction assumes a "thought-action-observation" cycle that does not map cleanly to the "hypothesize-instrument-execute-analyze" cycle required here.
- Agent frameworks add significant dependency weight (LangChain alone pulls in 50+ transitive dependencies) and complexity.
- Debugging requires precise control over the execution order of tool calls. Generic agent planners make it difficult to enforce that breakpoints are set before execution, or that analysis happens before re-hypothesizing.
- The custom loop is 300 lines, fully deterministic, and has no black-box behavior. Every branch is visible and testable.
- No vendor lock-in. The orchestrator can use any LLM provider by swapping the hypothesis engine.

**Trade-off:**
- We lose LangChain's built-in observability (LangSmith, callbacks). We compensate with Probe's own TraceEngine, which provides richer, more specific observability (hypothesis status, evidence gallery, decision tree) than a generic agent tracer would.
- We lose LangChain's tool abstraction. We compensate with a simple `ToolRegistry` (~50 lines) that is Protocol-based and sufficient for our needs.

## 3. Hypothesis-Driven over Linear Debugging

**Decision:** Instead of step-through or breakpoint-at-error debugging, Probe generates multiple competing hypotheses and uses falsification to eliminate them.

**Rationale:**
- Linear debugging (step through until you find the bug) does not scale to complex codebases. It is time-consuming and relies on the developer's intuition.
- Hypothesis-driven approach forces structured reasoning: every claim about the bug must be paired with a falsification criteria. This prevents confirmation bias loops where the agent keeps collecting evidence that "supports" its first idea.
- Multiple competing hypotheses mean Probe explores parallel causal paths. The evidence that refutes one hypothesis often confirms another.
- The approach naturally produces an auditable decision tree that users can review to understand why Probe reached its conclusion.

**Trade-off:**
- Generating hypotheses requires an LLM call, which has latency and cost. We mitigate by limiting to 3 iterations max and using heuristic fallbacks when the API is unavailable.
- Some bugs require deep exploration of a single path. The multi-hypothesis approach can feel "scatterbrained" for very specific bugs. We mitigate by allowing hypotheses to be refined across iterations, funneling toward the root cause.

## 4. Append-Only JSONL Trace over In-Memory Logging

**Decision:** Every TraceEvent is written immediately to an append-only JSONL file. No in-memory batching.

**Rationale:**
- If the debuggee crashes or the agent process is killed, partial traces are preserved. In-memory logging loses everything on crash.
- `tail -f probe_traces/<session_id>/trace.jsonl` works in real time for external monitoring.
- JSONL is trivially parseable line-by-line by any tool (jq, Python, log aggregators). No need for a custom parser.
- Append-only semantics mean no random-access overhead and no corruption risk from partial writes.

**Trade-off:**
- Disk I/O on every event adds latency. We mitigate by keeping events small (~1KB) and using `os.fsync()` to ensure durability. For the target use case (a debug session with ~50 events), the total I/O is negligible.
- JSONL files can grow large if a session generates thousands of events. We cap at ~50 events per session for v0.1 (one debug cycle).

## 5. Self-Contained HTML over Server-Rendered UI

**Decision:** The visualization report is a single HTML file with inline CSS and vanilla JavaScript. No web server, no framework, no external dependencies.

**Rationale:**
- Users can open the report directly in any modern browser by double-clicking the file. No `python -m http.server`, no port conflicts, no CORS issues.
- Self-contained files are trivially shareable (email, Slack, paste into GitHub issues).
- No data leaves the user's machine. This is critical for debugging proprietary code.
- Vanilla JavaScript (no React/Vue/Svelte) means zero build step and instant load time. The report is ~20KB gzipped.

**Trade-off:**
- No real-time streaming to the browser. The user must refresh to see new events. We compensate with the live Rich console dashboard for real-time observation.
- Limited interactivity compared to a full web app. The HTML report is designed for post-hoc review, not for live control.
- No syntax highlighting library. We implement simple CSS-based diff coloring.

## 6. SQLite over Postgres

**Decision:** Session metadata is stored in a local SQLite database. No external database server.

**Rationale:**
- Probe is a local CLI tool. Requiring a Postgres server would be a massive deployment barrier.
- SQLite requires zero configuration, zero daemons, and stores everything in a single file.
- The data volume is tiny (one row per session, ~100 bytes per row). Even a thousand sessions would occupy <1MB.
- SQLite supports the indexed lookups we need (by trace path, by verdict) without any setup.

**Trade-off:**
- No concurrent multi-process access. SQLite handles single-writer concurrency fine, but multiple simultaneous Probe instances writing to the same DB could conflict. For v0.1, single-session usage is the target.
- No network access. If a team wanted a shared session database, they would need to file-share the SQLite file or migrate to Postgres later. The `SessionStore` interface abstracts the backend, making this migration straightforward.

## 7. Protocol/ABC Interfaces for All Modules

**Decision:** Every module communicates through abstract interfaces (Protocol or ABC). The orchestrator depends on `ITracer`, `HypothesisEngine`, and `DAPAdapter` abstractions, not concrete implementations.

**Rationale:**
- This is the mechanism that enables future language adapters. A Go adapter can implement `DAPAdapter` and the orchestrator never changes.
- Testing is dramatically simpler. We can mock the tracer, hypothesis engine, and DAP client without any monkeypatching.
- The interfaces serve as documentation. Reading `DAPAdapter` immediately tells you what every adapter must implement.

**Trade-off:**
- Protocol-based interfaces in Python are "duck typing" -- errors surface at runtime, not at import time. We mitigate with comprehensive pytest tests that exercise all interface contracts.
- Slightly more verbose code (explicit Protocol definitions, type annotations). We consider this a worthwhile investment for maintainability.

## 8. debugpy TCP over stdin/stdout Communication

**Decision:** The Python adapter launches debugpy with `--listen <port> --wait-for-client` and connects over TCP, rather than managing debugpy as a child process communicating over stdin/stdout.

**Rationale:**
- debugpy's stdin/stdout mode requires careful management of its output stream (debugpy logs, DAP messages, and debuggee output all share stdout). TCP mode cleanly separates these concerns.
- TCP mode allows attaching to an already-running debuggee, which is useful for debugging long-running services.
- The TCP handshake is more robust than subprocess stdin/stdout management, especially across platforms (Windows line endings, buffering, etc.).

**Trade-off:**
- Requires finding a free port (we use `_find_free_port()`).
- Slightly more complex setup (port discovery, connection retry loop).
- Port conflicts possible in constrained environments. We mitigate by binding to 127.0.0.1 only.
