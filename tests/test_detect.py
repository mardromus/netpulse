import numpy as np
import pandas as pd
import pytest

from netpulse.detect import DetectOptions, detect_series
from netpulse.detect.base import robust_sigma, seasonal_profile
from netpulse.detect.methods import cusum, threshold

IDX = pd.date_range("2026-10-26", periods=288 * 7, freq="5min", tz="UTC")
RNG = np.random.default_rng(1)


def diurnal(level=100.0, noise=0.04):
    h = np.asarray(IDX.hour + IDX.minute / 60.0)
    base = level * (1 + 0.5 * np.cos(2 * np.pi * (h - 14) / 24))
    return pd.Series(base * RNG.lognormal(0, noise, len(IDX)), index=IDX)


def test_robust_sigma_ignores_outliers():
    x = np.r_[RNG.normal(0, 1, 1000), [1e6] * 10]
    assert robust_sigma(x) == pytest.approx(1.0, rel=0.1)


def test_seasonal_profile_follows_daily_cycle_and_ignores_own_incident():
    s = diurnal()
    s.iloc[600:612] *= 8  # incident on day 3
    prof = seasonal_profile(s)
    clean = diurnal(noise=0.0)
    assert (prof / clean).iloc[100:].median() == pytest.approx(1.0, rel=0.08)
    # the incident does not leak into its own baseline
    assert prof.iloc[600:612].max() < 2 * clean.iloc[600:612].max()


def test_spike_is_detected_as_short_segment():
    s = diurnal()
    s.iloc[1000:1006] *= 4
    d = detect_series(s, "e", "rx_bps")
    flagged = np.flatnonzero(d.flags.to_numpy())
    assert flagged.min() >= 999 and flagged.max() <= 1007
    assert len(flagged) >= 5


def test_drop_in_errors_is_not_an_anomaly():
    s = diurnal(level=5.0)
    s.iloc[1000:1010] = 0.0  # fewer errors than usual is good news
    d = detect_series(s, "e", "rx_err_ps")
    assert not d.flags.iloc[1000:1010].any()


def test_tiny_absolute_change_is_ignored():
    s = pd.Series(RNG.poisson(0.2, len(IDX)) / 300.0, index=IDX)
    d = detect_series(s, "e", "rx_err_ps")
    assert not d.flags.any()


def test_cusum_finds_level_shift_start():
    s = diurnal()
    s.iloc[700:] *= 1.35
    prof = seasonal_profile(diurnal(noise=0.0))
    r = cusum(s, prof)
    hits = np.flatnonzero(r.score.to_numpy() >= 1)
    assert hits.size and 690 <= hits[0] <= 730


def test_threshold_rules():
    s = pd.Series([50.0, 95.0, 99.0, 40.0], index=IDX[:4])
    r = threshold(s, "util_pct")
    assert r.score.tolist() == pytest.approx([0, 95 / 90, 99 / 90, 0])
    lat = threshold(pd.Series([100.0, 250.0], index=IDX[:2]), "latency_p95_ms", {"slo_p95_ms": 200})
    assert lat.score.iloc[1] > 1 and lat.score.iloc[0] == 0
    up = threshold(pd.Series([1.0, 0.0], index=IDX[:2]), "probe_up")
    assert up.score.tolist() == [0.0, 1.5]
    assert threshold(s, "rx_bps") is None


@pytest.fixture(scope="module")
def generated_detection(generated, week_result):
    return generated, week_result.prepared, week_result.detection


def test_every_injected_incident_is_found(generated_detection):
    g, _, d = generated_detection
    for t in g.truth:
        hit = [s for s in d.segments
               if s.entity == t.entity and s.start < t.end + pd.Timedelta("15min")
               and s.end > t.start - pd.Timedelta("15min")]
        assert hit, f"missed {t.kind} on {t.entity}"


def test_no_false_alarms_outside_incidents(generated_detection):
    g, _, d = generated_detection
    for s in d.segments:
        near = [t for t in g.truth if s.start < t.end + pd.Timedelta("1h")
                and s.end > t.start - pd.Timedelta("1h")]
        assert near, f"false alarm: {s.entity} {s.metric} at {s.start}"


def test_sensitivity_scales_thresholds():
    s = diurnal()
    s.iloc[1000:1004] *= 1.3  # a modest bump
    strict = detect_series(s, "e", "rx_bps", opts=DetectOptions(sensitivity=0.5))
    keen = detect_series(s, "e", "rx_bps", opts=DetectOptions(sensitivity=2.0))
    assert keen.flags.iloc[1000:1004].sum() >= strict.flags.iloc[1000:1004].sum()
    assert not strict.flags.iloc[1000:1004].any()
    assert keen.flags.iloc[1000:1004].any()
