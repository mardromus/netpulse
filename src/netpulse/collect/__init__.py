"""Live data collection: local counters, active probes and importers.

Every collector returns ``(entity, metric, value)`` tuples for "now";
:func:`run` polls them on an interval and writes samples to a :class:`Store`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Protocol

import pandas as pd

from ..model import Entity
from ..store import Store

Reading = tuple[str, str, float]


class Collector(Protocol):
    name: str

    def collect(self) -> list[Reading]: ...

    def entities(self) -> list[Entity]: ...


def readings_to_frame(readings: Iterable[Reading], ts: pd.Timestamp) -> pd.DataFrame:
    rows = [(ts, e, m, float(v)) for e, m, v in readings if v is not None]
    return pd.DataFrame(rows, columns=["ts", "entity", "metric", "value"])


def run(
    collectors: list[Collector],
    store: Store,
    interval_s: float = 5.0,
    duration_s: float | None = None,
    on_sample: Callable[[pd.DataFrame], None] | None = None,
    max_rounds: int | None = None,
) -> int:
    """Poll collectors until duration/max_rounds is reached (or Ctrl-C). Returns rows written."""
    ents: list[Entity] = []
    for c in collectors:
        ents += c.entities()
    if ents:
        store.write_entities(ents)
    store.set_meta("source", "live")
    store.set_meta("step_s", interval_s)
    written, rounds = 0, 0
    t_end = time.monotonic() + duration_s if duration_s else None
    next_tick = time.monotonic()
    try:
        while True:
            ts = pd.Timestamp.now(tz="UTC").floor("s")
            readings: list[Reading] = []
            for c in collectors:
                try:
                    readings += c.collect()
                except Exception as exc:  # a broken collector must not stop the others
                    readings.append((c.name, "collector_error", 1.0))
                    print(f"netpulse: collector {c.name} failed: {exc}")
            frame = readings_to_frame(readings, ts)
            written += store.write_samples(frame)
            if on_sample:
                on_sample(frame)
            rounds += 1
            if max_rounds and rounds >= max_rounds:
                break
            if t_end and time.monotonic() >= t_end:
                break
            next_tick += interval_s
            time.sleep(max(0.0, next_tick - time.monotonic()))
    except KeyboardInterrupt:
        pass
    return written
