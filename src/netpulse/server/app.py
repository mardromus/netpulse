"""FastAPI application behind `netpulse serve`.

Endpoints
---------
GET  /                      dashboard UI
GET  /api/summary           headline, period, severity counts, narrative
GET  /api/incidents         incidents (with segments)
GET  /api/entities          entities and their analysed metrics
GET  /api/series            one series with baseline and anomaly flags
GET  /api/report            the full HTML report (?ai=1 for the LLM narrative)
GET  /api/live/recent       latest live points (live mode)
GET  /api/live/stream       server-sent events with every new live point
POST /api/analyze           re-run the analysis now
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from .. import __version__
from ..pipeline import AnalysisResult, analyze_store
from ..store import Store

STATIC = Path(__file__).parent / "static"


class State:
    def __init__(self, store: Store, live=None, min_refresh_s: float = 30.0):
        self.store = store
        self.live = live
        self.min_refresh_s = min_refresh_s
        self._result: AnalysisResult | None = None
        self._count = -1
        self._at = 0.0
        self._lock = threading.Lock()

    def result(self, force: bool = False) -> AnalysisResult | None:
        with self._lock:
            n = self.store.count()
            stale = n != self._count and time.monotonic() - self._at >= self.min_refresh_s
            if force or self._result is None or stale:
                try:
                    self._result = analyze_store(self.store)
                except ValueError:
                    self._result = None
                self._count = n
                self._at = time.monotonic()
            return self._result


def _clean(values) -> list:
    return [None if (v is None or (isinstance(v, float) and not np.isfinite(v))) else float(v) for v in values]


def create_app(db: str | Path, live: bool = False, interval: float = 5.0,
               probes: list[str] | None = None, collectors=None) -> FastAPI:
    store = Store(db)
    live_collector = None
    if live or collectors:
        from .live import LiveCollector

        if collectors is None:
            from ..collect.probes import probe_for
            from ..collect.system import HostCollector, InterfaceCollector

            collectors = [InterfaceCollector(), HostCollector()] + [probe_for(p) for p in probes or []]
        live_collector = LiveCollector(collectors, store, interval)
    state = State(store, live_collector, min_refresh_s=30.0 if live_collector else 0.0)

    @asynccontextmanager
    async def lifespan(_app):
        if live_collector:
            live_collector.start()
        yield
        if live_collector:
            live_collector.stop()

    app = FastAPI(title="netpulse", version=__version__, lifespan=lifespan)
    app.state.netpulse = state

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/summary")
    def summary():
        r = state.result()
        base = {"version": __version__, "live": bool(live_collector), "samples": store.count()}
        if r is None:
            return {**base, "ready": False}
        from ..ai.facts import build_facts
        from ..ai.narrator import template_narrative

        facts = build_facts(r)
        n = template_narrative(facts)
        return {**base, "ready": True, "headline": n.headline, "summary": n.executive_summary,
                "period": facts["period"], "counts": facts["counts"],
                "evaluation": facts.get("evaluation"), "capacity": facts["capacity"]}

    @app.get("/api/incidents")
    def incidents():
        r = state.result()
        return [i.to_dict() for i in r.incidents] if r else []

    @app.get("/api/entities")
    def entities():
        r = state.result()
        if not r:
            return []
        return [{"name": e, "kind": (r.prepared.entities[e].kind if e in r.prepared.entities else "unknown"),
                 "metrics": list(f.columns)} for e, f in r.prepared.frames.items()]

    @app.get("/api/series")
    def series(entity: str, metric: str, points: int = Query(1500, ge=50, le=20000)):
        r = state.result()
        if not r or (entity, metric) not in r.detection.series:
            raise HTTPException(404, f"no series {entity} {metric}")
        d = r.detection.series[(entity, metric)]
        step = max(1, len(d.values) // points)
        idx = d.values.index[::step]
        flags = d.flags.to_numpy()
        anomalies = [(t.isoformat(), float(v)) for t, v, f in
                     zip(d.values.index, d.values.to_numpy(), flags, strict=False) if f and np.isfinite(v)]
        return {
            "entity": entity, "metric": metric,
            "ts": [t.isoformat() for t in idx],
            "value": _clean(d.values.to_numpy()[::step]),
            "baseline": _clean(d.baseline.to_numpy()[::step]),
            "anomalies": {"ts": [a[0] for a in anomalies], "value": [a[1] for a in anomalies]},
        }

    @app.get("/api/report", response_class=HTMLResponse)
    def report(ai: bool = False):
        r = state.result()
        if not r:
            raise HTTPException(409, "no data yet")
        from ..report import build_report, render_html

        return render_html(build_report(r, use_ai=ai))

    @app.post("/api/analyze")
    def reanalyze():
        r = state.result(force=True)
        return {"incidents": len(r.incidents) if r else 0}

    @app.get("/api/live/recent")
    def live_recent(limit: int = 500):
        if not live_collector:
            raise HTTPException(404, "not in live mode")
        pts = list(live_collector.recent)[-limit:]
        return {"points": [p.to_dict() for p in pts],
                "anomalies": [p.to_dict() for p in live_collector.anomalies]}

    @app.get("/api/live/stream")
    async def live_stream():
        if not live_collector:
            raise HTTPException(404, "not in live mode")
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        loop = asyncio.get_running_loop()

        def push(points):
            data = json.dumps([p.to_dict() for p in points])
            loop.call_soon_threadsafe(lambda: queue.full() or queue.put_nowait(data))

        live_collector.listeners.append(push)

        async def gen():
            try:
                yield "retry: 3000\n\n"
                while True:
                    data = await queue.get()
                    yield f"data: {data}\n\n"
            finally:
                if push in live_collector.listeners:
                    live_collector.listeners.remove(push)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app
