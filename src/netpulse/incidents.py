"""From anomalous segments to incidents an operator can act on.

Many segments describe one event: a DDoS raises bandwidth, packet rate, drops,
error ratio and utilisation on the same interface at the same time. Segments
are grouped when they overlap in time (with a small tolerance) and belong to
the same entity or the same device (``core-rtr-1/xe-0/0/1`` and
``core-rtr-1/xe-0/0/2`` share ``core-rtr-1``, which is how an uplink outage and
the failover traffic on the other uplink end up in one incident).

Each incident is then classified from the *shape* of its segments (which
metrics moved, in which direction, by how much, for how long) and given a
severity, probable cause, impact statement and recommendation.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .model import Incident, Segment, severity_rank, spec_for

TOLERANCE = pd.Timedelta("10min")


def _device(entity: str) -> str:
    return entity.split("/", 1)[0]


def group_segments(segments: list[Segment], tolerance: pd.Timedelta = TOLERANCE) -> list[list[Segment]]:
    groups: list[list[Segment]] = []
    for seg in sorted(segments, key=lambda s: s.start):
        placed = False
        for g in groups:
            g_start = min(s.start for s in g) - tolerance
            g_end = max(s.end for s in g) + tolerance
            related = any(s.entity == seg.entity or _device(s.entity) == _device(seg.entity) for s in g)
            if related and seg.start <= g_end and seg.end >= g_start:
                g.append(seg)
                placed = True
                break
        if not placed:
            groups.append([seg])
    return groups


def _has(segs, metric_prefixes, direction=None):
    out = []
    for s in segs:
        if any(s.metric.startswith(p) for p in metric_prefixes) and (direction is None or s.direction == direction):
            out.append(s)
    return out


def _ratio(s: Segment) -> float:
    if s.baseline and abs(s.baseline) > 1e-12:
        return s.peak_value / s.baseline
    return math.inf if s.peak_value else 1.0


def _fmt(v: float, unit: str) -> str:
    if unit == "bit/s":
        for scale, u in ((1e9, "Gbit/s"), (1e6, "Mbit/s"), (1e3, "kbit/s")):
            if abs(v) >= scale:
                return f"{v / scale:.2f} {u}"
        return f"{v:.0f} bit/s"
    if unit in ("pkt/s", "seg/s", "req/s", "err/s", "1/s"):
        if abs(v) >= 1e6:
            return f"{v / 1e6:.2f} M{unit}"
        if abs(v) >= 1e3:
            return f"{v / 1e3:.1f} k{unit}"
        return f"{v:.2f} {unit}"
    if unit == "%":
        return f"{v:.2f}%"
    if unit == "ms":
        return f"{v:.0f} ms"
    return f"{v:.3g} {unit}".strip()


def describe(seg: Segment) -> str:
    spec = spec_for(seg.metric)
    unit = spec.unit if spec else ""
    return f"{seg.metric} {_fmt(seg.peak_value, unit)} vs normal {_fmt(seg.baseline, unit)}"


def _monotonic_rise(seg: Segment, values: pd.Series | None) -> bool:
    if values is None:
        return False
    v = values[(values.index >= seg.start) & (values.index < seg.end)].dropna()
    if len(v) < 6:
        return False
    rank = pd.Series(np.arange(len(v)), index=v.index)
    return float(v.rank().corr(rank)) > 0.8


def classify(segs: list[Segment], series_lookup=None) -> dict:
    """Pick the incident kind and write human explanations from segment shapes."""
    ents = sorted({s.entity for s in segs})
    traffic_down = [s for s in _has(segs, ("rx_bps", "tx_bps", "rx_pps", "tx_pps"), "down") if _ratio(s) < 0.1]
    traffic_up = [s for s in _has(segs, ("rx_bps", "tx_bps", "rx_pps", "tx_pps", "util_pct"), "up")]
    errors = _has(segs, ("rx_err", "tx_err", "err_ratio"), "up")
    drops = _has(segs, ("rx_drop", "tx_drop"), "up")
    latency = _has(segs, ("latency",), "up")
    http_err = _has(segs, ("http_err", "http_5xx"), "up")
    conns = _has(segs, ("conn_established",), "up")
    cpu = _has(segs, ("cpu_pct", "mem_pct"), "up")
    shift_only = all("cusum" in s.detectors and "seasonal" not in s.detectors for s in segs)
    util = [s for s in segs if s.metric == "util_pct"]
    peak_util = max((s.peak_value for s in util), default=0.0)
    dur_min = (max(s.end for s in segs) - min(s.start for s in segs)).total_seconds() / 60

    def main(lst):
        return max(lst, key=lambda s: s.score)

    if traffic_down:
        down = main(traffic_down)
        others = [s for s in traffic_up if s.entity != down.entity]
        failover = f" Traffic failed over to {others[0].entity}, which peaked at {peak_util:.0f}% utilisation." if others else ""
        return dict(
            kind="outage",
            severity="critical",
            title=f"Outage on {down.entity}",
            cause=f"Traffic on {down.entity} fell to {_ratio(down) * 100:.2f}% of normal for {dur_min:.0f} min: "
                  f"the link or its upstream went down.{failover}",
            impact="Capacity lost; " + ("the surviving path ran near saturation, risking congestion and drops."
                                        if others else "services behind this link were unreachable."),
            rec=f"Check interface and optic status, BGP/IGP adjacencies and the carrier for {down.entity}; "
                "confirm the redundant path has headroom for a full failover.",
        )
    if traffic_up and (drops or peak_util >= 90) and any(_ratio(s) >= 2.5 for s in traffic_up):
        pps = _has(segs, ("rx_pps",), "up")
        small = ""
        bps = _has(segs, ("rx_bps",), "up")
        t = main(bps) if bps else main(traffic_up)  # describe bandwidth, not packets
        if pps and bps and _ratio(main(pps)) > 1.8 * _ratio(main(bps)):
            small = " Packet rate grew much faster than bandwidth (small packets), typical of a SYN/UDP flood."
        return dict(
            kind="traffic_surge",
            severity="critical" if peak_util >= 95 and drops else "high",
            title=f"Traffic surge saturating {t.entity}",
            cause=f"Inbound traffic jumped to {_ratio(t):.1f}x normal ({describe(t)}), driving utilisation to "
                  f"{peak_util:.0f}%" + (" with packet drops" if drops else "") + f".{small}",
            impact="Link saturated: legitimate traffic was dropped and latency rose for everything behind it.",
            rec="Identify top source prefixes (NetFlow/sFlow), apply rate limits or upstream DDoS scrubbing, "
                "and add an alert on utilisation > 85% for this edge link.",
        )
    if errors:
        e = main(errors)
        return dict(
            kind="link_degradation",
            severity="high",
            title=f"Link errors on {e.entity}",
            cause=f"Frame errors appeared on {e.entity} ({describe(e)}) for {dur_min:.0f} min without a matching "
                  "traffic change: a physical-layer problem (dirty or failing optic, bad cable, duplex/speed mismatch).",
            impact="Corrupted frames are retransmitted, so throughput falls and latency rises for flows on this link.",
            rec=f"Check optic light levels and CRC counters on both ends of {e.entity}; clean or replace the optic/"
                "cable and move traffic off the link if errors persist.",
        )
    if latency:
        lat = main(latency)
        extra = []
        err_pct = [s for s in http_err if s.metric.endswith("_pct")]
        if err_pct:
            extra.append(f"HTTP errors rose to {main(err_pct).peak_value:.1f}% of requests")
        elif http_err:
            extra.append("5xx errors rose")
        if cpu:
            extra.append(f"CPU reached {main(cpu).peak_value:.0f}%")
        sev = "critical" if err_pct and main(err_pct).peak_value >= 5 else "high"
        return dict(
            kind="latency_degradation",
            severity=sev,
            title=f"Latency degradation on {lat.entity}",
            cause=f"p95 latency on {lat.entity} rose to {_ratio(lat):.1f}x normal ({describe(lat)})"
                  + (", " + " and ".join(extra) if extra else "")
                  + (". CPU saturation points at the service itself (overload, slow code path or a noisy neighbour)."
                     if cpu else "."),
            impact="Users of this service saw slow responses" + (" and failed requests." if http_err else "."),
            rec=f"Scale out or shed load on {lat.entity}, check recent deploys and its dependencies, and alert on "
                "p95 against its SLO.",
        )
    if conns and any(dur_min >= 60 and _monotonic_rise(s, series_lookup(s) if series_lookup else None) for s in conns):
        c = main(conns)
        return dict(
            kind="connection_leak",
            severity="high" if _ratio(c) >= 5 else "medium",
            title=f"Connection leak on {c.entity}",
            cause=f"Established connections on {c.entity} climbed steadily for {dur_min:.0f} min to "
                  f"{c.peak_value:.0f} ({_ratio(c):.1f}x normal) and only dropped when the process restarted: "
                  "connections are opened but not closed.",
            impact="Each leaked connection holds memory and a file descriptor; left alone the service hits its "
                   "fd/port limits and starts refusing connections.",
            rec="Audit connection pooling / keep-alive handling on error paths and add an alert on connection count "
                "growth rate.",
        )
    if shift_only:
        s = main(segs)
        pct = (_ratio(s) - 1) * 100
        return dict(
            kind="level_shift",
            severity="medium" if abs(pct) >= 25 else "low",
            title=f"Sustained {'increase' if pct > 0 else 'decrease'} in traffic on {s.entity}",
            cause=f"Traffic on {s.entity} stepped {'up' if pct > 0 else 'down'} by about {abs(pct):.0f}% and stayed "
                  "there: a new baseline (new customer, routing change or service migration) rather than a spike.",
            impact="Not an outage, but it consumes headroom: capacity plans based on the old level are now wrong.",
            rec="Confirm the change is expected; if so update capacity forecasts and alert thresholds for this link.",
        )
    if util or cpu:
        s = main(util or cpu)
        return dict(kind="saturation", severity="high", title=f"Saturation on {s.entity}",
                    cause=f"{describe(s)}.", impact="Queuing and drops once the resource is full.",
                    rec="Add capacity or rebalance load.")
    if http_err:
        s = main(http_err)
        return dict(kind="error_burst", severity="high", title=f"Error burst on {s.entity}",
                    cause=f"{describe(s)}.", impact="Requests failed.", rec="Check service logs for the failing endpoint.")
    s = main(segs)
    return dict(kind="anomaly", severity="medium" if s.score >= 3 else "low",
                title=f"Unusual {s.metric} on {s.entity}", cause=f"{describe(s)}.",
                impact="Unknown; deviation from the normal pattern.", rec="Investigate whether this was expected.")


def build_incidents(segments: list[Segment], series_lookup=None) -> list[Incident]:
    """series_lookup(segment) -> pd.Series of that segment's values (optional)."""
    incidents = []
    for g in group_segments(segments):
        c = classify(g, series_lookup)
        start, end = min(s.start for s in g), max(s.end for s in g)
        dur = (end - start).total_seconds() / 60
        score = severity_rank(c["severity"]) * 10 + math.log1p(dur) + math.log1p(max(s.score for s in g))
        entities = []
        for s in sorted(g, key=lambda s: -s.score):
            if s.entity not in entities:
                entities.append(s.entity)
        incidents.append(
            Incident(
                id="",
                kind=c["kind"],
                title=c["title"],
                severity=c["severity"],
                start=start,
                end=end,
                entities=entities,
                segments=sorted(g, key=lambda s: -s.score),
                probable_cause=c["cause"],
                impact=c["impact"],
                recommendation=c["rec"],
                score=score,
            )
        )
    incidents.sort(key=lambda i: i.start)
    for n, inc in enumerate(incidents, 1):
        inc.id = f"INC-{n:02d}"
    return incidents
