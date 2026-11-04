"""Bring existing data in: CSV files and Prometheus metrics.

CSV can be *long* (``ts,entity,metric,value``) or *wide*: a ``ts`` column, an
optional ``entity`` column and one column per metric. Column names such as
``ifHCInOctets`` (SNMP) are mapped onto the catalog where possible.

Prometheus text exposition (``/metrics`` endpoints, node_exporter) is parsed and
common exporter metrics are mapped, e.g.
``node_network_receive_bytes_total{device="eth0"}`` -> ``(<instance>/eth0, rx_bytes)``.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pandas as pd

ALIASES = {
    # SNMP IF-MIB
    "ifhcinoctets": "rx_bytes",
    "ifhcoutoctets": "tx_bytes",
    "ifinoctets": "rx_bytes",
    "ifoutoctets": "tx_bytes",
    "ifhcinucastpkts": "rx_packets",
    "ifhcoutucastpkts": "tx_packets",
    "ifinerrors": "rx_errors",
    "ifouterrors": "tx_errors",
    "ifindiscards": "rx_drops",
    "ifoutdiscards": "tx_drops",
    # common spellings
    "bytes_in": "rx_bytes",
    "bytes_out": "tx_bytes",
    "latency": "latency_ms",
    "p95_latency_ms": "latency_p95_ms",
    "established": "conn_established",
}

PROM_MAP = {
    "node_network_receive_bytes_total": ("device", "rx_bytes"),
    "node_network_transmit_bytes_total": ("device", "tx_bytes"),
    "node_network_receive_packets_total": ("device", "rx_packets"),
    "node_network_transmit_packets_total": ("device", "tx_packets"),
    "node_network_receive_errs_total": ("device", "rx_errors"),
    "node_network_transmit_errs_total": ("device", "tx_errors"),
    "node_network_receive_drop_total": ("device", "rx_drops"),
    "node_network_transmit_drop_total": ("device", "tx_drops"),
    "node_netstat_Tcp_OutSegs": (None, "tcp_out_segs"),
    "node_netstat_Tcp_RetransSegs": (None, "tcp_retrans_segs"),
    "node_netstat_Tcp_CurrEstab": (None, "conn_established"),
    "node_netstat_Tcp_AttemptFails": (None, "tcp_attempt_fails"),
}


def _metric_name(col: str) -> str:
    key = re.sub(r"[^a-z0-9_]", "", col.strip().lower())
    return ALIASES.get(key, col.strip())


def read_csv(path: str | Path, entity: str | None = None) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("ts") or cols.get("timestamp") or cols.get("time")
    if not tcol:
        raise ValueError("CSV needs a ts / timestamp / time column")
    ts = df[tcol]
    ts = pd.to_datetime(ts, unit="s", utc=True) if pd.api.types.is_numeric_dtype(ts) else pd.to_datetime(ts, utc=True)
    if {"entity", "metric", "value"} <= set(cols):
        out = pd.DataFrame({"ts": ts, "entity": df[cols["entity"]].astype(str),
                            "metric": df[cols["metric"]].map(_metric_name),
                            "value": pd.to_numeric(df[cols["value"]], errors="coerce")})
        return out.dropna(subset=["value"])
    ecol = cols.get("entity") or cols.get("interface") or cols.get("host")
    ent = df[ecol].astype(str) if ecol else pd.Series(entity or Path(path).stem, index=df.index)
    frames = []
    for c in df.columns:
        if c in (tcol, ecol):
            continue
        vals = pd.to_numeric(df[c], errors="coerce")
        if vals.notna().sum() == 0:
            continue
        frames.append(pd.DataFrame({"ts": ts, "entity": ent, "metric": _metric_name(c), "value": vals}))
    if not frames:
        raise ValueError("no numeric metric columns found")
    return pd.concat(frames, ignore_index=True).dropna(subset=["value"])


_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eEInfa]+)(?:\s+(\d+))?$")
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse_prometheus(text: str, instance: str = "host", ts: pd.Timestamp | None = None) -> pd.DataFrame:
    ts = ts or pd.Timestamp.now(tz="UTC").floor("s")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name, labels_s, value_s, stamp = m.groups()
        try:
            value = float(value_s)
        except ValueError:
            continue
        labels = dict(_LABEL.findall(labels_s or ""))
        when = pd.Timestamp(int(stamp), unit="ms", tz="UTC") if stamp else ts
        inst = labels.get("instance", instance)
        if name in PROM_MAP:
            label, metric = PROM_MAP[name]
            if label and labels.get(label) in ("lo", None):
                continue
            entity = f"{inst}/{labels[label]}" if label else inst
        else:
            metric = name
            entity = inst
            if "device" in labels:
                entity = f"{inst}/{labels['device']}"
        rows.append((when, entity, metric, value))
    return pd.DataFrame(rows, columns=["ts", "entity", "metric", "value"])


def scrape_prometheus(url: str, timeout: float = 5.0) -> pd.DataFrame:
    host = httpx.URL(url).host
    r = httpx.get(url, timeout=timeout)
    r.raise_for_status()
    return parse_prometheus(r.text, instance=host)
