"""Shared pieces for detectors.

Every detector turns one series into a non-negative *score* series where
``score >= 1`` means "anomalous by this detector's standard" (so scores from
different methods can be compared and voted on). Detectors also expose the
*baseline* they compared against, which the report draws as the expected band.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

MAD_TO_SIGMA = 1.4826


@dataclass
class DetectorResult:
    name: str
    score: pd.Series  # >= 0, 1.0 = threshold
    baseline: pd.Series | None = None
    signed: pd.Series | None = None  # positive = above baseline


def robust_sigma(x: np.ndarray | pd.Series, floor: float = 1e-9) -> float:
    v = np.asarray(x, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return floor
    mad = np.median(np.abs(v - np.median(v)))
    sigma = MAD_TO_SIGMA * mad
    if sigma <= floor:  # mostly-constant series: fall back to mean absolute deviation
        sigma = float(np.mean(np.abs(v - np.median(v)))) * 1.2533
    return max(float(sigma), floor)


def steps_per_day(index: pd.DatetimeIndex) -> int:
    if len(index) < 2:
        return 1
    step = (index[1] - index[0]).total_seconds()
    return max(1, int(round(86400 / step)))


def seasonal_profile(s: pd.Series) -> pd.Series:
    """Expected value for every point: the median of the same time-of-day slot on
    all *other* days. Leaving the current day out means an incident does not
    pull its own baseline up. Days of the other type (weekday vs weekend) are
    rescaled by the ratio of the two levels before they vote, so a quiet
    weekend is compared with weekends while still having enough days for a
    robust median.

    Series shorter than two days fall back to a trailing rolling median."""
    n_day = steps_per_day(s.index)
    n = len(s)
    if n < 2 * n_day:
        return s.rolling(min(24, max(n, 1)), min_periods=1).median().shift(1).bfill()
    t0 = s.index[0].normalize()
    offset = int(round((s.index[0] - t0).total_seconds() / (86400 / n_day)))
    total = offset + n
    n_days = -(-total // n_day)
    flat = np.full(n_days * n_day, np.nan)
    flat[offset : offset + n] = s.to_numpy(dtype=float)
    mat = flat.reshape(n_days, n_day)  # days x slots
    day_starts = pd.date_range(t0, periods=n_days, freq="1D", tz=s.index.tz)
    weekend = np.asarray(day_starts.dayofweek >= 5)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        level = np.nanmedian(mat, axis=1)  # typical level of each day
        type_level = {
            we: np.nanmedian(level[weekend == we]) if (weekend == we).any() else np.nan
            for we in (False, True)
        }
        out = np.full_like(mat, np.nan)
        for d in range(n_days):
            others = [k for k in range(n_days) if k != d]
            if not others:
                continue
            rows = []
            for k in others:
                if weekend[k] == weekend[d]:
                    rows.append(mat[k])
                else:
                    a, b = type_level[weekend[d]], type_level[weekend[k]]
                    scale = a / b if np.isfinite(a) and np.isfinite(b) and b != 0 else 1.0
                    rows.append(mat[k] * scale)
            out[d] = np.nanmedian(np.vstack(rows), axis=0)
    prof = pd.Series(out.reshape(-1)[offset : offset + n], index=s.index)
    return prof.fillna(s.rolling(24, min_periods=1).median())
