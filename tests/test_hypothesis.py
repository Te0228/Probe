"""Tests for the hypothesis engine — generation, validation, and evaluation."""

from __future__ import annotations

from typing import Any

import pytest


class TestHypothesisEngine:
    """Tests for hypothesis generation and evaluation."""

    def test_engine_initializes(self, probe_config, mock_tracer):
        """HypothesisEngine initialises with config and tracer."""
        from probe.hypothesis import HypothesisEngine

        engine = HypothesisEngine(config=probe_config, tracer=mock_tracer)
        assert engine is not None
        assert engine._config is probe_config

    def test_heuristic_hypotheses_for_typeerror(self, probe_config):
        """Heuristic fallback generates type-mismatch hypotheses for TypeError."""
        from probe.hypothesis import HypothesisEngine

        bug_desc = "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
        source_code = {
            "calc.py": "def add(a, b):\n    return a + b\n"
        }

        hypotheses = HypothesisEngine._heuristic_hypotheses(bug_desc, source_code)

        assert isinstance(hypotheses, list)
        assert len(hypotheses) > 0
        # At least one hypothesis should mention type mismatch
        statements = [h.get("statement", "").lower() for h in hypotheses]
        assert any("type" in s for s in statements)

    def test_heuristic_hypotheses_for_attributeerror(self, probe_config):
        """Heuristic fallback detects null-reference patterns."""
        from probe.hypothesis import HypothesisEngine

        bug_desc = "AttributeError: 'NoneType' object has no attribute 'upper'"
        source_code = {
            "finder.py": "def get_name(x):\n    return x.upper()\n"
        }

        hypotheses = HypothesisEngine._heuristic_hypotheses(bug_desc, source_code)

        assert len(hypotheses) > 0
        statements = [h.get("statement", "").lower() for h in hypotheses]
        assert any(("none" in s or "null" in s or "attribute" in s) for s in statements)

    def test_every_hypothesis_has_required_fields(self, probe_config):
        """Every generated hypothesis has all 5 required fields."""
        from probe.hypothesis import HypothesisEngine

        bug_desc = "AssertionError: expected 5 but got 4"
        hypotheses = HypothesisEngine._heuristic_hypotheses(bug_desc, {})

        required_fields = [
            "hypothesis_id", "statement", "confidence",
            "verification_plan", "falsification_criteria",
        ]

        for h in hypotheses:
            for field in required_fields:
                assert field in h, f"Hypothesis {h.get('hypothesis_id', '?')} missing {field}"
            # falsification_criteria must be non-empty
            assert h["falsification_criteria"], (
                f"Hypothesis {h.get('hypothesis_id')} has empty falsification_criteria"
            )
            # confidence must be between 0 and 1
            assert 0.0 <= h["confidence"] <= 1.0, (
                f"Hypothesis {h.get('hypothesis_id')} has invalid confidence: {h['confidence']}"
            )

    def test_evaluate_heuristic_confirms_best_match(self, probe_config):
        """_evaluate_heuristic confirms the hypothesis that best matches evidence."""
        from probe.hypothesis import HypothesisEngine

        hypotheses = [
            {
                "hypothesis_id": "H1",
                "statement": "A type mismatch when comparing int to str",
                "confidence": 0.85,
                "verification_plan": [],
                "falsification_criteria": "If value is not int at line 4, refuted",
            },
            {
                "hypothesis_id": "H2",
                "statement": "A network timeout causing slow response",
                "confidence": 0.3,
                "verification_plan": [],
                "falsification_criteria": "If no network calls are made, refuted",
            },
        ]

        runtime_evidence = {
            "test_output": "TypeError: cannot compare int to str",
            "variables": {"value": "100"},
        }

        result = HypothesisEngine._evaluate_heuristic(hypotheses, runtime_evidence)

        assert "verdicts" in result
        assert result["verdicts"][
            "H1"
        ] == "confirmed", "Type-mismatch hypothesis should be confirmed"
        assert result["verdicts"][
            "H2"
        ] == "refuted", "Network timeout hypothesis should be refuted"

        # Evidence list should contain entries for both hypotheses
        evidence = result.get("evidence", [])
        assert len(evidence) >= 2

    def test_evaluate_heuristic_no_confirmation_without_evidence(self, probe_config):
        """When no hypothesis matches evidence, all are inconclusive."""
        from probe.hypothesis import HypothesisEngine

        hypotheses = [
            {
                "hypothesis_id": "H1",
                "statement": "A database deadlock issue",
                "confidence": 0.2,
                "verification_plan": [],
                "falsification_criteria": "If no database queries, refuted",
            },
        ]

        runtime_evidence = {
            "test_output": "AssertionError: expected 42 but got 0",
            "variables": {},
        }

        result = HypothesisEngine._evaluate_heuristic(hypotheses, runtime_evidence)
        assert result["verdicts"]["H1"] == "inconclusive"

    def test_evaluate_heuristic_at_most_one_confirmed(self, probe_config):
        """At most ONE hypothesis is ever confirmed."""
        from probe.hypothesis import HypothesisEngine

        hypotheses = [
            {
                "hypothesis_id": "H1",
                "statement": "Type mismatch: comparing int to str",
                "confidence": 0.9,
                "verification_plan": [],
                "falsification_criteria": "If value is int, refuted",
            },
            {
                "hypothesis_id": "H2",
                "statement": "Type error: str concatenation instead of addition",
                "confidence": 0.85,
                "verification_plan": [],
                "falsification_criteria": "If types match, refuted",
            },
        ]

        runtime_evidence = {
            "test_output": "TypeError: unsupported operand type(s)",
            "variables": {"result": "50100"},
        }

        result = HypothesisEngine._evaluate_heuristic(hypotheses, runtime_evidence)
        verdicts = result["verdicts"]
        confirmed_count = sum(1 for v in verdicts.values() if v == "confirmed")
        assert confirmed_count <= 1, (
            f"At most one hypothesis should be confirmed, got {confirmed_count}"
        )

    def test_evaluate_all_emits_trace_event(self, probe_config, mock_tracer):
        """evaluate_all emits an analyze trace event."""
        from probe.hypothesis import HypothesisEngine

        engine = HypothesisEngine(config=probe_config, tracer=mock_tracer)

        hypotheses = [
            {
                "hypothesis_id": "H1",
                "statement": "Type mismatch bug",
                "confidence": 0.8,
                "verification_plan": [],
                "falsification_criteria": "If value is not int, refuted",
            },
        ]

        runtime_evidence = {
            "test_output": "TypeError: comparing str to int",
            "variables": {"value": "100"},
        }

        result = engine.evaluate_all(hypotheses, runtime_evidence)

        assert "verdicts" in result
        assert "evidence" in result

        # Check that analyze event was emitted
        analyze_events = [
            e for e in mock_tracer.events if e.step_type == "analyze"
        ]
        assert len(analyze_events) >= 1

    def test_generate_hypotheses_convenience_function(self, probe_config, mock_tracer):
        """The generate_hypotheses convenience function works."""
        from probe.hypothesis import generate_hypotheses

        hypotheses = generate_hypotheses(
            bug_description="TypeError in calculator",
            tracer=mock_tracer,
            config=probe_config,
        )

        assert isinstance(hypotheses, list)
        assert len(hypotheses) > 0
        for h in hypotheses:
            assert "hypothesis_id" in h
            assert "falsification_criteria" in h
            assert h["falsification_criteria"]  # Must not be empty
