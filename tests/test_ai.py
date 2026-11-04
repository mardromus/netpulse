import json

import pytest

from netpulse.ai.facts import build_facts
from netpulse.ai.llm import LLMClient, LLMConfig, LLMError
from netpulse.ai.narrator import ai_narrative, template_narrative
from netpulse.ai.verify import drop_unverified_sentences, numbers_in, verify_text


@pytest.fixture(scope="module")
def facts(week_result):
    return build_facts(week_result)


def test_numbers_in_skips_ids_times_and_names():
    text = "INC-04 on edge-fw-1/eth1 at 03:10 on 2026-11-01 hit 966.86 Mbit/s (p95 9.1x, core-rtr-1)."
    assert numbers_in(text) == [966.86, 9.1]


def test_verify_accepts_rounding_and_rejects_inventions():
    facts = {"peak": "966.86 Mbit/s", "ratio": 9.1, "util": 96.7}
    assert verify_text("Traffic peaked at 967 Mbit/s, 9.1x normal, 97% utilisation.", facts).ok
    v = verify_text("Traffic peaked at 1.4 Gbit/s and 3 links failed; latency hit 812 ms.", facts)
    assert v.unverified == [1.4, 812.0]  # 3 is a small count and allowed
    text, dropped = drop_unverified_sentences("Peak was 967 Mbit/s. Latency hit 812 ms. Fine.", facts)
    assert dropped == 1 and "812" not in text and "967" in text


def test_facts_cover_incidents_capacity_and_quality(facts):
    assert facts["counts"]["incidents"] == 6
    assert {i["kind"] for i in facts["incidents"]} >= {"outage", "traffic_surge"}
    assert facts["capacity"] and facts["data_quality"]
    assert facts["evaluation"]["recall"] == 1.0
    assert all("IST" in i["start"] for i in facts["incidents"])


def test_template_narrative_is_fully_verified(facts):
    n = template_narrative(facts)
    assert "6 incidents" in n.headline
    assert set(n.incident_notes) == {i["id"] for i in facts["incidents"]}
    assert n.recommendations
    blob = json.dumps(n.to_dict())
    assert verify_text(blob, facts).ok


def _fake(replies):
    calls = []

    def transport(url, headers, body, timeout):
        calls.append(body)
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(reply, tuple):
            return reply
        return 200, {"choices": [{"message": {"content": reply}}]}

    return transport, calls


def test_ai_narrative_retries_then_succeeds(facts):
    inc = facts["incidents"][0]
    bad = json.dumps({"headline": "Bad week", "executive_summary": "Latency hit 123457 ms everywhere.",
                      "incidents": [], "capacity_outlook": "", "recommendations": ["Panic"]})
    good = json.dumps({"headline": "Six incidents, two critical",
                       "executive_summary": f"{inc['id']} lasted {inc['duration_min']} min.",
                       "incidents": [{"id": inc["id"], "narrative": inc["probable_cause"]}],
                       "capacity_outlook": "Headroom is fine.", "recommendations": ["Fix the optic."]})
    transport, calls = _fake([bad, good])
    n = ai_narrative(facts, LLMConfig(api_key="k", model="fake", transport=transport))
    assert n.engine == "ai:fake" and n.verified and n.attempts == 2
    assert "123457" in json.dumps(calls[1]["messages"][-1])  # feedback named the bad number
    assert calls[0]["response_format"] == {"type": "json_object"}
    # incidents the model skipped are filled from the template
    assert set(n.incident_notes) == {i["id"] for i in facts["incidents"]}


def test_ai_narrative_strips_unverifiable_sentences_after_retries(facts):
    bad = json.dumps({"headline": "x", "executive_summary": "All good. Packet loss was 4242 percent.",
                      "incidents": [], "capacity_outlook": "", "recommendations": []})
    transport, _ = _fake([bad])
    n = ai_narrative(facts, LLMConfig(api_key="k", model="fake", transport=transport))
    assert not n.verified and n.removed_sentences == 1
    assert "4242" not in n.executive_summary and "All good." in n.executive_summary


def test_ai_narrative_falls_back_on_errors(facts):
    transport, _ = _fake([(401, {"error": {"message": "bad key"}})])
    n = ai_narrative(facts, LLMConfig(api_key="k", transport=transport))
    assert n.engine == "template" and "401" in n.notes
    n = ai_narrative(facts, LLMConfig(api_key="", base_url="https://api.example"))
    assert n.engine == "template" and "no LLM" in n.notes
    with pytest.raises(LLMError):
        LLMClient(LLMConfig(api_key="")).chat([{"role": "user", "content": "hi"}])
