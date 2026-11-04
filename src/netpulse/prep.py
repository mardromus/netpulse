"""Turn raw samples into clean, regular, analysable time series.

Network counters need care before any statistics are meaningful:

* **Counters to rates.** ``rx_bytes`` only ever grows; what matters is bytes per
  second. Rates are computed from consecutive samples using the real time
  difference, so irregular polling does not distort them.
* **Wraps and resets.** A 32-bit counter wraps at 2^32; a device reboot resets
  counters to zero. Both show up as a negative difference. A wrap is corrected
  (``2^32 - prev + cur``) when the corrected rate is plausible; otherwise the
  point is treated as a reset and dropped (one NaN rate) instead of becoming a
  huge fake negative spike. Both are recorded as data quality notes.
* **Regular grid.** Everything is resampled to one step (inferred from the data)
  so detectors can compare like with like. Short holes (<= 2 steps) are
  interpolated, longer ones are kept as gaps and reported.
* **Derived KPIs.** Utilisation, error ratio, retransmission % and HTTP error %
  are what operators actually reason about, so they are computed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .model import CATALOG, Entity

WRAP32 = 2**32
WRAP64 = 2**64


@dataclass
class QualityNote:
    entity: str
    metric: str
    kind: str  # gap | counter_reset | counter_wrap
    start: pd.Timestamp
    end: pd.Timestamp
    detail: str

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "metric": self.metric,
            "kind": self.kind,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "detail": self.detail,
        }


@dataclass
class Prepared:
    step: pd.Timedelta
    frames: dict[str, pd.DataFrame]  # entity -> wide frame of analysed metrics
    quality: list[QualityNote] = field(default_factory=list)
    entities: dict[str, Entity] = field(default_factory=dict)

    @property
    def start(self) -> pd.Timestamp:
        return min(f.index.min() for f in self.frames.values())

    @property
    def end(self) -> pd.Timestamp:
        return max(f.index.max() for f in self.frames.values())

    def keys(self) -> list[tuple[str, str]]:
        return [(e, m) for e, f in self.frames.items() for m in f.columns]

    def series(self, entity: str, metric: str) -> pd.Series:
        return self.frames[entity][metric]

    def has(self, entity: str, metric: str) -> bool:
        return entity in self.frames and metric in self.frames[entity].columns


def infer_step(ts: pd.Series) -> pd.Timedelta:
    """Most common sampling interval, rounded to whole seconds (min 1 s)."""
    t = pd.Series(pd.to_datetime(ts, utc=True)).sort_values().drop_duplicates()
    if len(t) < 2:
        return pd.Timedelta("60s")
    d = t.diff().dropna().dt.total_seconds()
    step = float(np.median(d))
    return pd.Timedelta(seconds=max(1, round(step)))


def counter_to_rate(
    s: pd.Series, entity: str = "", metric: str = ""
) -> tuple[pd.Series, list[QualityNote]]:
    """Per-second rate of a monotonically increasing counter, handling wraps/resets."""
    s = s.sort_index()
    s = s[~s.index.duplicated(keep="last")]
    values = s.to_numpy(dtype=float)
    times = s.index
    notes: list[QualityNote] = []
    if len(values) < 2:
        return pd.Series(dtype=float, index=times), notes
    secs = (times - times[0]).total_seconds().to_numpy()
    dt = np.diff(secs)
    dv = np.diff(values)
    rate = np.where(dt > 0, dv / np.where(dt > 0, dt, 1), np.nan)
    good = rate[(dv >= 0) & np.isfinite(rate)]
    typical = float(np.median(good)) if good.size else 0.0
    for i in np.where(dv < 0)[0]:
        prev, cur = values[i], values[i + 1]
        fixed = None
        for width, name in ((WRAP32, "32-bit"), (WRAP64, "64-bit")):
            if prev < width:
                cand = (width - prev + cur) / dt[i]
                if typical == 0 or cand <= max(typical * 5, 1.0):
                    fixed = (cand, name)
                break
        when = times[i + 1]
        if fixed:
            rate[i] = fixed[0]
            notes.append(QualityNote(entity, metric, "counter_wrap", when, when,
                                     f"{fixed[1]} counter wrapped and was corrected"))
        else:
            rate[i] = np.nan
            notes.append(QualityNote(entity, metric, "counter_reset", when, when,
                                     f"counter dropped from {prev:.0f} to {cur:.0f} (device "
                                     "restart or counter clear)"))
    return pd.Series(rate, index=times[1:]), notes


def regularize(
    s: pd.Series, step: pd.Timedelta, start: pd.Timestamp, end: pd.Timestamp,
    entity: str = "", metric: str = "",
) -> tuple[pd.Series, list[QualityNote]]:
    """Resample onto a fixed grid; interpolate short holes, report long gaps."""
    grid = pd.date_range(start.floor(step), end.ceil(step), freq=step, tz="UTC")
    # assign each sample to the grid slot it falls into (mean when several)
    slots = s.groupby(s.index.floor(step)).mean()
    out = slots.reindex(grid)
    notes: list[QualityNote] = []
    missing = out.isna().to_numpy()
    if missing.any():
        idx = np.flatnonzero(missing)
        runs = np.split(idx, np.where(np.diff(idx) != 1)[0] + 1)
        for run in runs:
            if run.size == 0:
                continue
            first, last = grid[run[0]], grid[run[-1]]
            at_edge = run[0] == 0 or run[-1] == len(grid) - 1
            if run.size > 2 and not at_edge:
                notes.append(QualityNote(entity, metric, "gap", first, last + step,
                                         f"no data for {run.size} intervals"))
        out = out.interpolate(limit=2, limit_area="inside")
    return out, notes


def derive(frame: pd.DataFrame, entity: Entity | None) -> pd.DataFrame:
    """Add operator-level KPIs computed from the rates of one entity."""
    f = frame.copy()
    speed = entity.speed_bps if entity else None
    if speed and {"rx_bps", "tx_bps"} & set(f.columns):
        peak = f[[c for c in ("rx_bps", "tx_bps") if c in f.columns]].max(axis=1)
        f["util_pct"] = peak / speed * 100.0
    pk = [c for c in ("rx_pps", "tx_pps") if c in f.columns]
    bad = [c for c in ("rx_err_ps", "tx_err_ps", "rx_drop_ps", "tx_drop_ps") if c in f.columns]
    if pk and bad:
        pkts = f[pk].sum(axis=1)
        f["err_ratio_pct"] = (f[bad].sum(axis=1) / pkts.where(pkts > 0)) * 100.0
    if {"tcp_retrans_sps", "tcp_out_sps"} <= set(f.columns):
        f["retrans_pct"] = f["tcp_retrans_sps"] / f["tcp_out_sps"].where(f["tcp_out_sps"] > 0) * 100
    if {"http_5xx_ps", "http_rps"} <= set(f.columns):
        f["http_err_pct"] = f["http_5xx_ps"] / f["http_rps"].where(f["http_rps"] > 0) * 100
    return f


def prepare(
    df: pd.DataFrame,
    entities: dict[str, Entity] | None = None,
    step: pd.Timedelta | None = None,
) -> Prepared:
    """Raw long samples -> Prepared (regular wide frame per entity + quality notes)."""
    if df.empty:
        raise ValueError("no samples to prepare")
    entities = entities or {}
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    step = step or infer_step(df["ts"])
    start, end = df["ts"].min(), df["ts"].max()
    frames: dict[str, pd.DataFrame] = {}
    notes: list[QualityNote] = []

    for (ent, metric), g in df.groupby(["entity", "metric"], sort=True):
        s = pd.Series(g["value"].to_numpy(float), index=pd.DatetimeIndex(g["ts"]))
        spec = CATALOG.get(metric)
        if spec and spec.kind == "counter":
            s, n = counter_to_rate(s, ent, metric)
            notes += n
            s = s * spec.rate_scale
            name = spec.analysed_name
        else:
            name = metric
        reg, n = regularize(s, step, start, end, ent, name)
        notes += n
        frames.setdefault(ent, {})[name] = reg

    out: dict[str, pd.DataFrame] = {}
    for ent, cols in frames.items():
        frame = pd.DataFrame(cols)
        out[ent] = derive(frame, entities.get(ent))
    return Prepared(step=step, frames=out, quality=notes, entities=entities)
