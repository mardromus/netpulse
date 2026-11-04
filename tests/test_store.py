from datetime import datetime, timedelta, timezone

import pandas as pd

from netpulse.model import CATALOG, Entity, TruthEvent, spec_for
from netpulse.store import Store


def _df():
    t0 = pd.Timestamp("2026-11-04T00:00:00Z")
    return pd.DataFrame(
        {
            "ts": [t0, t0 + pd.Timedelta("5min"), t0],
            "entity": ["r1/eth0", "r1/eth0", "svc"],
            "metric": ["rx_bytes", "rx_bytes", "latency_p95_ms"],
            "value": [100.0, 400.0, 42.5],
        }
    )


def test_roundtrip_samples(tmp_path):
    s = Store(tmp_path / "x.db")
    assert s.write_samples(_df()) == 3
    out = s.read_samples()
    assert len(out) == 3
    assert str(out["ts"].dt.tz) == "UTC"
    assert out["ts"].iloc[0] == pd.Timestamp("2026-11-04T00:00:00Z")
    rx = s.read_samples(entity="r1/eth0", metric="rx_bytes")
    assert rx["value"].tolist() == [100.0, 400.0]
    assert s.series_keys() == [("r1/eth0", "rx_bytes"), ("svc", "latency_p95_ms")]
    assert s.count() == 3


def test_entities_truth_and_meta():
    s = Store(":memory:")
    s.write_entities([Entity("r1/eth0", "interface", {"speed_bps": 1e9})])
    assert s.read_entities()["r1/eth0"].speed_bps == 1e9
    start = datetime(2026, 11, 4, 10, tzinfo=timezone.utc)
    s.write_truth([TruthEvent("r1/eth0", "outage", start, start + timedelta(minutes=30), ["rx_bytes"])])
    t = s.read_truth()[0]
    assert t.kind == "outage" and t.metrics == ["rx_bytes"] and t.start == start
    s.set_meta("step_s", 300)
    assert s.get_meta("step_s") == 300
    assert s.get_meta("missing", "d") == "d"


def test_catalog_specs():
    assert CATALOG["rx_bytes"].kind == "counter"
    assert CATALOG["rx_bytes"].analysed_name == "rx_bps"
    rate = spec_for("rx_bps")
    assert rate is not None and rate.unit == "bit/s" and not rate.higher_is_worse
    assert spec_for("util_pct").group == "saturation"
    assert spec_for("nope") is None
