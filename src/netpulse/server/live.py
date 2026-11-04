"""Live mode: poll collectors in the background and score every new point.

Counters are turned into rates on the fly, and each new value is compared with
the recent history of its series using a robust z-score (median / MAD of the
last ``window`` points). This is the streaming counterpart of the batch
detectors: cheap, immediate and good at catching sudden jumps.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..collect import Collector, readings_to_frame
from ..model import CATALOG
from ..store import Store


@dataclass
class LivePoint:
    ts: str
    entity: str
    metric: str
    value: float
    z: float
    anomaly: bool

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class OnlineScorer:
    window: int = 60
    z_threshold: float = 6.0
    min_history: int = 12
    history: dict = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=60)))
    last_raw: dict = field(default_factory=dict)

    def update(self, ts: pd.Timestamp, entity: str, metric: str, value: float) -> LivePoint | None:
        spec = CATALOG.get(metric)
        name = metric
        if spec and spec.kind == "counter":
            prev = self.last_raw.get((entity, metric))
            self.last_raw[(entity, metric)] = (ts, value)
            if prev is None:
                return None
            dt = (ts - prev[0]).total_seconds()
            dv = value - prev[1]
            if dt <= 0 or dv < 0:  # reset: skip one point
                return None
            value = dv / dt * spec.rate_scale
            name = spec.analysed_name
        hist = self.history[(entity, name)]
        z = 0.0
        if len(hist) >= self.min_history:
            arr = np.fromiter(hist, float)
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med))) * 1.4826
            scale = max(mad, abs(med) * 0.02, 1e-9)
            z = (value - med) / scale
        hist.append(value)
        return LivePoint(ts.isoformat(), entity, name, float(value), round(float(z), 2),
                         abs(z) >= self.z_threshold)


class LiveCollector:
    def __init__(self, collectors: list[Collector], store: Store, interval: float = 5.0, keep: int = 720):
        self.collectors = collectors
        self.store = store
        self.interval = interval
        self.scorer = OnlineScorer()
        self.recent: deque[LivePoint] = deque(maxlen=keep * 16)
        self.anomalies: deque[LivePoint] = deque(maxlen=200)
        self.listeners: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.rounds = 0
        ents = []
        for c in collectors:
            ents += c.entities()
        if ents:
            store.write_entities(ents)
        store.set_meta("source", "live")
        store.set_meta("step_s", interval)

    def poll_once(self) -> list[LivePoint]:
        ts = pd.Timestamp.now(tz="UTC").floor("s")
        readings = []
        for c in self.collectors:
            try:
                readings += c.collect()
            except Exception:  # noqa: BLE001 - one bad probe must not stop the dashboard
                continue
        self.store.write_samples(readings_to_frame(readings, ts))
        out = []
        for entity, metric, value in readings:
            p = self.scorer.update(ts, entity, metric, float(value))
            if p is None:
                continue
            out.append(p)
            self.recent.append(p)
            if p.anomaly:
                self.anomalies.append(p)
        self.rounds += 1
        for fn in list(self.listeners):
            fn(out)
        return out

    def _run(self):
        next_tick = time.monotonic()
        while not self._stop.is_set():
            self.poll_once()
            next_tick += self.interval
            self._stop.wait(max(0.0, next_tick - time.monotonic()))

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="netpulse-live", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
