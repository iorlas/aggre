# Formal Verification PoC Results

Follow-up to [formal-verification-for-concurrency.md](formal-verification-for-concurrency.md) and [formal-verification-ecosystem.md](formal-verification-ecosystem.md). We modeled the actual Aggre pipeline in TLA+, Quint, and FizzBee to compare hands-on.

## What We Modeled

Two Aggre pipeline stages, both using the null-check queue pattern (`WHERE field IS NULL AND error IS NULL`):

1. **Content pipeline** — two-phase (download→extract), parallel download workers (ThreadPoolExecutor), sequential extraction. Most complex stage.
2. **Enrichment pipeline** — sequential, multi-platform (HN + Lobsters), known partial failure bug where one platform failing leaves the item in an infinite reprocessing loop.

Specs live in `.planning/verification/{tlaplus,quint,fizzbee}/`.

## Bug Found

All three tools confirmed the same real bug in `src/aggre/dagster_defs/enrichment/job.py:92`:

```python
if not failed:
    update_content(engine, row.id, enriched_at=now_iso())
# BUG: when failed=True, enriched_at stays NULL, no error recorded
# → item matches WHERE enriched_at IS NULL every run → infinite reprocessing
```

If HN or Lobsters persistently fails for a URL (rate limit, API down, URL blocked), that item gets re-queried and re-processed on every enrichment job run forever, wasting API calls.

The content pipeline passed all safety and liveness properties — no bugs found.

## Results by Tool

### TLA+ (PlusCal + TLC)

Model: 3 items, 2 workers. 4 PlusCal processes (sensor, 2 download workers, extractor).

| Spec | States (generated/distinct) | Time | Properties | Result |
|------|----------------------------|------|------------|--------|
| Content | 16,120 / 5,430 | ~1s | 8 (4 safety + 4 liveness) | All pass |
| Enrichment (bug) | 820 / 649 | <1s | NoInfiniteReprocess | Violated at state 38 |
| Enrichment (safe) | 2,568 / 1,997 | ~2s | SensorGuard, MonotonicEnriched | All pass |

Liveness checking works correctly with `fair process` + `WF_vars`. The counterexample trace for the enrichment bug showed 3 full reprocessing cycles across multiple job runs.

### Quint (Apalache backend)

Model: 3 items, 2 workers. 17 unit tests across 3 modules.

| Spec | Invariant | Time | Result |
|------|-----------|------|--------|
| NullCheckQueue | NoDoubleProcessing, MonotonicProgress, SingletonJob | ~17s | All pass |
| Content | 5 invariants | ~20s | All pass |
| Enrichment | NoReprocessing | ~12s | Violated (counterexample in 9 states) |
| Enrichment | BoundedReprocessing (<=3) | ~257s | Violated at 20 steps |

Liveness checking is broken — Apalache v0.51.1 lacks fairness constraint support. Every `eventually(...)` property produces spurious counterexamples where the system stutters forever (sensor loops without acting). Cannot distinguish real liveness bugs from false positives.

Unit tests (`run` declarations) all pass and serve as executable documentation.

### FizzBee

Model: 2 items, 2 workers. 7 atomic actions per spec.

| Spec | Nodes / Unique states | Time | Properties | Result |
|------|----------------------|------|------------|--------|
| Content | 174 / 58 | 47ms | 6 (5 safety + 1 liveness) | All pass |
| Enrichment | 130 | 28ms | 4 safety | PartialFailureReprocess violated |

Liveness works. Generated HTML visualization of counterexample state graph.

## The Comparison Was Not Fair

Three critical differences make the raw numbers misleading:

### 1. Different model sizes

TLA+ and Quint used 3 items; FizzBee used 2. Adding a third item multiplies the state space because each item's fields (`text`, `error`, `fetched_at`) are independent variables.

### 2. Different granularity

TLA+ (PlusCal) has **23 program-counter labels** across 4 processes — each label is an atomic step, and TLC explores every interleaving between them. FizzBee has **7 coarse `atomic action` blocks** — each collapses multiple operations into a single transition.

