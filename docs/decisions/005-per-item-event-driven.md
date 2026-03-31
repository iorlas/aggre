# 005: Per-item event-driven processing over batch cron

**Status:** Active
**Date:** 2026-03-08

## Why
Batch cron workflows (poll DB every N minutes, process all pending) caused: delayed processing of new items, batch orphaning on worker crash, and no per-item observability in Hatchet UI.

## Decision
Collectors emit `item.new` events for each discovered item. Downstream workflows (webpage, transcription, comments) trigger per-item with Hatchet concurrency control (`max_runs` per domain/source). Each item gets its own workflow run with independent retry and timeout.

## Not chosen
- Batch cron for all stages -- delays processing, batch orphaning, no per-item retry
- Hybrid (event for some, cron for others) -- inconsistent model, harder to reason about. (Note: collection itself still runs on cron, only downstream processing is event-driven)

## Consequence
Each new item creates a workflow run. Hatchet manages queuing and concurrency. `max_runs` must be tuned per workflow to avoid overwhelming external APIs.
