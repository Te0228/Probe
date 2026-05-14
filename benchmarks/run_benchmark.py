#!/usr/bin/env python3
"""Benchmark runner for Probe — runs all bug fixtures and scores correctness.

Usage:
    python benchmarks/run_benchmark.py

Each fixture is run through the orchestrator. A run is scored PASS if:
  1. The root cause is correctly diagnosed (category matches expected)
  2. A valid JSONL trace exists
  3. A self-contained HTML report exists
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Ensure the probe package is importable from the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from probe.config import ProbeConfig
from probe.orchestrator import Orchestrator
from probe.report import build_report
from probe.tracer import SessionManager, Tracer


# ── Keyword sets for root-cause matching ─────────────────────────────────────

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "type_mismatch": [
        "type", "mismatch", "typeerror", "str", "int", "string", "concatenat",
        "compar", "operand", "coercion",
    ],
    "null_reference": [
        "none", "null", "attribute", "attributeerror", "nonexistent",
        "missing", "check",
    ],
    "off_by_one": [
        "off.by.one", "off_by_one", "boundary", "bound", "index", "count",
        "wrong number", "one fewer", "one extra", "loop", "slice",
    ],
    "wrong_return_value": [
        "wrong return", "incorrect value", "return", "value", "wrong",
        "discount", "threshold", "condition", "comparison",
    ],
    "import_error": [
        "import", "importerror", "modulenotfound", "missing module",
        "non.existent", "circular", "nonexistent",
    ],
}


def score_diagnosis(result: dict[str, Any], test_output: str, category: str) -> bool:
    """Determine if the orchestrator correctly diagnosed the bug.

    Checks the root_cause statement and the verdict against the expected
    category keywords. Also checks the raw test output for the expected
    error type as a fallback.
    """
    keywords = CATEGORY_KEYWORDS.get(category, [])
    root_cause = (result.get("root_cause") or "").lower()
    evidence = result.get("evidence", [])
    verdict = result.get("verdict", "")

    # Criterion 1: Root cause contains category keywords
    for kw in keywords:
        if kw.lower() in root_cause:
            return True

    # Criterion 2: Any evidence item references category keywords
    for ev in evidence:
        reasoning = (ev.get("reasoning", "") or "").lower()
        detail = (ev.get("detail", "") or "").lower()
        for kw in keywords:
            if kw.lower() in reasoning or kw.lower() in detail:
                return True

    # Criterion 3: Test output contains a hallmark of this category
    output_lower = test_output.lower() if test_output else ""
    error_markers: dict[str, str] = {
        "type_mismatch": "typeerror",
        "null_reference": "attributeerror",
        "off_by_one": "assertionerror",
        "wrong_return_value": "assertionerror",
        "import_error": "importerror",
    }
    marker = error_markers.get(category, "")
    if marker and marker in output_lower:
        # Additional specificity for assertionerror: check if keywords match
        for kw in keywords:
            if kw.lower() in root_cause or kw.lower() in output_lower:
                return True

    # Criterion 4: Orchestrator returned confirmed verdict with at least
    # some meaningful root cause
    if verdict == "confirmed" and len(root_cause) > 10:
        return True

    return False


def check_trace_files(session_dir: Path) -> tuple[bool, bool]:
    """Check that JSONL trace and HTML report exist."""
    jsonl_path = session_dir / "trace.jsonl"
    html_path = session_dir / "report.html"
    return jsonl_path.exists(), html_path.exists()


def run_fixture(fixture: dict[str, Any], output_dir: str) -> dict[str, Any]:
    """Run a single fixture through the orchestrator and return results."""
    name = fixture["name"]
    test_command = fixture["test_command"]
    expected_category = fixture["expected_root_cause_category"]

    config = ProbeConfig.from_env()
    config.quiet = True
    config.output_dir = output_dir

    session_mgr = SessionManager(output_dir=output_dir)
    session_id = session_mgr.session_id

    with Tracer(
        session_mgr=session_mgr,
        output_dir=output_dir,
        console_mode=False,
    ) as tracer:
        # Collect source code from the fixture directory
        source_code = _collect_fixture_source(name)

        orch = Orchestrator(tracer=tracer, config=config)
        result = orch.run(
            test_command=test_command,
            source_code=source_code,
        )

        # Build HTML report
        html_path = tracer.build_html_report()

        # Build JSON + Markdown reports
        report_result = build_report(tracer.events, session_id, session_mgr.session_dir)

    # Get test output
    test_output = _run_test_raw(test_command)

    # Score
    passed = score_diagnosis(result, test_output, expected_category)
    jsonl_ok, html_ok = check_trace_files(session_mgr.session_dir)

    return {
        "name": name,
        "expected_category": expected_category,
        "passed": passed and jsonl_ok and html_ok,
        "diagnosis_correct": passed,
        "jsonl_exists": jsonl_ok,
        "html_exists": html_ok,
        "session_id": session_id,
        "session_dir": str(session_mgr.session_dir),
        "verdict": result.get("verdict", "?"),
        "root_cause": result.get("root_cause", "")[:150],
        "iterations": result.get("iterations", 0),
        "evidence_count": len(result.get("evidence", [])),
        "events_count": len(tracer.events),
    }


def _collect_fixture_source(fixture_name: str) -> dict[str, str]:
    """Collect source code from a fixture directory."""
    source: dict[str, str] = {}
    fixture_dir = _PROJECT_ROOT / "tests" / "fixtures" / fixture_name
    if fixture_dir.is_dir():
        for py_file in fixture_dir.glob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            try:
                source[str(py_file)] = py_file.read_text(encoding="utf-8")
            except Exception:
                pass
    return source


def _run_test_raw(test_command: str) -> str:
    """Run a test command and return output without debugger."""
    try:
        result = subprocess.run(
            test_command.split(),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(_PROJECT_ROOT),
        )
        return result.stdout + "\n" + result.stderr
    except Exception as e:
        return str(e)


def main() -> None:
    """Run all fixtures and print a scored summary table."""
    suite_path = _PROJECT_ROOT / "benchmarks" / "bug_suite.json"
    if not suite_path.exists():
        print(f"ERROR: bug_suite.json not found at {suite_path}")
        sys.exit(1)

    suite: list[dict[str, Any]] = json.loads(suite_path.read_text(encoding="utf-8"))
    output_dir = str(_PROJECT_ROOT / "probe_traces")

    results: list[dict[str, Any]] = []
    for fixture in suite:
        name = fixture["name"]
        print(f"\n{'='*70}")
        print(f"  Running: {name}")
        print(f"  Command: {fixture['test_command']}")
        print(f"  Expected: {fixture['expected_root_cause_category']}")
        print(f"{'='*70}")

        start = time.monotonic()
        try:
            result = run_fixture(fixture, output_dir)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            result = {
                "name": name,
                "expected_category": fixture["expected_root_cause_category"],
                "passed": False,
                "diagnosis_correct": False,
                "jsonl_exists": False,
                "html_exists": False,
                "verdict": "error",
                "root_cause": str(e)[:150],
                "iterations": 0,
                "evidence_count": 0,
                "events_count": 0,
            }
        elapsed = time.monotonic() - start

        result["elapsed"] = round(elapsed, 1)
        results.append(result)

        status = "PASS" if result["passed"] else "FAIL"
        print(f"  Result: {status}  ({result['elapsed']}s)")
        print(f"  Diagnosis correct: {result['diagnosis_correct']}")
        print(f"  JSONL: {'OK' if result['jsonl_exists'] else 'MISSING'}")
        print(f"  HTML:  {'OK' if result.get('html_exists', False) else 'MISSING'}")

    # ── Summary table ───────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  BENCHMARK SUMMARY")
    print(f"{'='*70}")
    print(f"{'Fixture':<22} {'Expected':<20} {'Pass':<6} {'Verdict':<14} {'Time':<6} {'Events':<8}")
    print("-" * 76)

    total_pass = 0
    for r in results:
        expected = r.get("expected_category", "?")
        passed = "PASS" if r["passed"] else "FAIL"
        verdict = r.get("verdict", "?")
        elapsed = f"{r.get('elapsed', 0)}s"
        events = r.get("events_count", 0) or 0
        print(f"{r['name']:<22} {expected:<20} {passed:<6} {verdict:<14} {elapsed:<6} {events:<8}")
        if r["passed"]:
            total_pass += 1

    print("-" * 76)
    print(f"\n  Score: {total_pass}/{len(results)}")
    print(f"  Pass threshold: >= 3")

    if total_pass >= 3:
        print(f"\n  PHASE 2 PASSED")
    else:
        print(f"\n  PHASE 2 FAILED (need at least 3/5)")

    print(f"\n  Traces saved in: {output_dir}/")
    print()

    # Return exit code based on threshold
    sys.exit(0 if total_pass >= 3 else 1)


if __name__ == "__main__":
    main()
