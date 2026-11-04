import numpy as np
import pandas as pd

from netpulse.generate import FIREWALL, OFFICE, PAYMENTS, UPLINK_B, GenConfig, generate
from netpulse.prep import prepare


def test_generator_is_deterministic_and_complete():
    a = generate(GenConfig(seed=3))
    b = generate(GenConfig(seed=3))
    pd.testing.assert_frame_equal(a.samples, b.samples)
    assert len(a.entities) == 6
    kinds = sorted(t.kind for t in a.truth)
    assert kinds == sorted(
        ["traffic_surge", "link_degradation", "outage", "traffic_shift", "latency_degradation",
         "connection_leak", "level_shift"]
    )
    # 7 days of 5-minute samples
    n = a.samples.query("entity == @PAYMENTS and metric == 'latency_p95_ms'").shape[0]
    assert n == 7 * 288


def test_generated_data_has_real_world_quirks():
    g = generate(GenConfig())
    p = prepare(g.samples, {e.name: e for e in g.entities})
    kinds = {(q.entity, q.kind) for q in p.quality}
    assert (OFFICE, "counter_wrap") in kinds  # 32-bit packet counter
    assert (FIREWALL, "counter_reset") in kinds  # operator cleared counters
    assert ("api-gateway", "gap") in kinds  # collector missed an hour


def test_injected_incidents_are_visible_in_prepared_series():
    g = generate(GenConfig())
    p = prepare(g.samples, {e.name: e for e in g.entities})
    truth = {t.kind: t for t in g.truth}
    lat = p.series(PAYMENTS, "latency_p95_ms")
    w = truth["latency_degradation"]
    inside = lat[(lat.index >= w.start) & (lat.index < w.end)].median()
    assert inside > 4 * lat.median()
    out = truth["outage"]
    rx = p.series(UPLINK_B, "rx_bps")
    assert rx[(rx.index >= out.start) & (rx.index < out.end)].max() < 0.01 * rx.median()
    util = p.series(FIREWALL, "util_pct")
    assert util.max() > 90


def test_quiet_mode_has_no_incidents():
    g = generate(GenConfig(days=2, incidents=False))
    assert g.truth == []
    assert np.isfinite(g.samples["value"]).all()
