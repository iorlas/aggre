# Formal Verification PoC — TLA+, Quint, FizzBee

Comparison of three formal verification tools modeling the Aggre pipeline's null-check queue pattern. Each tool modeled the **content pipeline** (parallel workers, two-phase download+extract) and the **enrichment pipeline** (sequential, multi-platform with a known partial failure bug).

Date: 2026-03-01

## Results Summary

### Bug Detection

All three tools successfully detected the **enrichment partial failure bug** — when one platform (HN/Lobsters) persistently fails, `enriched_at` stays NULL and the item is re-queried indefinitely.

| Tool | Bug Found | Counterexample Quality |
|------|-----------|----------------------|
| TLA+ | Yes — `NoInfiniteReprocess` violated at state 38 | Full trace: 38 states showing 3 reprocessing cycles |
| Quint | Yes — `NoReprocessing` violated | 9-state condensed trace showing 2 reprocessing cycles |
| FizzBee | Yes — `PartialFailureReprocess` violated | 4-run trace showing persistent Lobsters failure |

### Content Pipeline — Safety Properties

| Property | TLA+ | Quint | FizzBee |
|----------|------|-------|---------|
| NoDoubleProcessing | PASS | PASS | PASS |
| MonotonicProgress | PASS | PASS | PASS |
| SensorGuard | PASS | PASS | PASS |
| PhaseOrder | PASS | PASS | PASS |
| MutualExclusion | PASS | PASS | PASS |

### Content Pipeline — Liveness Properties

| Property | TLA+ | Quint | FizzBee |
|----------|------|-------|---------|
| AllComplete | PASS | N/A (spurious failure) | PASS |
| MonotonicText | PASS | N/A | N/A |
| MonotonicError | PASS | N/A | N/A |
| MonotonicFetchedAt | PASS | N/A | N/A |

### Enrichment Pipeline

| Property | TLA+ | Quint | FizzBee |
|----------|------|-------|---------|
| SensorGuard | PASS | PASS | PASS |
| MonotonicProgress | PASS | PASS | PASS |
| NoDoubleProcessing | N/A | PASS | PASS |
| InfiniteReprocess (bug) | VIOLATED | VIOLATED | VIOLATED |

## Performance Comparison

> **Warning:** These numbers are not directly comparable. See "Fairness of Comparison" below.

| Metric | TLA+ (TLC) | Quint (Apalache) | FizzBee |
|--------|-----------|-----------------|---------|
| **Content: States** | 16,120 gen / 5,430 distinct | ~10-step bounded | 174 nodes / 58 unique |
| **Content: Time** | ~1s | ~20s | ~47ms |
| **Content: Model size** | 3 items, 2 workers | 3 items, 2 workers | **2 items**, 2 workers |
| **Content: Granularity** | 23 labels (fine) | Comparable to TLA+ | **7 atomic actions (coarse)** |
| **Enrichment: States** | 820 gen / 649 distinct | ~10-step bounded | 130 nodes |
| **Enrichment: Time** | <1s | ~12s (bug), ~257s (bounded) | ~28ms |
| **Enrichment: Model size** | 3 items | 3 items | **2 items** |

### Fairness of Comparison

The raw numbers above are **misleading**. Three factors make this an uneven comparison:

1. **Different model sizes**: TLA+/Quint used 3 items; FizzBee used 2. Each additional item multiplies the state space.
2. **Different granularity**: TLA+ has 23 fine-grained atomic steps across 4 processes; FizzBee has 7 coarse atomic actions. TLC explores interleavings between every label; FizzBee collapses multi-step operations into single transitions.
3. **Per-state efficiency**: TLC is ~4.5x faster per state (0.18ms vs 0.81ms). FizzBee only appears fast because it checked 93x fewer states.

### Agent Authoring Time (the real bottleneck)

Model checker runtime is negligible. The AI agent writing specs dominated:

| Agent | Wall-clock | Tool calls | Checker time |
|-------|-----------|------------|--------------|
| FizzBee | 574s (~9.5 min) | **110** | 75ms |
| TLA+ | 738s (~12.3 min) | **49** | ~4s |
| Quint | 1015s (~17 min) | **74** | ~289s |

FizzBee needed **2.2x more tool calls** than TLA+ despite producing simpler specs with fewer items. TLA+ has far more training data (decades of examples) making the LLM more efficient at writing it. FizzBee's "easy to learn" advantage applies to humans, not LLMs.

## Feature Comparison

