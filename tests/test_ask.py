import json

import pytest

from netpulse.ai.ask import ask, keyword_answer
from netpulse.ai.facts import build_facts
from netpulse.ai.llm import LLMConfig
from netpulse.generate import GenConfig, generate
from netpulse.pipeline import analyze


@pytest.fixture(scope="module")
def facts():
    g = generate(GenConfig())
    return build_facts(analyze(g.samples, {e.name: e for e in g.entities}))


def test_keyword_answers(facts):
    a = keyword_answer("What happened to payments-svc?", facts)
    kinds = {i["id"]: i["kind"] for i in facts["incidents"]}
    assert a.incidents and kinds[a.incidents[0]] == "latency_degradation"
    a = keyword_answer("Was the firewall edge-fw-1 attacked?", facts)
    assert kinds[a.incidents[0]] == "traffic_surge"
    a = keyword_answer("Which link needs a capacity upgrade first?", facts)
    assert "core-rtr-1/xe-0/0/1" in a.answer
    a = keyword_answer("What was the worst incident?", facts)
    assert a.incidents and "critical" in a.answer
    assert "could not find" in keyword_answer("how is the coffee machine", facts).answer


def test_ai_answer_is_verified(facts):
    inc = facts["incidents"][0]

    def transport(url, headers, body, timeout):
        content = json.dumps({"answer": f"{inc['id']} lasted {inc['duration_min']} min. It cost 777777 dollars.",
                              "incidents": [inc["id"], "INC-99"], "confidence": 0.9})
        return 200, {"choices": [{"message": {"content": content}}]}

    a = ask("what happened first?", facts, use_ai=True, cfg=LLMConfig(api_key="k", model="m", transport=transport))
    assert a.engine == "ai:m" and not a.verified
    assert "777777" not in a.answer and inc["id"] in a.answer
    assert a.incidents == [inc["id"]]


def test_ask_without_llm_falls_back(facts):
    a = ask("outage?", facts, use_ai=True, cfg=LLMConfig(api_key="", base_url="https://x.example"))
    assert a.engine == "keyword" and "no LLM" in a.notes
