# Concurrency Tuning: Increase Worker Slots and Workflow Parallelism

**Date:** 2026-03-16
**Status:** Approved

## Problem

The Hatchet worker has 20 slots but workflows can generate 44+ concurrent tasks, causing queuing. Additionally, conservative `max_runs=1` limits on comments, discussion search, and webpage workflows bottleneck processing — discussion search has processed only 327 of 28,307 eligible items.

## Decision

Increase worker slots from 20 to 40, and raise per-workflow concurrency limits while keeping existing grouping strategies unchanged.

## Changes

| File | Setting | Old | New | Grouping |
|------|---------|-----|-----|----------|
| `workflows/__init__.py:48` | `slots` | 20 | 40 | — |
| `workflows/comments.py:69` | `max_runs` | 1 | 5 | per source (`input.source`) |
| `workflows/discussion_search.py:108` | `max_runs` | 1 | 5 | global (`'search'`) |
| `workflows/webpage.py:288` | `max_runs` | 1 | 3 | per domain (`input.domain`) |
| `workflows/transcription.py:214` | `max_runs` | 20 | 20 | global, no change |

## Effective Parallelism

| Workflow | Max concurrent tasks |
|----------|---------------------|
| Transcription | 20 |
| Comments | 15 (5 × 3 sources) |
| Discussion search | 5 |
| Webpage | 3 per unique domain |

Total possible exceeds 40 slots, so the worker slot count becomes the natural cap.

## Rationale

- **Zero API failures** observed across all sources (7,418 Reddit, 19,459 HN, 548 Lobsters comments fetched with no errors).
- **Proxies in place** — multiple proxies available for web requests.
- **Discussion search backlog** is the most urgent gap — 27,980 items awaiting search, only 327 done. At `max_runs=5`, backlog clears ~5× faster.
- **Grouping strategies preserved** — per-source for comments (each API has own rate limits), per-domain for webpage (prevents hammering single sites), global for discussion search (each item hits both HN + Lobsters).

## Risk

Low. Discussion search goes from 1 to 5 concurrent callers hitting HN Algolia and Lobsters search APIs (sequential per item, so up to 5 HN + 5 Lobsters requests in flight). While zero failures at current throughput, undocumented rate limits could surface at 5× the request rate. Mitigated by existing retry config (7 retries, backoff factor 4, max 3600s) on all workflows. If rate limiting occurs, retries handle it automatically. No code logic changes — only configuration values.

Rollback: revert the 4 values and restart the worker.

## Deployment

Worker restart picks up new values. No migration needed.

## Docstring Updates

| File | Line | Old text | New text |
|------|------|----------|----------|
| `comments.py` | 4 | "max 1 per source" | "max 5 per source" |
| `discussion_search.py` | 4 | "max 1 search at a time" | "max 5 searches at a time" |
| `webpage.py` | 4 | "max 1 per domain" | "max 3 per domain" |

## Validation

After deployment, monitor:
- Discussion search backlog decreasing at ~5× previous rate (check `discussions_searched_at IS NULL` count)
- No HTTP 429 or rate-limit errors in worker logs within 24 hours
- Worker slot utilization in Hatchet UI approaching 40

## Scope

- 4 config value changes + 3 docstring updates
- No test changes (concurrency config is in `# pragma: no cover` registration blocks)
