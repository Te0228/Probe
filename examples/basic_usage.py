"""Example: Using Probe programmatically to debug a failing test or script.

This demonstrates the Python API for integrating Probe into custom
debugging workflows, CI pipelines, or development tools.
"""

from pathlib import Path

from probe.config import ProbeConfig
from probe.orchestrator import Orchestrator
from probe.tracer import SessionManager, Tracer


def debug_a_failing_test() -> dict:
    """Run Probe against a pytest test and return the diagnosis."""
    config = ProbeConfig.from_env()
    config.quiet = True

    session_mgr = SessionManager(output_dir="probe_traces")

    with Tracer(session_mgr=session_mgr, output_dir="probe_traces", console_mode=False) as tracer:
        # Collect source code from the test's module
        test_path = Path("tests/fixtures/type_mismatch/test_calculator.py")
        source_code: dict[str, str] = {}
        for py_file in test_path.parent.glob("*.py"):
            source_code[str(py_file)] = py_file.read_text(encoding="utf-8")

        orch = Orchestrator(tracer=tracer, config=config)
        result = orch.run(
            test_command="pytest tests/fixtures/type_mismatch/test_calculator.py",
            source_code=source_code,
        )

        print(f"Verdict: {result['verdict']}")
        print(f"Root cause: {result['root_cause']}")
        print(f"Iterations: {result['iterations']}")
        print(f"Trace: {tracer.trace_path}")

        return result


def debug_a_script() -> dict:
    """Run Probe directly against a crashing Python script."""
    config = ProbeConfig.from_env()
    config.quiet = True

    session_mgr = SessionManager(output_dir="probe_traces")

    with Tracer(session_mgr=session_mgr, output_dir="probe_traces", console_mode=False) as tracer:
        script_path = "broken_script.py"
        source_code = {script_path: Path(script_path).read_text(encoding="utf-8")}

        orch = Orchestrator(tracer=tracer, config=config)
        result = orch.run(
            script=script_path,
            source_code=source_code,
        )

        return result


def debug_from_description() -> dict:
    """Run Probe against a natural-language bug description."""
    config = ProbeConfig.from_env()
    config.quiet = True

    session_mgr = SessionManager(output_dir="probe_traces")

    with Tracer(session_mgr=session_mgr, output_dir="probe_traces", console_mode=False) as tracer:
        orch = Orchestrator(tracer=tracer, config=config)
        result = orch.run(
            bug_description="get_user() returns None for existing user IDs, "
                            "causing AttributeError when the caller tries to "
                            "access .email on the result",
            source_code={},
        )

        return result


if __name__ == "__main__":
    print("=" * 60)
    print("Probe — Programmatic Usage Examples")
    print("=" * 60)

    # Example 1: Debug a failing test
    print("\n[1] Debugging a failing pytest test...")
    result = debug_a_failing_test()
    print(f"    -> {result['verdict']}: {result['root_cause'][:80]}...")

    # Example 3: Debug from a description
    print("\n[3] Debugging from a bug description...")
    result = debug_from_description()
    print(f"    -> {result['verdict']}: {result['root_cause'][:80]}...")
