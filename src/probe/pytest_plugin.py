"""pytest plugin — auto-diagnose failures with Probe.

Usage:
    pytest tests/ --probe          # Auto-diagnose every failure
    pytest tests/ --probe --tb=short  # Combine with standard pytest flags

When a test fails, Probe automatically:
  1. Collects the traceback and source code
  2. Generates hypotheses
  3. Instruments via DAP (debugpy), collects runtime state
  4. Analyses evidence, refutes or confirms each hypothesis
  5. Prints the root cause diagnosis inline
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from probe.config import ProbeConfig
from probe.orchestrator import Orchestrator
from probe.tracer import SessionManager, Tracer


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("probe", "AI-powered failure diagnosis")
    group.addoption(
        "--probe",
        action="store_true",
        default=False,
        help="Auto-diagnose test failures with Probe (DAP + hypothesis-driven).",
    )
    group.addoption(
        "--probe-output",
        action="store",
        default="probe_traces",
        help="Output directory for Probe trace files (default: probe_traces).",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--probe", default=False):
        config._probe_failures: list[dict[str, Any]] = []
        config._probe_console = Console()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Any:
    outcome = yield
    report = outcome.get_result()

    if not item.config.getoption("--probe", default=False):
        return

    if report.when == "call" and report.failed:
        item.config._probe_failures.append({
            "nodeid": item.nodeid,
            "fspath": str(item.path),
            "call": call,
            "longrepr": str(report.longrepr),
        })


def pytest_sessionfinish(session: pytest.Session) -> None:
    if not session.config.getoption("--probe", default=False):
        return

    failures = getattr(session.config, "_probe_failures", [])
    if not failures:
        return

    console = session.config._probe_console
    output_dir = session.config.getoption("--probe-output", "probe_traces")
    config = ProbeConfig.from_env()
    config.output_dir = output_dir
    config.quiet = True  # Don't flood — one summary per failure

    console.print()
    console.rule("[bold cyan]Probe — Diagnosing Failures[/bold cyan]")
    console.print()

    for i, failure in enumerate(failures, 1):
        nodeid: str = failure["nodeid"]
        fspath: str = failure["fspath"]

        console.print(f"[bold]  [{i}/{len(failures)}][/bold] {nodeid}")

        # Collect source code from the test module's directory
        source_code = _collect_source(Path(fspath))

        # Build a test command that pytest would use
        test_command = f"pytest {fspath}::{nodeid.split('::')[-1]}"

        # Run Probe: orchestrator uses asyncio internally, so run in a dedicated thread
        try:
            def _run_in_thread():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(
                        _diagnose(config, output_dir, test_command, source_code)
                    )
                finally:
                    loop.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_run_in_thread)
                result = future.result(timeout=120)
        except Exception as exc:
            console.print(f"    [red]Probe error:[/red] {exc}")
            continue

        # Print verdict
        if result["verdict"] == "confirmed":
            console.print(f"    [green]Root cause:[/green] {result['root_cause']}")
        elif result["verdict"] == "refuted":
            console.print(f"    [yellow]Best guess:[/yellow] {result.get('root_cause', 'N/A')}")
        else:
            console.print(f"    [dim]Inconclusive[/dim] — {result.get('root_cause', 'N/A')[:120]}")

        console.print(
            f"    [dim]Iterations: {result['iterations']} | "
            f"Trace: {output_dir}/[/dim]"
        )
        console.print()

    console.rule("[dim]End of Probe diagnosis[/dim]")
    console.print()


async def _diagnose(
    config: ProbeConfig,
    output_dir: str,
    test_command: str,
    source_code: dict[str, str],
) -> dict[str, Any]:
    """Run the Probe orchestrator for a single test failure.

    Uses _run_async directly because the caller already manages the event loop
    (orchestrator.run() calls asyncio.run() internally, which conflicts with
    pytest's own event loop).
    """
    session_mgr = SessionManager(output_dir=output_dir)

    with Tracer(
        session_mgr=session_mgr,
        output_dir=output_dir,
        console_mode=False,
    ) as tracer:
        orch = Orchestrator(tracer=tracer, config=config)
        result = await orch._run_async(
            test_command=test_command,
            bug_description=None,
            source_code=source_code,
        )
        tracer.build_html_report()

    return result


def _collect_source(test_file: Path) -> dict[str, str]:
    """Collect .py source files from the test file's directory tree."""
    source: dict[str, str] = {}
    search_root = test_file.parent

    for py_file in search_root.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            source[str(py_file)] = py_file.read_text(encoding="utf-8")
        except Exception:
            pass
    return source
