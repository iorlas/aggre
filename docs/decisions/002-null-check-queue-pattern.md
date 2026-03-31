# 002: Null-check queue pattern for processing state

**Status:** Active
**Date:** 2026-02-20

## Why
Need to track which items have been processed without adding orchestration-specific status columns. The processing pipeline has multiple independent stages (webpage download, transcription, comments) that run concurrently.

## Decision
Use NULL columns as implicit work queues: `text IS NULL` means "needs processing," non-NULL means "done." Each stage queries for NULLs, processes, and fills the column. PostgreSQL partial indexes on `WHERE column IS NULL` make these queries efficient.

## Not chosen
- Explicit status enum columns -- adds orchestration state to the data model, creates coupling between data layer and scheduler
- External task queue (Redis, RabbitMQ) -- additional infrastructure for a pattern PostgreSQL handles natively
- Hatchet-only tracking -- can't query processing state with SQL for dashboards/debugging

## Consequence
Adding a new processing stage means adding a nullable column. The NULL/non-NULL boundary is the only contract between stages.
