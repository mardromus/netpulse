"""Counters from the machine netpulse runs on (Linux, macOS and Windows via psutil)."""

from __future__ import annotations

import socket
from pathlib import Path

import psutil

from ..model import Entity
from . import Reading

NIC_MAP = {
    "bytes_recv": "rx_bytes",
    "bytes_sent": "tx_bytes",
    "packets_recv": "rx_packets",
    "packets_sent": "tx_packets",
    "errin": "rx_errors",
    "errout": "tx_errors",
    "dropin": "rx_drops",
    "dropout": "tx_drops",
}


class InterfaceCollector:
    """Per-interface byte, packet, error and drop counters."""

    name = "interfaces"

    def __init__(self, include: list[str] | None = None, skip_loopback: bool = True):
        self.host = socket.gethostname()
        self.include = set(include or [])
        self.skip_loopback = skip_loopback

    def _wanted(self, nic: str) -> bool:
        if self.include:
            return nic in self.include
        if self.skip_loopback and (nic == "lo" or nic.lower().startswith("loopback")):
            return False
        return True

    def entities(self) -> list[Entity]:
        out = []
        for nic, st in psutil.net_if_stats().items():
            if not self._wanted(nic):
                continue
            attrs = {"host": self.host}
            if st.speed:
                attrs["speed_bps"] = st.speed * 1e6
            out.append(Entity(f"{self.host}/{nic}", "interface", attrs))
        return out

    def collect(self) -> list[Reading]:
        out: list[Reading] = []
        for nic, c in psutil.net_io_counters(pernic=True).items():
            if not self._wanted(nic):
                continue
            ent = f"{self.host}/{nic}"
            for field, metric in NIC_MAP.items():
                out.append((ent, metric, float(getattr(c, field))))
        return out


def read_proc_snmp(path: str = "/proc/net/snmp") -> dict[str, float]:
    """TCP counters from Linux /proc/net/snmp (empty dict elsewhere)."""
    p = Path(path)
    if not p.exists():
        return {}
    lines = p.read_text().splitlines()
    tcp = [line.split() for line in lines if line.startswith("Tcp:")]
    if len(tcp) < 2:
        return {}
    return {k: float(v) for k, v in zip(tcp[0][1:], tcp[1][1:], strict=False)}


class HostCollector:
    """CPU, memory, established connections and kernel TCP counters."""

    name = "host"

    def __init__(self, snmp_path: str = "/proc/net/snmp"):
        self.host = socket.gethostname()
        self.snmp_path = snmp_path

    def entities(self) -> list[Entity]:
        return [Entity(self.host, "host", {})]

    def collect(self) -> list[Reading]:
        h = self.host
        out: list[Reading] = [
            (h, "cpu_pct", psutil.cpu_percent(interval=None)),
            (h, "mem_pct", psutil.virtual_memory().percent),
        ]
        try:
            est = sum(1 for c in psutil.net_connections(kind="tcp") if c.status == "ESTABLISHED")
            out.append((h, "conn_established", float(est)))
        except (psutil.AccessDenied, PermissionError, OSError):
            pass
        snmp = read_proc_snmp(self.snmp_path)
        for key, metric in (
            ("OutSegs", "tcp_out_segs"),
            ("RetransSegs", "tcp_retrans_segs"),
            ("AttemptFails", "tcp_attempt_fails"),
            ("EstabResets", "tcp_estab_resets"),
        ):
            if key in snmp:
                out.append((h, metric, snmp[key]))
        return out
