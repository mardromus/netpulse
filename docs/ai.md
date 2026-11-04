# AI narration and verification

## The fact sheet

`ai/facts.py` turns an analysis into a JSON fact sheet: period, counts, per-interface
and per-service KPIs, incidents (with cause, impact, recommendation and evidence:
peak vs normal per metric), capacity forecasts, data-quality notes and, if available,
the evaluation. Every quantity appears pre-formatted with its unit
(`"966.86 Mbit/s"`), so a model can quote figures without doing arithmetic.

## Narration

The LLM (any OpenAI-compatible endpoint; JSON mode) is asked for a headline,
an executive summary, a short story per incident, a capacity outlook and prioritised
recommendations, using only the fact sheet.

```
facts ──► LLM ──► JSON ──► verify every number ──ok──► report
                     ▲            │ not ok
                     └── "these numbers are not in the facts: [...]" (one retry)
                                  │ still not ok
                                  ▼
                     drop the sentences that contain them, mark the report
```

The verifier (`ai/verify.py`) extracts numbers from the generated text, ignoring
identifiers (`INC-04`), interface and device names, clock times, dates and small
counting numbers, and accepts a number when it matches a fact within rounding
(3% relative or half a unit of the last digit written). The hero banner of the
report says whether every figure was verified or how many sentences were removed.

If no model is configured, or the request fails, the deterministic template
narrative is used. It is built from the same facts and is always verifiable.

## Questions

`netpulse ask data.db "what happened to payments-svc?" [--ai]`, `POST /api/ask`
and the dashboard box answer from the same fact sheet. With `--ai` the answer is
verified the same way; without it a keyword matcher picks the relevant incidents,
capacity rows or the worst incident.

## Configuration

| Variable | Default |
|---|---|
| `NETPULSE_LLM_API_KEY` (or `GROQ_API_KEY`, `OPENAI_API_KEY`) | none |
| `NETPULSE_LLM_BASE_URL` | `https://api.groq.com/openai/v1` (OpenAI when only `OPENAI_API_KEY` is set) |
| `NETPULSE_LLM_MODEL` | `llama-3.3-70b-versatile` (`gpt-4o-mini` for OpenAI) |

Ollama: `NETPULSE_LLM_BASE_URL=http://localhost:11434/v1 NETPULSE_LLM_MODEL=llama3.1`.
