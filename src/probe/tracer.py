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
      1. Timeline View — horizontal scrollable colored blocks, expandable
      2. Hypothesis Decision Tree — root -> hypotheses -> evidence -> verdicts
      3. Evidence Gallery — table of runtime evidence linked to hypotheses
      4. Patch Review — syntax-highlighted diff (if fix was generated)
    """

    def __init__(self, events: list[TraceEvent], session_id: str) -> None:
        self._events = events
        self._session_id = session_id

    def build(self) -> str:
        events_json = json.dumps([e.to_dict() for e in self._events], ensure_ascii=False)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Probe Debug Report — {self._session_id[:8]}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; padding: 24px; }}
  h1 {{ color: #58a6ff; margin-bottom: 8px; }}
  h2 {{ color: #8b949e; margin: 24px 0 12px; border-bottom: 1px solid #21262d; padding-bottom: 4px; }}
  .subtitle {{ color: #8b949e; font-size: 14px; margin-bottom: 24px; }}
  /* Timeline */
  .timeline {{ display: flex; gap: 4px; overflow-x: auto; padding: 12px 0; flex-wrap: wrap; }}
  .tl-block {{ padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; white-space: nowrap; flex-shrink: 0; transition: transform .1s; }}
  .tl-block:hover {{ transform: scale(1.03); }}
  .tl-block.active {{ box-shadow: 0 0 0 2px #fff; }}
  .tl-hypothesize {{ background: #1f6feb; color: #fff; }}
  .tl-instrument {{ background: #1a7f37; color: #fff; }}
  .tl-execute {{ background: #9a6700; color: #fff; }}
  .tl-analyze {{ background: #8250df; color: #fff; }}
  .tl-iterate {{ background: #cf222e; color: #fff; }}
  .tl-fix {{ background: #bf4b8a; color: #fff; }}
  .tl-observe {{ background: #6e7681; color: #fff; }}
  /* Detail panel */
  .detail {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin: 12px 0; display: none; }}
  .detail.open {{ display: block; }}
  .detail pre {{ background: #0d1117; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 12px; margin-top: 8px; }}
  /* Decision tree */
  .tree {{ margin: 12px 0; }}
  .tree-node {{ margin: 4px 0 4px 24px; padding: 6px 10px; border-left: 3px solid #30363d; }}
  .tree-root {{ border-left-color: #58a6ff; }}
  .tree-confirmed {{ border-left-color: #3fb950; }}
  .tree-refuted {{ border-left-color: #f85149; text-decoration: line-through; opacity: 0.7; }}
  .tree-inconclusive {{ border-left-color: #d29922; }}
  .tree-label {{ font-size: 11px; color: #8b949e; }}
  /* Evidence table */
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }}
  th {{ color: #8b949e; font-weight: 600; }}
  .verdict-confirmed {{ color: #3fb950; font-weight: bold; }}
  .verdict-refuted {{ color: #f85149; font-weight: bold; }}
  .verdict-inconclusive {{ color: #d29922; }}
  /* Diff */
  .diff-add {{ color: #3fb950; }}
  .diff-remove {{ color: #f85149; }}
  .diff-hunk {{ color: #58a6ff; }}
  /* Section visibility */
  .section-hidden {{ display: none; }}
</style>
</head>
<body>
<h1>Probe Debug Report</h1>
<div class="subtitle">Session: {self._session_id[:16]} &middot; {len(self._events)} events</div>

<h2>Timeline</h2>
<div class="timeline" id="timeline"></div>
<div class="detail" id="event-detail"></div>

<h2>Hypothesis Decision Tree</h2>
<div class="tree" id="decision-tree"></div>

<h2>Evidence Gallery</h2>
<div id="evidence-gallery"></div>

<h2>Patch Review</h2>
<div id="patch-review"></div>

<script>
const EVENTS = {events_json};

function colorForType(t) {{
  const map = {{
    hypothesize: 'tl-hypothesize', instrument: 'tl-instrument',
    execute: 'tl-execute', analyze: 'tl-analyze',
    iterate: 'tl-iterate', fix: 'tl-fix', observe: 'tl-observe'
  }};
  return map[t] || 'tl-observe';
}}

// ── Timeline ──
const tl = document.getElementById('timeline');
const detail = document.getElementById('event-detail');
EVENTS.forEach((ev, i) => {{
  const b = document.createElement('span');
  b.className = 'tl-block ' + colorForType(ev.step_type);
  const ts = ev.timestamp ? ev.timestamp.slice(11, 19) : '';
  b.textContent = ts + ' ' + ev.step_type;
  b.onclick = () => {{
    detail.className = 'detail open';
    detail.innerHTML = '<h3>' + ev.step_type + ' <span style="font-weight:normal;color:#8b949e">' + ev.event_id + '</span></h3><pre>' + JSON.stringify(ev.data, null, 2).replace(/</g,'&lt;') + '</pre>';
    document.querySelectorAll('.tl-block.active').forEach(el => el.classList.remove('active'));
    b.classList.add('active');
  }};
  tl.appendChild(b);
}});

// ── Decision Tree ──
const tree = document.getElementById('decision-tree');
let rootAdded = false;
EVENTS.forEach(ev => {{
  if (ev.step_type === 'observe' && !rootAdded) {{
    const d = document.createElement('div');
    d.className = 'tree-node tree-root';
    d.innerHTML = '<span class="tree-label">ROOT</span> ' + (ev.data.bug_description || 'Bug').slice(0, 120);
    tree.appendChild(d);
    rootAdded = true;
  }}
  if (ev.step_type === 'hypothesize' && ev.data.hypotheses) {{
    ev.data.hypotheses.forEach(h => {{
      const d = document.createElement('div');
      d.className = 'tree-node';
      d.innerHTML = '<span class="tree-label">' + (h.hypothesis_id || '?') + ' (' + Math.round((h.confidence||0)*100) + '%)</span> ' + (h.statement || '').slice(0, 100);
      tree.appendChild(d);
    }});
  }}
  if (ev.step_type === 'analyze' && ev.data.evidence) {{
    ev.data.evidence.forEach(e => {{
      const cls = 'tree-node tree-' + (e.verdict || 'inconclusive');
      const d = document.createElement('div');
      d.className = cls;
      d.innerHTML = '<span class="tree-label">' + (e.hypothesis_id || '?') + ' &rarr; ' + (e.verdict || '?') + '</span> ' + (e.reasoning || e.detail || '').slice(0, 120);
      tree.appendChild(d);
    }});
  }}
  if (ev.step_type === 'fix') {{
    const d = document.createElement('div');
    d.className = 'tree-node tree-confirmed';
    d.innerHTML = '<span class="tree-label">FIX</span> ' + (ev.data.patch_description || 'Patch generated').slice(0, 120);
    tree.appendChild(d);
  }}
}});

// ── Evidence Gallery ──
const gallery = document.getElementById('evidence-gallery');
let evidenceRows = [];
EVENTS.forEach(ev => {{
  if (ev.step_type === 'analyze' && ev.data.evidence) {{
    ev.data.evidence.forEach(e => evidenceRows.push(e));
  }}
  if (ev.step_type === 'execute' && ev.data.variables) {{
    const vars = ev.data.variables;
    Object.entries(vars).forEach(([k,v]) => {{
      evidenceRows.push({{ hypothesis_id: 'runtime', verdict: 'evidence', reasoning: k + ' = ' + JSON.stringify(v) }});
    }});
  }}
}});
if (evidenceRows.length > 0) {{
  let html = '<table><tr><th>Hypothesis</th><th>Verdict</th><th>Reasoning / Evidence</th></tr>';
  evidenceRows.forEach(r => {{
    const vcls = 'verdict-' + (r.verdict || 'inconclusive');
    html += '<tr><td>' + (r.hypothesis_id || '-') + '</td><td class="' + vcls + '">' + (r.verdict || '-') + '</td><td>' + (r.reasoning || r.detail || '').slice(0, 200) + '</td></tr>';
  }});
  html += '</table>';
  gallery.innerHTML = html;
}} else {{
  gallery.innerHTML = '<p style="color:#8b949e">No evidence collected yet.</p>';
}}

// ── Patch Review ──
const patchDiv = document.getElementById('patch-review');
let foundPatch = false;
EVENTS.forEach(ev => {{
  if (ev.step_type === 'fix' && ev.data.patch) {{
    foundPatch = true;
    let diffHtml = '<pre>';
    (ev.data.patch || '').split('\\n').forEach(line => {{
      let cls = '';
      if (line.startsWith('+')) cls = 'diff-add';
      else if (line.startsWith('-')) cls = 'diff-remove';
      else if (line.startsWith('@@')) cls = 'diff-hunk';
      diffHtml += '<span class="' + cls + '">' + line.replace(/</g,'&lt;') + '</span>\\n';
    }});
    diffHtml += '</pre>';
    const verdict = ev.data.sandbox_result ? ('<p>Sandbox test: <b>' + ev.data.sandbox_result + '</b></p>') : '';
    patchDiv.innerHTML = verdict + diffHtml;
  }}
}});
if (!foundPatch) patchDiv.innerHTML = '<p style="color:#8b949e">No fix generated in this session.</p>';
</script>
</body>
</html>"""
