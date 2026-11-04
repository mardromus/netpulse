"""Write the human part of the report: headline, summary, incident stories, outlook.

Two engines produce the same :class:`Narrative`:

* ``template``: deterministic sentences built from the facts. Always available.
* ``ai:<model>``: an LLM writes the narrative from the fact sheet. Its output is
  verified (:mod:`.verify`); if it quotes numbers that are not in the facts it is
  asked once to correct itself, and any sentence that still cannot be verified is
  removed. On any error the template narrative is used instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from ..model import severity_rank
from .llm import LLMClient, LLMConfig, LLMError
from .verify import drop_unverified_sentences, verify_payload

SYSTEM = """You are a senior network reliability engineer writing the weekly network health report \
for engineering managers. You receive a JSON fact sheet produced by statistical analysis and anomaly \
detection. Write clearly and concretely for busy readers.

Rules:
- Use ONLY information in the fact sheet. Never invent causes, hosts, numbers or dates.
- Quote numbers exactly as they appear in the facts (the pre-formatted strings such as "966.86 Mbit/s" \
are best). Do not compute new numbers.
- Refer to incidents by their id (INC-01) and entity names.
- Lead with business impact, then cause, then what to do.

Return a JSON object with exactly these keys:
{"headline": "max 14 words",
 "executive_summary": "3-5 sentences: overall health, the most serious incidents, what matters next",
 "incidents": [{"id": "INC-01", "narrative": "2-3 sentences: what happened, probable cause, impact"}],
 "capacity_outlook": "1-3 sentences on utilisation trends and headroom",
 "recommendations": ["3-6 short imperative actions, most important first"]}"""


@dataclass
class Narrative:
    headline: str
    executive_summary: str
    incident_notes: dict[str, str]
    capacity_outlook: str
    recommendations: list[str]
    engine: str = "template"
    verified: bool = True
    checked_numbers: int = 0
    removed_sentences: int = 0
    notes: str = ""
    attempts: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# template engine
# ---------------------------------------------------------------------------


def template_narrative(facts: dict) -> Narrative:
    incs = sorted(facts["incidents"], key=lambda i: (-severity_rank(i["severity"]), i["id"]))
    c = facts["counts"]
    days = facts["period"]["days"]
    if not incs:
        headline = f"Network healthy: no incidents across {c['entities']} entities"
        summary = (f"Over {days} days ({facts['period']['start']} to {facts['period']['end']}) no anomalies "
                   "were detected; all monitored links and services stayed within their normal patterns.")
    else:
        crit = c.get("critical_incidents", 0)
        headline = (f"{len(incs)} incidents this period, {crit} critical" if crit
                    else f"{len(incs)} incidents this period, none critical")
        worst = incs[0]
        parts = [f"Over {days} days, {len(incs)} incidents were detected across {c['entities']} monitored "
                 f"entities ({crit} critical, {c.get('high_incidents', 0)} high)."]
        parts.append(f"The most serious was {worst['id']}, {worst['title'].lower()} on {worst['start']} "
                     f"lasting {worst['duration_min']} min: {worst['probable_cause']}")
        if len(incs) > 1:
            others = ", ".join(f"{i['id']} ({i['kind'].replace('_', ' ')})" for i in incs[1:4])
            parts.append(f"Other notable incidents: {others}.")
        summary = " ".join(parts)
    notes = {i["id"]: f"{i['probable_cause']} Impact: {i['impact']}" for i in facts["incidents"]}
    cap = []
    for row in facts["capacity"]:
        d = row["days_to_80pct"]
        if d is not None and d <= 90:
            cap.append(f"{row['entity']} runs at {row['current_p95_util_pct']}% p95 utilisation and, growing "
                       f"{row['growth_points_per_day']} points per day, reaches 80% in about {d} days.")
    if not cap and facts["capacity"]:
        top = max(facts["capacity"], key=lambda r: r["current_p95_util_pct"])
        cap.append(f"No link is on course to exceed 80% utilisation; the busiest, {top['entity']}, runs at "
                   f"{top['current_p95_util_pct']}% p95.")
    recs = []
    for i in incs:
        if i["recommendation"] not in recs:
            recs.append(i["recommendation"])
    if cap and any(r["days_to_80pct"] is not None and r["days_to_80pct"] <= 90 for r in facts["capacity"]):
        recs.append("Plan capacity upgrades for links forecast to cross 80% utilisation within 90 days.")
    if facts["data_quality"]:
        recs.append("Review data quality notes (gaps and counter resets) so monitoring stays trustworthy.")
    return Narrative(headline, summary, notes, " ".join(cap) or "Not enough interface data for a forecast.",
                     recs[:6], engine="template", verified=True)


# ---------------------------------------------------------------------------
# AI engine
# ---------------------------------------------------------------------------


def _parse(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _payload_to_narrative(p: dict, facts: dict, model: str) -> Narrative:
    ids = {i["id"] for i in facts["incidents"]}
    notes = {}
    for item in p.get("incidents") or []:
        if isinstance(item, dict) and item.get("id") in ids and item.get("narrative"):
            notes[item["id"]] = str(item["narrative"])
    recs = [str(r) for r in (p.get("recommendations") or []) if str(r).strip()][:6]
    return Narrative(
        headline=str(p.get("headline", "")).strip(),
        executive_summary=str(p.get("executive_summary", "")).strip(),
        incident_notes=notes,
        capacity_outlook=str(p.get("capacity_outlook", "")).strip(),
        recommendations=recs,
        engine=f"ai:{model}",
    )


def ai_narrative(facts: dict, cfg: LLMConfig | None = None, max_attempts: int = 2) -> Narrative:
    cfg = cfg or LLMConfig.from_env()
    fallback = template_narrative(facts)
    if not cfg.usable:
        fallback.notes = "AI narration skipped: no LLM configured."
        return fallback
    client = LLMClient(cfg)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Fact sheet:\n" + json.dumps(facts, ensure_ascii=False)}]
    payload = None
    v = None
    attempts = 0
    try:
        for attempts in range(1, max_attempts + 1):  # noqa: B007
            text = client.chat(messages, json_mode=True)
            payload = _parse(text)
            v = verify_payload(payload, facts)
            if v.ok:
                break
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": (
                    "These numbers in your answer do not appear in the fact sheet: "
                    f"{sorted(set(v.unverified))[:15]}. Rewrite the JSON using only numbers that appear in the "
                    "facts (quote them exactly), and keep the same keys.")},
            ]
    except (LLMError, json.JSONDecodeError, ValueError, TypeError) as exc:
        fallback.notes = f"AI narration failed ({exc}); used the template narrative."
        return fallback

    n = _payload_to_narrative(payload or {}, facts, cfg.model)
    n.attempts = attempts
    n.checked_numbers = v.checked if v else 0
    if v and not v.ok:  # last resort: strip the sentences we cannot back up
        removed = 0
        n.executive_summary, r = drop_unverified_sentences(n.executive_summary, facts)
        removed += r
        n.capacity_outlook, r = drop_unverified_sentences(n.capacity_outlook, facts)
        removed += r
        for k in list(n.incident_notes):
            n.incident_notes[k], r = drop_unverified_sentences(n.incident_notes[k], facts)
            removed += r
        n.recommendations = [x for x in n.recommendations if drop_unverified_sentences(x, facts)[1] == 0]
        n.removed_sentences = removed
        n.verified = False
        n.notes = f"{removed} sentence(s) with unverifiable numbers were removed."
    # fill anything the model left out from the deterministic narrative
    if not n.headline:
        n.headline = fallback.headline
    if not n.executive_summary:
        n.executive_summary = fallback.executive_summary
    for k, txt in fallback.incident_notes.items():
        n.incident_notes.setdefault(k, txt)
    if not n.capacity_outlook:
        n.capacity_outlook = fallback.capacity_outlook
    if not n.recommendations:
        n.recommendations = fallback.recommendations
    return n


def narrate(facts: dict, use_ai: bool = False, cfg: LLMConfig | None = None) -> Narrative:
    return ai_narrative(facts, cfg) if use_ai else template_narrative(facts)
