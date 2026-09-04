# ADR-009: Controlled, development-only failure injection

- Status: Accepted
- Date: 2026-08-31

## Context

SentinelOps' whole premise is telling normal behaviour from abnormal behaviour.
To build and evaluate that (Phase 2 onward) we need to *produce* abnormal
behaviour on demand and reproducibly: latency degradation, error spikes, a
flaky dependency. Relying on real incidents is not an option — they are rare,
uncontrolled, and unrepeatable.

## Decision

`orders-service` has a small failure-injection mechanism:

- Exactly three bounded numeric knobs: `simulate_latency_ms` (0–60000),
  `simulate_error_rate` (0.0–1.0), `simulate_publish_error_rate` (0.0–1.0).
- **Disabled by default** (all zero).
- Set via env (`ORDERS_SIMULATE_*`) or, at runtime, via the dev-only
  `PUT /admin/simulation` endpoint.
- **Fails closed in production:** if `APP_ENV=production` and any knob is
  non-default, or the admin endpoint is enabled, the process refuses to start.
  The admin router is not mounted when `APP_ENV=production`.
- No shell, no arbitrary code, no infrastructure calls — only these three
  in-process effects.

## Alternatives considered

- **A dedicated chaos tool (Toxiproxy, Chaos Mesh).** Powerful but heavy for
  Phase 1, and mostly targets network/infra rather than application-semantic
  faults like "25% of orders fail validation downstream". Revisit for Phase 9.
- **Only env-var configuration (no runtime endpoint).** Reproducing a
  scenario sequence (normal → latency → recovery) then needs process restarts,
  which muddies the telemetry timeline. The guarded endpoint is worth it.
- **Leaving fault injection until Phase 2.** Phase 2 needs it as an input;
  building it with the service that emits the telemetry is the natural place.

## Consequences

- Reproducible operational scenarios (`docs/development/telemetry-scenarios.md`)
  that later phases use as evaluation material.
- A dev-only HTTP surface that must stay locked down — hence the hard production
  guards and tests for them.
- These are **controlled experiments**, not real incidents, and documentation
  must always say so.
