"""Core data model: the metric catalog and the objects that flow through the pipeline.

Raw data is always a *long* DataFrame with columns::

    ts (UTC timestamp) | entity (str) | metric (str) | value (float)

``entity`` is whatever produces the numbers: an interface (``core-rtr-1/xe-0/0/1``),
a host, or a service (``payments-svc``). Counters (monotonically increasing totals
such as ``rx_bytes``) are converted to rates by :mod:`netpulse.prep`; gauges
(instantaneous values such as ``latency_p95_ms``) are used as they are.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd

Kind = Literal["counter", "gauge"]
Group = Literal["traffic", "errors", "latency", "saturation", "connections", "availability"]


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: Kind
    unit: str
    group: Group
    description: str
    higher_is_worse: bool = True
    rate_name: str | None = None  # name of the derived per-second series for counters
    rate_unit: str | None = None
    rate_scale: float = 1.0  # e.g. 8 to turn bytes/s into bits/s

    @property
    def analysed_name(self) -> str:
        return self.rate_name or self.name


def _c(name, unit, group, desc, rate, rate_unit, scale=1.0, worse=True):
    return MetricSpec(name, "counter", unit, group, desc, worse, rate, rate_unit, scale)


def _g(name, unit, group, desc, worse=True):
    return MetricSpec(name, "gauge", unit, group, desc, worse)


CATALOG: dict[str, MetricSpec] = {
    m.name: m
    for m in [
        _c("rx_bytes", "bytes", "traffic", "Bytes received", "rx_bps", "bit/s", 8.0, worse=False),
        _c("tx_bytes", "bytes", "traffic", "Bytes sent", "tx_bps", "bit/s", 8.0, worse=False),
        _c("rx_packets", "packets", "traffic", "Packets received", "rx_pps", "pkt/s", worse=False),
        _c("tx_packets", "packets", "traffic", "Packets sent", "tx_pps", "pkt/s", worse=False),
        _c("rx_errors", "packets", "errors", "Receive errors (CRC, frame)", "rx_err_ps", "err/s"),
        _c("tx_errors", "packets", "errors", "Transmit errors", "tx_err_ps", "err/s"),
        _c("rx_drops", "packets", "errors", "Packets dropped on receive", "rx_drop_ps", "pkt/s"),
        _c("tx_drops", "packets", "errors", "Packets dropped on transmit", "tx_drop_ps", "pkt/s"),
        _c("tcp_out_segs", "segments", "traffic", "TCP segments sent", "tcp_out_sps", "seg/s",
           worse=False),
        _c("tcp_retrans_segs", "segments", "errors", "TCP segments retransmitted",
           "tcp_retrans_sps", "seg/s"),
        _c("tcp_attempt_fails", "count", "errors", "Failed TCP connection attempts",
           "tcp_fail_ps", "1/s"),
        _c("tcp_estab_resets", "count", "errors", "Established TCP connections reset",
           "tcp_reset_ps", "1/s"),
        _c("http_requests", "requests", "traffic", "HTTP requests served", "http_rps", "req/s",
           worse=False),
        _c("http_5xx", "requests", "errors", "HTTP 5xx responses", "http_5xx_ps", "req/s"),
        _g("conn_established", "connections", "connections", "Established TCP connections"),
        _g("latency_p95_ms", "ms", "latency", "95th percentile latency"),
        _g("latency_ms", "ms", "latency", "Probe round-trip latency"),
        _g("probe_up", "bool", "availability", "1 when the probe succeeded", worse=False),
        _g("cpu_pct", "%", "saturation", "CPU utilisation"),
        _g("mem_pct", "%", "saturation", "Memory utilisation"),
    ]
}

# Metrics computed from others by netpulse.prep.derive()
DERIVED: dict[str, MetricSpec] = {
    m.name: m
    for m in [
        _g("util_pct", "%", "saturation", "Link utilisation (max of rx/tx vs link speed)"),
        _g("err_ratio_pct", "%", "errors", "Errors + drops as % of packets"),
        _g("retrans_pct", "%", "errors", "TCP retransmissions as % of segments sent"),
        _g("http_err_pct", "%", "errors", "HTTP 5xx as % of requests"),
    ]
}


def spec_for(metric: str) -> MetricSpec | None:
    """Spec for a raw, rate or derived metric name."""
    if metric in CATALOG:
        return CATALOG[metric]
    if metric in DERIVED:
        return DERIVED[metric]
    for spec in CATALOG.values():
        if spec.rate_name == metric:
            return MetricSpec(metric, "gauge", spec.rate_unit or spec.unit, spec.group,
                              spec.description + " per second", spec.higher_is_worse)
    return None


def unit_for(metric: str) -> str:
    s = spec_for(metric)
    return s.unit if s else ""


def is_counter(metric: str) -> bool:
    s = CATALOG.get(metric)
    return bool(s and s.kind == "counter")


# ---------------------------------------------------------------------------
# pipeline objects
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    name: str
    kind: str = "interface"  # interface | host | service
    attrs: dict = field(default_factory=dict)  # e.g. {"speed_bps": 1e10, "site": "pune"}

    @property
    def speed_bps(self) -> float | None:
        v = self.attrs.get("speed_bps")
        return float(v) if v else None


@dataclass
class TruthEvent:
    """A labelled, injected anomaly (synthetic data) used for evaluation."""

    entity: str
    kind: str
    start: datetime
    end: datetime
    metrics: list[str]
    note: str = ""


@dataclass
class Segment:
    """A contiguous anomalous stretch of one (entity, metric) series."""

    entity: str
    metric: str
    start: pd.Timestamp
    end: pd.Timestamp
    peak_ts: pd.Timestamp
    peak_value: float
    baseline: float
    score: float
    direction: str  # "up" | "down"
    detectors: list[str]
    points: int

    @property
    def deviation_pct(self) -> float:
        if self.baseline == 0:
            return float("inf") if self.peak_value else 0.0
        return (self.peak_value - self.baseline) / abs(self.baseline) * 100.0


@dataclass
class Incident:
    id: str
    kind: str  # traffic_surge | outage | link_degradation | latency_degradation | ...
    title: str
    severity: str  # low | medium | high | critical
    start: pd.Timestamp
    end: pd.Timestamp
    entities: list[str]
    segments: list[Segment]
    probable_cause: str
    impact: str
    recommendation: str
    score: float = 0.0

    @property
    def duration_min(self) -> float:
        return max((self.end - self.start).total_seconds() / 60.0, 0.0)

    @property
    def metrics(self) -> list[str]:
        seen: list[str] = []
        for s in self.segments:
            if s.metric not in seen:
                seen.append(s.metric)
        return seen

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "severity": self.severity,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_min": round(self.duration_min, 1),
            "entities": self.entities,
            "metrics": self.metrics,
            "probable_cause": self.probable_cause,
            "impact": self.impact,
            "recommendation": self.recommendation,
            "score": round(self.score, 2),
            "segments": [],
        }
        for s in self.segments:
            sd = asdict(s)
            for k in ("start", "end", "peak_ts"):
                sd[k] = sd[k].isoformat()
            sd["deviation_pct"] = round(s.deviation_pct, 1) if s.baseline else None
            d["segments"].append(sd)
        return d


SEVERITIES = ["low", "medium", "high", "critical"]


def severity_rank(sev: str) -> int:
    return SEVERITIES.index(sev) if sev in SEVERITIES else 0
