"""Plotly figures for the report. Each function returns a plain dict (figure JSON)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.utils

from ..analytics import capacity_forecast, daily, heatmap
from ..model import Incident, spec_for

INK = "#1c2333"
MUTED = "#6b7385"
GRID = "#eef0f5"
ACCENT = "#4338ca"
TEAL = "#0e9384"
SEV = {"critical": "#c0262d", "high": "#e4572e", "medium": "#f2a900", "low": "#2e90fa"}
PALETTE = ["#4338ca", "#0e9384", "#e4572e", "#7a5af8", "#f2a900", "#2e90fa", "#c11574", "#667085"]
IST = "Asia/Kolkata"


def _layout(fig: go.Figure, height: int = 300, **kw) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=56, r=18, t=28, b=36),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, ui-sans-serif, system-ui, Segoe UI, sans-serif", size=12, color=INK),
        legend=dict(orientation="h", y=1.12, x=0, font=dict(size=11, color=MUTED)),
        hovermode="x unified",
        **kw,
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return fig


def to_json(fig: go.Figure) -> str:
    return json.dumps(fig.to_plotly_json(), cls=plotly.utils.PlotlyJSONEncoder)


def _scale(metric: str) -> tuple[float, str]:
    spec = spec_for(metric)
    unit = spec.unit if spec else ""
    if unit == "bit/s":
        return 1e-9, "Gbit/s"
    if unit in ("pkt/s",):
        return 1e-3, "kpkt/s"
    return 1.0, unit


def _local(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return idx.tz_convert(IST).tz_localize(None)


def incident_chart(inc: Incident, detection, pad: str = "6h") -> str | None:
    seg = inc.segments[0]
    key = (seg.entity, seg.metric)
    if key not in detection.series:
        return None
    d = detection.series[key]
    lo, hi = inc.start - pd.Timedelta(pad), inc.end + pd.Timedelta(pad)
    v = d.values[(d.values.index >= lo) & (d.values.index <= hi)]
    b = d.baseline.reindex(v.index)
    k, unit = _scale(seg.metric)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=_local(b.index), y=b * k, name="expected", mode="lines",
                             line=dict(color="#98a2b3", width=1.5, dash="dot")))
    fig.add_trace(go.Scatter(x=_local(v.index), y=v * k, name=seg.metric, mode="lines",
                             line=dict(color=ACCENT, width=2)))
    flagged = v[d.flags.reindex(v.index).fillna(False).astype(bool)]
    fig.add_trace(go.Scatter(x=_local(flagged.index), y=flagged * k, name="anomalous", mode="markers",
                             marker=dict(color=SEV.get(inc.severity, "#e4572e"), size=6)))
    fig.add_vrect(x0=_local(pd.DatetimeIndex([inc.start]))[0], x1=_local(pd.DatetimeIndex([inc.end]))[0],
                  fillcolor=SEV.get(inc.severity, "#e4572e"), opacity=0.08, line_width=0)
    _layout(fig, 240, yaxis_title=unit)
    return to_json(fig)


def traffic_overview(prepared, max_points: int = 1200) -> str | None:
    fig = go.Figure()
    n = 0
    for i, (ent, f) in enumerate(prepared.frames.items()):
        if "rx_bps" not in f:
            continue
        s = f["rx_bps"].dropna()
        if len(s) > max_points:
            s = s.resample(f"{int(np.ceil(len(s) / max_points)) * int(prepared.step.total_seconds())}s").mean()
        fig.add_trace(go.Scatter(x=_local(s.index), y=s / 1e9, name=ent, mode="lines",
                                 line=dict(width=1.6, color=PALETTE[i % len(PALETTE)])))
        n += 1
    if not n:
        return None
    _layout(fig, 300, yaxis_title="inbound Gbit/s")
    return to_json(fig)


def latency_overview(prepared, entities) -> str | None:
    fig = go.Figure()
    n = 0
    for i, (ent, f) in enumerate(prepared.frames.items()):
        col = "latency_p95_ms" if "latency_p95_ms" in f else ("latency_ms" if "latency_ms" in f else None)
        if not col:
            continue
        s = f[col].dropna()
        color = PALETTE[(i + 2) % len(PALETTE)]
        fig.add_trace(go.Scatter(x=_local(s.index), y=s, name=f"{ent} {col}", mode="lines",
                                 line=dict(width=1.5, color=color)))
        slo = entities.get(ent).attrs.get("slo_p95_ms") if ent in entities else None
        if slo:
            fig.add_hline(y=slo, line=dict(color=color, dash="dash", width=1),
                          annotation_text=f"{ent} SLO {slo} ms", annotation_font_size=10)
        n += 1
    if not n:
        return None
    _layout(fig, 280, yaxis_title="ms", yaxis_type="log")
    return to_json(fig)


def util_heatmap(prepared) -> tuple[str, str] | None:
    best, best_v = None, -1.0
    for ent, f in prepared.frames.items():
        if "util_pct" in f and f["util_pct"].mean() > best_v:
            best, best_v = ent, float(f["util_pct"].mean())
    if not best:
        return None
    s = prepared.frames[best]["util_pct"]
    s = pd.Series(s.to_numpy(), index=s.index.tz_convert(IST))
    hm = heatmap(s)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    fig = go.Figure(go.Heatmap(z=hm.to_numpy(), x=[f"{h:02d}:00" for h in hm.columns], y=days,
                               colorscale=[[0, "#eef4ff"], [0.5, "#7a5af8"], [1, "#c0262d"]],
                               colorbar=dict(title="util %", thickness=10), hoverongaps=False))
    _layout(fig, 260)
    fig.update_layout(hovermode="closest")
    return to_json(fig), best


def capacity_chart(prepared) -> str | None:
    fig = go.Figure()
    n = 0
    for i, (ent, f) in enumerate(prepared.frames.items()):
        if "util_pct" not in f:
            continue
        d = daily(f["util_pct"], "p95").dropna()
        fc = capacity_forecast(ent, f["util_pct"])
        if d.empty or fc is None:
            continue
        color = PALETTE[i % len(PALETTE)]
        x = _local(d.index)
        fig.add_trace(go.Scatter(x=x, y=d, mode="markers+lines", name=ent, line=dict(color=color, width=1.5)))
        horizon = 30
        xs = pd.date_range(x[-1], periods=horizon, freq="1D")
        ys = fc.current_p95_pct + fc.growth_pct_per_day * np.arange(horizon)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", showlegend=False,
                                 line=dict(color=color, dash="dot", width=1)))
        n += 1
    if not n:
        return None
    fig.add_hline(y=80, line=dict(color="#c0262d", dash="dash", width=1), annotation_text="80% planning threshold",
                  annotation_font_size=10)
    _layout(fig, 300, yaxis_title="daily p95 utilisation %")
    return to_json(fig)


def incident_timeline(incidents: list[Incident]) -> str | None:
    if not incidents:
        return None
    fig = go.Figure()
    for inc in incidents:
        start = _local(pd.DatetimeIndex([inc.start]))[0]
        end = _local(pd.DatetimeIndex([max(inc.end, inc.start + pd.Timedelta("20min"))]))[0]
        fig.add_trace(go.Scatter(
            x=[start, end], y=[inc.entities[0]] * 2, mode="lines",
            line=dict(color=SEV.get(inc.severity, ACCENT), width=14),
            name=inc.id, hovertemplate=f"{inc.id} {inc.title}<extra>{inc.severity}</extra>", showlegend=False))
        fig.add_annotation(x=start, y=inc.entities[0], text=inc.id, showarrow=False, yshift=14,
                           font=dict(size=10, color=MUTED))
    _layout(fig, 60 + 34 * len({i.entities[0] for i in incidents}))
    fig.update_layout(hovermode="closest", margin=dict(l=170, r=18, t=18, b=30))
    return to_json(fig)
