# ADR-005: Build incrementally in phases, not all at once

- Status: Accepted
- Date: 2026-08-31

## Context

The target system spans distributed systems, ML, MLOps, agentic AI,
observability, Docker, Kubernetes, cloud, and IaC. Generating all of it upfront
would produce large amounts of unverified, interdependent code and many
placeholder implementations that only *look* complete. That is hard to test,
hard to explain, and hard to trust.

## Decision

Deliver in numbered phases. Each phase:

- adds one coherent capability end to end,
- is fully tested and documented before the next begins,
- introduces a dependency/technology only when that phase actually uses it,
- never adds fake implementations, placeholder services, or commented-out
  future code to make the architecture look finished.

Phase 0 establishes only the repository and development foundation. The phase
roadmap lives in [docs/phases/roadmap.md](../phases/roadmap.md).

## Alternatives considered

- **Big-bang scaffold of every service/technology now.** Maximises apparent
  completeness, minimises actual working software; high risk, low
  explainability. Rejected.
- **Vertical slice of the full pipeline with stubs at every stage.** Tempting,
  but the stubs become load-bearing and misrepresent progress. Rejected in
  favour of fewer, real capabilities.

## Consequences

- Slower to "look impressive", faster to have something that genuinely works.
- The repository stays small and understandable; every file has a reason.
- Documentation must always distinguish IMPLEMENTED from PLANNED.
- Requires discipline to resist pulling future work forward.
