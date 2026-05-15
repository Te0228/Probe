"""Tests for the orchestrator module — ReAct loop integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch, MagicMock

import pytest


class TestOrchestrator:
    """Tests for the ReAct orchestrator main loop."""

    def test_orchestrator_initializes(self, mock_tracer, probe_config):
        """The orchestrator initialises with a tracer and config."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)
        assert orch is not None
        assert orch._tracer is mock_tracer
        assert orch._config is probe_config

    def test_observe_runs_test_and_captures_output(self, mock_tracer, probe_config):
        """_observe runs a test command and captures failure output."""
        from probe.orchestrator import Orchestrator
        import asyncio

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        result = asyncio.run(
            orch._observe(
                test_command="echo bug found",
                bug_description=None,
                source_code=None,
            )
        )

        assert "bug found" in result["test_output"].lower()
        assert "bug found" in result["description"]

        # Check that observe event was emitted
        observe_events = [e for e in mock_tracer.events if e.step_type == "observe"]
        assert len(observe_events) >= 1

    def test_observe_with_description(self, mock_tracer, probe_config):
        """_observe accepts a natural language bug description."""
        from probe.orchestrator import Orchestrator
        import asyncio

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        result = asyncio.run(
            orch._observe(
                test_command=None,
                bug_description="Comparing str to int in calculator.py",
                source_code=None,
            )
        )

        assert "Comparing str to int" in result["description"]

    def test_hypothesize_generates_hypotheses(self, mock_tracer, probe_config, sample_source_code):
        """_hypothesize generates hypotheses from a bug description."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        hypotheses = orch._hypothesize(
            bug_description="Bug: comparing str to int causes TypeError in calculator",
            source_code=sample_source_code,
        )

        assert hypotheses is not None
        assert isinstance(hypotheses, list)

        # Each hypothesis must have the 5 required fields
        for h in hypotheses:
            assert "hypothesis_id" in h
            assert "statement" in h
            assert "confidence" in h
            assert "verification_plan" in h
            assert "falsification_criteria" in h

        # A hypothesize trace event should have been emitted
        hypothesize_events = [e for e in mock_tracer.events if e.step_type == "hypothesize"]
        assert len(hypothesize_events) >= 1

    def test_plan_instrumentation_extracts_breakpoints(self, mock_tracer, probe_config, sample_source_code):
        """_plan_instrumentation extracts breakpoint locations from hypothesis verification plans."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        hypotheses = [
            {
                "hypothesis_id": "H1",
                "statement": "Type mismatch",
                "confidence": 0.8,
                "verification_plan": [
                    {"action": "set_breakpoint", "file": "calculator.py", "line": 4},
                ],
                "falsification_criteria": "If value is not int, refuted",
            }
        ]

        breakpoints = orch._plan_instrumentation(
            hypotheses=hypotheses,
            source_code=sample_source_code,
            bug_info={"description": "type bug", "test_output": ""},
        )

        assert len(breakpoints) >= 1
        # Should find the matching file from source_code keys
        assert any("calculator.py" in bp["file"] for bp in breakpoints)
        assert any(bp["line"] == 4 for bp in breakpoints)

        # An instrument event should have been emitted
        instrument_events = [e for e in mock_tracer.events if e.step_type == "instrument"]
        assert len(instrument_events) >= 1

    def test_heuristic_breakpoints_falls_back(self, mock_tracer, probe_config, sample_source_code):
        """When no breakpoints from LLM, _heuristic_breakpoints provides fallback locations."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        breakpoints = orch._heuristic_breakpoints(
            source_code=sample_source_code,
            bug_info={"description": "bug", "test_output": ""},
        )

        assert len(breakpoints) > 0, "Heuristic should find breakpoint locations"
        # Each breakpoint should have file and line
        for bp in breakpoints:
            assert "file" in bp
            assert "line" in bp
            assert bp["line"] > 0

    def test_static_inspect_extracts_variables(self, mock_tracer, probe_config, sample_source_code):
        """_static_inspect extracts variable names from source code at breakpoint locations."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        # Line 2 of the sample is "    return a + b" — variables a and b
        breakpoints = [{"file": "calculator.py", "line": 2}]
        result = orch._static_inspect(sample_source_code, breakpoints)

        assert "variables" in result
        assert len(result["variables"]) > 0
        variables = result["variables"]
        assert "a" in variables or "b" in variables

    def test_run_test_no_debug(self, mock_tracer, probe_config):
        """_run_test_no_debug runs a command and captures output."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        output = orch._run_test_no_debug("echo hello_probe")
        assert "hello_probe" in output

    def test_heuristic_analyse_confirms_best_match(self, mock_tracer, probe_config):
        """_heuristic_analyse confirms the best-matching hypothesis and refutes others."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        hypotheses = [
            {
                "hypothesis_id": "H1",
                "statement": "A type mismatch occurs: comparing int to str",
                "confidence": 0.85,
                "verification_plan": [],
                "falsification_criteria": "If value is not int, refuted",
            },
            {
                "hypothesis_id": "H2",
                "statement": "A logic error: wrong comparison operator",
                "confidence": 0.4,
                "verification_plan": [],
                "falsification_criteria": "If operators are correct, refuted",
            },
        ]

        runtime_state = {
            "test_output": "TypeError: can't compare int to str",
            "variables": {"value": "100", "result": "42"},
        }

        result = orch._heuristic_analyse(hypotheses, runtime_state)

        assert "verdicts" in result
        assert result["verdicts"].get("H1") == "confirmed"
        assert result["verdicts"].get("H2") == "refuted"

        # Evidence should have entries for both hypotheses
        evidence = result.get("evidence", [])
        assert len(evidence) >= 2

    def test_heuristic_analyse_inconclusive_when_no_match(self, mock_tracer, probe_config):
        """_heuristic_analyse returns inconclusive when no hypothesis matches evidence."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        hypotheses = [
            {
                "hypothesis_id": "H1",
                "statement": "A network timeout issue",
                "confidence": 0.3,
                "verification_plan": [],
                "falsification_criteria": "If no timeout occurs, refuted",
            },
        ]

        runtime_state = {
            "test_output": "TypeError: unsupported operand type(s)",
            "variables": {},
        }

        result = orch._heuristic_analyse(hypotheses, runtime_state)
        assert result["verdicts"].get("H1") == "inconclusive"

    def test_emit_always_produces_trace_event(self, mock_tracer, probe_config):
        """Every _emit call produces a trace event with correct step_type."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)
        orch._emit("hypothesize", {"test": True})

        assert len(mock_tracer.events) == 1
        assert mock_tracer.events[0].step_type == "hypothesize"
        assert mock_tracer.events[0].data["test"] is True

    def test_run_react_loop_with_static_fallback(self, mock_tracer, probe_config, sample_source_code):
        """A full run() call completes the ReAct loop using heuristic fallback."""
        from probe.orchestrator import Orchestrator

        orch = Orchestrator(tracer=mock_tracer, config=probe_config)

        result = orch.run(
            test_command="echo TypeError: comparing str to int",
            source_code=sample_source_code,
        )

        assert result is not None
        assert "verdict" in result
        assert "iterations" in result
        assert "evidence" in result
        assert result["iterations"] >= 1

        # Should have events for observe, hypothesize, instrument, execute, analyze
        event_types = {e.step_type for e in mock_tracer.events}
        assert "observe" in event_types
        assert "hypothesize" in event_types
        assert "analyze" in event_types
