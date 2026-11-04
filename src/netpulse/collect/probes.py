"""Active probes: measure what users feel (latency, availability, errors).

Targets are written as URLs::

    tcp://10.0.0.5:443      TCP handshake time
    http://example.com/     HTTP GET latency, status, 5xx counter
    https://api.example/health
    dns://example.com       name resolution time
"""

from __future__ import annotations

import socket
import time
from urllib.parse import urlparse

import httpx

from ..model import Entity
from . import Reading


class TcpProbe:
    def __init__(self, host: str, port: int, timeout: float = 3.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.name = f"tcp://{host}:{port}"

    def entities(self) -> list[Entity]:
        return [Entity(self.name, "probe", {"type": "tcp"})]

    def collect(self) -> list[Reading]:
        t0 = time.perf_counter()
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout):
                ms = (time.perf_counter() - t0) * 1000
            return [(self.name, "latency_ms", ms), (self.name, "probe_up", 1.0)]
        except OSError:
            return [(self.name, "probe_up", 0.0)]


class HttpProbe:
    def __init__(self, url: str, timeout: float = 5.0):
        self.url, self.timeout = url, timeout
        self.name = url
        self.requests = 0
        self.errors = 0
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)

    def entities(self) -> list[Entity]:
        return [Entity(self.name, "probe", {"type": "http"})]

    def collect(self) -> list[Reading]:
        self.requests += 1
        t0 = time.perf_counter()
        out: list[Reading] = []
        try:
            r = self.client.get(self.url)
            ms = (time.perf_counter() - t0) * 1000
            if r.status_code >= 500:
                self.errors += 1
            out += [(self.name, "latency_ms", ms),
                    (self.name, "probe_up", 1.0 if r.status_code < 500 else 0.0)]
        except httpx.HTTPError:
            self.errors += 1
            out.append((self.name, "probe_up", 0.0))
        out += [(self.name, "http_requests", float(self.requests)),
                (self.name, "http_5xx", float(self.errors))]
        return out


class DnsProbe:
    def __init__(self, hostname: str):
        self.hostname = hostname
        self.name = f"dns://{hostname}"

    def entities(self) -> list[Entity]:
        return [Entity(self.name, "probe", {"type": "dns"})]

    def collect(self) -> list[Reading]:
        t0 = time.perf_counter()
        try:
            socket.getaddrinfo(self.hostname, None)
            return [(self.name, "latency_ms", (time.perf_counter() - t0) * 1000),
                    (self.name, "probe_up", 1.0)]
        except OSError:
            return [(self.name, "probe_up", 0.0)]


def probe_for(target: str):
    """Build a probe from a target URL (tcp://, http(s)://, dns://)."""
    u = urlparse(target)
    if u.scheme == "tcp":
        if not u.hostname or not u.port:
            raise ValueError(f"tcp probe needs host:port: {target}")
        return TcpProbe(u.hostname, u.port)
    if u.scheme in ("http", "https"):
        return HttpProbe(target)
    if u.scheme == "dns":
        return DnsProbe(u.netloc or u.path)
    raise ValueError(f"unsupported probe target {target!r} (use tcp://, http(s):// or dns://)")
