# Changelog

## 1.0.0

- Collectors: interface and host counters (psutil, `/proc/net/snmp`), TCP/HTTP/DNS probes.
- Importers: long and wide CSV with SNMP IF-MIB names, Prometheus exposition and scraping.
- Synthetic labelled week with real-world quirks and seven incident types.
- Preparation: wrap-aware counter rates, reset handling, regular grid, gap reports, derived KPIs.
- Analytics: percentiles, Theil-Sen trends, peak hours, heatmaps, capacity forecasts.
- Detection ensemble: leave-one-day-out seasonal baseline, local jumps, STL, CUSUM level shifts,
  operational thresholds, Isolation Forest; direction and minimum-effect filters; sensitivity knob.
- Incidents grouped by time and device and classified (outage/failover, traffic surge, link
  degradation, latency degradation, connection leak, level shift) with cause, impact and action.
- Evaluation against ground truth: precision, recall, F1, kind accuracy, detection delay.
- AI narrative from a fact sheet with number-by-number verification, a correction round and
  sentence filtering; deterministic fallback. Plain-language questions (`ask`).
- HTML report with Plotly charts, Markdown and JSON output.
- FastAPI dashboard with series explorer, live collection, online scoring and SSE stream.
