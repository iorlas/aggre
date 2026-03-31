# Formal Verification with TLA+

TLA+ specs live in `.planning/verification/`. They model pipeline concurrency, sensor guards, and retry logic — areas where bugs are subtle and hard to catch with tests alone.

## When to Write/Update Specs

**Write or update a spec when changing:**

- Concurrency controls — sensor guards, `default_op_concurrency_limit`, transaction boundaries
- Pipeline stages — adding/removing ops, changing phase ordering
- Mutation functions — `update_content()`, `ensure_content()`, `_upsert_discussion()`
- Retry/backoff logic — retry counts, throttle state, partial progress
- Parallel processing — ThreadPoolExecutor usage, batch processing

**Don't write specs for:**

- New collectors, URL normalization, config changes, logging
- Bronze storage (append-only, no concurrency concerns)
- UI/dashboard changes
- Test infrastructure

## Spec-First Workflow

1. **Write TLA+ spec** — model the new behavior or changed concurrency
2. **Verify with TLC** — `make verify` must pass (expected violations still violate, expected passes still pass)
3. **Implement Python** — read the `.tla` spec first, then write code matching the verified model. The spec is the source of truth for state transitions, guards, and invariants.
4. **Derive tests from the spec** — TLC counterexample traces show exact failure scenarios. Translate these into test cases (pytest parametrize or property-based tests) that exercise the same state transitions.
5. **Commit together** — spec and implementation in the same PR

### Why spec-first works with AI

Writing the spec IS the thinking. The spec captures the concurrent design decisions that are hardest to get right — state transitions, guards, invariants. Once verified, the spec serves as unambiguous context for implementation:

- **Spec as implementation context** — when implementing concurrency changes, read the corresponding `.tla` spec before writing Python. The spec defines what states are legal, what transitions are allowed, and what properties must hold. This prevents misinterpretation.
- **AI implements, humans design** — AI excels at translating a verified spec into code. It struggles with architectural and concurrent design decisions. Keep the spec human-authored; let AI handle the implementation.
- **Counterexample traces as test cases** — TLC violation traces show concrete state sequences that trigger bugs. These translate directly into regression tests.

## Running Specs

```bash
# Run all specs
make verify

# Run individual spec groups
bash .planning/verification/run.sh content
bash .planning/verification/run.sh enrichment
bash .planning/verification/run.sh enrichment-retry
bash .planning/verification/run.sh concurrent
```

Requires Java. TLA+ tools resolution order:
1. `$TLA2TOOLS` env var
2. `.planning/verification/lib/tla2tools.jar` (local copy)
3. `/Applications/TLA+ Toolbox.app/Contents/Eclipse/tla2tools.jar` (macOS Toolbox)

## Current Spec Inventory

| Spec | What it models | Key properties |
|------|---------------|----------------|
| `ContentPipeline` | Two-phase content download + extraction with parallel workers | NoDoubleProcessing, PhaseOrder, MutualExclusion, SensorGuard, AllComplete |
| `EnrichmentPipeline` | Enrichment with partial failure bug (known) | SensorGuard (PASS), NoInfiniteReprocess (VIOLATED — known bug), MonotonicEnriched (PASS), AllEnriched (PASS — liveness; state constraint limits detection of logical violation) |
| `EnrichmentRetry` | Retry system with per-platform tracking, backoff, throttle (not yet implemented in Python) | BoundedRetries, EventualTermination, NoThrottledCall, PartialProgressPreserved, PartialProgressHN, PartialProgressLobsters, NoInfiniteReprocess, SensorGuard |
| `ConcurrentJobs` | Why sensor guards prevent double-processing and lost updates | NoDoubleProcessing, NoLostUpdate, AllComplete (unsafe: violations expected; safe: all pass) |

## Conventions

- **PlusCal** for algorithm definition, translated to TLA+ with `pcal.trans`
- **String-based state values** — `"null"`, `"pending"`, `"done"`, `"failed"`, `"error"` (not TLA+ model values)
- **`fair process`** for all processes (ensures weak fairness for liveness)
- **Safety properties** as `INVARIANT` in `.cfg`
- **Liveness properties** as `PROPERTY` in `.cfg`
- **Expected violations** use `expect_fail="true"` in `run.sh` — TLC exit code is non-zero but the script continues
- **Separate `.cfg` files** for different verification scenarios of the same spec (e.g., `ConcurrentJobs_unsafe.cfg` vs `ConcurrentJobs_safe.cfg`)
- **State space bounds** — keep `NumItems` small (2-3) and use `CONSTRAINT` when needed to prevent infinite exploration
