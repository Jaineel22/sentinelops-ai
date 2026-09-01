# ADR-015: Deterministic anomaly-to-incident correlation

- Status: Accepted
- Date: 2026-09-01

## Context

Phase 3 turns a stream of `anomaly.detected` events into **incidents**. The
grouping logic must be **explainable** — an on-call engineer (and, later, the
Phase 4 RCA agent) has to be able to say *why* two anomalies are the same
incident. It must also be cheap: it runs on every anomaly event.

An LLM or a learned clustering model was explicitly ruled out for this step:
non-deterministic, hard to test, hard to justify in an interview.

## Decision

Correlate deterministically on a **correlation key** with a **time window**.

* `correlation_key = "<service>:<environment>"`. Phase 3 correlates by service.
  Finer keys (metric family, dependency graph, trace linkage) are deferred.
* For an incoming anomaly, look up the **single active incident** for its key
  (the partial unique index guarantees there is at most one — ADR-014):
  * **none** → `CREATE` a new incident.
  * **exists, and the anomaly is within `CORRELATION_WINDOW_SECONDS` of the
    incident's last evidence** → `APPEND` (add evidence, recompute severity).
  * **exists, but the gap exceeds the window** (the incident went quiet) →
    `SUPERSEDE`: auto-resolve the stale incident (`resolution = "auto:stale"`)
    and open a fresh one.
* `decide(anomaly, active_incident, config)` is a **pure function**; the
  processor applies its verdict inside one database transaction.

### Why 300 seconds by default

The telemetry cadence is ~10 s (the Phase 2 collector's scrape step, kept in
Phase 3's detector). A sustained problem therefore emits an anomaly roughly
every 10 s. 300 s = 30 consecutive missing windows before the platform decides
the problem is "over" — long enough to bridge a brief recovery dip or a scrape
gap, short enough that a genuinely new problem 10 minutes later gets its own
incident. It is a single environment variable; operators retune it without a
code change.

## Complexity

O(1) per anomaly: one indexed lookup (`correlation_key`, `status`) of the one
active incident, an O(1) severity recompute over the incident's running
aggregates, and three inserts/updates. **No scan of incident history**, ever —
a naive O(n²)-over-all-incidents approach is explicitly rejected.

## Alternatives considered

- **LLM / embedding similarity for grouping.** Non-deterministic, unexplainable,
  expensive per event. Deferred — Phase 4 may *summarise* an incident, but not
  decide its membership.
- **Graph/topology-aware correlation** (group anomalies across dependent
  services). Valuable later; needs a service dependency graph the platform
  doesn't have yet.
- **No window (one incident per key forever until manually resolved).** A
  transient blip and a fresh outage an hour later would share one ever-growing
  incident.
- **Session-gap clustering after the fact (batch).** Loses the real-time
  property; Phase 4 wants to react as the incident forms.

## Consequences

- Every incident carries `severity_reasons` and every evidence row carries the
  `correlation_reason` that attached it — the audit trail is built in.
- "Supersede" is the platform's answer to reopening: there is **no reopen**
  (ADR-017); a new correlated anomaly after resolution is a new incident.
- Correlation quality is bounded by the correlation key. Documented as a known
  limitation, not a defect.
