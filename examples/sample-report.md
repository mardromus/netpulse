# 6 incidents this period, 3 critical

_Wed 28 Oct 05:30 IST → Wed 04 Nov 05:25 IST · 6 entities · narrative: template (verified)_

Over 7 days, 6 incidents were detected across 6 monitored entities (3 critical, 2 high). The most serious was INC-03, latency degradation on payments-svc on Sat 31 Oct 19:00 IST lasting 70 min: p95 latency on payments-svc rose to 7.2x normal (latency_p95_ms 451 ms vs normal 63 ms), HTTP errors rose to 6.3% of requests and CPU reached 94%. CPU saturation points at the service itself (overload, slow code path or a noisy neighbour). Other notable incidents: INC-04 (traffic surge), INC-06 (outage), INC-02 (link degradation).

## Incidents

### INC-01 · MEDIUM · Sustained increase in traffic on core-rtr-1/xe-0/0/1

Thu 29 Oct 17:25 IST → Thu 29 Oct 20:25 IST (180 min) · core-rtr-1/xe-0/0/1

Sustained increase in traffic on core-rtr-1/xe-0/0/1 started Thu 29 Oct 17:25 IST and lasted 180 min. Not an outage, but it consumes headroom: capacity plans based on the old level are now wrong.

- **Cause:** Traffic on core-rtr-1/xe-0/0/1 stepped up by about 29% and stayed there: a new baseline (new customer, routing change or service migration) rather than a spike.
- **Impact:** Not an outage, but it consumes headroom: capacity plans based on the old level are now wrong.
- **Action:** Confirm the change is expected; if so update capacity forecasts and alert thresholds for this link.

| metric | entity | peak | normal | × normal |
|---|---|---:|---:|---:|
| `tx_bps` | `core-rtr-1/xe-0/0/1` | 2.12 Gbit/s | 1.65 Gbit/s | 1.3 |
| `tx_pps` | `core-rtr-1/xe-0/0/1` | 262.4 kpkt/s | 203.7 kpkt/s | 1.3 |
| `rx_pps` | `core-rtr-1/xe-0/0/1` | 751.9 kpkt/s | 593.2 kpkt/s | 1.3 |
| `rx_bps` | `core-rtr-1/xe-0/0/1` | 5.53 Gbit/s | 4.37 Gbit/s | 1.3 |

### INC-02 · HIGH · Link errors on core-rtr-1/xe-0/0/2

Fri 30 Oct 15:30 IST → Fri 30 Oct 18:00 IST (150 min) · core-rtr-1/xe-0/0/2

Link errors on core-rtr-1/xe-0/0/2 started Fri 30 Oct 15:30 IST and lasted 150 min. Corrupted frames are retransmitted, so throughput falls and latency rises for flows on this link.

- **Cause:** Frame errors appeared on core-rtr-1/xe-0/0/2 (rx_err_ps 1.9 kerr/s vs normal 0.01 err/s) for 150 min without a matching traffic change: a physical-layer problem (dirty or failing optic, bad cable, duplex/speed mismatch).
- **Impact:** Corrupted frames are retransmitted, so throughput falls and latency rises for flows on this link.
- **Action:** Check optic light levels and CRC counters on both ends of core-rtr-1/xe-0/0/2; clean or replace the optic/cable and move traffic off the link if errors persist.

| metric | entity | peak | normal | × normal |
|---|---|---:|---:|---:|
| `rx_err_ps` | `core-rtr-1/xe-0/0/2` | 1.9 kerr/s | 0.01 err/s | — |
| `err_ratio_pct` | `core-rtr-1/xe-0/0/2` | 0.46% | 0.00% | — |
| `rx_drop_ps` | `core-rtr-1/xe-0/0/2` | 939.37 pkt/s | 0.19 pkt/s | 5032.0 |

### INC-03 · CRITICAL · Latency degradation on payments-svc

Sat 31 Oct 19:00 IST → Sat 31 Oct 20:10 IST (70 min) · payments-svc

Latency degradation on payments-svc started Sat 31 Oct 19:00 IST and lasted 70 min. Users of this service saw slow responses and failed requests.

- **Cause:** p95 latency on payments-svc rose to 7.2x normal (latency_p95_ms 451 ms vs normal 63 ms), HTTP errors rose to 6.3% of requests and CPU reached 94%. CPU saturation points at the service itself (overload, slow code path or a noisy neighbour).
- **Impact:** Users of this service saw slow responses and failed requests.
- **Action:** Scale out or shed load on payments-svc, check recent deploys and its dependencies, and alert on p95 against its SLO.

| metric | entity | peak | normal | × normal |
|---|---|---:|---:|---:|
| `http_5xx_ps` | `payments-svc` | 6.09 req/s | 0.08 req/s | 74.0 |
| `http_err_pct` | `payments-svc` | 6.25% | 0.09% | 73.0 |
| `latency_p95_ms` | `payments-svc` | 451 ms | 63 ms | 7.2 |
| `cpu_pct` | `payments-svc` | 93.70% | 32.27% | 2.9 |
| `tcp_retrans_sps` | `payments-svc` | 16.72 seg/s | 3.44 seg/s | 4.9 |
| `retrans_pct` | `payments-svc` | 1.31% | 0.31% | 4.2 |

### INC-04 · CRITICAL · Traffic surge saturating edge-fw-1/eth1

Sun 01 Nov 08:40 IST → Sun 01 Nov 09:25 IST (45 min) · edge-fw-1/eth1

