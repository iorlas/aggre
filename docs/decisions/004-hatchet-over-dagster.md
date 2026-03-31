# 004: Hatchet over Dagster for workflow orchestration

**Status:** Active
**Date:** 2026-03-07

## Why
Dagster orchestrates jobs (asset-centric batches). Aggre needs to orchestrate items (per-URL task processing with concurrency control). The abstraction mismatch caused: sensors polling status columns, no per-item retry, batch orphaning on failure.

## Decision
Migrate to Hatchet. Business logic was already decoupled from Dagster (pure functions wrapped in `@op`). Migration replaced the orchestration wrapper only. Hatchet provides per-item workflow runs, built-in concurrency control via `ConcurrencyExpression`, and per-task retry.

## Not chosen
- Dagster assets -- right model for data pipelines, wrong model for per-item task processing
- Temporal -- heavier infrastructure, more complex SDK, overkill for our scale
- Celery -- no built-in concurrency grouping, no workflow DAGs, would need custom orchestration on top
- Keep Dagster with workarounds -- sensor anti-patterns and batch orphaning were fundamental mismatches, not fixable with better config

## Consequence
Workflows live in `src/aggre/workflows/`. Hatchet runs as a Docker service with its own PostgreSQL. Worker started via `python -m aggre.workflows`.
