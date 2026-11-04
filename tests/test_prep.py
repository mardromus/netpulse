import numpy as np
import pandas as pd
import pytest

from netpulse.model import Entity
from netpulse.prep import counter_to_rate, infer_step, prepare, regularize

T0 = pd.Timestamp("2026-11-04T00:00:00Z")


def _idx(n, step="60s"):
    return pd.date_range(T0, periods=n, freq=step, tz="UTC")


def test_counter_rate_is_per_second():
    s = pd.Series([0, 600, 1200, 1800], index=_idx(4))
    rate, notes = counter_to_rate(s)
    assert rate.tolist() == [10.0, 10.0, 10.0]
    assert notes == []


def test_counter_wrap_is_corrected():
    near = 2**32 - 300
    s = pd.Series([near - 1200, near - 600, near, 300], index=_idx(4))
    rate, notes = counter_to_rate(s, "r1", "rx_bytes")
    assert rate.iloc[-1] == pytest.approx(10.0)
    assert notes[0].kind == "counter_wrap"


def test_counter_reset_becomes_gap_not_spike():
    s = pd.Series([5_000_000, 5_000_600, 5_001_200, 120, 720], index=_idx(5))
    rate, notes = counter_to_rate(s, "r1", "rx_bytes")
    assert np.isnan(rate.iloc[2])
    assert rate.iloc[3] == pytest.approx(10.0)
    assert notes[0].kind == "counter_reset"


def test_regularize_interpolates_short_and_reports_long_gaps():
    idx = _idx(20)
    s = pd.Series(np.arange(20, dtype=float), index=idx)
    s = s.drop(idx[3:5]).drop(idx[10:15])
    out, notes = regularize(s, pd.Timedelta("60s"), idx[0], idx[-1], "e", "m")
    assert len(out) == 20
    assert out.iloc[3] == pytest.approx(3.0)  # short hole interpolated
    assert out.iloc[12] != out.iloc[12]  # long hole kept as NaN
    assert [n.kind for n in notes] == ["gap"]
    assert "5 intervals" in notes[0].detail


def test_infer_step_and_prepare_derives_kpis():
    idx = _idx(10, "300s")
    rows = []
    for i, t in enumerate(idx):
        rows += [
            (t, "r1/eth0", "rx_bytes", i * 300 * 1_000_000),  # 8 Mbit/s
            (t, "r1/eth0", "tx_bytes", i * 300 * 500_000),
            (t, "r1/eth0", "rx_packets", i * 300 * 1000),
            (t, "r1/eth0", "tx_packets", i * 300 * 1000),
            (t, "r1/eth0", "rx_errors", i * 300 * 2),
            (t, "svc", "latency_p95_ms", 40.0 + i),
        ]
    df = pd.DataFrame(rows, columns=["ts", "entity", "metric", "value"])
    assert infer_step(df["ts"]) == pd.Timedelta("300s")
    p = prepare(df, {"r1/eth0": Entity("r1/eth0", "interface", {"speed_bps": 1e8})})
    f = p.frames["r1/eth0"]
    assert f["rx_bps"].dropna().iloc[0] == pytest.approx(8e6)
    assert f["util_pct"].dropna().iloc[0] == pytest.approx(8.0)
    assert f["err_ratio_pct"].dropna().iloc[0] == pytest.approx(0.1)
    assert p.has("svc", "latency_p95_ms") and not p.has("svc", "rx_bps")
    assert ("r1/eth0", "util_pct") in set(p.keys())
