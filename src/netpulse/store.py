"""SQLite storage for samples, entities and (for synthetic data) ground truth.

One file holds everything about a dataset, so it can be passed around, served by
the dashboard and re-analysed later::

    store = Store("out/demo.db")
    store.write_samples(df)          # long format: ts, entity, metric, value
    df = store.read_samples()
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .model import Entity, TruthEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts     REAL NOT NULL,      -- unix seconds (UTC)
    entity TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL
);
CREATE INDEX IF NOT EXISTS samples_series ON samples (entity, metric, ts);
CREATE TABLE IF NOT EXISTS entities (
    name  TEXT PRIMARY KEY,
    kind  TEXT NOT NULL,
    attrs TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS truth (
    entity  TEXT NOT NULL,
    kind    TEXT NOT NULL,
    start   REAL NOT NULL,
    end     REAL NOT NULL,
    metrics TEXT NOT NULL,
    note    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _to_epoch(ts: pd.Series) -> pd.Series:
    """UTC timestamps -> float unix seconds (independent of pandas' time unit)."""
    t = pd.to_datetime(ts, utc=True)
    return (t - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript(SCHEMA)

    # -- samples ---------------------------------------------------------------
    def write_samples(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        rows = pd.DataFrame(
            {
                "ts": _to_epoch(df["ts"]),
                "entity": df["entity"].astype(str),
                "metric": df["metric"].astype(str),
                "value": df["value"].astype(float),
            }
        )
        with self.conn:
            self.conn.executemany(
                "INSERT INTO samples (ts, entity, metric, value) VALUES (?, ?, ?, ?)",
                rows.itertuples(index=False, name=None),
            )
        return len(rows)

    def read_samples(
        self,
        entity: str | None = None,
        metric: str | None = None,
        since: datetime | None = None,
    ) -> pd.DataFrame:
        q = "SELECT ts, entity, metric, value FROM samples WHERE 1=1"
        args: list = []
        if entity:
            q += " AND entity = ?"
            args.append(entity)
        if metric:
            q += " AND metric = ?"
            args.append(metric)
        if since:
            q += " AND ts >= ?"
            args.append(since.replace(tzinfo=since.tzinfo or timezone.utc).timestamp())
        q += " ORDER BY entity, metric, ts"
        df = pd.read_sql_query(q, self.conn, params=args)
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        return df

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0])

    def series_keys(self) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT DISTINCT entity, metric FROM samples ORDER BY entity, metric"
        ).fetchall()
        return [(e, m) for e, m in rows]

    # -- entities ----------------------------------------------------------------
    def write_entities(self, entities: Iterable[Entity]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO entities (name, kind, attrs) VALUES (?, ?, ?)",
                [(e.name, e.kind, json.dumps(e.attrs)) for e in entities],
            )

    def read_entities(self) -> dict[str, Entity]:
        out: dict[str, Entity] = {}
        for name, kind, attrs in self.conn.execute("SELECT name, kind, attrs FROM entities"):
            out[name] = Entity(name, kind, json.loads(attrs))
        return out

    # -- ground truth ---------------------------------------------------------------
    def write_truth(self, events: Iterable[TruthEvent]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO truth (entity, kind, start, end, metrics, note) VALUES (?,?,?,?,?,?)",
                [
                    (t.entity, t.kind, t.start.timestamp(), t.end.timestamp(),
                     json.dumps(t.metrics), t.note)
                    for t in events
                ],
            )

    def read_truth(self) -> list[TruthEvent]:
        out = []
        for entity, kind, start, end, metrics, note in self.conn.execute(
            "SELECT entity, kind, start, end, metrics, note FROM truth ORDER BY start"
        ):
            out.append(
                TruthEvent(
                    entity,
                    kind,
                    datetime.fromtimestamp(start, tz=timezone.utc),
                    datetime.fromtimestamp(end, tz=timezone.utc),
                    json.loads(metrics),
                    note,
                )
            )
        return out

    # -- metadata -----------------------------------------------------------------
    def set_meta(self, key: str, value) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value))
            )

    def get_meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
