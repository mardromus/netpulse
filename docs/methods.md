# Detection methods

## 1. Preparation

- **Rates.** `rate = Δvalue / Δt` using real timestamps. Rates are labelled with the
  end of their interval (like Prometheus).
- **Wraps vs resets.** A negative Δ is a 32/64-bit wrap only if the previous value was
  close to the boundary *and* the corrected rate is plausible (≤ 5× the typical rate);
  otherwise it is a reset (device restart, `clear counters`) and that one rate is
  dropped. Both are reported as data-quality notes.
- **Grid.** Samples are bucketed onto one step (median sampling interval). Holes of up
  to 2 steps are interpolated; longer ones stay empty and are reported as gaps.

## 2. Baseline: leave-one-day-out time-of-day profile

For each point the expected value is the **median of the same time-of-day slot on all
other days**. Days of the other type (weekday/weekend) are rescaled by the ratio of the
two levels before they vote. Leaving the current day out means an incident cannot
raise its own baseline. Series shorter than two days use a trailing rolling median.

## 3. Detectors (score ≥ 1 = "anomalous by this method")

| Detector | What it measures | Threshold |
|---|---|---|
| `seasonal` | residual vs profile (relative for traffic and connections), robust z = r / (1.4826·MAD) | z ≥ 4.5 |
| `local` | residual vs the previous 2 hours of residuals (sudden jumps) | z ≥ 5 |
| `stl` | robust STL decomposition residual (daily period) | z ≥ 4.5 |
| `cusum` | two-sided CUSUM on log(value/profile), confirmed over the next 3 h and ≥ 15% effect | h = 12 |
| `threshold` | utilisation ≥ 90%, error ratio ≥ 0.5%, retransmissions ≥ 3%, HTTP errors ≥ 2%, CPU ≥ 90%, latency ≥ the entity's `slo_p95_ms`, probe down | fixed |
| `iforest` | Isolation Forest over each entity's vector of per-metric robust z-scores | 99.5th percentile |

`--sensitivity` divides all z thresholds (2.0 = flag deviations half as large).

## 4. Voting and filters

A point is anomalous when

```
(seasonal ≥ 1 and at least one more of {local, stl, iforest} ≥ 1)
or seasonal ≥ 2
or a CUSUM level shift starts
or an operational threshold is crossed
```

then two filters apply:

- **Direction**: for "higher is worse" metrics (errors, latency, CPU) only increases
  count; traffic counts both ways; availability only drops.
- **Minimum effect** by group: traffic ±25%, connections ±30% and +20, errors ≥ 2.5× and
  +0.05/s (or +0.05 points for percentages), latency +30% and +10 ms, saturation +15 points.

Consecutive anomalous points (gaps of one step allowed) form a segment; a segment
needs 2+ points unless a threshold was crossed.

## 5. Incidents

Segments are grouped when they overlap within 10 minutes **and** share an entity or a
device prefix (`core-rtr-1/...`). The shape decides the kind:

| Kind | Shape |
|---|---|
| outage | a traffic series falls below 10% of normal (others on the same device rising = failover) |
| traffic_surge | traffic ≥ 2.5× normal with drops or utilisation ≥ 90% (pps rising faster than bps → flood) |
| link_degradation | error/CRC rates up without a traffic change |
| latency_degradation | p95 latency up (with HTTP errors / CPU as supporting evidence) |
| connection_leak | established connections rising monotonically for ≥ 1 h |
| level_shift | only CUSUM fired: a sustained new baseline |
| saturation / error_burst / anomaly | fallbacks |

## 6. Evaluation

With ground truth (synthetic data, or labels you add to the `truth` table):
recall = labelled events overlapped by an incident on the same entity (±15 min),
precision = incidents that overlap a labelled event, kind accuracy = correctly
classified detections, delay = incident start − event start.
