"""Assemble facts + narrative + charts into HTML, Markdown and JSON reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import __version__
from ..ai.facts import build_facts
from ..ai.llm import LLMConfig
from ..ai.narrator import Narrative, narrate
from . import charts as ch

TEMPLATES = Path(__file__).parent / "templates"


@dataclass
class Report:
    facts: dict
    narrative: Narrative
    result: object  # AnalysisResult

    @property
    def headline(self) -> str:
        return self.narrative.headline


def build_report(result, use_ai: bool = False, llm: LLMConfig | None = None) -> Report:
    facts = build_facts(result)
    return Report(facts, narrate(facts, use_ai, llm), result)


def _charts(report: Report) -> tuple[dict, dict, str | None]:
    r = report.result
    out: dict = {}
    per_inc: dict = {}
    for inc in r.incidents:
        js = ch.incident_chart(inc, r.detection)
        if js:
            per_inc[inc.id] = True
            out[f"ch-{inc.id}"] = json.loads(js)
    flags = {"incidents": per_inc}
    for key, fn in (("timeline", lambda: ch.incident_timeline(r.incidents)),
                    ("traffic", lambda: ch.traffic_overview(r.prepared)),
                    ("latency", lambda: ch.latency_overview(r.prepared, r.prepared.entities)),
                    ("capacity", lambda: ch.capacity_chart(r.prepared))):
        js = fn()
        flags[key] = bool(js)
        if js:
            out[f"ch-{key}"] = json.loads(js)
    hm = ch.util_heatmap(r.prepared)
    flags["heatmap"] = bool(hm)
    hm_entity = None
    if hm:
        out["ch-heatmap"] = json.loads(hm[0])
        hm_entity = hm[1]
    return out, flags, hm_entity


def render_html(report: Report, inline_plotly: bool = False) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    tpl = env.get_template("report.html.j2")
    charts_json, flags, hm_entity = _charts(report)
    f = report.facts
    peak_util = max((i.get("util_peak_pct") or 0 for i in f["interfaces"]), default=0)
    total_tb = sum(i.get("total_traffic_tb") or 0 for i in f["interfaces"])
    plotly_js = None
    if inline_plotly:
        from plotly.offline import get_plotlyjs

        plotly_js = get_plotlyjs()
    ev = report.result.evaluation.to_dict() if report.result.evaluation else None
    if ev:
        ev["kind_accuracy"] = round(report.result.evaluation.kind_accuracy, 3)
    return tpl.render(
        facts=f,
        narrative=report.narrative,
        incidents=f["incidents"],
        charts=flags,
        charts_json=json.dumps(charts_json),
        heatmap_entity=hm_entity,
        points=f"{sum(len(df) * len(df.columns) for df in report.result.prepared.frames.values()):,}",
        peak_util=f"{peak_util:.0f}%" if peak_util else "n/a",
        total_traffic=(f"{total_tb / 1000:.2f} PB" if total_tb >= 1000 else f"{total_tb:.1f} TB") if total_tb else "n/a",
        evaluation=ev,
        plotly_inline=plotly_js,
        version=__version__,
        generated=pd.Timestamp.now(tz="Asia/Kolkata").strftime("%d %b %Y %H:%M IST"),
    )


def render_markdown(report: Report) -> str:
    f, n = report.facts, report.narrative
    lines = [f"# {n.headline}", "",
             f"_{f['period']['start']} → {f['period']['end']} · {f['counts']['entities']} entities · "
             f"narrative: {n.engine}{' (verified)' if n.verified else ''}_", "", n.executive_summary, ""]
    lines += ["## Incidents", ""]
    if not f["incidents"]:
        lines.append("No incidents detected.")
    for i in f["incidents"]:
        lines += [f"### {i['id']} · {i['severity'].upper()} · {i['title']}", "",
                  f"{i['start']} → {i['end']} ({i['duration_min']} min) · {', '.join(i['entities'])}", "",
                  n.incident_notes.get(i["id"], i["probable_cause"]), "",
                  f"- **Cause:** {i['probable_cause']}", f"- **Impact:** {i['impact']}",
                  f"- **Action:** {i['recommendation']}", "",
                  "| metric | entity | peak | normal | × normal |", "|---|---|---:|---:|---:|"]
        for m, ev in i["evidence"].items():
            lines.append(f"| `{m}` | `{ev['entity']}` | {ev['peak']} | {ev['normal']} | "
                         f"{ev['times_normal'] if ev['times_normal'] is not None else '—'} |")
        lines.append("")
    lines += ["## Recommended actions", ""] + [f"{k}. {r}" for k, r in enumerate(n.recommendations, 1)]
    lines += ["", "## Capacity outlook", "", n.capacity_outlook, ""]
    if f["data_quality"]:
        lines += ["## Data quality", ""] + [f"- {q['kind'].replace('_', ' ')} on `{q['entity']}` "
                                            f"({q['occurrences']}×)" for q in f["data_quality"]]
    if f.get("evaluation"):
        e = f["evaluation"]
        lines += ["", "## Detection quality", "",
                  f"{e['detected']}/{e['events']} events detected, {e['false_alarms']} false alarms, "
                  f"precision {e['precision']}, recall {e['recall']}, F1 {e['f1']}."]
    return "\n".join(lines) + "\n"


def render_json(report: Report) -> str:
    r = report.result
    return json.dumps({
        "facts": report.facts,
        "narrative": report.narrative.to_dict(),
        "incidents": [i.to_dict() for i in r.incidents],
        "evaluation": r.evaluation.to_dict() if r.evaluation else None,
    }, indent=2, default=str)
