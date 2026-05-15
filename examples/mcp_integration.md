# MCP Integration Guide

Probe can run as an MCP (Model Context Protocol) server, exposing 8 debugging
tools to any MCP-compatible client (Claude Code, Cursor, Continue, etc.).

## Quick Start

```bash
# Start Probe as an MCP server
probe serve --mcp
```

The server communicates over stdio using JSON-RPC. No TCP port needed.

## Registering Probe as an MCP Tool Provider

### Claude Code

Add to `~/.claude/claude_desktop_config.json` or `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "probe": {
      "command": "probe",
      "args": ["serve", "--mcp"]
    }
  }
}
```

### Cursor

Add to Cursor's MCP configuration (Settings → MCP → Add Server):

```json
{
  "mcpServers": {
    "probe": {
      "command": "probe",
      "args": ["serve", "--mcp"]
    }
  }
}
```

## Available Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `start_debug_session` | Start a new DAP debug session | `test_command`, `cwd` |
| `set_breakpoint` | Set a breakpoint at file:line | `file_path`, `line`, `condition?` |
| `remove_breakpoint` | Remove a breakpoint | `file_path`, `line` |
| `continue_execution` | Continue past a breakpoint | `thread_id?` |
| `eval_expression` | Evaluate a Python expression | `expression`, `frame_id?` |
| `get_stack_trace` | Get current stack frames | `thread_id?` |
| `get_variables` | Get local variables at a frame | `frame_id` |
| `run_test` | Run a pytest test under DAP | `test_path`, `args?` |

## Example Workflow

When a client like Claude Code connects, it can drive Probe through a debug
session:

1. `start_debug_session` — launches debugpy for the target test
2. `set_breakpoint` — places breakpoints at suspected bug locations
3. `run_test` — executes the test; pauses at breakpoints
4. `get_variables` — inspects local variables at each pause
5. `get_stack_trace` — examines the call path
6. `eval_expression` — evaluates hypotheses inline
7. `remove_breakpoint` — cleans up unneeded breakpoints
8. `continue_execution` — resumes to next breakpoint or test completion

## Observability

Every MCP tool invocation emits a TraceEvent to the session's JSONL trace file
at `probe_traces/<session_id>/trace.jsonl`. After the session, open the HTML
report for a visual timeline of every MCP operation.
