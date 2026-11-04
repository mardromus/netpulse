"""Anomaly detection ensemble.

For every analysed series the univariate detectors in :mod:`.methods` run, and
for every entity an Isolation Forest looks at all of its metrics together. A
point is anomalous when the evidence agrees:

* the seasonal detector fires **and** a second method agrees (local jump, STL,
  isolation forest), or
* a detector is very confident on its own (score >= 2), or
* a hard operational threshold is crossed, or a persistent level shift starts.

Then two sanity filters remove technically-anomalous-but-irrelevant points:
*direction* (more errors is bad, fewer is not; traffic matters both ways) and
*minimum effect* (a jump from 0.001 to 0.004 errors/s is not an incident).
Consecutive anomalous points become :class:`~netpulse.model.Segment` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..model import Segment, spec_for
from ..prep import Prepared
from .base import DetectorResult
from .methods import all_univariate
from .multivariate import isolation_forest


@dataclass
class DetectOptions:
    use_stl: bool = True
    use_iforest: bool = True
    min_points: int = 2
    merge_gap: int = 1
    seed: int = 0
    sensitivity: float = 1.0  # >1 flags smaller deviations, <1 only larger ones


# (relative floor, absolute floor) by metric group
FLOORS = {
    "traffic": (0.25, 0.0),
    "connections": (0.30, 20.0),
    "errors": (1.5, 0.05),  # at least 2.5x normal: error counters are bursty
    "latency": (0.30, 10.0),
    "saturation": (0.0, 15.0),
    "availability": (0.0, 0.5),
}


@dataclass
class SeriesDetection:
    entity: str
    metric: str
    values: pd.Series
    baseline: pd.Series
    score: pd.Series
    flags: pd.Series
    detectors: dict[str, pd.Series] = field(default_factory=dict)


@dataclass
class Detection:
    series: dict[tuple[str, str], SeriesDetection]
    segments: list[Segment]

    def flagged_series(self) -> list[SeriesDetection]:
        return [d for d in self.series.values() if d.flags.any()]


def _passes_floor(value: float, base: float, group: str, metric: str) -> bool:
    rel, abs_ = FLOORS.get(group, (0.2, 0.0))
    if metric.endswith("_pct") and group == "errors":
        abs_ = 0.05
    delta = abs(value - base)
    if abs_ and delta < abs_:
        return False
    if rel and base and abs(base) > 1e-9 and delta / abs(base) < rel:
        return False
    return not (rel and (not base or abs(base) <= 1e-9) and delta < max(abs_, 1e-9))


def _segments(det: SeriesDetection, group: str, opts: DetectOptions, step: pd.Timedelta) -> list[Segment]:
    flags = det.flags.to_numpy()
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return []
    runs, cur = [], [idx[0]]
    for i in idx[1:]:
        if i - cur[-1] <= opts.merge_gap + 1:
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]
    runs.append(cur)
    out = []
    vals = det.values.to_numpy()
    base = det.baseline.to_numpy()
    score = det.score.to_numpy()
    thr = det.detectors.get("threshold")
    for run in runs:
        lo, hi = run[0], run[-1]
        peak_score = float(np.nanmax(score[lo : hi + 1]))
        thr_hit = thr is not None and bool((thr.to_numpy()[lo : hi + 1] >= 1).any())
        if len(run) < opts.min_points and not thr_hit:
            continue
        dev = np.abs(vals[lo : hi + 1] - base[lo : hi + 1])
        dev = np.where(np.isfinite(dev), dev, -1)
        pk = lo + int(np.argmax(dev))
        names = [n for n, s in det.detectors.items() if (s.to_numpy()[lo : hi + 1] >= 1).any()]
        peak_value = float(vals[pk])
        baseline = float(base[pk]) if np.isfinite(base[pk]) else 0.0
        if "cusum" in names and "seasonal" not in names:
            # a level shift: compare the new level with the day before it began
            ratio = det.values / det.baseline.where(det.baseline > 0)
            t0 = det.values.index[lo]
            before = ratio[(ratio.index >= t0 - pd.Timedelta("1D")) & (ratio.index < t0)].median()
            seg_vals = det.values.iloc[lo : hi + 1]
            seg_base = det.baseline.iloc[lo : hi + 1]
            if np.isfinite(before) and before > 0:
                peak_value = float(seg_vals.median())
                baseline = float(seg_base.median() * before)
        out.append(
            Segment(
                entity=det.entity,
                metric=det.metric,
                start=det.values.index[lo],
                end=det.values.index[hi] + step,
                peak_ts=det.values.index[pk],
                peak_value=peak_value,
                baseline=baseline,
                score=peak_score,
                direction="up" if peak_value >= baseline else "down",
                detectors=names,
                points=len(run),
            )
        )
    return out


def detect_series(s: pd.Series, entity: str, metric: str, attrs: dict | None = None,
                  entity_score: pd.Series | None = None,
                  opts: DetectOptions | None = None) -> SeriesDetection:
    opts = opts or DetectOptions()
    spec = spec_for(metric)
    group = spec.group if spec else "traffic"
    worse_up = spec.higher_is_worse if spec else True
    results, profile = all_univariate(s, metric, group, attrs, opts.use_stl, opts.sensitivity)
    by = {r.name: r for r in results}
    z = lambda n: by[n].score if n in by else pd.Series(0.0, index=s.index)  # noqa: E731
    seas, loc, stl_s, cus, thr = z("seasonal"), z("local"), z("stl"), z("cusum"), z("threshold")
    ifs = entity_score.reindex(s.index).fillna(0.0) if entity_score is not None else 0.0 * seas
    votes = (seas >= 1).astype(int) + (loc >= 1) + (stl_s >= 1) + (ifs >= 1)
    statistical = ((seas >= 1) & (votes >= 2)) | (seas >= 2)
    shift = cus >= 1  # level shifts are judged by CUSUM's own effect size
    hard = thr >= 1

    values = s.to_numpy()
    base = profile.to_numpy()
    ok = np.zeros(len(s), dtype=bool)
    for i in np.flatnonzero(statistical.to_numpy()):
        v, b = values[i], base[i]
        if not (np.isfinite(v) and np.isfinite(b)):
            continue
        up = v >= b
        if group == "availability":
            if up:
                continue
        elif worse_up and not up:
            continue  # fewer errors / lower latency is not a problem
        if _passes_floor(v, b, group, metric):
            ok[i] = True
    flags = pd.Series(ok, index=s.index) | hard | shift
    combined = pd.concat([seas, stl_s, loc * 0.8, thr, cus], axis=1).max(axis=1)
    dets = {r.name: r.score for r in results}
    if entity_score is not None:
        dets["iforest"] = ifs
    return SeriesDetection(entity, metric, s, profile, combined.where(flags, 0.0), flags, dets)


def detect(p: Prepared, opts: DetectOptions | None = None) -> Detection:
    opts = opts or DetectOptions()
    series: dict[tuple[str, str], SeriesDetection] = {}
    segments: list[Segment] = []
    for ent, frame in p.frames.items():
        attrs = p.entities[ent].attrs if ent in p.entities else {}
        ent_score = None
        if opts.use_iforest:
            r: DetectorResult | None = isolation_forest(frame, seed=opts.seed)
            ent_score = r.score if r is not None else None
        for metric in frame.columns:
            s = frame[metric]
            if s.dropna().size < 12:
                continue
            d = detect_series(s, ent, metric, attrs, ent_score, opts)
            series[(ent, metric)] = d
            spec = spec_for(metric)
            segments += _segments(d, spec.group if spec else "traffic", opts, p.step)
    segments.sort(key=lambda g: (g.start, g.entity, g.metric))
    return Detection(series, segments)