| Feature | TLA+ | Quint | FizzBee |
|---------|------|-------|---------|
| **Syntax** | ASCII math (PlusCal helps) | TypeScript-like, typed | Python-like |
| **Learning curve** | Steep (~days) | Moderate (~hours) | Easy (~10 min) |
| **Module reuse** | Yes (EXTENDS, INSTANCE) | Yes (import) | No module system |
| **Liveness checking** | Full support (WF/SF) | Experimental (spurious failures) | Supported (`always eventually`) |
| **State storage** | Disk-backed (scales) | Bounded model checking (SMT) | In-memory only |
| **Unit tests** | No built-in | Yes (`run` declarations) — 17 tests | No |
| **Type system** | Weak (TLC runtime checks) | Strong (compile-time) | None |
| **IDE support** | TLA+ Toolbox, VS Code | VS Code extension | Web playground |
| **Ecosystem** | Largest (decades, AWS/Azure) | Growing (Cosmos/IBC) | Minimal |
| **Maturity** | Production-grade | Pre-1.0 (v0.31.0) | Early (v0.3.1) |

## Modularity Comparison

| Aspect | TLA+ | Quint | FizzBee |
|--------|------|-------|---------|
| **Shared module** | `NullCheckQueue.tla` — abstract pattern, EXTENDS in pipelines | `null_check_queue.qnt` — typed module with import | None — pattern duplicated |
| **Code reuse** | INSTANCE with parameter substitution | Module import with type-safe constants | Copy-paste |
| **Parameterization** | CONSTANTS in .cfg files | `const` declarations with type annotations | Global variables |
| **Files for 2 pipelines** | 7 files (3 .tla + 4 .cfg) | 4 files (3 .qnt + run.sh) | 3 files (2 .fizz + run.sh) |

## Error Message Quality

### TLA+ (TLC)
- Verbose but complete counterexample traces
- Shows every state transition with all variable values
- State numbers reference the full state graph
- Requires TLA+ knowledge to parse

### Quint (Apalache)
- Clean, condensed counterexample traces
- Shows state transitions with typed values
- Easier to read than TLA+ for developers
- Some spurious failures from missing fairness support

### FizzBee
- Most readable counterexamples (Python-like)
- Generates HTML visualization of error states (`error-states.html`)
- Graph DOT files for state space visualization
- Counterexamples are action-oriented, easy to map to code

## Strengths and Weaknesses

### TLA+ (PlusCal + TLC)
**Strengths:**
- Full liveness checking with fairness (WF/SF) — no spurious failures
- Disk-backed state exploration — scales to large models
- Most properties verified (8 for content, including all temporal)
- Decades of ecosystem, community specs, battle-tested
- Counterexample traces are exhaustive

**Weaknesses:**
- Syntax is alien to most developers (even PlusCal has a learning curve)
- No built-in unit tests or REPL
- Requires separate .cfg files for each verification run
- No type system (errors caught at runtime)

### Quint
**Strengths:**
- TypeScript-like syntax — readable by any developer
- Strong type system catches errors at compile time
- Built-in unit tests (`run` declarations) — 17 tests as executable documentation
- Module system with imports — cleanest code reuse
- REPL for interactive exploration

**Weaknesses:**
- Liveness checking broken (Apalache lacks fairness support)
- Slower than TLC (~20s vs ~1s for safety checks)
- Pre-1.0 — some features experimental or broken
- Bounded model checking (10-step default) may miss deep bugs
- Apalache SMT solver overhead

### FizzBee
**Strengths:**
- Python-like syntax — lowest barrier to entry for humans
- HTML visualization of counterexamples
- Liveness checking works out of the box

**Weaknesses:**
- No module system — must duplicate patterns
- In-memory only — will hit memory limits on larger models
- Slowest per-state (0.81ms vs TLC's 0.18ms) — only appears fast due to smaller models
- No type system, no unit tests
- Smallest ecosystem, least documentation
- Single-threaded model checker
- Worst LLM authoring efficiency (2.2x more tool calls than TLA+)

## Verdict

### For the Aggre project specifically

**Recommended: TLA+ (PlusCal + TLC)**

TLA+ is the only tool that verified ALL properties including liveness with fairness. It has the fastest per-state model checker, scales to large models (disk-backed), produces the most detailed counterexamples, and is most efficiently authored by AI agents (training data advantage).

**Quint** has the best developer experience (types, tests, imports) but broken liveness is a dealbreaker. Worth revisiting when Apalache supports fairness or the TLC backend matures.

**FizzBee is not recommended.** The "easy syntax" advantage is irrelevant for LLM-authored specs. Slower per-state, no module system, in-memory only, and required the most agent effort to produce.

See `docs/researches/formal-verification-poc-results.md` for the full analysis.

## How to Run

```bash
# TLA+ (requires TLA+ Toolbox installed)
bash .planning/verification/run.sh all
```
