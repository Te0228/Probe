"""CLI entry point for Probe — hypothesis-driven AI debugging agent.

Commands:
  probe debug --script <script>      Debug any Python script that crashes
  probe debug --test <test_path>     Debug a failing pytest test
  probe debug --describe "<desc>"    Debug from a bug description
  probe debug --quiet                Suppress console, write trace only
  probe debug --output <dir>         Custom output directory
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from probe.config import ProbeConfig
from probe.orchestrator import Orchestrator
from probe.tracer import SessionManager, Tracer

app = typer.Typer(
    name="probe",
    help="Hypothesis-driven AI debugging agent.",
    add_completion=False,
)

console = Console()


@app.command()
def debug(
    script: Optional[str] = typer.Option(
        None, "--script", "-s",
        help="Path to a Python script to debug (e.g., 'broken.py').",
    ),
    run_: Optional[str] = typer.Option(
        None, "--run", "-r",
        help="Any Python command to debug (e.g., 'python -m myapp serve').",
    ),
    test: Optional[str] = typer.Option(
        None, "--test", "-t",
        help="Path to a pytest test to debug (e.g., 'pytest tests/test_user.py').",
    ),
    describe: Optional[str] = typer.Option(
        None, "--describe", "-d",
        help="Natural language bug description to investigate.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q",
        help="Suppress live console display; write trace files only.",
    ),
    output: str = typer.Option(
        "probe_traces",
        "--output", "-o",
        help="Custom output directory for trace files.",
    ),
) -> None:
    """Run the hypothesis-driven debugging loop against a test, script, or bug description.

    Examples:
        probe debug --script broken.py
        probe debug --test "pytest tests/fixtures/type_mismatch/test_calculator.py"
        probe debug --describe "Comparing str to int in the calculator module"
    """
    if not script and not run_ and not test and not describe:
        console.print("[red]Error:[/red] Must provide --script, --run, --test, or --describe.")
        console.print("Examples:")
        console.print("  probe debug --script broken.py")
        console.print("  probe debug --run 'python -m myapp serve'")
        console.print("  probe debug --test 'pytest tests/test_user.py::test_create_user'")
        console.print("  probe debug --describe 'get_user returns None for existing users'")
        raise typer.Exit(code=1)

    # Build config
    config = ProbeConfig.from_env()
    config.quiet = quiet
    if output:
        config.output_dir = output

    console.print()
    console.print("[bold cyan]Probe[/bold cyan] — Hypothesis-Driven Debugging Agent", highlight=False)
    console.print(f"[dim]Session output: {output}/[/dim]")
    console.print()

    # Create tracer and session manager
    session_mgr = SessionManager(output_dir=config.output_dir)
    console.print(f"[dim]Session ID: {session_mgr.session_id}[/dim]")

    with Tracer(
        session_mgr=session_mgr,
        output_dir=config.output_dir,
        console_mode=not quiet,
    ) as tracer:
        source_code: dict[str, str] = {}
        test_command: str | None = None

        if script:
            source_code = _collect_source_code(script)

        if run_:
            source_code = _collect_source_code(run_)

        if test:
            test_command = test
            if not test.startswith("pytest"):
                test_command = f"pytest {test}"
            source_code = _collect_source_code(test)

        if describe:
            source_code = _collect_source_code(".")

        # Run the orchestrator
        orch = Orchestrator(tracer=tracer, config=config)
        result = orch.run(
            test_command=test_command,
            bug_description=describe,
            source_code=source_code,
            script=script,
            run_command=run_,
        )

        # Generate HTML report
        html_path = tracer.build_html_report()

        # Print results
        console.print()
        if result["verdict"] == "confirmed":
            console.print(f"[bold green] Root cause confirmed![/bold green]")

            # Location
            loc = result.get("location", {})
            if loc and loc.get("file"):
                console.print(
                    f"  [bold]Location:[/bold] {loc['file']}:{loc['line']}"
                    f" in {loc.get('function', '')}"
                )
                if loc.get("code"):
                    console.print(f"  [dim]  {loc['code']}[/dim]")

            console.print(f"  [bold]Diagnosis:[/bold] {result['root_cause']}")

            # Fix
            fix = result.get("fix", {})
            if fix.get("generated"):
                console.print()
                console.print(f"  [bold cyan]Suggested Fix:[/bold cyan] {fix.get('description', '')}")
                if fix.get("diff"):
                    console.print(f"  [dim]Sandbox: {fix.get('sandbox_result', '?')}[/dim]")
                    for line in fix["diff"].split("\n")[:20]:
                        if line.startswith("+++") or line.startswith("---"):
                            console.print(f"  [bold]{line}[/bold]")
                        elif line.startswith("+"):
                            console.print(f"  [green]{line}[/green]")
                        elif line.startswith("-"):
                            console.print(f"  [red]{line}[/red]")
                        elif line.startswith("@@"):
                            console.print(f"  [cyan]{line}[/cyan]")
                        else:
                            console.print(f"  [dim]{line}[/dim]")
            elif fix.get("description"):
                console.print(f"  [yellow]Fix:[/yellow] {fix['description']}")

        elif result["verdict"] == "inconclusive":
            console.print(f"[bold yellow] Investigation inconclusive[/bold yellow]")

            # Still show location if available
            loc = result.get("location", {})
            if loc and loc.get("file"):
                console.print(
                    f"  [bold]Crash site:[/bold] {loc['file']}:{loc['line']}"
                    f" in {loc.get('function', '')}"
                )
                if loc.get("code"):
                    console.print(f"  [dim]  {loc['code']}[/dim]")

            console.print(f"  Best hypothesis: {result.get('root_cause', 'N/A')}")
        else:
            console.print(f"[bold red] Unable to determine root cause[/bold red]")

        console.print()
        console.print(f"  Iterations: {result['iterations']}")
        console.print(f"  Evidence items: {len(result.get('evidence', []))}")
        console.print()
        console.print(f"[bold]Trace:[/bold] {tracer.trace_path}")
        console.print(f"[bold]HTML Report:[/bold] {html_path}")
        console.print()
        console.print("[dim]Open the HTML report in a browser for full visualization.[/dim]")


def _collect_source_code(test_path: str) -> dict[str, str]:
    """Collect source code from Python files related to the test.

    Walks the directory containing the test file and reads all .py files.
    """
    source_code: dict[str, str] = {}

    # Determine search root
    search_root = Path.cwd()
    test_file = None

    # Extract the actual file/module path
    path_str = test_path
    if path_str.startswith("pytest "):
        path_str = path_str[len("pytest "):].strip().split()[0]
    elif path_str.startswith("python "):
        parts = path_str.strip().split()
        if len(parts) >= 3 and parts[1] == "-m":
            # python -m module.path → convert to file path
            path_str = parts[2].replace(".", "/") + ".py"
        elif len(parts) >= 2:
            path_str = parts[1]

    candidate = Path(path_str)
    if candidate.exists():
        if candidate.is_file():
            test_file = candidate
            search_root = candidate.parent
        elif candidate.is_dir():
            search_root = candidate

    # Collect .py files from the search root (non-recursive, just the module's directory)
    py_files = list(search_root.glob("*.py"))
    py_files = [f for f in py_files if "__pycache__" not in str(f)]
    if test_file and test_file not in py_files:
        py_files.append(test_file)

    for py_file in py_files:
        try:
            source_code[str(py_file)] = py_file.read_text(encoding="utf-8")
        except Exception:
            pass

    return source_code


@app.command()
def serve(
    mcp: bool = typer.Option(
        False, "--mcp", help="Start as an MCP server (Model Context Protocol).",
    ),
    output: str = typer.Option(
        "probe_traces",
        "--output", "-o",
        help="Output directory for trace files.",
    ),
) -> None:
    """Start Probe in server mode.

    With --mcp, starts and registers tools via stdio using the
    Model Context Protocol, so it can be used as a tool provider
    in MCP-compatible clients (Claude Code, Cursor, etc.).
    """
    if mcp:
        from probe.mcp_server import start_mcp_server

        console.print("[bold cyan]Probe MCP Server[/bold cyan] — starting on stdio")
        console.print(f"[dim]Trace output: {output}[/dim]")
        start_mcp_server(config=ProbeConfig.from_env())
    else:
        console.print("[yellow]Server mode requires --mcp flag.[/yellow]")
        console.print("Usage: probe serve --mcp")


def main() -> None:
    """Entry point for the probe CLI."""
    app()


if __name__ == "__main__":
    main()
