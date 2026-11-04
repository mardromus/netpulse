# Architecture

```
 collect ─┐                        ┌─► analytics (percentiles, trends, capacity)
 import  ─┼─► store (SQLite) ─► prep ─┤
 generate ┘   samples, entities,     └─► detect ─► incidents ─► evaluate (if ground truth)
              ground truth                                     │
                                                               ▼
                                     facts ─► narrator (template | LLM + verifier) ─► report / dashboard / ask
```

| Module | Responsibility |
|---|---|
| `model.py` | metric catalog (counter/gauge, unit, group, "higher is worse"), `Entity`, `Segment`, `Incident`, `TruthEvent` |
| `store.py` | one SQLite file per dataset: samples (long format), entities, ground truth, metadata |
| `collect/` | psutil interface + host counters, `/proc/net/snmp` TCP counters, TCP/HTTP/DNS probes, CSV and Prometheus importers |
| `generate.py` | a realistic labelled week (diurnal traffic, weekends, noise, 32-bit wrap, counter clear, collector gap, 7 incidents) |
| `prep.py` | counters → rates (wrap/reset aware), regular grid, gap handling, derived KPIs (utilisation, error %, retransmission %, HTTP error %) |
| `analytics.py` | summaries, Theil-Sen trends, peak hours, weekday×hour heatmaps, capacity forecasts, top talkers |
| `detect/` | univariate detectors + isolation forest + voting ensemble → segments (see [methods.md](methods.md)) |
| `incidents.py` | group segments by time and device, classify the shape, write cause / impact / recommendation |
| `evaluate.py` | precision, recall, F1, kind accuracy and detection delay against ground truth |
| `ai/` | fact sheet, LLM client, number verifier, narrator, question answering (see [ai.md](ai.md)) |
| `report/` | Plotly charts, Jinja2 HTML template, Markdown and JSON renderers |
| `server/` | FastAPI API, live collector with online scoring, SSE stream, dashboard UI |
| `cli.py` | `netpulse demo / generate / collect / import / detect / report / ask / eval / serve` |

## Design decisions

- **Long format everywhere upstream, wide per entity downstream.** Storage and
  import stay schema-free (any metric name works), analysis gets fast aligned frames.
- **Statistics decide, the LLM explains.** The model never sees raw series and never
  decides whether something is an incident. It gets a fact sheet and its numbers are
  checked, so the report stays reproducible and auditable.
- **Everything works offline.** No LLM: template narrative and keyword Q&A. No CDN:
  `--inline` embeds plotly.js. No live network: `generate` and `import`.
- **Evaluate continuously.** The generator's labelled week is a regression test
  (CI fails if detection drops below F1 1.0 on it).
