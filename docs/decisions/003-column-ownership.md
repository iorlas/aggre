# 003: Column ownership for concurrent workflow safety

**Status:** Active
**Date:** 2026-03-10

## Why
Webpage download and transcription workflows run concurrently on the same `silver_content` rows. Need safe concurrent writes without row-level locking or contention.

## Decision
Each processing stage owns exclusive columns. No two stages write the same column on the same row. Partitioned by domain: webpage owns `text`/`title` for non-YouTube content, transcription owns `text`/`detected_language`/`transcribed_by` for YouTube content. CEL filters in Hatchet ensure partitioning.

## Not chosen
- Row-level locking (`SELECT FOR UPDATE`) -- adds contention under batch processing, defeats concurrency
- Optimistic concurrency (version column) -- over-engineered for our write pattern where stages never conflict
- Separate output tables per stage -- unnecessary indirection when column partitioning is sufficient

## Consequence
Adding a new processing stage requires its own columns or its own table. The CLAUDE.md Column Ownership table is the operational quick-reference for this rule.
