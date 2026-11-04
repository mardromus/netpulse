<div align="center">

# netpulse

**AI-generated analytics and anomaly reports from network counters and metrics.**

Collect or import interface counters, service metrics and probes · detect anomalies with an ensemble of explainable detectors · group them into incidents with probable cause, impact and action · get a report whose AI narrative is **checked number by number against the data**.

</div>

---

```bash
pip install -e ".[server]"
netpulse demo            # generate a labelled week → detect → evaluate → out/netpulse-report.html
netpulse serve out/demo.db
```

```text
                                   6 incidents
 INC-06  critical  outage               Outage on core-rtr-1/xe-0/0/2              Mon 02 Nov 21:50   35
 INC-04  critical  traffic surge        Traffic surge saturating edge-fw-1/eth1    Sun 01 Nov 08:40   45
 INC-03  critical  latency degradation  Latency degradation on payments-svc        Sat 31 Oct 19:00   70
 INC-02  high      link degradation     Link errors on core-rtr-1/xe-0/0/2         Fri 30 Oct 15:30  150
 INC-05  high      connection leak      Connection leak on api-gateway             Mon 02 Nov 07:25  380
 INC-01  medium    level shift          Sustained increase in traffic on xe-0/0/1  Thu 29 Oct 17:25  180

 detection vs ground truth: precision 1.0  recall 1.0  F1 1.0  kind accuracy 1.0
```

## Why

Network teams drown in counters: bytes, packets, errors, drops, retransmissions, latency, connections, on every interface and service, every few minutes. Thresholds either miss real problems or page at 3 a.m. for nothing, and the weekly report is a pile of graphs someone has to interpret.

netpulse does the interpretation:

1. **Clean the data the way networks need it.** Counters become rates; 32/64-bit wraps are corrected, counter resets don't turn into fake spikes, gaps are reported, utilisation and error/retransmission/HTTP-error percentages are derived.
2. **Learn what normal looks like.** Each series is compared with a leave-one-day-out time-of-day baseline, so 20:00 is compared with other evenings and a quiet weekend with other weekends.
3. **Only flag what several methods agree on.** Seasonal robust z-scores, local jumps, STL residuals, CUSUM level shifts, operational thresholds and an Isolation Forest across each entity's metrics vote. Then direction and minimum-effect filters remove "anomalies" nobody cares about.
4. **Turn anomalies into incidents.** Related anomalies (same device, same time) are grouped and classified by shape (outage with failover, traffic surge / flood, link degradation, latency degradation, connection leak, level shift), each with probable cause, impact and a recommended action.
5. **Write the report.** Analytics, capacity forecasts and incidents go into a fact sheet. An LLM writes the narrative from it, and **every number it writes is verified against the fact sheet**: wrong figures are sent back once, then removed. With no LLM, a deterministic narrative is used.

## The report

`netpulse report data.db -o report.html [--ai] [--md report.md] [--json report.json]`

- headline and executive summary (AI or deterministic) with a verification badge
- severity tiles, incident timeline
- one card per incident: story, probable cause, impact, action, chart with the expected band, evidence table (peak vs normal)
- recommended actions, capacity outlook with a 30-day projection to 80% utilisation
- traffic per interface, service p95 latency vs SLO, weekday × hour utilisation heatmap
- data quality (wraps, resets, gaps) and, with ground truth, detection precision/recall/F1

See [`examples/sample-report.html`](examples/sample-report.html) (open it in a browser).

## Dashboard and live mode

```bash
netpulse serve data.db                                   # explore a dataset
netpulse serve --live -p https://example.com/health      # this machine's counters, scored as they arrive
```

Incident list, series explorer with the expected band and anomalies, live cards with sparklines and a live anomaly feed over Server-Sent Events, plus an **Ask** box: *"what happened to payments-svc?"*, *"which link needs an upgrade first?"*. The same questions work from the CLI with `netpulse ask data.db "..." [--ai]`.

## Getting data in

| Source | Command |
|---|---|
| this machine (interfaces, CPU, memory, TCP retransmissions) + TCP/HTTP/DNS probes | `netpulse collect -o live.db --duration 30m -p tcp://10.0.0.5:443` |
| CSV, long (`ts,entity,metric,value`) or wide, SNMP names understood (`ifHCInOctets`...) | `netpulse import export.csv -o data.db` |
| Prometheus exposition / node_exporter | `netpulse import http://host:9100/metrics -o data.db` |
| a realistic labelled week for demos and evaluation | `netpulse generate -o data.db` |

Details and the metric catalog: [docs/data.md](docs/data.md).

## AI configuration

Any OpenAI-compatible API. Nothing is sent unless you pass `--ai`.

```bash
export GROQ_API_KEY=...                     # or NETPULSE_LLM_API_KEY / OPENAI_API_KEY
netpulse report data.db --ai -o report.html

# fully local
export NETPULSE_LLM_BASE_URL=http://localhost:11434/v1 NETPULSE_LLM_MODEL=llama3.1
```

The model only receives the fact sheet (aggregates, incident summaries), never raw samples. See [docs/ai.md](docs/ai.md).

## How good is the detection?

`netpulse generate` creates a week with diurnal traffic, quiet weekends, noise, a 32-bit counter wrap, an operator counter clear, an hour of missing data, and seven labelled incidents. On it netpulse finds all seven, classifies them correctly and raises **zero false alarms**. `netpulse eval data.db` prints the table and CI enforces it. Methods are documented in [docs/methods.md](docs/methods.md).

## Commands

| | |
|---|---|
| `netpulse demo [--ai]` | generate → analyse → evaluate → reports in `out/` |
| `netpulse generate -o data.db [--days 7] [--seed 7] [--quiet]` | synthetic labelled telemetry |
| `netpulse collect -o live.db [--interval 5] [--duration 10m] [-p URL ...]` | live collection |
| `netpulse import FILE\|URL -o data.db` | CSV / Prometheus import |
| `netpulse detect data.db [--json] [--sensitivity 1.0]` | incidents in the terminal (exit 1 on high/critical) |
| `netpulse report data.db [-o r.html] [--md] [--json] [--ai] [--inline]` | the report (`--inline` embeds plotly for offline use) |
| `netpulse ask data.db "question" [--ai]` | questions answered from the facts |
| `netpulse eval data.db` | precision / recall / F1 against ground truth |
| `netpulse serve [data.db] [--live] [-p URL]` | dashboard on http://127.0.0.1:8050 |

## Project layout

```text
src/netpulse/
  model.py store.py prep.py analytics.py generate.py incidents.py evaluate.py pipeline.py cli.py
  collect/   system counters, probes, csv + prometheus importers
  detect/    baseline, univariate detectors, isolation forest, voting ensemble
  ai/        fact sheet, llm client, number verifier, narrator, ask
  report/    plotly charts, html template, markdown/json renderers
  server/    fastapi app, live collector + online scorer, dashboard
docs/        architecture, methods, data, ai
examples/    snmp-style csv export, sample report
```

Architecture: [docs/architecture.md](docs/architecture.md).

## Development

```bash
pip install -e ".[dev]"
make lint test
docker build -t netpulse . && docker run -p 8050:8050 netpulse     # live dashboard
```

## License

MIT © Kushagra Goyal
