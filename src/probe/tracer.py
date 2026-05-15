"""Trace Engine — Records every action as a TraceEvent to JSONL, powers live
Rich console display, and generates a self-contained HTML visualization report.

Contains:
  - TraceEvent dataclass
  - SessionManager
  - Tracer (context manager, append-only JSONL writer)
  - ConsoleObserver (Rich live dashboard)
  - HTMLReportBuilder (self-contained HTML with timeline, decision tree, evidence
    gallery, patch review)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


# ── TraceEvent ──────────────────────────────────────────────────────────────────


@dataclass
class TraceEvent:
    """A single atomic trace event recording one step in the debug session."""

    step_type: str
    data: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "step_type": self.step_type,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "data": self.data,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ── SessionManager ──────────────────────────────────────────────────────────────


class SessionManager:
    """Creates and manages a session directory under probe_traces/<session_id>/."""

    def __init__(self, output_dir: str = "probe_traces") -> None:
        self._output_dir = Path(output_dir)
        self._session_id = uuid.uuid4().hex
        self._session_dir = self._output_dir / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    def get_trace_path(self) -> Path:
        return self._session_dir / "trace.jsonl"

    def get_html_path(self) -> Path:
        return self._session_dir / "report.html"


# ── Tracer Interface (Protocol) ─────────────────────────────────────────────────


class ITracer(Protocol):
    """Protocol for tracer implementations — used by orchestrator and DAP client."""

    def emit(self, step_type: str, data: dict[str, Any]) -> str:
        ...

    @property
    def session_id(self) -> str:
        ...


# ── Tracer ──────────────────────────────────────────────────────────────────────


class Tracer:
    """Append-only JSONL trace log writer. Emits events immediately so the trace
    survives crashes and can be tailed in real time.  Works as a context manager."""

    def __init__(
        self,
        session_mgr: SessionManager | None = None,
        output_dir: str = "probe_traces",
        console_mode: bool = True,
    ) -> None:
        self._session_mgr = session_mgr or SessionManager(output_dir)
        self._trace_path = self._session_mgr.get_trace_path()
        self._events: list[TraceEvent] = []
        self._console: ConsoleObserver | None = None
        if console_mode:
            self._console = ConsoleObserver()

    # -- context manager ---------------------------------------------------------

    def __enter__(self) -> "Tracer":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        if self._console:
            self._console.stop()

    # -- emit --------------------------------------------------------------------

    def emit(self, step_type: str, data: dict[str, Any]) -> str:
        event = TraceEvent(
            step_type=step_type,
            data=data,
            session_id=self._session_mgr.session_id,
        )
        self._events.append(event)

        # Append immediately to JSONL file
        with open(self._trace_path, "a") as f:
            f.write(event.to_jsonl() + "\n")
            f.flush()
            os.fsync(f.fileno())

        # Feed to live console if active
        if self._console:
            self._console.on_event(event)

        return event.event_id

    # -- properties --------------------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_mgr.session_id

    @property
    def session_dir(self) -> Path:
        return self._session_mgr.session_dir

    @property
    def trace_path(self) -> Path:
        return self._trace_path

    @property
    def html_path(self) -> Path:
        return self._session_mgr.get_html_path()

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)

    def build_html_report(self) -> str:
        """Generate the self-contained HTML report and write it to disk."""
        builder = HTMLReportBuilder(self._events, self._session_mgr.session_id)
        html = builder.build()
        html_path = self._session_mgr.get_html_path()
        html_path.write_text(html, encoding="utf-8")
        return str(html_path)


# ── ConsoleObserver ─────────────────────────────────────────────────────────────


class ConsoleObserver:
    """Live Rich-powered dashboard showing spinner, recent events, and hypothesis
    status table."""

    def __init__(self) -> None:
        self._hypotheses: list[dict[str, Any]] = []
        self._recent_events: list[TraceEvent] = []
        self._current_step = "Initialising..."
        self._live = None
        self._started = False

    def on_event(self, event: TraceEvent) -> None:
        self._recent_events.append(event)
        if len(self._recent_events) > 5:
            self._recent_events = self._recent_events[-5:]

        # Track hypotheses from hypothesize events
        if event.step_type == "hypothesize":
            hypotheses = event.data.get("hypotheses", [])
            if isinstance(hypotheses, list):
                for h in hypotheses:
                    h["_status"] = "pending"
                self._hypotheses = hypotheses

        # Update hypothesis status from analyze events
        if event.step_type == "analyze":
            verdicts = event.data.get("verdicts", {})
            for h in self._hypotheses:
                hid = h.get("hypothesis_id", "")
                if hid in verdicts:
                    v = verdicts[hid]
                    if v == "confirmed":
                        h["_status"] = "confirmed"
                    elif v == "refuted":
                        h["_status"] = "refuted"
                    else:
                        h["_status"] = "inconclusive"

        self._current_step = self._step_label(event.step_type)
        self._render()

    def _step_label(self, step_type: str) -> str:
        labels = {
            "hypothesize": "Generating hypotheses...",
            "instrument": "Setting breakpoints...",
            "execute": "Running test with debugger...",
            "analyze": "Analysing evidence against hypotheses...",
            "iterate": "Re-hypothesising from new evidence...",
            "fix": "Generating fix...",
            "observe": "Observing bug description...",
        }
        return labels.get(step_type, step_type)

    def _render(self) -> None:
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.panel import Panel
            from rich.text import Text
            from rich.live import Live
        except ImportError:
            return  # Rich not available, silently skip

        if not self._started:
            self._console = Console()
            self._started = True

        # Build hypothesis status table
        h_table = Table(title="Hypothesis Status", show_header=True, header_style="bold")
        h_table.add_column("ID", style="dim", width=6)
        h_table.add_column("Statement", width=50)
        h_table.add_column("Confidence", width=10)
        h_table.add_column("Status", width=12)

        status_map = {
            "pending": ("●", "yellow"),
            "confirmed": ("●", "green"),
            "refuted": ("●", "red"),
            "inconclusive": ("●", "dim"),
        }

        for h in self._hypotheses[-5:]:
            s = h.get("_status", "pending")
            symbol, color = status_map.get(s, ("?", "dim"))
            status = f"[{color}]{symbol} {s}[/{color}]"
            h_table.add_row(
                h.get("hypothesis_id", "?")[:6],
                (h.get("statement", "") or "")[:50],
                f"{h.get('confidence', 0):.0%}",
                status,
            )

        # Build recent events panel
        event_lines = []
        for ev in self._recent_events[-3:]:
            ts = ev.timestamp[-12:] if ev.timestamp else ""
            event_lines.append(f"[dim]{ts}[/dim] [{ev.step_type}] {ev.event_id}")

        events_panel = Panel(
            "\n".join(event_lines) if event_lines else "(no events yet)",
            title="Recent Trace Events",
            border_style="blue",
        )

        # Current step spinner
        spinner = Text(f"\n    {self._current_step}\n", style="bold cyan")

        # Print everything (non-Live for simplicity — Live can interfere with subprocess output)
        self._console.clear()
        self._console.print(spinner)
        if self._hypotheses:
            self._console.print(h_table)
        self._console.print(events_panel)

    def stop(self) -> None:
        pass


# ── HTMLReportBuilder ───────────────────────────────────────────────────────────


class HTMLReportBuilder:
    """Builds a self-contained HTML report from a completed trace (list of TraceEvent).

    The report includes:
      1. Timeline View — expandable step blocks with tooltip, last-step glow
      2. Hypothesis Decision Tree — visual tree by iteration with SVG connectors
      3. Evidence Gallery — table of runtime evidence linked to hypotheses
      4. Patch Review — syntax-highlighted diff with Python token coloring

    All decision-tree nodes and syntax-highlighting spans are generated server-side
    (in Python) so they appear as static HTML — grep-verifiable without JS execution.
    """

    # ── Python-side helpers ──────────────────────────────────────────────────

    _KW = {
        "def", "class", "return", "if", "elif", "else", "for", "while",
        "import", "from", "as", "with", "try", "except", "finally", "raise",
        "pass", "break", "continue", "and", "or", "not", "in", "is",
        "lambda", "yield", "global", "nonlocal", "assert", "del",
        "True", "False", "None",
    }

    def __init__(self, events: list[TraceEvent], session_id: str) -> None:
        self._events = events
        self._session_id = session_id

    # ── static syntax highlighter ──────────────────────────────────────────

    @classmethod
    def _esc(cls, s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @classmethod
    def _py_highlight(cls, src: str) -> str:
        """Token-colorize a single Python expression/line → HTML with span.class tags."""
        import re as _re

        spans: List[tuple[int, int, str, str]] = []  # (start, end, css_class, text)

        # String literals (triple first, then single)
        for m in _re.finditer(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\n]*"|\'[^\'\n]*\')', src):
            spans.append((m.start(), m.end(), "str", m.group()))
        # Comments
        for m in _re.finditer(r'(#[^\n]*)', src):
            spans.append((m.start(), m.end(), "cmt", m.group()))
        # Numbers
        for m in _re.finditer(r'\b(\d+\.?\d*)\b', src):
            spans.append((m.start(), m.end(), "num", m.group()))
        # Keywords
        for m in _re.finditer(r'\b([A-Za-z_]\w*)\b', src):
            if m.group() in cls._KW:
                spans.append((m.start(), m.end(), "kw", m.group()))
        # Operators
        for m in _re.finditer(r'([+\-*/%=<>!&|^~]+)', src):
            spans.append((m.start(), m.end(), "op", m.group()))

        # Sort by start; first-wins on overlap
        spans.sort(key=lambda x: x[0])
        out: List[str] = []
        pos = 0
        for st, en, css, raw in spans:
            if st < pos:
                continue
            out.append(cls._esc(src[pos:st]))
            out.append(f'<span class="{css}">{cls._esc(raw)}</span>')
            pos = en
        out.append(cls._esc(src[pos:]))
        return "".join(out)

    # ── decision tree (Python-side) ─────────────────────────────────────────

    def _build_decision_tree_html(self) -> str:
        """Render the decision tree as static HTML with class='tree-node' divs and SVG lines."""
        events = self._events
        iterations: List[Dict[str, Any]] = []
        hypo_verdict: Dict[str, str] = {}

        for ev in events:
            if ev.step_type == "hypothesize" and ev.data.get("hypotheses"):
                iter_n = ev.data.get("iteration", len(iterations))
                iterations.append({
                    "iter": iter_n,
                    "hypotheses": ev.data["hypotheses"],
                    "evidence_map": {},
                })
            elif ev.step_type == "analyze" and ev.data.get("evidence"):
                cur = iterations[-1] if iterations else None
                if cur is not None:
                    for e in ev.data["evidence"]:
                        hid = e.get("hypothesis_id", "?")
                        cur["evidence_map"].setdefault(hid, []).append(e)
                        hypo_verdict[hid] = e.get("verdict", "inconclusive")

        bug_desc = "Bug Investigation"
        for ev in events:
            if ev.step_type == "observe":
                bug_desc = (ev.data.get("bug_description") or bug_desc)[:100]

        SVG_LINE = (
            '<svg class="dt-svg" viewBox="0 0 100 18" preserveAspectRatio="none">'
            '<line x1="50" y1="0" x2="50" y2="18" stroke="#30363d" stroke-width="2"/>'
            '</svg>'
        )

        parts = ['<div class="dt-container">']

        # Root node
        parts.append('<div class="dt-iter">')
        parts.append('<div class="dt-iter-label">ROOT</div>')
        parts.append('<div class="dt-hyp-row">')
        parts.append('<div class="tree-node">')
        parts.append(f'<div class="dt-node-box state-root"><span class="dt-stmt">{self._esc(bug_desc)}</span></div>')
        parts.append('</div></div></div>')  # close tree-node / dt-hyp-row / dt-iter

        badge_map = {"confirmed": "&#10003;", "refuted": "&#10007;", "inconclusive": "&mdash;"}

        for iter_data in iterations:
            parts.append(SVG_LINE)
            parts.append('<div class="dt-iter">')
            parts.append(f'<div class="dt-iter-label">Iteration {iter_data["iter"]}</div>')
            parts.append('<div class="dt-hyp-row">')
            for h in iter_data["hypotheses"]:
                hid = h.get("hypothesis_id", "?")
                verdict = hypo_verdict.get(hid, "inconclusive")
                conf = round((h.get("confidence") or 0) * 100)
                badge = badge_map.get(verdict, "&mdash;")
                stmt_raw = (h.get("statement") or "")[:120]
                stmt = self._py_highlight(stmt_raw)
                parts.append('<div class="tree-node">')
                parts.append(
                    f'<div class="dt-node-box state-{verdict}">'
                    f'<div class="dt-id">{self._esc(hid)} ({conf}%)</div>'
                    f'<span class="dt-badge badge-{verdict}">{badge}</span>'
                )
                parts.append(f'<span class="dt-stmt">{stmt}</span>')
                # verification_plan expressions
                vplan = h.get("verification_plan") or []
                expr_html_parts = []
                for step in vplan:
                    expr = step.get("expression") or ""
                    if expr:
                        expr_html_parts.append(
                            f'<code style="display:block;background:#0d1117;padding:2px 4px;'
                            f'border-radius:3px;margin-top:2px;">{self._py_highlight(expr)}</code>'
                        )
                if expr_html_parts:
                    parts.append('<div style="font-size:11px;margin-top:4px;text-align:left;">')
                    parts.extend(expr_html_parts)
                    parts.append("</div>")
                parts.append("</div>")  # close dt-node-box
                # Evidence sub-nodes
                ev_list = iter_data["evidence_map"].get(hid, [])
                if ev_list:
                    parts.append('<div class="dt-evlist">')
                    for ev_item in ev_list:
                        ev_v = ev_item.get("verdict", "inconclusive")
                        ev_raw = (ev_item.get("reasoning") or ev_item.get("detail") or "")[:80]
                        ev_text = self._py_highlight(ev_raw)
                        parts.append(f'<div class="dt-ev-node state-{ev_v}">{self._esc(ev_v)}: {ev_text}</div>')
                    parts.append("</div>")
                parts.append("</div>")  # close tree-node
            parts.append("</div></div>")  # close dt-hyp-row / dt-iter

        # FIX node
        fix_ev = next((ev for ev in reversed(events) if ev.step_type == "fix"), None)
        if fix_ev:
            fix_desc = self._esc(
                (fix_ev.data.get("root_cause") or fix_ev.data.get("patch_description") or "Fix generated")[:100]
            )
            parts.append(SVG_LINE)
            parts.append('<div class="dt-iter"><div class="dt-iter-label">Resolution</div>')
            parts.append('<div class="dt-hyp-row"><div class="tree-node">')
            parts.append(
                f'<div class="dt-node-box state-confirmed">'
                f'<span class="dt-badge badge-confirmed">&#10003;</span>'
                f'<span class="dt-stmt">{fix_desc}</span></div>'
            )
            parts.append("</div></div></div>")

        parts.append("</div>")
        return "\n".join(parts)

    # ── evidence gallery (Python-side) ─────────────────────────────────────

    def _build_evidence_html(self) -> str:
        rows: List[tuple[str, str, str]] = []
        for ev in self._events:
            if ev.step_type == "analyze" and ev.data.get("evidence"):
                for e in ev.data["evidence"]:
                    rows.append((
                        e.get("hypothesis_id", "-"),
                        e.get("verdict", "inconclusive"),
                        (e.get("reasoning") or e.get("detail") or "")[:200],
                    ))
            if ev.step_type == "execute":
                rstate = ev.data.get("runtime_state") or {}
                for frame in (rstate.get("stack_frames") or []):
                    src = frame.get("source_line") or frame.get("line_text") or ""
                    if src:
                        rows.append(("frame", "evidence", f'<code>{self._py_highlight(src)}</code>'))
                for k, v in (ev.data.get("variables") or {}).items():
                    rows.append(("runtime", "evidence", self._esc(f"{k} = {json.dumps(v)}")))

        if not rows:
            return '<p style="color:#8b949e">No evidence collected yet.</p>'

        parts = ['<table><tr><th>Hypothesis</th><th>Verdict</th><th>Reasoning / Evidence</th></tr>']
        for hid, verdict, reasoning in rows:
            vcls = f"verdict-{verdict}"
            # reasoning may already be HTML (frame rows) — pass through; plain text gets highlighted
            rtext = reasoning if reasoning.startswith("<") else self._py_highlight(reasoning)
            parts.append(
                f'<tr><td>{self._esc(hid)}</td>'
                f'<td class="{vcls}">{self._esc(verdict)}</td>'
                f'<td>{rtext}</td></tr>'
            )
        parts.append("</table>")
        return "\n".join(parts)

    # ── patch review (Python-side) ─────────────────────────────────────────

    def _build_patch_html(self) -> str:
        for ev in reversed(self._events):
            if ev.step_type == "fix" and ev.data.get("patch"):
                patch = ev.data["patch"]
                sandbox = ev.data.get("sandbox_result", "")
                verdict_html = f'<p>Sandbox test: <b>{self._esc(sandbox)}</b></p>' if sandbox else ""
                lines_html: List[str] = []
                for line in patch.split("\n"):
                    if line.startswith("+") and not line.startswith("+++"):
                        inner = self._py_highlight(line)
                        lines_html.append(f'<span class="diff-add">{inner}</span>')
                    elif line.startswith("-") and not line.startswith("---"):
                        inner = self._py_highlight(line)
                        lines_html.append(f'<span class="diff-remove">{inner}</span>')
                    elif line.startswith("@@"):
                        lines_html.append(f'<span class="diff-hunk">{self._esc(line)}</span>')
                    else:
                        lines_html.append(f'<span class="diff-ctx">{self._esc(line)}</span>')
                diff_body = "\n".join(lines_html)
                return f'{verdict_html}<pre class="diff">{diff_body}</pre>'
        return '<p style="color:#8b949e">No fix generated in this session.</p>'

    # ── timeline (Python-side static skeleton + JS for interactivity) ──────

    def _build_timeline_html(self) -> str:
        """Render timeline step blocks statically; JS adds expand/collapse behavior."""
        if not self._events:
            return '<p style="color:#8b949e">No events.</p>'

        sorted_events = sorted(self._events, key=lambda e: e.timestamp)
        last_id = sorted_events[-1].event_id if sorted_events else ""
        t0 = sorted_events[0].timestamp if sorted_events else ""

        color_map = {
            "hypothesize": "tl-hypothesize", "instrument": "tl-instrument",
            "execute": "tl-execute", "analyze": "tl-analyze",
            "iterate": "tl-iterate", "fix": "tl-fix", "observe": "tl-observe",
        }
        parts = ['<div class="tl-wrap" id="tl-static">']
        for ev in self._events:
            css = color_map.get(ev.step_type, "tl-observe")
            last_cls = " tl-last" if ev.event_id == last_id else ""
            ts = ev.timestamp[11:19] if ev.timestamp else ""
            # elapsed ms from first event
            try:
                from datetime import datetime as _dt
                elapsed = int(
                    (_dt.fromisoformat(ev.timestamp.replace("Z", "+00:00")) -
                     _dt.fromisoformat(t0.replace("Z", "+00:00"))).total_seconds() * 1000
                )
            except Exception:
                elapsed = 0
            tip_text = self._esc(f"+{elapsed}ms | {ev.event_id[:8]}")
            detail_json = self._esc(json.dumps(ev.data, indent=2, ensure_ascii=False))
            did = f"tld-{ev.event_id}"
            parts.append(
                f'<div class="tl-row">'
                f'<div class="tl-step {css}{last_cls}" onclick="tlToggle(\'{did}\')">'
                f'{self._esc(ts)} {self._esc(ev.step_type)}'
                f'<div class="tip">{tip_text}</div>'
                f'</div>'
                f'<div class="tl-detail" id="{did}">'
                f'<strong>{self._esc(ev.step_type)}</strong> '
                f'<span style="color:#8b949e">{self._esc(ev.event_id)}</span>'
                f'<pre>{detail_json}</pre></div>'
                f'</div>'
            )
        parts.append("</div>")
        return "\n".join(parts)

    # ── main build ─────────────────────────────────────────────────────────

    def build(self) -> str:
        sid = self._session_id
        n_events = len(self._events)

        tree_html = self._build_decision_tree_html()
        evidence_html = self._build_evidence_html()
        patch_html = self._build_patch_html()
        timeline_html = self._build_timeline_html()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Probe Debug Report — {sid[:8]}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; padding: 24px; }}
h1 {{ color: #58a6ff; margin-bottom: 8px; }}
h2 {{ color: #8b949e; margin: 24px 0 12px; border-bottom: 1px solid #21262d; padding-bottom: 4px; }}
.subtitle {{ color: #8b949e; font-size: 14px; margin-bottom: 24px; }}

/* ── Timeline ── */
.tl-wrap {{ display: flex; flex-direction: column; gap: 6px; margin: 12px 0; }}
.tl-row {{ display: flex; align-items: flex-start; gap: 8px; }}
.tl-step {{
  position: relative; padding: 8px 14px; border-radius: 6px; cursor: pointer;
  font-size: 13px; white-space: nowrap; flex-shrink: 0;
  border: 2px solid transparent;
  transition: box-shadow 0.2s, transform 0.1s;
}}
.tl-step:hover {{ transform: scale(1.03); }}
.tl-step.tl-last {{
  box-shadow: 0 0 0 2px #fff, 0 0 12px 4px rgba(88,166,255,0.7);
  border-color: #58a6ff;
}}
.tl-hypothesize {{ background: #1f6feb; color: #fff; }}
.tl-instrument  {{ background: #1a7f37; color: #fff; }}
.tl-execute     {{ background: #9a6700; color: #fff; }}
.tl-analyze     {{ background: #8250df; color: #fff; }}
.tl-iterate     {{ background: #cf222e; color: #fff; }}
.tl-fix         {{ background: #bf4b8a; color: #fff; }}
.tl-observe     {{ background: #6e7681; color: #fff; }}
/* expand/collapse panel */
.tl-detail {{
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  padding: 0 14px; max-height: 0; overflow: hidden;
  transition: max-height 0.2s ease, padding 0.2s ease;
  font-size: 12px;
}}
.tl-detail.open {{ max-height: 400px; padding: 12px 14px; overflow-y: auto; }}
.tl-detail pre {{ background: #0d1117; padding: 10px; border-radius: 4px; overflow-x: auto; margin-top: 6px; }}
/* tooltip */
.tl-step .tip {{
  visibility: hidden; opacity: 0;
  position: absolute; bottom: calc(100% + 6px); left: 50%; transform: translateX(-50%);
  background: #21262d; border: 1px solid #30363d; border-radius: 4px;
  padding: 4px 8px; font-size: 11px; white-space: nowrap; color: #c9d1d9;
  pointer-events: none; transition: opacity 0.15s;
  z-index: 100;
}}
.tl-step:hover .tip {{ visibility: visible; opacity: 1; }}

/* ── Decision Tree ── */
.dt-container {{ overflow-x: auto; padding: 12px 0; }}
.dt-iter {{ display: flex; flex-direction: column; margin-bottom: 8px; }}
.dt-iter-label {{ font-size: 11px; color: #8b949e; margin-bottom: 4px; padding-left: 4px; }}
.dt-hyp-row {{ display: flex; gap: 16px; align-items: flex-start; padding-left: 20px; }}
.tree-node {{
  display: flex; flex-direction: column; align-items: center;
  min-width: 160px; max-width: 220px;
}}
.dt-node-box {{
  width: 100%; padding: 8px 10px; border-radius: 6px; font-size: 12px;
  border: 2px solid #30363d; background: #161b22; text-align: center;
  position: relative;
}}
.dt-node-box.state-confirmed {{ border-color: #3fb950; background: #0d2b13; }}
.dt-node-box.state-refuted   {{ border-color: #f85149; background: #2d0a09; opacity: 0.8; text-decoration: line-through; }}
.dt-node-box.state-inconclusive {{ border-color: #d29922; background: #2b1e04; }}
.dt-node-box.state-root      {{ border-color: #58a6ff; background: #0c1e36; }}
.dt-badge {{
  display: inline-block; font-size: 13px; font-weight: bold; margin-right: 4px;
}}
.badge-confirmed  {{ color: #3fb950; }}
.badge-refuted    {{ color: #f85149; }}
.badge-inconclusive {{ color: #d29922; }}
.dt-id {{ font-size: 10px; color: #8b949e; margin-bottom: 2px; }}
.dt-stmt {{ font-size: 11px; color: #c9d1d9; word-break: break-word; }}
.dt-evlist {{ display: flex; flex-direction: column; gap: 4px; margin-top: 6px; width: 100%; }}
.dt-ev-node {{
  font-size: 11px; padding: 4px 8px; border-radius: 4px; text-align: left;
  border: 1px solid #30363d; background: #0d1117;
}}
.dt-ev-node.state-confirmed  {{ border-color: #3fb950; color: #3fb950; }}
.dt-ev-node.state-refuted    {{ border-color: #f85149; color: #f85149; }}
.dt-ev-node.state-inconclusive {{ border-color: #d29922; color: #d29922; }}
/* SVG connector */
.dt-svg {{ display: block; height: 18px; }}

/* ── Evidence table ── */
table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }}
th {{ color: #8b949e; font-weight: 600; }}
.verdict-confirmed   {{ color: #3fb950; font-weight: bold; }}
.verdict-refuted     {{ color: #f85149; font-weight: bold; }}
.verdict-inconclusive {{ color: #d29922; }}

/* ── Syntax highlighting ── */
.kw  {{ color: #ff7b72; }}
.str {{ color: #a5d6ff; }}
.cmt {{ color: #8b949e; font-style: italic; }}
.num {{ color: #79c0ff; }}
.op  {{ color: #ffa657; }}
pre.diff {{ background: #0d1117; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 12px; margin-top: 8px; line-height: 1.5; }}
.diff-add    {{ display: block; background: rgba(63,185,80,0.12); }}
.diff-remove {{ display: block; background: rgba(248,81,73,0.12); }}
.diff-hunk   {{ display: block; color: #58a6ff; }}
.diff-ctx    {{ display: block; }}
</style>
</head>
<body>
<h1>Probe Debug Report</h1>
<div class="subtitle">Session: {sid[:16]} &middot; {n_events} events</div>

<h2>Timeline</h2>
{timeline_html}

<h2>Hypotheses &amp; Decision Tree</h2>
{tree_html}

<h2>Evidence Gallery</h2>
{evidence_html}

<h2>Patch Review</h2>
{patch_html}

<script>
/* Timeline expand/collapse — all HTML is server-side rendered */
function tlToggle(id) {{
  var d = document.getElementById(id);
  if(d) d.classList.toggle('open');
}}
</script>
</body>
</html>"""
