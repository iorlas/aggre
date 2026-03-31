# 001: Medallion architecture for data processing

**Status:** Active
**Date:** 2026-02-15

## Why
Need to separate raw external data from transformed queryable data, with the ability to replay processing without re-fetching from APIs.

## Decision
Two-layer medallion: Bronze stores raw API responses as immutable files on the filesystem (`data/bronze/{source_type}/{external_id}/`). Silver stores normalized, deduplicated entities in PostgreSQL.

## Not chosen
- Single-layer (raw + processed in same DB) -- couples source fidelity with query optimization, can't replay processing
- Three-layer with Gold -- premature; no analytics consumers yet

## Consequence
Every processing stage must read from bronze and write to silver. Bronze filesystem layout is a stable contract.