This means TLA+ explored interleavings *within* what FizzBee treated as indivisible. TLA+ found more intermediate states (mid-claim, mid-release), but for this system those interleavings are arguably unreachable — `update_content()` is a single SQL transaction.

### 3. Per-state efficiency tells the real story

| Tool | Distinct states | Time | Per state |
|------|----------------|------|-----------|
| TLA+ (TLC) | 5,430 | ~1s | **0.18ms** |
| FizzBee | 58 | 47ms | **0.81ms** |

TLC is **~4.5x faster per state** than FizzBee. FizzBee only appears fast because it checked 93x fewer states. Given a 3-item model with TLA+-level granularity, FizzBee would likely be both slower and at risk of hitting its in-memory ceiling.

Quint's ~20s times are dominated by Apalache/JVM startup overhead, not state exploration. It uses bounded model checking (SMT solver), a fundamentally different approach than exhaustive enumeration.

### 4. Agent authoring time dwarfed model-checking time

| Agent | Wall-clock time | Tool calls | Model checker time |
|-------|----------------|------------|-------------------|
| FizzBee | 574s (~9.5 min) | 110 | 75ms |
| TLA+ | 738s (~12.3 min) | 49 | ~4s |
| Quint | 1015s (~17 min) | 74 | ~289s |

The AI agent spent 99%+ of time writing and debugging specs, not running checkers. FizzBee's "easy to learn" advantage didn't help — the agent needed **2.2x more tool calls** than TLA+ to produce a simpler spec with fewer items. TLA+ has far more training data (decades of examples, AWS/Azure specs) which made the agent more efficient at writing it.

The "10-minute learning curve" claim for FizzBee applies to humans, not LLMs. For LLM-authored specs, ecosystem maturity and training data coverage dominate.

## Revised Tool Assessment

| Dimension | TLA+ | Quint | FizzBee |
|-----------|------|-------|---------|
| **Safety checking** | Full exhaustive | Bounded (10-step default) | Full exhaustive |
| **Liveness checking** | Full (WF/SF fairness) | Broken (no fairness) | Works |
| **Per-state speed** | Fastest (0.18ms) | N/A (SMT-based) | Slow (0.81ms) |
| **Scalability** | Disk-backed, scales | SMT overhead, ~20s startup | In-memory, will OOM |
| **Module reuse** | EXTENDS/INSTANCE | import (cleanest) | None (copy-paste) |
| **LLM authoring** | Best (most training data, fewest tool calls) | Good (typed, familiar syntax) | Worst (2x tool calls, Starlark quirks) |
| **Unit tests** | None built-in | 17 `run` tests (best DX) | None |
| **Bug detection** | 38-state trace, most detailed | 9-state trace, clean | 4-run trace, HTML viz |
| **Counterexample quality** | Most thorough | Most readable | Best visualization |

## Verdict

**TLA+ (PlusCal + TLC) wins.** It is the only tool that:
- Verified all properties including liveness with fairness
- Has the fastest per-state model checker
- Scales to large models (disk-backed)
- Produces the most detailed counterexamples
- Is most efficiently authored by AI agents (training data advantage)

**Quint is the best DX** (types, tests, imports) but broken liveness is a dealbreaker. Revisit when Apalache supports fairness or the TLC backend matures. The unit testing capability (`run` declarations) is genuinely useful — TLA+ has nothing comparable.

**FizzBee is not recommended.** The "easy syntax" advantage is irrelevant for LLM-authored specs. The in-memory model checker, lack of module system, and slower per-state performance make it strictly worse than TLA+ for production use. The HTML state graph visualization is nice but doesn't compensate.

### Recommended workflow

1. Write TLA+ (PlusCal) specs for concurrency-critical designs
2. Run TLC to verify safety + liveness
3. Feed verified spec to Claude when implementing — spec-first development
4. Consider Quint for safety-only checks where unit tests and readable syntax matter more than liveness
