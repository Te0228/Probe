"""Python DAP adapter using debugpy.

Starts the debuggee process with ``debugpy --listen --wait-for-client``
and then connects to it as a DAP client over TCP.  This avoids the
complexity of ``debugpy.adapter`` stdin/stdout subprocess management.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
from typing import Any

from probe.dap.protocol import Breakpoint, Event, Request, Response, StackFrame, Variable


def _find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PythonAdapter:
    """DAP adapter for Python using debugpy over TCP.

    Architecture:
      1. Pick a free TCP port.
      2. Launch the debuggee::

             python -m debugpy --listen <port> --wait-for-client -m pytest ...

      3. Connect to that port as a DAP client.
      4. Exchange DAP JSON-RPC messages (with Content-Length headers) over TCP.
    """

    def __init__(self, debugpy_path: str = "debugpy") -> None:
        self._debugpy_path = debugpy_path
        self._process: asyncio.subprocess.Process | None = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._port: int = 0

    # -- public API --------------------------------------------------------------

    async def start(
        self,
        program: str = "",
        args: list[str] | None = None,
        cwd: str | None = None,
        module: str = "",
    ) -> None:
        """Launch the debuggee with debugpy and connect as a DAP client.

        Args:
            program: Python script path to debug (launch mode).
            args: Arguments to pass to the program / module.
            cwd: Working directory.
            module: Python module name to run (e.g. 'pytest'). When set,
                    takes precedence over *program*.
        """
        self._port = _find_free_port()
        actual_cwd = cwd or os.getcwd()

        # Build the debuggee command.
        # ``python -m debugpy --listen <port> --wait-for-client -m <module> <args>``
        cmd = [
            sys.executable,
            "-m", "debugpy",
            "--listen", f"127.0.0.1:{self._port}",
            "--wait-for-client",
        ]

        if module:
            cmd.extend(["-m", module])
            cmd.extend(args or [])
        elif program:
            cmd.append(program)
            cmd.extend(args or [])

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=actual_cwd,
        )

        # Connect to debugpy's DAP server on the chosen port.
        # Retry for up to 5 s — the subprocess needs a moment to start
        # listening.
        deadline = time.monotonic() + 5.0
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self._port),
                    timeout=deadline - time.monotonic(),
                )
                break
            except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as exc:
                last_err = exc
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError(
                f"Could not connect to debugpy on 127.0.0.1:{self._port}: {last_err}"
            )

        # Start reading DAP messages from the TCP stream.
        self._reader_task = asyncio.create_task(self._read_loop())

        # Give the TCP stack a moment to settle after connection before we
        # send the first request.  This avoids a race where debugpy hasn't
        # fully set up its handler callbacks yet.
        await asyncio.sleep(0.1)

        # Send the initialize request.
        await self.send_request("initialize", {
            "clientID": "probe",
            "clientName": "Probe Debug Agent",
            "adapterID": "python",
            "pathFormat": "path",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "supportsVariableType": True,
            "supportsVariablePaging": False,
            "supportsRunInTerminalRequest": False,
            "locale": "en",
        })

        # Wait for the "initialized" event.  debugpy sends it after we
        # connect (it is the debuggee side of the handshake).
        initialized_received = False
        try:
            await self.read_event("initialized", timeout=10.0)
            initialized_received = True
        except TimeoutError:
            pass

        # Send a launch request.  Even though debugpy already started the
        # debuggee (via --wait-for-client), the DAP protocol requires a
        # launch/attach handshake before the server enters the "configured"
        # state and accepts breakpoint requests.
        try:
            await self.send_request("launch", {
                "name": "Probe Debug Session",
                "type": "python",
                "request": "launch",
                "noDebug": False,
            })
        except Exception:
            # If launch fails, try attach as a fallback
            try:
                await self.send_request("attach", {
                    "name": "Probe Debug Session",
                    "type": "python",
                    "request": "attach",
                })
            except Exception:
                pass

        # Some debugpy versions send initialized after launch, so check
        # again if we missed it the first time.
        if not initialized_received:
            try:
                await self.read_event("initialized", timeout=5.0)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        """Stop the debug session and clean up resources."""
        # Attempt a graceful disconnect.
        try:
            await asyncio.wait_for(
                self.send_request("disconnect", {"terminateDebuggee": True}),
                timeout=5.0,
            )
        except Exception:
            pass

        # Cancel the reader task.
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        # Close the TCP connection.
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

        # Kill the subprocess if still alive.
        if self._process:
            try:
                if self._process.returncode is None:
                    self._process.kill()
                await self._process.wait()
            except Exception:
                pass
            self._process = None

    async def send_request(
        self, command: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a DAP request and wait for the response."""
        if self._writer is None:
            raise RuntimeError("Debug adapter not connected")

        self._seq += 1
        seq = self._seq
        req = Request(seq=seq, command=command, arguments=arguments or {})
        msg = json.dumps(req.to_dict())

        # Create a future for the response.
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[seq] = fut

        # Write message with Content-Length header (DAP wire format).
        body = msg.encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        self._writer.write(header + body)
        await self._writer.drain()

        try:
            result = await asyncio.wait_for(fut, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(seq, None)
            raise TimeoutError(f"DAP request '{command}' timed out after 30s")
        finally:
            self._pending.pop(seq, None)

        return result

    async def wait_for_stopped(self, timeout: float = 30.0) -> dict[str, Any]:
        """Wait for a 'stopped' event from the debug adapter."""
        return await self.read_event(event_type="stopped", timeout=timeout)

    async def wait_for_stop_or_terminated(
        self, timeout: float = 60.0
    ) -> dict[str, Any]:
        """Wait for a stopped, exited, or terminated event.

        Returns the first matching event.  Intermediate events (output,
        process, etc.) are silently consumed so the caller only sees
        terminal state.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for debuggee stop or exit")
            try:
                msg = await asyncio.wait_for(
                    self._event_queue.get(), timeout=remaining
                )
            except asyncio.TimeoutError:
                raise TimeoutError("Timed out waiting for debuggee stop or exit")
            event_type = msg.get("event", "")
            if event_type in ("stopped", "exited", "terminated"):
                return msg
            # Otherwise: silently consume output / process / module events
        raise TimeoutError("Timed out waiting for debuggee stop or exit")

    async def read_event(
        self, event_type: str | None = None, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Read the next DAP event, optionally filtered by type."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for event '{event_type or '*'}'"
                    )
                msg = await asyncio.wait_for(
                    self._event_queue.get(), timeout=remaining
                )
                if event_type is None or msg.get("event") == event_type:
                    return msg
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"Timed out waiting for event '{event_type or '*'}'"
                )
        raise TimeoutError(
            f"Timed out waiting for event '{event_type or '*'}'"
        )

    # -- internal read loop ------------------------------------------------------

    async def _read_loop(self) -> None:
        """Continuously read DAP messages from the TCP connection."""
        if self._reader is None:
            return

        buffer = b""
        try:
            while True:
                chunk = await self._reader.read(4096)
                if not chunk:
                    break
                buffer += chunk
                buffer = self._process_messages(buffer)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _process_messages(self, buffer: bytes) -> bytes:
        """Parse DAP messages from the buffer using Content-Length header format."""
        while True:
            # Look for Content-Length header
            header_end = buffer.find(b"\r\n\r\n")
            if header_end == -1:
                break

            header = buffer[:header_end].decode("utf-8", errors="replace")
            content_length = 0
            for line in header.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    try:
                        content_length = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass

            if content_length == 0:
                # Skip malformed header
                buffer = buffer[header_end + 4:]
                continue

            body_start = header_end + 4
            if len(buffer) < body_start + content_length:
                break  # Need more data

            body = buffer[body_start:body_start + content_length].decode(
                "utf-8", errors="replace"
            )
            buffer = buffer[body_start + content_length:]

            try:
                msg = json.loads(body)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")
            if msg_type == "response":
                req_seq = msg.get("request_seq", 0)
                fut = self._pending.get(req_seq)
                if fut and not fut.done():
                    if msg.get("success"):
                        fut.set_result(msg.get("body", {}))
                    else:
                        fut.set_exception(
                            RuntimeError(
                                f"DAP error: {msg.get('command', '?')} - "
                                f"{msg.get('message', 'unknown')}"
                            )
                        )
            elif msg_type == "event":
                self._event_queue.put_nowait(msg)

        return buffer
