import http.server
import socket
import threading

import pandas as pd
import pytest

from netpulse import collect
from netpulse.collect.importers import parse_prometheus, read_csv
from netpulse.collect.probes import HttpProbe, TcpProbe, probe_for
from netpulse.collect.system import HostCollector, InterfaceCollector, read_proc_snmp
from netpulse.store import Store


def test_interface_and_host_collectors_return_catalog_metrics():
    ic = InterfaceCollector(skip_loopback=False)
    readings = ic.collect()
    assert readings, "psutil should see at least one interface"
    metrics = {m for _, m, _ in readings}
    assert {"rx_bytes", "tx_bytes", "rx_packets"} <= metrics
    host = HostCollector().collect()
    assert any(m == "cpu_pct" for _, m, _ in host)


def test_proc_snmp_parser(tmp_path):
    p = tmp_path / "snmp"
    p.write_text("Ip: Forwarding\nIp: 1\nTcp: RtoAlgorithm OutSegs RetransSegs\nTcp: 1 1000 7\n")
    assert read_proc_snmp(str(p)) == {"RtoAlgorithm": 1.0, "OutSegs": 1000.0, "RetransSegs": 7.0}
    assert read_proc_snmp(str(tmp_path / "missing")) == {}


@pytest.fixture
def http_server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code = 503 if self.path == "/fail" else 200
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


def test_probes(http_server):
    up = dict(((m, v) for _, m, v in TcpProbe("127.0.0.1", http_server).collect()))
    assert up["probe_up"] == 1.0 and up["latency_ms"] >= 0
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()
    down = dict(((m, v) for _, m, v in TcpProbe("127.0.0.1", closed_port, 0.5).collect()))
    assert down == {"probe_up": 0.0}
    hp = HttpProbe(f"http://127.0.0.1:{http_server}/fail")
    hp.collect()
    r = dict(((m, v) for _, m, v in hp.collect()))
    assert r["http_requests"] == 2 and r["http_5xx"] == 2 and r["probe_up"] == 0.0
    assert isinstance(probe_for("dns://localhost"), object)
    with pytest.raises(ValueError):
        probe_for("ftp://x")


def test_run_writes_samples(tmp_path):
    store = Store(tmp_path / "live.db")
    n = collect.run([InterfaceCollector(skip_loopback=False)], store, interval_s=0.01, max_rounds=2)
    assert n > 0 and store.count() == n
    assert store.get_meta("source") == "live"


def test_prometheus_parser_maps_node_exporter():
    text = """
# HELP node_network_receive_bytes_total Network device statistic receive_bytes.
node_network_receive_bytes_total{device="eth0"} 1.5e+09
node_network_receive_bytes_total{device="lo"} 42
node_netstat_Tcp_RetransSegs 1234
http_requests_total{code="500",instance="api:9100"} 7 1793750400000
"""
    df = parse_prometheus(text, instance="node1")
    rows = {(e, m): v for _, e, m, v in df.itertuples(index=False)}
    assert rows[("node1/eth0", "rx_bytes")] == 1.5e9
    assert ("node1/lo", "rx_bytes") not in rows
    assert rows[("node1", "tcp_retrans_segs")] == 1234
    assert rows[("api:9100", "http_requests_total")] == 7
    assert df["ts"].iloc[-1] == pd.Timestamp("2026-11-04T00:00:00Z")


def test_csv_long_and_wide(tmp_path):
    long = tmp_path / "long.csv"
    long.write_text("ts,entity,metric,value\n2026-11-04T00:00:00Z,r1,rx_bytes,10\n")
    assert read_csv(long)["metric"].tolist() == ["rx_bytes"]
    wide = tmp_path / "wide.csv"
    wide.write_text("timestamp,interface,ifHCInOctets,ifInErrors\n1793750400,ge-0/0/1,100,0\n")
    df = read_csv(wide)
    assert set(df["metric"]) == {"rx_bytes", "rx_errors"}
    assert df["entity"].iloc[0] == "ge-0/0/1"
    assert df["ts"].iloc[0] == pd.Timestamp("2026-11-04T00:00:00Z")
