import pandas as pd
import pytest
from fastapi.testclient import TestClient

from netpulse.generate import GenConfig, generate
from netpulse.server.app import create_app
from netpulse.server.live import LiveCollector, OnlineScorer
from netpulse.store import Store


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    path = tmp_path_factory.mktemp("srv") / "d.db"
    g = generate(GenConfig())
    s = Store(path)
    s.write_entities(g.entities)
    s.write_samples(g.samples)
    s.write_truth(g.truth)
    with TestClient(create_app(path)) as c:
        yield c


def test_dashboard_and_summary(client):
    assert "netpulse dashboard" in client.get("/").text
    s = client.get("/api/summary").json()
    assert s["ready"] and s["counts"]["incidents"] == 6 and s["evaluation"]["f1"] == 1.0


def test_incidents_entities_and_series(client):
    incs = client.get("/api/incidents").json()
    assert {i["kind"] for i in incs} >= {"outage", "traffic_surge"}
    ents = client.get("/api/entities").json()
    fw = next(e for e in ents if e["name"] == "edge-fw-1/eth1")
    assert "util_pct" in fw["metrics"]
    r = client.get("/api/series", params={"entity": "edge-fw-1/eth1", "metric": "util_pct", "points": 200})
    body = r.json()
    assert r.status_code == 200 and len(body["ts"]) <= 220 and body["anomalies"]["ts"]
    assert client.get("/api/series", params={"entity": "x", "metric": "y"}).status_code == 404


def test_ask_endpoint(client):
    a = client.post("/api/ask", json={"question": "what happened to payments-svc?"}).json()
    assert a["engine"] == "keyword" and a["incidents"]
    assert client.post("/api/ask", json={}).status_code == 400


def test_report_and_reanalyze(client):
    html = client.get("/api/report").text
    assert html.startswith("<!DOCTYPE html>") and "INC-01" in html
    assert client.post("/api/analyze").json()["incidents"] == 6
    assert client.get("/api/live/recent").status_code == 404


class FakeCollector:
    name = "fake"

    def __init__(self):
        self.n = 0

    def entities(self):
        return []

    def collect(self):
        self.n += 1
        value = 1000.0 * self.n if self.n < 30 else 1000.0 * 29 + 5e6 * (self.n - 29)
        return [("lab/eth0", "rx_bytes", value), ("lab", "cpu_pct", 20.0 + (self.n % 3))]


def test_online_scorer_flags_sudden_jump():
    sc = OnlineScorer()
    t0 = pd.Timestamp("2026-11-04T00:00:00Z")
    pts = [sc.update(t0 + pd.Timedelta(seconds=i), "h", "cpu_pct", 20.0 + (i % 3)) for i in range(40)]
    assert not any(p.anomaly for p in pts if p)
    p = sc.update(t0 + pd.Timedelta(seconds=41), "h", "cpu_pct", 95.0)
    assert p.anomaly and p.z > 6
    assert sc.update(t0, "h/eth0", "rx_bytes", 10.0) is None  # first counter value: no rate yet


def test_live_collector_writes_and_scores(tmp_path):
    store = Store(tmp_path / "live.db")
    live = LiveCollector([FakeCollector()], store, interval=0.01)
    got = []
    live.listeners.append(got.extend)
    t = pd.Timestamp("2026-11-04T00:00:00Z")
    for i in range(35):  # drive the clock ourselves for a deterministic test
        readings = live.collectors[0].collect()
        for e, m, v in readings:
            p = live.scorer.update(t + pd.Timedelta(seconds=5 * i), e, m, v)
            if p:
                live.recent.append(p)
                if p.anomaly:
                    live.anomalies.append(p)
    assert any(p.metric == "rx_bps" and p.anomaly for p in live.anomalies)
    pts = live.poll_once()
    assert store.count() > 0 and got == pts
    app = create_app(tmp_path / "live.db", collectors=[FakeCollector()])
    with TestClient(app) as c:
        assert c.get("/api/summary").json()["live"] is True
        assert "points" in c.get("/api/live/recent").json()
