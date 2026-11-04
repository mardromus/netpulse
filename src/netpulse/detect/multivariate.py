"""Multivariate detection with Isolation Forest.

Each metric of an entity is first turned into a robust z-score against its own
time-of-day profile, so the forest sees "how unusual is this combination of
deviations" rather than raw magnitudes (bytes/s and milliseconds would
otherwise dominate each other). A combination can be anomalous even when no
single metric crosses its own threshold, e.g. slightly more retransmissions
*and* slightly higher latency *and* fewer requests at the same time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import DetectorResult, robust_sigma, seasonal_profile


def isolation_forest(frame: pd.DataFrame, seed: int = 0, quantile: float = 0.995) -> DetectorResult | None:
    cols = [c for c in frame.columns if frame[c].notna().sum() > 50 and frame[c].nunique() > 3]
    if len(cols) < 2 or len(frame) < 200:
        return None
    from sklearn.ensemble import IsolationForest

    feats = {}
    for c in cols:
        s = frame[c]
        prof = seasonal_profile(s)
        r = s - prof
        feats[c] = (r / robust_sigma(r.to_numpy())).clip(-25, 25)
    X = pd.DataFrame(feats).fillna(0.0)
    model = IsolationForest(n_estimators=200, contamination="auto", random_state=seed)
    model.fit(X.to_numpy())
    raw = -model.score_samples(X.to_numpy())  # higher = more anomalous
    base = np.median(raw)
    cut = np.quantile(raw, quantile)
    spread = max(cut - base, 1e-9)
    score = np.clip((raw - base) / spread, 0, None)
    return DetectorResult("iforest", pd.Series(score, index=frame.index))
