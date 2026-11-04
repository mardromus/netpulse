"""Ask questions about an analysis in plain language.

"What happened to payments-svc on Saturday?", "Which link needs an upgrade
first?", "Was the firewall attacked?". With an LLM configured the question is
answered from the fact sheet only and every number in the answer is verified;
without one, a keyword matcher answers from the same facts.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from ..model import severity_rank
from .llm import LLMClient, LLMConfig, LLMError
from .verify import drop_unverified_sentences, verify_text

ASK_SYSTEM = """You answer questions about a network analytics report. You are given the fact sheet \
that the report was built from. Answer ONLY from the facts; if they do not contain the answer, say so \
plainly. Quote numbers exactly as they appear in the facts and refer to incidents by id.
Return JSON: {"answer": "2-5 sentences", "incidents": ["INC-02"], "confidence": 0.0-1.0}"""

STOP = set("a an the of on in at to for is was were what which when where who why how did does do and or "
           "with any this that there it its be been about happened happen my our".split())


@dataclass
class Answer:
    question: str
    answer: str
    incidents: list[str] = field(default_factory=list)
    engine: str = "keyword"
    verified: bool = True
    confidence: float = 0.5
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_./-]+", text.lower()) if w not in STOP and len(w) > 1}


def keyword_answer(question: str, facts: dict) -> Answer:
    q = question.lower()
    words = _words(question)
    incs = facts["incidents"]
    if re.search(r"capacity|headroom|upgrade|forecast|run out|saturat", q) and facts["capacity"]:
        rows = sorted(facts["capacity"], key=lambda r: (r["days_to_80pct"] is None, r["days_to_80pct"] or 0))
        top = rows[0]
        if top["days_to_80pct"] is not None:
            text = (f"{top['entity']} is the first link to watch: it runs at {top['current_p95_util_pct']}% p95 "
                    f"utilisation, growing {top['growth_points_per_day']} points per day, and reaches 80% in about "
                    f"{top['days_to_80pct']} days.")
        else:
            text = (f"No link is on course to reach 80% utilisation within a year; the busiest is "
                    f"{top['entity']} at {top['current_p95_util_pct']}% p95.")
        return Answer(question, text, confidence=0.7)
    if re.search(r"worst|most serious|biggest|critical|summar|overall", q) and incs:
        worst = sorted(incs, key=lambda i: (-severity_rank(i["severity"]), i["id"]))[0]
        text = (f"The most serious incident was {worst['id']} ({worst['severity']}): {worst['title']} on "
                f"{worst['start']} for {worst['duration_min']} min. {worst['probable_cause']}")
        return Answer(question, text, [worst["id"]], confidence=0.7)
    scored = []
    for i in incs:
        hay = _words(" ".join([i["id"], i["kind"].replace("_", " "), i["title"], " ".join(i["entities"]),
                               i["probable_cause"], i["start"]]))
        ents = {e.lower() for e in i["entities"]}
        score = len(words & hay) + 3 * sum(1 for w in words if any(w in e for e in ents))
        if i["id"].lower() in q:
            score += 10
        if score:
            scored.append((score, i))
    if not scored:
        return Answer(question, "I could not find anything about that in this analysis.", confidence=0.2)
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    best = [i for s, i in scored if s >= scored[0][0] * 0.6][:3]
    parts = [f"{i['id']} ({i['severity']}, {i['start']}, {i['duration_min']} min): {i['probable_cause']} "
             f"Recommended: {i['recommendation']}" for i in best]
    return Answer(question, " ".join(parts), [i["id"] for i in best], confidence=0.6)


def ask(question: str, facts: dict, use_ai: bool = False, cfg: LLMConfig | None = None) -> Answer:
    if not use_ai:
        return keyword_answer(question, facts)
    cfg = cfg or LLMConfig.from_env()
    if not cfg.usable:
        a = keyword_answer(question, facts)
        a.notes = "no LLM configured; answered with keyword search"
        return a
    try:
        text = LLMClient(cfg).chat([
            {"role": "system", "content": ASK_SYSTEM},
            {"role": "user", "content": "Fact sheet:\n" + json.dumps(facts, ensure_ascii=False)
             + "\n\nQuestion: " + question},
        ])
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except (LLMError, AttributeError, json.JSONDecodeError) as exc:
        a = keyword_answer(question, facts)
        a.notes = f"LLM failed ({exc}); answered with keyword search"
        return a
    answer = str(data.get("answer", "")).strip()
    ids = {i["id"] for i in facts["incidents"]}
    cited = [x for x in data.get("incidents") or [] if x in ids]
    verified = verify_text(answer, facts).ok
    notes = ""
    if not verified:
        answer, n = drop_unverified_sentences(answer, facts)
        notes = f"{n} sentence(s) with unverifiable numbers removed"
    try:
        conf = float(data.get("confidence", 0.6))
    except (TypeError, ValueError):
        conf = 0.6
    return Answer(question, answer or "The facts do not answer that.", cited, f"ai:{cfg.model}", verified,
                  max(0.0, min(1.0, conf)), notes)
