# ADR 0003: Postgres Over MongoDB for Incident Storage

## Status
Accepted

## Date
2026-05-05

## Context
The platform stores more than free-form incident notes. It persists incidents, runs, policy decisions, actions, remediation receipts, benchmark packs, benchmark results, feedback signals, postmortem data, cost summaries, and normalized projections used for analytics and comparison. These records are connected to each other by identifiers such as `incident_id`, `run_id`, benchmark names, and policy document versions.

We needed storage that could support both operational writes and queryable analysis. The platform benefits from structured joins, projections, reporting, and predictable integrity between related records.

## Decision
We use Postgres as the primary production storage backend instead of MongoDB.

Postgres is the better fit for this project because incident operations, replay, cost tracking, policy evaluation, benchmarking, and analytics all benefit from relational structure and queryability. The application still keeps flexible JSON payloads where appropriate, but the system of record is relational.

## Alternatives Considered

### MongoDB
MongoDB would have been a reasonable option for a looser document-first design, especially early in the project when payloads changed frequently.

We did not choose it because the project matured into a platform with strong relationships between runs, incidents, actions, policy outcomes, and benchmarks. MongoDB would make the early schema evolution feel lighter, but it would make analytical queries, cross-entity reporting, and consistency checks less natural.

## Consequences

### Positive
- Better fit for normalized operational data and cross-record analytics.
- Stronger integrity between incidents, runs, policies, actions, and benchmarks.
- Easier to support cost summaries, evaluation trends, and governance reporting.
- More credible production storage choice for a DevOps incident platform.

### Negative
- Requires more up-front schema discipline than a pure document store.
- Some flexible payloads still need JSON columns or projection tables.

## Resulting Architecture Impact
Postgres acts as the preferred production store for the platform, while local JSON storage remains a development fallback. This gives the project a clean path from local experimentation to production-shaped persistence without rewriting the service layer.
