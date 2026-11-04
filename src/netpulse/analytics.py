"""Descriptive analytics: what normal looks like, trends and capacity.

Everything here is plain, explainable statistics (percentiles, robust trends)
so the numbers quoted in a report can be checked by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .model import spec_for
from .prep import Prepared


def summary(s: pd.Series) -> dict:
    v = s.dropna()
    if v.empty:
        return {"count": 0}
    return {
        "count": int(v.size),
        "missing_pct": round(100.0 * (1 - v.size / max(len(s), 1)), 2),
        "mean": float(v.mean()),
        "min": float(v.min()),
        "p50": float(v.quantile(0.50)),
        "p95": float(v.quantile(0.95)),
        "p99": float(v.quantile(0.99)),
        "max": float(v.max()),
        "max_at": v.idxmax().isoformat(),
        "last": float(v.iloc[-1]),
    }


def theil_sen(y: np.ndarray, x: np.ndarray | None = None) -> tuple[float, float]:
    """Robust linear fit: median of pairwise slopes. Returns (slope, intercept)."""
    y = np.asarray(y, dtype=float)
    x = np.arange(len(y), dtype=float) if x is None else np.asarray(x, dtype=float)
    ok = np.isfinite(y) & np.isfinite(x)
    x, y = x[ok], y[ok]
    if len(y) < 2:
        return 0.0, float(y[0]) if len(y) else 0.0
    i, j = np.triu_indices(len(y), k=1)
    dx = x[j] - x[i]
    slopes = (y[j] - y[i])[dx != 0] / dx[dx != 0]
    slope = float(np.median(slopes)) if slopes.size else 0.0
    intercept = float(np.median(y - slope * x))
    return slope, intercept


def daily(s: pd.Series, how: str = "mean") -> pd.Series:
    g = s.dropna().resample("1D")
    return getattr(g, how)() if how != "p95" else g.quantile(0.95)


def trend(s: pd.Series) -> dict:
    """Day-over-day trend of the daily mean, in % per day of the overall mean."""
    d = daily(s).dropna()
    if len(d) < 3 or d.mean() == 0:
        return {"pct_per_day": 0.0, "days": int(len(d))}
    slope, _ = theil_sen(d.to_numpy())
    return {"pct_per_day": round(100.0 * slope / float(d.mean()), 2), "days": int(len(d))}


def peak_hour(s: pd.Series) -> dict:
    v = s.dropna()
    if v.empty:
        return {}
    by_hour = v.groupby(v.index.hour).mean()
    return {"hour_utc": int(by_hour.idxmax()), "quiet_hour_utc": int(by_hour.idxmin()),
            "peak_to_trough": round(float(by_hour.max() / by_hour.min()), 2) if by_hour.min() > 0 else None}


def heatmap(s: pd.Series) -> pd.DataFrame:
    """Mean value by weekday (rows, Mon..Sun) and hour of day (columns)."""
    v = s.dropna()
    t = pd.DataFrame({"v": v.to_numpy(), "dow": v.index.dayofweek, "hour": v.index.hour})
    return t.pivot_table(index="dow", columns="hour", values="v", aggfunc="mean").reindex(range(7))


@dataclass
class CapacityForecast:
    entity: str
    current_p95_pct: float
    growth_pct_per_day: float
    days_to_threshold: float | None
    threshold_pct: float

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "current_p95_util_pct": round(self.current_p95_pct, 1),
            "growth_pct_points_per_day": round(self.growth_pct_per_day, 2),
            "days_to_threshold": None if self.days_to_threshold is None else round(self.days_to_threshold, 1),
            "threshold_pct": self.threshold_pct,
        }


def capacity_forecast(entity: str, util: pd.Series, threshold: float = 80.0) -> CapacityForecast | None:
    """Linear (Theil-Sen) projection of the daily p95 utilisation."""
    d = daily(util, "p95").dropna()
    if len(d) < 3:
        return None
    slope, intercept = theil_sen(d.to_numpy())
    current = float(intercept + slope * (len(d) - 1))
    days = None
    if current >= threshold:
        days = 0.0
    elif slope > 1e-6:
        days = (threshold - current) / slope
        if days > 365:  # not a planning concern within a year
            days = None
    return CapacityForecast(entity, current, slope, days, threshold)


def entity_kpis(p: Prepared, entity: str) -> dict:
    f = p.frames[entity]
    step_s = p.step.total_seconds()
    out: dict = {}
    for col in f.columns:
        s = f[col]
        if s.dropna().empty:
            continue
        spec = spec_for(col)
        st = summary(s)
        out[col] = {
            "unit": spec.unit if spec else "",
            "mean": st["mean"], "p95": st["p95"], "max": st["max"], "max_at": st["max_at"],
        }
    for direction in ("rx", "tx"):
        col = f"{direction}_bps"
        if col in f.columns:
            out[f"{direction}_total_bytes"] = float(np.nansum(f[col].to_numpy()) * step_s / 8.0)
    return out


def overview(p: Prepared, util_threshold: float = 80.0) -> dict:
    """Dataset-wide analytics used by the report and the AI narrator."""
    entities = {}
    forecasts = []
    for ent in p.frames:
        kp = entity_kpis(p, ent)
        e = p.entities.get(ent)
        info = {"kind": e.kind if e else "unknown", "attrs": e.attrs if e else {}, "kpis": kp}
        main = next((c for c in ("rx_bps", "http_rps", "latency_ms") if c in p.frames[ent]), None)
        if main:
            info["trend"] = trend(p.frames[ent][main])
            info["peak"] = peak_hour(p.frames[ent][main])
        if "util_pct" in p.frames[ent]:
            fc = capacity_forecast(ent, p.frames[ent]["util_pct"], util_threshold)
            if fc:
                forecasts.append(fc.to_dict())
        entities[ent] = info
    talkers = sorted(
        ((ent, i["kpis"].get("rx_total_bytes", 0) + i["kpis"].get("tx_total_bytes", 0))
         for ent, i in entities.items()),
        key=lambda x: -x[1],
    )
    return {
        "period": {"start": p.start.isoformat(), "end": p.end.isoformat(),
                   "step_s": p.step.total_seconds(),
                   "days": round((p.end - p.start).total_seconds() / 86400, 2)},
        "entities": entities,
        "capacity": sorted(forecasts, key=lambda d: (d["days_to_threshold"] is None,
                                                     d["days_to_threshold"] or 0)),
        "top_talkers": [{"entity": e, "bytes": b} for e, b in talkers if b > 0][:5],
        "data_quality": [q.to_dict() for q in p.quality],
    }
