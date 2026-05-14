"""Report Builder — generates structured JSON and Markdown investigation summaries.

Distinct from the interactive HTML trace visualization (in tracer.py), this
produces static, shareable deliverables that summarise the debugging session.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from probe.tracer import TraceEvent


class ReportBuilder:
    """Generates a JSON + Markdown investigation summary from trace events.

    The JSON report is a machine-readable summary. The Markdown report is
    human-readable and suitable for pasting into issues or PRs.
    """

    def __init__(self, events: list[TraceEvent], session_id: str = "") -> None:
        self._events = events
        self._session_id = session_id

    def build_json(self) -> dict[str, Any]:
        """Build a structured JSON investigation report."""
        hypotheses = []
        evidence_items = []
        verdict = "inconclusive"
        root_cause = ""
        iterations = 0

        for ev in self._events:
            if ev.step_type == "hypothesize":
                for h in ev.data.get("hypotheses", []):
                    hypotheses.append({
                        "hypothesis_id": h.get("hypothesis_id", "?"),
                        "statement": h.get("statement", ""),
                        "confidence": h.get("confidence", 0),
                        "falsification_criteria": h.get("falsification_criteria", ""),
                    })
            elif ev.step_type == "analyze":
                for e in ev.data.get("evidence", []):
                    evidence_items.append({
                        "hypothesis_id": e.get("hypothesis_id", "?"),
                        "verdict": e.get("verdict", "inconclusive"),
                        "reasoning": e.get("reasoning", "")[:300],
                    })
            elif ev.step_type == "fix":
                verdict = ev.data.get("verdict", "inconclusive")
                root_cause = ev.data.get("root_cause", ev.data.get("best_hypothesis", ""))
                iterations = ev.data.get("iterations", 0)

        return {
            "session_id": self._session_id,
            "verdict": verdict,
            "root_cause": root_cause,
            "iterations": iterations,
            "total_events": len(self._events),
            "hypotheses": hypotheses,
            "evidence": evidence_items,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def build_markdown(self) -> str:
        """Build a human-readable Markdown investigation report."""
        summary = self.build_json()

        lines: list[str] = []
        lines.append("# Probe Investigation Report")
        lines.append("")
        lines.append(f"**Session ID:** `{summary['session_id'][:16]}...`")
        lines.append(f"**Verdict:** {summary['verdict'].upper()}")
        lines.append(f"**Iterations:** {summary['iterations']}")
        lines.append(f"**Total Events:** {summary['total_events']}")
        lines.append("")

        if summary["root_cause"]:
            lines.append("## Root Cause")
            lines.append("")
            lines.append(f"> {summary['root_cause']}")
            lines.append("")

        if summary["hypotheses"]:
            lines.append("## Hypotheses")
            lines.append("")
            for h in summary["hypotheses"]:
                confidence_pct = round(h["confidence"] * 100)
                lines.append(f"### {h['hypothesis_id']} (Confidence: {confidence_pct}%)")
                lines.append("")
                lines.append(f"**Statement:** {h['statement']}")
                lines.append("")
                lines.append(f"*Falsifiable:* {h['falsification_criteria']}")
                lines.append("")

        if summary["evidence"]:
            lines.append("## Evidence")
            lines.append("")
            lines.append("| Hypothesis | Verdict | Reasoning |")
            lines.append("|------------|---------|-----------|")
            for e in summary["evidence"]:
                v_emoji = {"confirmed": "confirmed", "refuted": "refuted", "inconclusive": "inconclusive"}
                v_label = v_emoji.get(e["verdict"], e["verdict"])
                reasoning = (e.get("reasoning", "") or "")[:80].replace("|", "/")
                lines.append(f"| {e['hypothesis_id']} | {v_label} | {reasoning} |")
            lines.append("")

        lines.append("---")
        lines.append(f"*Report generated at {summary['generated_at']} by Probe*")
        lines.append("")

        return "\n".join(lines)

    def save(self, directory: Path) -> tuple[Path, Path]:
        """Save both JSON and Markdown reports to the given directory."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        json_path = directory / "report.json"
        md_path = directory / "report.md"

        json_report = self.build_json()
        json_path.write_text(json.dumps(json_report, indent=2, ensure_ascii=False), encoding="utf-8")

        md_report = self.build_markdown()
        md_path.write_text(md_report, encoding="utf-8")

        return json_path, md_path


# ── Convenience function ──────────────────────────────────────────────────────


def build_report(
    events: list[TraceEvent],
    session_id: str = "",
    output_dir: str | Path = ".",
) -> dict[str, Any]:
    """Build and save an investigation report. Returns the JSON report dict."""
    builder = ReportBuilder(events, session_id)
    json_path, md_path = builder.save(Path(output_dir))
    result = builder.build_json()
    result["json_report_path"] = str(json_path)
    result["markdown_report_path"] = str(md_path)
    return result
