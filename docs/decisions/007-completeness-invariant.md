# 007: Completeness invariant for processing stages

**Status:** Active
**Date:** 2026-03-01

## Why
Processing stages that silently skip inputs make monitoring unreliable. If a stage drops items without recording why, dashboards undercount, audits miss items, and consumers must replicate internal skip logic to get correct totals.

## Decision
Every processing component is a total function over its input domain: every input must exit with an explicit disposition -- processed, skipped (with reason), or errored (with details). No input may be silently dropped. The completeness invariant is: `processed + skipped + errored = total_inputs`.

## Not chosen
- Silent filtering (skip without recording) -- consumers can't distinguish "not yet processed" from "intentionally skipped"
- Implicit skip via NULL ambiguity -- NULL output means both "pending" and "skipped," breaking monitoring

## Consequence
New processing stages must account for every item in their input scope. Skip records are data, not implementation details.
