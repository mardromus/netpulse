"""Univariate detectors.

=============  ==============================================================
seasonal       residual against the time-of-day profile, robust z (MAD)
local          sudden jumps: rolling robust z of that residual (last 2 hours)
stl            STL decomposition residual (statsmodels), robust z
cusum          persistent level shifts: two-sided CUSUM on the log ratio to
               the profile
threshold      hard operational limits (utilisation, error %, SLOs, probes)
=============  ==============================================================
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .base import DetectorResult, robust_sigma, seasonal_profile, steps_per_day

RELATIVE_GROUPS = {"traffic", "connections"}


def residual(s: pd.Series, profile: pd.Series, relative: bool) -> pd.Series:
    if relative:
        denom = profile.abs().where(profile.abs() > 1e-9)
        return (s - profile) / denom
    return s - profile


def seasonal(s: pd.Series, profile: pd.Series, relative: bool, z: float = 4.5) -> DetectorResult:
    r = residual(s, profile, relative)
    sigma = robust_sigma(r.to_numpy())
    signed = r / sigma
    return DetectorResult("seasonal", (signed.abs() / z).fillna(0.0), profile, signed)


def local(s: pd.Series, profile: pd.Series, relative: bool, window: int = 24,
          z: float = 5.0) -> DetectorResult:
    """Robust z of the residual against the *previous* `window` points only."""
    r = residual(s, profile, relative)
    med = r.rolling(window, min_periods=max(6, window // 3)).median().shift(1)
    mad = (r - med).abs().rolling(window, min_periods=max(6, window // 3)).median().shift(1)
    global_sigma = robust_sigma(r.to_numpy())
    sigma = (1.4826 * mad).clip(lower=global_sigma * 0.5)
    signed = (r - med) / sigma
    return DetectorResult("local", (signed.abs() / z).fillna(0.0), None, signed)


def stl(s: pd.Series, z: float = 4.5) -> DetectorResult | None:
    n_day = steps_per_day(s.index)
    clean = s.interpolate(limit_direction="both")
    if len(s) < 3 * n_day or clean.isna().any() or n_day < 4:
        return None
    from statsmodels.tsa.seasonal import STL

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        jump = max(1, n_day // 10)  # interpolate the LOESS fits: ~30x faster, same shape
        res = STL(clean.to_numpy(), period=n_day, robust=True, seasonal_jump=jump,
                  trend_jump=jump, low_pass_jump=jump).fit()
    resid = pd.Series(res.resid, index=s.index)
    signed = resid / robust_sigma(resid.to_numpy())
    signed[s.isna()] = np.nan
    base = pd.Series(res.trend + res.seasonal, index=s.index)
    return DetectorResult("stl", (signed.abs() / z).fillna(0.0), base, signed)


def cusum(s: pd.Series, profile: pd.Series, k: float = 0.5, h: float = 12.0,
          hold: str = "3h", min_effect: float = 0.15) -> DetectorResult:
    """Flag the start of a sustained shift in level (relative to the profile).

    Runs on the log ratio s / profile so multiplicative steps (+30% traffic)
    look the same at any time of day."""
    ratio = np.log((s.clip(lower=0) + 1e-9) / (profile.clip(lower=0) + 1e-9))
    ratio = ratio.replace([np.inf, -np.inf], np.nan)
    x = ratio.to_numpy()
    ok = np.isfinite(x)
    score = np.zeros(len(x))
    signed = np.zeros(len(x))
    if ok.sum() < 30:
        return DetectorResult("cusum", pd.Series(score, index=s.index))
    sigma = robust_sigma(np.diff(x[ok])) / np.sqrt(2)
    mu = np.median(x[ok][: max(30, ok.sum() // 4)])  # reference level: early data
    hold_n = max(1, int(pd.Timedelta(hold) / (s.index[1] - s.index[0])))
    gp = gn = 0.0
    start_p = start_n = None
    i = 0
    while i < len(x):
        if not ok[i]:
            i += 1
            continue
        zt = (x[i] - mu) / sigma
        gp = max(0.0, gp + zt - k)
        gn = max(0.0, gn - zt - k)
        if gp == 0:
            start_p = i
        if gn == 0:
            start_n = i
        if gp > h or gn > h:
            up = gp > h
            begin = (start_p if up else start_n) or i
            # confirm the shift persists: the next hold_n points stay on the new side
            after = x[i : i + hold_n]
            after = after[np.isfinite(after)]
            shift = (np.median(after) - mu) * (1 if up else -1) if after.size else 0.0
            if shift > 2 * sigma and shift > np.log1p(min_effect):
                end = min(len(x), begin + hold_n)
                score[begin:end] = np.maximum(score[begin:end], 1.0 + min(gp if up else gn, 50) / h / 10)
                signed[begin:end] = 1.0 if up else -1.0
                mu = float(np.median(after))  # re-baseline after the shift
            gp = gn = 0.0
            start_p = start_n = i
            i += hold_n
            continue
        i += 1
    return DetectorResult("cusum", pd.Series(score, index=s.index), None,
                          pd.Series(signed, index=s.index))


THRESHOLDS = {
    "util_pct": 90.0,
    "err_ratio_pct": 0.5,
    "retrans_pct": 3.0,
    "http_err_pct": 2.0,
    "cpu_pct": 90.0,
    "mem_pct": 92.0,
}


def threshold(s: pd.Series, metric: str, attrs: dict | None = None) -> DetectorResult | None:
    attrs = attrs or {}
    limit = THRESHOLDS.get(metric)
    if metric in ("latency_p95_ms", "latency_ms") and attrs.get("slo_p95_ms"):
        limit = float(attrs["slo_p95_ms"])
    if metric == "probe_up":
        down = (s < 0.5).astype(float) * 1.5
        return DetectorResult("threshold", down.fillna(0.0), None, -down)
    if limit is None:
        return None
    score = (s / limit).where(s >= limit, 0.0).fillna(0.0)
    return DetectorResult("threshold", score, pd.Series(limit, index=s.index), score)


def all_univariate(s: pd.Series, metric: str, group: str, attrs: dict | None = None,
                   use_stl: bool = True) -> tuple[list[DetectorResult], pd.Series]:
    """Run every applicable univariate detector. Returns (results, profile)."""
    profile = seasonal_profile(s)
    relative = group in RELATIVE_GROUPS
    results = [seasonal(s, profile, relative), local(s, profile, relative)]
    if use_stl:
        r = stl(s)
        if r is not None:
            results.append(r)
    if group in ("traffic", "connections"):
        results.append(cusum(s, profile))
    t = threshold(s, metric, attrs)
    if t is not None:
        results.append(t)
    return results, profile