Traffic surge saturating edge-fw-1/eth1 started Sun 01 Nov 08:40 IST and lasted 45 min. Link saturated: legitimate traffic was dropped and latency rose for everything behind it.

- **Cause:** Inbound traffic jumped to 9.1x normal (rx_bps 966.86 Mbit/s vs normal 105.80 Mbit/s), driving utilisation to 97% with packet drops. Packet rate grew much faster than bandwidth (small packets), typical of a SYN/UDP flood.
- **Impact:** Link saturated: legitimate traffic was dropped and latency rose for everything behind it.
- **Action:** Identify top source prefixes (NetFlow/sFlow), apply rate limits or upstream DDoS scrubbing, and add an alert on utilisation > 85% for this edge link.

| metric | entity | peak | normal | × normal |
|---|---|---:|---:|---:|
| `rx_drop_ps` | `edge-fw-1/eth1` | 34.5 kpkt/s | 0.00 pkt/s | — |
| `err_ratio_pct` | `edge-fw-1/eth1` | 3.86% | 0.00% | — |
| `rx_pps` | `edge-fw-1/eth1` | 863.3 kpkt/s | 14.4 kpkt/s | 60.0 |
| `rx_bps` | `edge-fw-1/eth1` | 966.86 Mbit/s | 105.80 Mbit/s | 9.1 |
| `tx_pps` | `edge-fw-1/eth1` | 36.0 kpkt/s | 4.9 kpkt/s | 7.3 |
| `util_pct` | `edge-fw-1/eth1` | 96.69% | 10.58% | 9.1 |

### INC-05 · HIGH · Connection leak on api-gateway

Mon 02 Nov 07:25 IST → Mon 02 Nov 13:45 IST (380 min) · api-gateway

Connection leak on api-gateway started Mon 02 Nov 07:25 IST and lasted 380 min. Each leaked connection holds memory and a file descriptor; left alone the service hits its fd/port limits and starts refusing connections.

- **Cause:** Established connections on api-gateway climbed steadily for 380 min to 9118 (8.1x normal) and only dropped when the process restarted: connections are opened but not closed.
- **Impact:** Each leaked connection holds memory and a file descriptor; left alone the service hits its fd/port limits and starts refusing connections.
- **Action:** Audit connection pooling / keep-alive handling on error paths and add an alert on connection count growth rate.

| metric | entity | peak | normal | × normal |
|---|---|---:|---:|---:|
| `conn_established` | `api-gateway` | 9.12e+03 connections | 1.12e+03 connections | 8.1 |

### INC-06 · CRITICAL · Outage on core-rtr-1/xe-0/0/2

Mon 02 Nov 21:50 IST → Mon 02 Nov 22:25 IST (35 min) · core-rtr-1/xe-0/0/2, core-rtr-1/xe-0/0/1

Outage on core-rtr-1/xe-0/0/2 started Mon 02 Nov 21:50 IST and lasted 35 min. Capacity lost; the surviving path ran near saturation, risking congestion and drops.

- **Cause:** Traffic on core-rtr-1/xe-0/0/2 fell to 0.05% of normal for 35 min: the link or its upstream went down. Traffic failed over to core-rtr-1/xe-0/0/1, which peaked at 98% utilisation.
- **Impact:** Capacity lost; the surviving path ran near saturation, risking congestion and drops.
- **Action:** Check interface and optic status, BGP/IGP adjacencies and the carrier for core-rtr-1/xe-0/0/2; confirm the redundant path has headroom for a full failover.

| metric | entity | peak | normal | × normal |
|---|---|---:|---:|---:|
| `rx_pps` | `core-rtr-1/xe-0/0/1` | 1.34 Mpkt/s | 698.3 kpkt/s | 1.9 |
| `rx_bps` | `core-rtr-1/xe-0/0/1` | 9.85 Gbit/s | 5.14 Gbit/s | 1.9 |
| `util_pct` | `core-rtr-1/xe-0/0/1` | 98.50% | 51.40% | 1.9 |
| `tx_bps` | `core-rtr-1/xe-0/0/1` | 3.99 Gbit/s | 1.94 Gbit/s | 2.1 |

## Recommended actions

1. Scale out or shed load on payments-svc, check recent deploys and its dependencies, and alert on p95 against its SLO.
2. Identify top source prefixes (NetFlow/sFlow), apply rate limits or upstream DDoS scrubbing, and add an alert on utilisation > 85% for this edge link.
3. Check interface and optic status, BGP/IGP adjacencies and the carrier for core-rtr-1/xe-0/0/2; confirm the redundant path has headroom for a full failover.
4. Check optic light levels and CRC counters on both ends of core-rtr-1/xe-0/0/2; clean or replace the optic/cable and move traffic off the link if errors persist.
5. Audit connection pooling / keep-alive handling on error paths and add an alert on connection count growth rate.
6. Confirm the change is expected; if so update capacity forecasts and alert thresholds for this link.

## Capacity outlook

core-rtr-1/xe-0/0/1 runs at 59.6% p95 utilisation and, growing 0.61 points per day, reaches 80% in about 33.5 days.

## Data quality

- counter reset on `edge-fw-1/eth1` (7×)
- counter wrap on `dist-sw-3/ge-1/0/7` (3×)
- gap on `api-gateway` (7×)

## Detection quality

7/7 events detected, 0 false alarms, precision 1.0, recall 1.0, F1 1.0.
