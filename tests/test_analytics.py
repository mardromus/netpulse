import numpy as np
import pandas as pd
import pytest

from netpulse.analytics import (
    capacity_forecast,
    heatmap,
    overview,
    peak_hour,
    summary,
    theil_sen,
    trend,
)
from netpulse.generate import FIREWALL, UPLINK_A, GenConfig, generate
from netpulse.prep import prepare

IDX = pd.date_range("2026-11-01", periods=24 * 12 * 10, freq="5min", tz="UTC")


def test_theil_sen_ignores_outliers():
    y = np.arange(20, dtype=float) * 2 + 5
    y[7] = 500.0
    slope, intercept = theil_sen(y)
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(5.0)


def test_summary_and_peak_hour():
    hours = IDX.hour.to_numpy()
    s = pd.Series(100 + 50 * np.cos(2 * np.pi * (hours - 14) / 24), index=IDX)
    st = summary(s)
    assert st["max"] == pytest.approx(150.0)
    assert st["count"] == len(s)
    assert peak_hour(s)["hour_utc"] == 14
    assert peak_hour(s)["quiet_hour_utc"] == 2
    hm = heatmap(s)
    assert hm.shape == (7, 24)


def test_trend_and_capacity_forecast():
    days = np.arange(len(IDX)) / 288.0
    util = pd.Series(40 + 3 * days, index=IDX)  # +3 points per day
    t = trend(util)
    assert t["pct_per_day"] > 0
    fc = capacity_forecast("r1", util, threshold=80)
    assert fc is not None
    assert fc.growth_pct_per_day == pytest.approx(3.0, rel=0.05)
    assert fc.days_to_threshold == pytest.approx((80 - fc.current_p95_pct) / fc.growth_pct_per_day)
    flat = capacity_forecast("r2", pd.Series(30.0, index=IDX))
    assert flat.days_to_threshold is None


def test_overview_on_generated_week():
    g = generate(GenConfig())
    p = prepare(g.samples, {e.name: e for e in g.entities})
    ov = overview(p)
    assert ov["period"]["days"] == pytest.approx(7.0, abs=0.01)
    assert UPLINK_A in ov["entities"]
    assert ov["top_talkers"][0]["entity"] in (UPLINK_A, "core-rtr-1/xe-0/0/2")
    assert any(c["entity"] == FIREWALL for c in ov["capacity"])
    assert ov["entities"][UPLINK_A]["trend"]["pct_per_day"] > 0  # +30% level shift
    assert {q["kind"] for q in ov["data_quality"]} >= {"gap", "counter_reset", "counter_wrap"}
