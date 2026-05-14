# Adding a Language

Probe uses DAP (Debug Adapter Protocol) to communicate with language runtimes. Adding support for a new language means implementing a DAP adapter that bridges Probe to that language's debugger. The orchestrator, hypothesis engine, tracer, and all other modules remain unchanged.

## Overview

To add a new language, you need:

1. A DAP-compliant debug adapter for the language (e.g., `delve` for Go, `node --inspect` for JavaScript, `cppdbg` for C++)
2. A new adapter class in `src/probe/dap/adapters/` that implements the `DAPAdapter` Protocol
3. Register the adapter in the adapter `__init__.py`

## Step-by-Step Guide

### Step 1: Understand the DAPAdapter Protocol

All adapters must implement this interface (defined in `src/probe/dap/adapters/base.py`):

```python
class DAPAdapter(Protocol):
    async def start(self, program: str, args: list[str] | None = None, cwd: str | None = None) -> None:
        """Start a debug session with the given program and arguments."""
        ...

    async def stop(self) -> None:
        """Stop the debug session and clean up resources."""
        ...

    async def send_request(self, command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a DAP request and return the response body."""
        ...

    async def wait_for_stopped(self, timeout: float = 30.0) -> dict[str, Any]:
        """Wait for a 'stopped' event and return the event body."""
        ...

    async def read_event(self, event_type: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
        """Read the next DAP event, optionally filtered by type."""
        ...
```

### Step 2: Create the Adapter Class

Create a new file, e.g., `src/probe/dap/adapters/go.py`:

```python
"""Go DAP adapter using delve."""

import asyncio
import json
import os
import socket
from typing import Any

from probe.dap.adapters.base import DAPAdapter


class GoAdapter:
    """DAP adapter for Go using delve."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue()

    async def start(
        self,
        program: str = "",
        args: list[str] | None = None,
        cwd: str | None = None,
        module: str = "",
    ) -> None:
        """Launch the debuggee with delve in DAP mode."""
        # Find the delve binary (dlv)
        dlv_path = "dlv"

        cmd = [
            dlv_path, "dap",
            "--listen", "127.0.0.1:0",   # Pick a free port
        ]

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or os.getcwd(),
        )

        # Parse the port from delve's output and connect...
        # (Implementation follows the same pattern as PythonAdapter)

    async def stop(self) -> None:
        """Send disconnect and clean up."""
        # ...

    async def send_request(
        self, command: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a DAP request over TCP and wait for response."""
        # Uses the same Content-Length: ...\r\n\r\n wire format
        # ...

    async def wait_for_stopped(self, timeout: float = 30.0) -> dict[str, Any]:
        """Wait for a 'stopped' event."""
        return await self.read_event(event_type="stopped", timeout=timeout)

    async def read_event(
        self, event_type: str | None = None, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Read the next DAP event, optionally filtered by type."""
        # ...
```

### Step 3: Handle Language-Specific Initialization

Different debug adapters have different initialization sequences. The key DAP requests are:

1. **`initialize`** -- Advertise client capabilities. Must include `clientID`, `adapterID` for the target language.
2. **`launch`** or **`attach`** -- How the debuggee is started. Python uses `launch` with `--wait-for-client`. Go/delve might use `launch` with `program` and `args`. Node.js might use `attach` to a running inspector.
3. **`configurationDone`** -- Sent after all breakpoints are set, signals the debuggee to start executing.
4. **`setBreakpoints`** -- Set breakpoints at specific file:line locations.
5. **`setExceptionBreakpoints`** -- Optional. Some languages support catching uncaught exceptions.

The `PythonAdapter` in `src/probe/dap/adapters/python.py` is the reference implementation. Study it for the TCP connection loop, `Content-Length` header parsing, and the `_read_loop` pattern.

### Step 4: Register the Adapter

Update `src/probe/dap/adapters/__init__.py`:

```python
from probe.dap.adapters.python import PythonAdapter
from probe.dap.adapters.go import GoAdapter  # NEW

__all__ = ["PythonAdapter", "GoAdapter"]
```

### Step 5: Update the Orchestrator

In `src/probe/orchestrator.py`, add language detection and adapter selection:

```python
# In _run_async(), replace:
adapter = PythonAdapter()

# With:
language = detect_language(source_code, test_command)
if language == "python":
    adapter = PythonAdapter()
elif language == "go":
    adapter = GoAdapter()
else:
    raise ValueError(f"Unsupported language: {language}")
```

Detection can be based on file extensions (`.py` vs `.go` vs `.js`), test runner commands (`pytest` vs `go test`), or explicit configuration.

### Step 6: Update Runtime State Collection

Different languages expose different runtime information through DAP:

- **Python (debugpy):** Evaluates expressions via `evaluate` request with `context: "repl"`. Variables available through `scopes` -> `variables`.
- **Go (delve):** May not support expression evaluation. Variables available through `scopes` -> `variables` with Go-specific types.
- **JavaScript (node):** Supports expression evaluation in the console context. Variables may include closures and prototypes.

The `DAPClient` in `src/probe/dap/client.py` handles the common DAP operations. If a language has quirks, you may need to add adapter-specific logic there.

## Key Considerations

### Path Resolution
DAP requires absolute paths for source files. Make sure your adapter resolves relative paths from the working directory. The `PythonAdapter` does this in the `start()` method by passing `cwd`.

### Breakpoint Verification
After `setBreakpoints`, check the response for `verified: true`. If a breakpoint is not verified, it may be on a non-executable line or the file path may be wrong. Log this and fall back to heuristic breakpoint placement.

### Thread Support
Multi-threaded debuggees may send `stopped` events on multiple threads. The `DAPClient.get_stack_trace()` defaults to `threadId=1`. For multi-threaded scenarios, use the `threadId` from the stopped event body.

### Timeouts
Always set reasonable timeouts on `wait_for_stopped()` and `send_request()`. A debuggee that hangs in an infinite loop will block the entire ReAct loop. The default timeout is 30s for stopped events and 10s for requests.

## Testing Your Adapter

1. Create a fixture project under `tests/fixtures/` that demonstrates a bug in the target language.
2. Run `probe debug --test "pytest tests/fixtures/your_fixture/test_bug.py"` and verify:
   - The DAP session starts and breakpoints are verified
   - Runtime state (variables, stack) is collected
   - The orchestrator produces a root cause diagnosis
   - A JSONL trace and HTML report are generated
3. Write a pytest test in `tests/test_dap_client.py` that uses a mock adapter for your language.

## Reference: PythonAdapter Architecture

The `PythonAdapter` is the canonical reference. Here's its flow:

```
start()
  ├─ Find free TCP port
  ├─ Launch: python -m debugpy --listen <port> --wait-for-client -m pytest ...
  ├─ Connect to TCP port (retry loop up to 5s)
  ├─ _read_loop() starts in background
  ├─ Send initialize request
  ├─ Wait for initialized event
  └─ Send launch request

stop()
  ├─ Send disconnect request
  ├─ Cancel _read_loop task
  ├─ Close TCP connection
  └─ Kill subprocess

send_request(command, args)
  ├─ Increment seq
  ├─ Create pending Future
  ├─ Write Content-Length header + JSON body
  ├─ Wait for Future (timeout 30s)
  └─ Return response body

_read_loop()
  ├─ Read 4KB chunks from TCP
  ├─ Parse Content-Length headers
  ├─ Extract JSON message body
  ├─ If response: resolve pending Future
  └─ If event: enqueue in event_queue
```
