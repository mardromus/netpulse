"""Score detection against ground truth (synthetic data or labelled history).

* **Event recall**: an injected event counts as detected when an incident on the
  same entity overlaps it (with tolerance). The incident kind is also compared.
* **Event precision**: incidents that overlap no real event are false alarms.
* **Time to detect**: incident start minus event start (negative = early,
  which happens when the event ramps up before its labelled start).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .model import Incident, TruthEvent

TOL = pd.Timedelta("15min")


@dataclass
class EventMatch:
    truth: TruthEvent
    incident: Incident | None
    kind_ok: bool
    delay_min: float | None


@dataclass
class Evaluation:
    matches: list[EventMatch]
    false_alarms: list[Incident]

    @property
    def recall(self) -> float:
        return sum(m.incident is not None for m in self.matches) / max(len(self.matches), 1)

    @property
    def precision(self) -> float:
        n_inc = len({id(m.incident) for m in self.matches if m.incident}) + len(self.false_alarms)
        tp = len({id(m.incident) for m in self.matches if m.incident})
        return tp / n_inc if n_inc else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def kind_accuracy(self) -> float:
        found = [m for m in self.matches if m.incident]
        return sum(m.kind_ok for m in found) / max(len(found), 1)

    def to_dict(self) -> dict:
        return {
            "events": len(self.matches),
            "detected": sum(m.incident is not None for m in self.matches),
            "false_alarms": len(self.false_alarms),
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
            "kind_accuracy": round(self.kind_accuracy, 3),
            "matches": [
                {
                    "event": m.truth.kind,
                    "entity": m.truth.entity,
                    "start": pd.Timestamp(m.truth.start).isoformat(),
                    "incident": m.incident.id if m.incident else None,
                    "incident_kind": m.incident.kind if m.incident else None,
                    "kind_ok": m.kind_ok,
                    "delay_min": m.delay_min,
                }
                for m in self.matches
            ],
        }


# an incident kind that legitimately explains a labelled event kind
COMPATIBLE = {
    "traffic_shift": {"outage", "traffic_surge", "level_shift"},
    "outage": {"outage"},
}


def evaluate(truth: list[TruthEvent], incidents: list[Incident], tol: pd.Timedelta = TOL) -> Evaluation:
    matches = []
    used = set()
    for t in truth:
        ts, te = pd.Timestamp(t.start), pd.Timestamp(t.end)
        cands = [i for i in incidents if t.entity in i.entities and i.start <= te + tol and i.end >= ts - tol]
        best = min(cands, key=lambda i: abs((i.start - ts).total_seconds())) if cands else None
        if best:
            used.add(id(best))
        ok = bool(best) and (best.kind == t.kind or best.kind in COMPATIBLE.get(t.kind, set()))
        delay = round((best.start - ts).total_seconds() / 60, 1) if best else None
        matches.append(EventMatch(t, best, ok, delay))
    false_alarms = [i for i in incidents if id(i) not in used]
    return Evaluation(matches, false_alarms)
