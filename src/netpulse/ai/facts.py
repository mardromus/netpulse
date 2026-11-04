"""The fact sheet: everything the narrator may say, as numbers *and* as text.

Numbers are rounded the way a human would quote them, and every quantity also
appears pre-formatted with its unit ("966.9 Mbit/s"). The verifier accepts a
number in the narrative only if it matches one of these.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd

from ..incidents import _fmt
from ..model import spec_for

IST = "Asia/Kolkata"


def _r(x: float | None, nd: int = 3) -> float | None:
    if x is None:
        return None
    if x == 0 or not pd.notna(x):
        return 0.0
    from math import floor, log10

    return round(float(x), max(0, nd - 1 - int(floor(log10(abs(x))))))


def _when(ts: str | pd.Timestamp) -> str:
    t = pd.Timestamp(ts).tz_convert(IST)
    return t.strftime("%a %d %b %H:%M IST")


def build_facts(result) -> dict:
    ov = result.overview
    facts: dict = {
        "period": {
            "start": _when(ov["period"]["start"]),
            "end": _when(ov["period"]["end"]),
            "days": ov["period"]["days"],
            "sample_interval_min": round(ov["period"]["step_s"] / 60, 1),
        },
        "counts": {
            "entities": len(ov["entities"]),
            "incidents": len(result.incidents),
            **{f"{k}_incidents": v for k, v in result.severity_counts.items()},
        },
        "interfaces": [],
        "services": [],
        "incidents": [],
        "capacity": [],
        "data_quality": [],
    }
    for name, info in ov["entities"].items():
        k = info["kpis"]
        if info["kind"] == "interface":
            speed = info["attrs"].get("speed_bps")
            row = {"name": name, "role": info["attrs"].get("role", "")}
            if speed:
                row["speed"] = _fmt(speed, "bit/s")
            for col, label in (("rx_bps", "rx"), ("tx_bps", "tx")):
                if col in k:
                    row[f"{label}_avg"] = _fmt(k[col]["mean"], "bit/s")
                    row[f"{label}_peak"] = _fmt(k[col]["max"], "bit/s")
            if "util_pct" in k:
                row["util_p95_pct"] = _r(k["util_pct"]["p95"])
                row["util_peak_pct"] = _r(k["util_pct"]["max"])
                row["util_peak_at"] = _when(k["util_pct"]["max_at"])
            tb = (k.get("rx_total_bytes", 0) + k.get("tx_total_bytes", 0)) / 1e12
            if tb:
                row["total_traffic_tb"] = _r(tb)
            if "trend" in info:
                row["trend_pct_per_day"] = info["trend"]["pct_per_day"]
            if info.get("peak"):
                row["busiest_hour_ist"] = f"{(info['peak']['hour_utc'] + 5) % 24:02d}:30"
            facts["interfaces"].append(row)
        else:
            row = {"name": name, "kind": info["kind"]}
            for col, label, unit in (("http_rps", "requests", "req/s"), ("latency_p95_ms", "p95_latency", "ms"),
                                     ("http_err_pct", "error_rate", "%"), ("cpu_pct", "cpu", "%"),
                                     ("conn_established", "connections", ""), ("latency_ms", "latency", "ms")):
                if col in k:
                    row[f"{label}_typical"] = _r(k[col]["mean"])
                    row[f"{label}_worst"] = _r(k[col]["max"])
                    if unit:
                        row[f"{label}_unit"] = unit
            if info["attrs"].get("slo_p95_ms"):
                row["slo_p95_ms"] = info["attrs"]["slo_p95_ms"]
            facts["services"].append(row)

    for inc in result.incidents:
        key = {}
        for s in inc.segments[:6]:
            spec = spec_for(s.metric)
            unit = spec.unit if spec else ""
            ratio = s.peak_value / s.baseline if s.baseline else None
            key[s.metric] = {
                "entity": s.entity,
                "peak": _fmt(s.peak_value, unit),
                "normal": _fmt(s.baseline, unit),
                "times_normal": _r(ratio, 2) if ratio and ratio < 1e4 else None,
                "direction": s.direction,
            }
        facts["incidents"].append({
            "id": inc.id,
            "kind": inc.kind,
            "severity": inc.severity,
            "title": inc.title,
            "entities": inc.entities,
            "start": _when(inc.start),
            "end": _when(inc.end),
            "duration_min": round(inc.duration_min),
            "probable_cause": inc.probable_cause,
            "impact": inc.impact,
            "recommendation": inc.recommendation,
            "evidence": key,
        })
    for c in ov["capacity"]:
        facts["capacity"].append({
            "entity": c["entity"],
            "current_p95_util_pct": c["current_p95_util_pct"],
            "growth_points_per_day": c["growth_pct_points_per_day"],
            "days_to_80pct": c["days_to_threshold"],
        })
    dq = Counter((q["kind"], q["entity"]) for q in ov["data_quality"])
    for (kind, entity), n in sorted(dq.items()):
        facts["data_quality"].append({"kind": kind, "entity": entity, "occurrences": n})
    if result.evaluation:
        e = result.evaluation.to_dict()
        facts["evaluation"] = {k: e[k] for k in ("events", "detected", "false_alarms", "precision", "recall", "f1")}
    return facts
