"""Realistic synthetic network telemetry with labelled incidents.

The generated week looks like a small data centre edge: two 10G ISP uplinks on a
core router, an internet-edge firewall, an office distribution switch, an API
gateway and a payments service. Traffic follows a daily cycle (peak around 20:00
IST), weekends are quieter, every series has noise, counters are cumulative (one
of them 32-bit, so it wraps), one counter is cleared by an operator and the
collector misses an hour of data: everything a real pipeline has to cope with.

On top of that, incidents are injected and recorded as ground truth so the
detectors can be evaluated (``netpulse eval``):

=====================  =======================================================
traffic_surge          DDoS-like flood on the firewall: bandwidth, pps, drops
link_degradation       CRC errors on a 10G uplink (failing optic)
outage + traffic_shift uplink B goes dark, its traffic fails over to uplink A
latency_degradation    payments-svc slows down at peak: p95, 5xx, CPU
connection_leak        api-gateway leaks connections until it is restarted
level_shift            a permanent +30% step in uplink A traffic (new customer)
=====================  =======================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .model import Entity, TruthEvent

UPLINK_A = "core-rtr-1/xe-0/0/1"
UPLINK_B = "core-rtr-1/xe-0/0/2"
FIREWALL = "edge-fw-1/eth1"
OFFICE = "dist-sw-3/ge-1/0/7"
GATEWAY = "api-gateway"
PAYMENTS = "payments-svc"

ENTITIES = [
    Entity(UPLINK_A, "interface", {"speed_bps": 10e9, "site": "pune-dc1", "role": "uplink to ISP-A"}),
    Entity(UPLINK_B, "interface", {"speed_bps": 10e9, "site": "pune-dc1", "role": "uplink to ISP-B"}),
    Entity(FIREWALL, "interface", {"speed_bps": 1e9, "site": "pune-dc1", "role": "internet edge"}),
    Entity(OFFICE, "interface", {"speed_bps": 1e9, "site": "pune-hq", "role": "office access"}),
    Entity(GATEWAY, "service", {"site": "pune-dc1", "slo_p95_ms": 120}),
    Entity(PAYMENTS, "service", {"site": "pune-dc1", "slo_p95_ms": 200}),
]


@dataclass
class GenConfig:
    days: int = 7
    step_s: int = 300
    seed: int = 7
    start: datetime = datetime(2026, 10, 28, tzinfo=timezone.utc)
    incidents: bool = True


@dataclass
class Generated:
    samples: pd.DataFrame
    entities: list[Entity]
    truth: list[TruthEvent]
    config: GenConfig


class _Builder:
    def __init__(self, cfg: GenConfig):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        n = int(cfg.days * 86400 / cfg.step_s)
        self.ts = pd.date_range(cfg.start, periods=n, freq=f"{cfg.step_s}s", tz="UTC")
        self.n = n
        hours = np.asarray(self.ts.hour + self.ts.minute / 60.0, dtype=float)
        self.hours = hours
        self.weekend = np.asarray(self.ts.dayofweek >= 5)
        self.rows: list[pd.DataFrame] = []
        self.truth: list[TruthEvent] = []

    # -- helpers -------------------------------------------------------------------
    def at(self, day: int, hhmm: str) -> pd.Timestamp:
        h, m = map(int, hhmm.split(":"))
        return pd.Timestamp(self.cfg.start) + pd.Timedelta(days=day, hours=h, minutes=m)

    def window(self, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
        return np.asarray((self.ts >= start) & (self.ts < end))

    def diurnal(self, peak_utc: float = 14.5, depth: float = 0.45, weekend: float = 0.8):
        d = 1.0 + depth * np.cos(2 * np.pi * (self.hours - peak_utc) / 24.0)
        return d * np.where(self.weekend, weekend, 1.0)

    def noise(self, sigma: float = 0.05) -> np.ndarray:
        return self.rng.lognormal(0.0, sigma, self.n)

    def emit(self, entity: str, metric: str, values: np.ndarray, keep: np.ndarray | None = None):
        df = pd.DataFrame({"ts": self.ts, "entity": entity, "metric": metric, "value": values})
        if keep is not None:
            df = df[keep]
        self.rows.append(df)

    def counter(self, rate: np.ndarray, start: float, wrap: float | None = None) -> np.ndarray:
        # value at ts[k] includes the interval ending at ts[k], so the rate that
        # netpulse.prep labels with ts[k] is exactly rate[k]
        inc = np.maximum(rate, 0) * self.cfg.step_s
        total = start + np.cumsum(inc)
        if wrap:
            total = np.mod(total, wrap)
        return np.floor(total)

    def label(self, entity, kind, start, end, metrics, note=""):
        if self.cfg.incidents:
            self.truth.append(TruthEvent(entity, kind, start.to_pydatetime(), end.to_pydatetime(), metrics, note))

    # -- interfaces ----------------------------------------------------------------
    def interface(self, name: str, mean_bps: float, speed: float, *, tx_ratio=0.38,
                  pkt_size=920.0, weekend=0.8, depth=0.45, mods=None, wrap_packets=False,
                  clear_at: pd.Timestamp | None = None):
        rx = mean_bps * self.diurnal(depth=depth, weekend=weekend) * self.noise(0.06)
        tx = rx * tx_ratio * self.noise(0.04)
        pkt = np.full(self.n, pkt_size)
        err_rate = np.full(self.n, 2e-8)
        drop_rate = np.full(self.n, 4e-7)
        if mods:
            rx, tx, pkt, err_rate, drop_rate = mods(rx, tx, pkt, err_rate, drop_rate)
        rx = np.minimum(rx, speed * 0.985)
        tx = np.minimum(tx, speed * 0.985)
        rx_pps = rx / 8 / pkt
        tx_pps = tx / 8 / (pkt * 1.1)
        rx_err = self.rng.poisson(np.maximum(rx_pps * err_rate * self.cfg.step_s, 0)) / self.cfg.step_s
        rx_drop = self.rng.poisson(np.maximum(rx_pps * drop_rate * self.cfg.step_s, 0)) / self.cfg.step_s
        tx_drop = self.rng.poisson(np.maximum(tx_pps * 1e-7 * self.cfg.step_s, 0)) / self.cfg.step_s
        offset = float(self.rng.integers(10**9, 10**12))
        series = {
            "rx_bytes": (rx / 8, offset * 50),
            "tx_bytes": (tx / 8, offset * 20),
            "rx_packets": (rx_pps, (2**32 - rx_pps.mean() * 86400 * 3.2) if wrap_packets else offset),
            "tx_packets": (tx_pps, offset / 2),
            "rx_errors": (rx_err, float(self.rng.integers(0, 5000))),
            "rx_drops": (rx_drop, float(self.rng.integers(0, 50000))),
            "tx_drops": (tx_drop, float(self.rng.integers(0, 50000))),
        }
        for metric, (rate, start) in series.items():
            wrap = 2**32 if (wrap_packets and metric == "rx_packets") else None
            values = self.counter(rate, start, wrap)
            if clear_at is not None:  # an operator ran "clear counters"
                after = np.asarray(self.ts >= clear_at)
                if after.any():
                    first = np.argmax(after)
                    values = values.copy()
                    values[after] = values[after] - values[first] + np.floor(rate[first] * 10)
            self.emit(name, metric, values)

    # -- services ------------------------------------------------------------------
    def service(self, name: str, rps: float, p95: float, err: float, conns: float, cpu: float,
                mods=None, gap: tuple[pd.Timestamp, pd.Timestamp] | None = None):
        load = self.diurnal(depth=0.55, weekend=0.7)
        req = rps * load * self.noise(0.05)
        lat = p95 * (1 + 0.25 * (load - 1)) * self.noise(0.08)
        err_rate = np.full(self.n, err)
        conn = conns * load * self.noise(0.04)
        cpu_v = cpu * load * self.noise(0.06)
        out_segs = req * 14
        retrans = out_segs * 0.003 * self.noise(0.2)
        if mods:
            req, lat, err_rate, conn, cpu_v, retrans = mods(req, lat, err_rate, conn, cpu_v, retrans)
        five = self.rng.poisson(np.maximum(req * err_rate * self.cfg.step_s, 0)) / self.cfg.step_s
        keep = None
        if gap:
            keep = ~self.window(*gap)
        self.emit(name, "http_requests", self.counter(req, 1e7), keep)
        self.emit(name, "http_5xx", self.counter(five, 1e4), keep)
        self.emit(name, "latency_p95_ms", np.round(lat, 2), keep)
        self.emit(name, "conn_established", np.round(conn), keep)
        self.emit(name, "cpu_pct", np.clip(np.round(cpu_v, 1), 0, 100), keep)
        self.emit(name, "tcp_out_segs", self.counter(out_segs, 5e8), keep)
        self.emit(name, "tcp_retrans_segs", self.counter(retrans, 2e6), keep)


def generate(cfg: GenConfig | None = None) -> Generated:
    cfg = cfg or GenConfig()
    b = _Builder(cfg)
    inc = cfg.incidents and cfg.days >= 7

    # timings of the injected incidents (day offsets from the start)
    shift_at = b.at(1, "12:00")
    crc = (b.at(2, "10:00"), b.at(2, "12:30"))
    clear_at = b.at(3, "06:15")
    lat = (b.at(3, "13:30"), b.at(3, "14:40"))
    gap = (b.at(1, "21:00"), b.at(1, "22:00"))
    flood = (b.at(4, "03:10"), b.at(4, "03:55"))
    leak = (b.at(5, "02:00"), b.at(5, "08:00"))
    out = (b.at(5, "16:20"), b.at(5, "16:55"))

    def uplink_a(rx, tx, pkt, er, dr):
        if inc:
            step = np.asarray(b.ts >= shift_at)
            rx = rx * np.where(step, 1.3, 1.0)
            tx = tx * np.where(step, 1.3, 1.0)
            w = b.window(*out)  # failover from uplink B
            rx = np.where(w, rx * 1.65, rx)
            tx = np.where(w, tx * 1.65, tx)
        return rx, tx, pkt, er, dr

    def uplink_b(rx, tx, pkt, er, dr):
        if inc:
            w = b.window(*crc)
            er = np.where(w, 4e-3, er)  # 0.4% of frames with CRC errors
            dr = np.where(w, 2e-3, dr)
            w = b.window(*out)
            rx = np.where(w, rx * 0.0005, rx)
            tx = np.where(w, tx * 0.0005, tx)
        return rx, tx, pkt, er, dr

    def firewall(rx, tx, pkt, er, dr):
        if inc:
            w = b.window(*flood)
            rx = np.where(w, 0.93e9 + rx * 0.3, rx)  # saturates the 1G link
            pkt = np.where(w, 140.0, pkt)  # small packets: SYN / UDP flood
            dr = np.where(w, 0.04, dr)
        return rx, tx, pkt, er, dr

    b.interface(UPLINK_A, 3.1e9, 10e9, mods=uplink_a)
    b.interface(UPLINK_B, 2.2e9, 10e9, mods=uplink_b)
    b.interface(FIREWALL, 2.4e8, 1e9, mods=firewall, clear_at=clear_at if inc else None)
    b.interface(OFFICE, 1.6e8, 1e9, tx_ratio=0.6, weekend=0.22, depth=0.75, wrap_packets=True)

    def gateway(req, latv, err, conn, cpu, retr):
        if inc:
            w = b.window(*leak)
            ramp = np.cumsum(w) * (8000.0 / max(w.sum(), 1))
            conn = conn + np.where(w, ramp, 0)
        return req, latv, err, conn, cpu, retr

    def payments(req, latv, err, conn, cpu, retr):
        if inc:
            w = b.window(*lat)
            latv = np.where(w, latv * 6.5, latv)
            err = np.where(w, 0.06, err)
            cpu = np.where(w, np.minimum(cpu * 2.6, 97.0), cpu)
            retr = np.where(w, retr * 3.0, retr)
        return req, latv, err, conn, cpu, retr

    b.service(GATEWAY, 420.0, 48.0, 0.002, 1200.0, 38.0, mods=gateway, gap=gap if inc else None)
    b.service(PAYMENTS, 85.0, 62.0, 0.001, 260.0, 30.0, mods=payments)

    if inc:
        b.label(FIREWALL, "traffic_surge", *flood, ["rx_bps", "rx_pps", "rx_drop_ps"],
                "SYN/UDP flood saturating the internet edge")
        b.label(UPLINK_B, "link_degradation", *crc, ["rx_err_ps", "err_ratio_pct"],
                "CRC errors from a failing optic")
        b.label(UPLINK_B, "outage", *out, ["rx_bps", "tx_bps"], "ISP-B uplink down")
        b.label(UPLINK_A, "traffic_shift", *out, ["rx_bps", "tx_bps"],
                "traffic failed over from ISP-B")
        b.label(PAYMENTS, "latency_degradation", *lat, ["latency_p95_ms", "http_err_pct", "cpu_pct"],
                "payments-svc overloaded at peak")
        b.label(GATEWAY, "connection_leak", *leak, ["conn_established"],
                "connections leak until the gateway restarts")
        b.label(UPLINK_A, "level_shift", shift_at, shift_at + pd.Timedelta(hours=3),
                ["rx_bps", "tx_bps"], "permanent +30% traffic from a new customer")

    samples = pd.concat(b.rows, ignore_index=True)
    return Generated(samples, list(ENTITIES), b.truth, cfg)
