"""One call from raw samples to everything the report needs."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .analytics import overview
from .detect import Detection, DetectOptions, detect
from .evaluate import Evaluation, evaluate
from .incidents import build_incidents
from .model import Entity, Incident, TruthEvent
from .prep import Prepared, prepare
from .store import Store


@dataclass
class AnalysisResult:
    prepared: Prepared
    detection: Detection
    incidents: list[Incident]
    overview: dict
    evaluation: Evaluation | None
    source: str = "dataset"

    @property
    def severity_counts(self) -> dict[str, int]:
        out = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for i in self.incidents:
            out[i.severity] = out.get(i.severity, 0) + 1
        return out


def analyze(
    samples: pd.DataFrame,
    entities: dict[str, Entity] | None = None,
    truth: list[TruthEvent] | None = None,
    opts: DetectOptions | None = None,
    source: str = "dataset",
) -> AnalysisResult:
    p = prepare(samples, entities or {})
    d = detect(p, opts)
    incidents = build_incidents(d.segments, lambda s: p.series(s.entity, s.metric))
    ov = overview(p)
    ev = evaluate(truth, incidents) if truth else None
    return AnalysisResult(p, d, incidents, ov, ev, source)


def analyze_store(store: Store, opts: DetectOptions | None = None) -> AnalysisResult:
    samples = store.read_samples()
    if samples.empty:
        raise ValueError(f"{store.path} has no samples")
    return analyze(samples, store.read_entities(), store.read_truth() or None, opts,
                   source=store.get_meta("source", "dataset"))
