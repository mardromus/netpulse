# Getting data in

## Live collection

```bash
netpulse collect -o live.db --interval 5 --duration 30m \
  --probe tcp://10.0.0.5:443 --probe https://example.com/health --probe dns://example.com
netpulse serve --live -p https://example.com/health     # dashboard with streaming anomalies
```

| Collector | Metrics |
|---|---|
| interfaces (psutil, every OS) | `rx/tx_bytes`, `rx/tx_packets`, `rx/tx_errors`, `rx/tx_drops`; link speed when known |
| host | `cpu_pct`, `mem_pct`, `conn_established`; on Linux `tcp_out_segs`, `tcp_retrans_segs`, `tcp_attempt_fails`, `tcp_estab_resets` from `/proc/net/snmp` |
| `tcp://host:port` | handshake `latency_ms`, `probe_up` |
| `http(s)://url` | `latency_ms`, `probe_up`, `http_requests`, `http_5xx` |
| `dns://name` | resolution `latency_ms`, `probe_up` |

## Import

```bash
netpulse import export.csv -o data.db                 # long or wide CSV
netpulse import node.prom  -o data.db --format prom   # Prometheus text exposition
netpulse import http://host:9100/metrics -o data.db   # scrape once
```

**Long CSV**: `ts,entity,metric,value`.
**Wide CSV**: `timestamp` (ISO or unix seconds), optional `entity`/`interface`/`host`,
then one column per metric. SNMP IF-MIB names are mapped automatically
(`ifHCInOctets → rx_bytes`, `ifInErrors → rx_errors`, `ifInDiscards → rx_drops`, ...).
**Prometheus**: node_exporter network and netstat metrics are mapped; anything else is
kept under its own name as a gauge.

## Metric catalog

Counters are converted to per-second rates (`rx_bytes → rx_bps` in bits/s).
Unknown metric names are analysed as gauges.

| Raw | Kind | Analysed as | Group |
|---|---|---|---|
| `rx_bytes`, `tx_bytes` | counter | `rx_bps`, `tx_bps` (bit/s) | traffic |
| `rx_packets`, `tx_packets` | counter | `rx_pps`, `tx_pps` | traffic |
| `rx_errors`, `tx_errors` | counter | `rx_err_ps`, `tx_err_ps` | errors |
| `rx_drops`, `tx_drops` | counter | `rx_drop_ps`, `tx_drop_ps` | errors |
| `tcp_out_segs`, `tcp_retrans_segs` | counter | `tcp_out_sps`, `tcp_retrans_sps` | traffic / errors |
| `http_requests`, `http_5xx` | counter | `http_rps`, `http_5xx_ps` | traffic / errors |
| `latency_p95_ms`, `latency_ms` | gauge | as is | latency |
| `conn_established` | gauge | as is | connections |
| `cpu_pct`, `mem_pct` | gauge | as is | saturation |
| `probe_up` | gauge | as is | availability |

Derived: `util_pct` (needs `speed_bps` on the entity), `err_ratio_pct`, `retrans_pct`,
`http_err_pct`.

Entity attributes (`entities` table) that change the analysis: `speed_bps`
(utilisation), `slo_p95_ms` (latency threshold), `role`/`site` (shown in reports).
