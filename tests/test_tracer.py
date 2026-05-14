"""Tests for the trace engine — TraceEvent, Tracer, SessionManager, HTMLReportBuilder."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from probe.tracer import (
    TraceEvent,
    SessionManager,
    Tracer,
    HTMLReportBuilder,
)


# ── TraceEvent ─────────────────────────────────────────────────────────────────


class TestTraceEvent:
    """Tests for the TraceEvent dataclass."""

    def test_event_has_required_fields(self):
        """A TraceEvent has timestamp, step_type, event_id, session_id, data."""
        event = TraceEvent(
            step_type="hypothesize",
            data={"hypotheses": []},
            session_id="test-123",
        )

        d = event.to_dict()
        assert "timestamp" in d
        assert d["step_type"] == "hypothesize"
        assert "event_id" in d
        assert d["session_id"] == "test-123"
        assert "data" in d

    def test_event_id_is_unique(self):
        """Each event gets a unique event_id."""
        e1 = TraceEvent(step_type="test", data={})
        e2 = TraceEvent(step_type="test", data={})
        assert e1.event_id != e2.event_id

    def test_to_jsonl_produces_valid_json_line(self):
        """to_jsonl produces a single valid JSON line."""
        event = TraceEvent(
            step_type="execute",
            data={"result": "pass"},
            session_id="s1",
        )

        line = event.to_jsonl()
        parsed = json.loads(line)
        assert parsed["step_type"] == "execute"
        assert parsed["data"]["result"] == "pass"

    def test_timestamp_is_iso8601(self):
        """The timestamp is in ISO 8601 format."""
        event = TraceEvent(step_type="test", data={})
        ts = event.timestamp
        assert "T" in ts
        assert "+" in ts or "Z" in ts


# ── SessionManager ─────────────────────────────────────────────────────────────


class TestSessionManager:
    """Tests for the SessionManager — session directory management."""

    def test_creates_session_directory(self, temp_dir):
        """SessionManager creates a session directory under the output dir."""
        mgr = SessionManager(output_dir=temp_dir)
        assert mgr.session_dir.exists()
        assert mgr.session_dir.is_dir()

    def test_session_id_is_unique(self, temp_dir):
        """Each SessionManager gets a unique session ID."""
        mgr1 = SessionManager(output_dir=temp_dir)
        mgr2 = SessionManager(output_dir=temp_dir)
        assert mgr1.session_id != mgr2.session_id

    def test_trace_path_and_html_path(self, temp_dir):
        """get_trace_path and get_html_path return paths within session dir."""
        mgr = SessionManager(output_dir=temp_dir)

        trace = mgr.get_trace_path()
        html = mgr.get_html_path()

        assert trace.parent == mgr.session_dir
        assert html.parent == mgr.session_dir
        assert trace.name == "trace.jsonl"
        assert html.name == "report.html"


# ── Tracer ─────────────────────────────────────────────────────────────────────


class TestTracer:
    """Tests for the Tracer — append-only JSONL trace log writer."""

    def test_emit_writes_to_jsonl(self, temp_dir):
        """emit() appends a JSON line to the trace file."""
        with Tracer(output_dir=temp_dir, console_mode=False) as tracer:
            tracer.emit("observe", {"bug": "TypeError"})
            tracer.emit("hypothesize", {"hypotheses": []})

            # Verify the file has 2 lines
            trace_path = str(tracer.trace_path)
            assert os.path.exists(trace_path)

            lines = Path(trace_path).read_text().strip().split("\n")
            assert len(lines) == 2

            e0 = json.loads(lines[0])
            assert e0["step_type"] == "observe"
            assert e0["data"]["bug"] == "TypeError"

    def test_emit_returns_event_id(self, temp_dir):
        """emit returns the event ID string."""
        with Tracer(output_dir=temp_dir, console_mode=False) as tracer:
            eid = tracer.emit("test", {})
            assert isinstance(eid, str)
            assert len(eid) > 0

    def test_context_manager_closes_cleanly(self, temp_dir):
        """Tracer works correctly as a context manager."""
        with Tracer(output_dir=temp_dir, console_mode=False) as tracer:
            tracer.emit("test", {"x": 1})
        # Should not raise on exit

    def test_events_property_returns_all_events(self, temp_dir):
        """The events property returns all emitted events."""
        with Tracer(output_dir=temp_dir, console_mode=False) as tracer:
            tracer.emit("step1", {})
            tracer.emit("step2", {})

            events = tracer.events
            assert len(events) == 2
            assert events[0].step_type == "step1"
            assert events[1].step_type == "step2"

    def test_build_html_report_produces_file(self, temp_dir):
        """build_html_report generates an HTML file with timeline and tree."""
        with Tracer(output_dir=temp_dir, console_mode=False) as tracer:
            tracer.emit("observe", {"bug_description": "TypeError in calculator"})
            tracer.emit("hypothesize", {
                "hypotheses": [
                    {
                        "hypothesis_id": "H1",
                        "statement": "Type mismatch",
                        "confidence": 0.8,
                    }
                ]
            })
            tracer.emit("analyze", {
                "evidence": [
                    {"hypothesis_id": "H1", "verdict": "confirmed", "reasoning": "test"}
                ]
            })
            tracer.emit("fix", {
                "verdict": "confirmed",
                "root_cause": "Type mismatch",
            })

            html_path = tracer.build_html_report()
            assert os.path.exists(html_path)

            html_content = Path(html_path).read_text()
            assert "Probe Debug Report" in html_content
            assert "Timeline" in html_content
            assert "Decision Tree" in html_content
            assert "Evidence Gallery" in html_content
            assert "Patch Review" in html_content

    def test_session_id_available(self, temp_dir):
        """The session_id is accessible from the tracer."""
        with Tracer(output_dir=temp_dir, console_mode=False) as tracer:
            assert tracer.session_id
            assert len(tracer.session_id) > 0


# ── HTMLReportBuilder ──────────────────────────────────────────────────────────


class TestHTMLReportBuilder:
    """Tests for the HTMLReportBuilder — self-contained HTML report generation."""

    def test_build_contains_required_sections(self):
        """The generated HTML contains all 4 visualization components."""
        events = [
            TraceEvent("observe", {"bug_description": "test bug"}, session_id="s1"),
            TraceEvent("hypothesize", {
                "hypotheses": [{"hypothesis_id": "H1", "statement": "test", "confidence": 0.8}]
            }, session_id="s1"),
            TraceEvent("analyze", {
                "evidence": [{"hypothesis_id": "H1", "verdict": "confirmed", "reasoning": "test"}]
            }, session_id="s1"),
            TraceEvent("fix", {
                "patch": "--- a/test.py\n+++ b/test.py\n@@ -1,1 +1,1 @@\n-old\n+new",
                "verdict": "confirmed",
            }, session_id="s1"),
        ]

        builder = HTMLReportBuilder(events, "s1")
        html = builder.build()

        assert "<!DOCTYPE html>" in html
        assert "Probe Debug Report" in html
        assert "Timeline" in html
        assert "Decision Tree" in html
        assert "Evidence Gallery" in html
        assert "Patch Review" in html

    def test_build_is_self_contained(self):
        """The HTML report has no external CSS/JS dependencies."""
        events = [TraceEvent("observe", {"bug_description": "test"}, session_id="s1")]
        builder = HTMLReportBuilder(events, "s1")
        html = builder.build()

        # No external resource references
        assert "http://" not in html
        assert "https://" not in html
        assert "<script>" in html  # Inline JavaScript only
        # No CDN or external stylesheets
        assert "//cdn" not in html.lower()

    def test_build_handles_empty_events(self):
        """Empty event list produces valid HTML."""
        builder = HTMLReportBuilder([], "empty")
        html = builder.build()
        assert "<!DOCTYPE html>" in html
        assert "Probe Debug Report" in html
