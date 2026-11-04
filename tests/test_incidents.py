import pandas as pd
import pytest

from netpulse.evaluate import evaluate
from netpulse.generate import FIREWALL, GATEWAY, PAYMENTS, UPLINK_A, UPLINK_B
from netpulse.incidents import build_incidents, group_segments
from netpulse.model import Segment

T = pd.Timestamp("2026-11-04T10:00:00Z")


def seg(entity, metric, start_min, end_min, peak, base, direction="up", dets=("seasonal",), score=5.0):
    return Segment(entity, metric, T + pd.Timedelta(minutes=start_min), T + pd.Timedelta(minutes=end_min),
                   T + pd.Timedelta(minutes=start_min), peak, base, score, direction, list(dets), 3)


def test_grouping_by_time_and_device():
    segs = [
        seg("r1/xe-1", "rx_bps", 0, 30, 1e9, 5e8),
        seg("r1/xe-2", "rx_bps", 5, 30, 1e3, 5e8, "down"),  # same device
        seg("fw/eth1", "rx_bps", 10, 20, 9e8, 1e8),  # overlaps but unrelated device
        seg("r1/xe-1", "rx_err_ps", 200, 220, 50, 0),  # same entity, much later
    ]
    groups = group_segments(segs)
    assert sorted(len(g) for g in groups) == [1, 1, 2]


def test_classification_shapes():
    out = build_incidents([seg("r1/xe-2", "rx_bps", 0, 30, 1e6, 3e9, "down"),
                           seg("r1/xe-1", "util_pct", 0, 30, 98, 50)])
    assert out[0].kind == "outage" and out[0].severity == "critical"
    assert "failed over" in out[0].probable_cause
    out = build_incidents([seg("svc", "latency_p95_ms", 0, 60, 450, 60),
                           seg("svc", "http_err_pct", 0, 60, 6.2, 0.1),
                           seg("svc", "cpu_pct", 0, 60, 94, 32)])
    assert out[0].kind == "latency_degradation" and out[0].severity == "critical"
    out = build_incidents([seg("r1/xe-1", "rx_bps", 0, 180, 5.5e9, 4.2e9, dets=("cusum", "stl"))])
    assert out[0].kind == "level_shift"
    assert out[0].id == "INC-01"


@pytest.fixture(scope="module")
def week(generated, week_result):
    return generated, week_result.incidents


def test_generated_week_yields_one_incident_per_event(week):
    g, incs = week
    kinds = {i.kind: i for i in incs}
    assert set(kinds) == {"traffic_surge", "link_degradation", "outage", "latency_degradation",
                          "connection_leak", "level_shift"}
    assert kinds["traffic_surge"].entities[0] == FIREWALL
    assert kinds["outage"].entities[:2] in ([UPLINK_B, UPLINK_A], [UPLINK_A, UPLINK_B])
    assert kinds["latency_degradation"].entities == [PAYMENTS]
    assert kinds["connection_leak"].entities == [GATEWAY]
    assert "flood" in kinds["traffic_surge"].probable_cause


def test_evaluation_scores(week):
    g, incs = week
    ev = evaluate(g.truth, incs)
    assert ev.recall == 1.0
    assert ev.precision == 1.0
    assert ev.kind_accuracy == 1.0
    assert ev.f1 == 1.0
    d = ev.to_dict()
    assert d["events"] == 7 and d["false_alarms"] == 0
    assert all(abs(m["delay_min"]) <= 15 for m in d["matches"])
