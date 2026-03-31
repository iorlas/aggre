# Formal Verification Ecosystem Research

Deep research into tools for catching concurrency bugs in system design, with focus on what's practical for modeling a PostgreSQL-based task queue with `FOR UPDATE SKIP LOCKED`.

## Tool Map

```
You write spec
    |
    v
+------------------+     +------------------+     +------------------+
|     Quint        |     |     TLA+         |     |    FizzBee       |
| (TypeScript-ish) |     | (ASCII math)     |     | (Python-like)    |
| typed, REPL,     |     | PlusCal sugar    |     | Starlark-based   |
| test runner      |     | most expressive  |     | crash simulation |
+--------+---------+     +--------+---------+     +--------+---------+
         |                        |                        |
    compiles to TLA+         native TLA+            independent
         |                        |                        |
         v                        v                        v
+------------------+     +------------------+     +------------------+
|    Apalache      |     |      TLC         |     | FizzBee checker  |
| symbolic (Z3)    |     | explicit-state   |     | BFS in-memory    |
| bounded checking |     | exhaustive       |     | Go interpreter   |
| fast invariants  |     | full liveness    |     | single-threaded  |
+--------+---------+     +------------------+     +------------------+
         |
         v
+------------------+
|       Z3         |
| SMT solver       |
| (Microsoft)      |
+------------------+
```

---

## TLA+ Ecosystem

### What It Is
Formal specification language by Leslie Lamport. ASCII representation of temporal logic of actions. The gold standard for specifying concurrent/distributed systems.

### Tooling

| Tool | Status | Purpose |
|------|--------|---------|
| **TLC** | Mature (25+ years) | Explicit-state model checker. Multi-threaded (16+ cores), disk-backed. The workhorse |
| **SANY** | Mature | Parser. Used by all tools |
| **VS Code Extension** | Active dev | Replacing Eclipse-based Toolbox. Has AI tool support |
| **TLAPS** | Niche | Proof system (Isabelle, Z3, Zenon backends). For mathematical proofs |
| **Apalache** | Slowing dev | Symbolic model checker (Z3). See dedicated section below |
| **Spectacle** | Experimental | Web-based interpreter/playground |
| **CommunityModules** | Stable | 24 reusable modules (SequencesExt, FiniteSetsExt, Functions, Graphs, JSON, CSV, etc.) |

### TLA+ Foundation
Established after Lamport's 2024 retirement from Microsoft Research. Funds core contributors (Andrew Helwer, Markus Kuppe, Calvin Loncaric). Pursuing "one billion states per minute" bytecode interpreter (potential 1000x TLC speedup).

### Existing Specs Relevant to Our Queue

| Spec | What | Source |
|------|------|--------|
| **boring-task-queue** | PostgreSQL task queue with locking, timeouts, healthchecks | [github.com/arnaudbos/boring-task-queue](https://github.com/arnaudbos/boring-task-queue) |
| **BlockingQueue** | Producer-consumer with bounded buffer. **Closest analog to FOR UPDATE SKIP LOCKED** | [github.com/lemmy/BlockingQueue](https://github.com/lemmy/BlockingQueue) |
| **SnapshotIsolation** | PostgreSQL MVCC modeling | [github.com/will62794/snapshot-isolation-spec](https://github.com/will62794/snapshot-isolation-spec) |
| **Reusable Communication Primitives** | Modular library for perfect/fair-loss/stubborn links | [SBC Digital Library](https://sol.sbc.org.br/index.php/wtf/article/view/35652) |
| **tlaplus/Examples** | 109 specs (Paxos, 2PC, Raft, DiningPhilosophers, ReadersWriters, Disruptor) | [github.com/tlaplus/Examples](https://github.com/tlaplus/Examples) |
| **Azure Cosmos DB** | Five consistency levels modeled | [github.com/Azure/azure-cosmos-tla](https://github.com/Azure/azure-cosmos-tla) |
| **MongoDB Distributed Txns** | 2PC, snapshot isolation, WiredTiger | [github.com/mongodb-labs/vldb25-dist-txns](https://github.com/mongodb-labs/vldb25-dist-txns) |
| **Kafka Transactions** | Multi-iteration specs (TLA+ and FizzBee) | [github.com/Vanlightly/kafka-tlaplus](https://github.com/Vanlightly/kafka-tlaplus) |

### Learning Path (recommended order)

1. **learntla.com** (FREE, Hillel Wayne) — PlusCal first, then pure TLA+. Best starting point
2. **Lamport's Video Course** (FREE) — state machines, 2PC, Paxos
3. **BlockingQueue tutorial** — teaches through incremental git commits, closest to our use case
4. **David Beazley's "An Introduction to TLA+"** — concise, practical
5. **Jack Vanlightly's Primer** — real-world verification perspective
6. **"Specifying Systems" by Lamport** (FREE PDF) — definitive reference

**Time to productivity:** AWS reports 2-3 weeks for engineers (entry-level to principal). Marc Brooker: "two days of modeling paying for itself."

### Industry Adoption

| Company | What They Verify |
|---------|-----------------|
| **AWS** | S3, DynamoDB, EBS, EC2, IoT, Aurora, MemoryDB. "Executive management proactively encouraging teams" |
| **Azure** | Cosmos DB consistency levels |
| **MongoDB** | Distributed transactions, logless reconfig, Raft |
| **Datadog** | Courier message queuing (5.5M states verified) |
| **Confluent** | Kafka transactions |
| **Elastic, CockroachDB, Oracle** | Various protocols |

### Community Opinions

**Pain points (recurring):**
- ASCII syntax (`/\`, `\/`, `\E`) is the #1 barrier — engineers spend meeting time on syntax, not semantics
- New users treat it as a programming language, not a specification tool
- PlusCal hides "wildly different semantics" behind familiar-looking code
- Scattered documentation (2024 survey complaint)
- Maintenance burden: "how do teams keep specs in sync as systems evolve?"

**Strengths (recurring):**
- "Prevented subtle, serious bugs that we would not have found via any other technique" (AWS)
- Primarily a **thinking tool** — even before model checking finds bugs, writing the spec clarifies the design
- "Two days of modeling paid for itself" (Marc Brooker, AWS)
- MongoDB: developed safe reconfiguration protocol "in just a couple of weeks"

### AI/LLM Integration

- **SYSMOBENCH (2025)**: Claude Sonnet 4 outperforms other models on TLA+ generation, but struggles with complex distributed protocols. TLA+ was the language where LLMs performed best (likely more training data)
- **TLAiBench**: TLA+ Foundation benchmark for LLM evaluation (9 problems, 4 metrics). No published aggregate results yet
- **VS Code extension**: Native AI tool support for co-piloting specs
- **Specula**: Won 2025 GenAI TLA+ Challenge — auto-derives TLA+ from source code. Controversial (see previous research doc)

Sources: [learntla.com](https://learntla.com/), [tlaplus/Examples](https://github.com/tlaplus/Examples), [tlaplus/CommunityModules](https://github.com/tlaplus/CommunityModules), [AWS formal methods paper](https://lamport.azurewebsites.net/tla/formal-methods-amazon.pdf), [Datadog blog](https://www.datadoghq.com/blog/engineering/formal-modeling-and-simulation/), [MongoDB blog](https://www.mongodb.com/company/blog/engineering/formal-methods-beyond-correctness-isolation-permissiveness-distributed-transactions), [Current state of TLA+ dev](https://ahelwer.ca/post/2025-05-15-tla-dev-status/), [Marc Brooker's blog](https://brooker.co.za/blog/2024/04/17/formal.html)

---

## Quint Ecosystem

### What It Is
Modern specification language by Informal Systems. Compiles to TLA+ but with TypeScript/functional syntax. Typed, has REPL, simulator, test runner.

### Tooling

| Tool | Status | Description |
|------|--------|-------------|
| `quint repl` | Working | Interactive exploration |
| `quint run` | Working | Random simulation (like stateful property-based testing). Multi-threaded since v0.29.0 |
| `quint test` | Working | Unit test runner |
| `quint verify` | Working | Model checking via Apalache (auto-downloads). Bounded (default 10 steps) |
| `quint compile` | Working | Transpiles to TLA+ or JSON |
| `quint typecheck` | Working | Static type inference + effect checking |
| VS Code extension | Basic | ~950 installs. Syntax highlighting, error diagnostics, hover types. No auto-completion |
| `quint lint` | **Not implemented** | Listed but non-functional |

**Latest release**: v0.31.0 (Feb 2026). Monthly release cadence. 66 releases total. Rust-based evaluator now default.

**v0.31.0 key change**: `--backend tlc` option added — first-class TLC support arriving (previously Apalache-only for verification).

### TLA+ Compatibility

| Question | Answer |
|----------|--------|
| Can Quint import TLA+ specs? | **No.** One-way only (Quint → TLA+). No `tla2quint` converter |
| Can Quint use CommunityModules? | **No.** Own module system ("spells"), no TLA+ module interop |
| Can Quint output run on TLC? | **Yes, with friction.** `quint compile --target tlaplus` produces TLA+. TLC backend support in v0.31.0 |
| What's lost vs native TLA+? | No proofs (TLAPS), no refinement, no recursion, no model values/symmetry sets, partial temporal properties only |
| No liveness checking? | **Correct.** Cannot verify "every task eventually gets processed" — safety only |

### Existing Specs

**Real-world:**
- ZKSync governance (50+ invariants, found 5 violations lighter tools missed)
- ChonkyBFT consensus (Matter Labs / ZKsync)
- Tendermint consensus, IBC protocols (Informal Systems)
- Neutron DEX liquidity migration (found real bugs)

**Examples in repo:** Paxos, Two-Phase Commit, Dining Philosophers, ERC20, Tic-Tac-Toe, Cosmos ecosystem specs.

**Choreo:** Framework for distributed protocol specs with pre-built message passing abstractions. Very recent, minimal adoption.

**Database/queue specs: None exist.** Would be writing from scratch.

**Reusable modules ("spells"):** basicSpells, commonSpells, rareSpells, BoundedUInt. Must be copied manually (no package manager).

### Community Opinions

**Positive:**
- "First thing I've seen that appears approachable from a developer's perspective" (HN)
- Modern tooling (REPL, type checking, Go-to-definition) fills a real gap
- Used in production for blockchain protocol verification

**Negative:**
- Hillel Wayne (TLA+ expert): **"None of the replacements I've seen so far are mature enough for me to recommend them over TLA+"**
- "Cannot do the most powerful and useful things TLA+ does (esp. refinement)" (HN)
- "Kinda raw at this point of development" (HN Aug 2024)
- 29 open bugs including in core module system (flattening, transpilation)
- Pre-1.0, breaking changes expected

**Konnov (Quint co-creator):** Still uses TLA+ himself. "It's up to the customer."

### LLM Integration
- **quint-llm-kit**: Docker environment with Claude Code, MCP servers for Quint knowledge. "Originated from internal use, lacks comprehensive public testing"
- Quint's simpler syntax is likely easier for LLMs to generate/read than TLA+

### Key Limitation for Our Use Case
**No liveness checking.** Cannot verify "every task eventually gets processed" — only safety properties like "no task is processed twice." This is a significant gap for queue verification where you want to prove the system makes progress.

### Maturity Assessment

| Metric | Value |
|--------|-------|
| GitHub stars | 1,200 |
| Releases | 66 (monthly cadence) |
| Backing | Informal Systems (blockchain company) |
| Team | ~8 core contributors |
| License | Apache-2.0 |
| Stability | Pre-1.0, breaking changes expected |
| Risk | Priorities skew toward blockchain use cases |

Sources: [quint-lang.org](https://quint-lang.org/), [github.com/informalsystems/quint](https://github.com/informalsystems/quint), [Quint FAQ](https://quint-lang.org/docs/faq), [quint-llm-kit](https://github.com/informalsystems/quint-llm-kit), [HN Dec 2023](https://news.ycombinator.com/item?id=38694278), [HN Aug 2024](https://news.ycombinator.com/item?id=41383084)

---

## FizzBee Ecosystem

### What It Is
Python-like formal verification tool using Starlark (Google's Python subset for Bazel). Standalone model checker — no TLA+ connection.

### Tooling

| Feature | Status |
|---------|--------|
| Model checker (BFS/DFS/random) | Working |
| Web playground | Working (fizzbee.io/play) |
| CLI (`fizz`) | Working |
| Model-Based Testing (MBT) | **Go only.** No Python support |
| Performance/probabilistic modeling | Working (unique to FizzBee) |
| Visualization (block/sequence diagrams) | Working |
| Crash simulation | Working (`@state(ephemeral=...)`) |
| Homebrew install | Working |

**Latest release**: v0.3.1 (Nov 2024), binaries Apr 2025. Pre-1.0.

### Language Features

Starlark-based (Python subset). Key constructs:
- `atomic action` — no context switches (critical sections)
- `fair action` — weak/strong fairness for liveness
- `any` — non-deterministic choice (model checker explores all)
- `oneof` — explore all branches
- `always`, `eventually`, `always eventually` — temporal assertions
- `role` — actor model with state annotations
- `@state(ephemeral=["field"])` — field resets on crash

**What you CAN'T express:** No classes (use `role`), no exceptions, no imports, no generators, no `with` statements.

### The Memory Problem (CRITICAL)

**This is the dealbreaker for non-trivial models.**

Jack Vanlightly's benchmarks:
| Model | TLC | FizzBee |
|-------|-----|---------|
| Kafka 3 clients/3 brokers | 8 seconds | 1m 4s |
| Kafka 4 clients/4 brokers | 23 seconds (64GB RAM, 16 threads) | **180GB swap, killed after 5 hours** |
| Apache Paimon | Runs fine | Needed 40GB even with artificial constraints |

The checker:
- Keeps **all states in RAM** — no disk spilling
- **Single-threaded, single-process**
- Symmetry computation alone took 41 minutes for a moderate model
- Distributed checking is "planned" but not implemented

**Workarounds:** Simulation mode (random sampling), aggressive `atomic` blocks, symmetry reduction, `max_concurrent_actions` limits, artificial guard clauses.

### Existing Specs

**Official examples:** Wire Transfer, Two Phase Commit, Raft (~188 lines), Gossip Protocol, EWD426 Token Ring, Die Hard.

**Real-world:**
- Jack Vanlightly: Apache Paimon (1,198 lines), Kafka Transactions
- Lorin Hochstein: Locks/leases/fencing tokens
- Varghese Kuruvilla: Raft "the FizzBee way"

**Database/queue patterns: None exist.**

### Unique Capabilities (not in TLA+/Quint)

- **Performance modeling**: Counters tracking latency, throughput, error rates. Scipy-compatible distributions. Pre-built AWS profiles (`aws.elasticache.redis.read()`)
- **Auto-generated diagrams**: Block and sequence diagrams from specs
- **Model-Based Testing**: Generate and run thousands of action sequences, linearizability checking via Porcupine. **But Go only.**
- **Implicit crash simulation**: Annotate which state is ephemeral

### Community Opinions

**Positive:**
- "FizzBee cured my fear of formal methods after struggling with TLA+ years ago"
- "Surprisingly easy to get started with" (Lorin Hochstein)
- Jack Vanlightly: "Allowed me to model more features than I might have done with TLA+" (readability advantage)

**Negative:**
- Jack Vanlightly: Memory is the critical blocker. TLC finished in 23 seconds what FizzBee couldn't do in 5 hours with 180GB
- "Near-zero presence" on Reddit/Lobsters
- Essentially a **one-person project** (JP Kadarkarai, ex-Google GFS/Bigtable/Spanner)
- Pre-1.0, very small community

### Maturity Assessment

| Metric | Value |
|--------|-------|
| GitHub stars | ~287 |
| Total commits | 267 |
| Creator | JP Kadarkarai (sole developer, ex-Google) |
| License | Apache-2.0 |
| Community | Very small (HN-concentrated) |
| Risk | Single-person project, memory limitations, no Python MBT |

Sources: [fizzbee.io](https://fizzbee.io/), [github.com/fizzbee-io/fizzbee](https://github.com/fizzbee-io/fizzbee), [Jack Vanlightly: Paimon Part 3](https://jack-vanlightly.com/analyses/2024/7/3/understanding-apache-paimon-consistency-model-part-3), [Jack Vanlightly: Kafka Diary Entry 4](https://jack-vanlightly.com/analyses/2024/12/5/verifying-kafka-transactions-diary-entry-4-writing-an-initial-fizzbee-spec), [Surfing Complexity blog](https://surfingcomplexity.blog/2025/03/03/locks-leases-fencing-tokens-fizzbee/), [HN Show HN Apr 2024](https://news.ycombinator.com/item?id=39904256)

---

## Apalache (Symbolic Model Checker)

### What It Is
Symbolic model checker for TLA+ specs. Uses Z3 SMT solver instead of enumerating states. Created by Igor Konnov, initially funded by Informal Systems.

### How It Differs from TLC

| Dimension | TLC | Apalache |
|-----------|-----|----------|
| Approach | Enumerates every reachable state | Encodes as SMT formula for Z3 |
| Execution length | Unbounded (all reachable states) | **Bounded** (default 10 steps, practical limit 6-12) |
| Large numeric domains | Must enumerate each value | Handles symbolically (e.g., 0..2^32 trivially) |
| Liveness | Full support (WF, SF, fairness) | **Partial only** — no WF/SF/ENABLED |
| Inductive invariants | 3 hours on TwoPhase-7 | **4 seconds** on same benchmark |
| Maturity | 25+ years | ~7 years |
| Refinement | Supported | **Not supported** |
| Parallelism | Multi-threaded, disk-backed | Single-solver, memory-bound |

### When Apalache Is Better
- Large numeric domains (timestamps, counters, sequence numbers)
- Inductive invariant checking (dramatically faster)
- Quick bug-finding during iterative development
- Complex interleavings where TLC state space explodes (e.g., 10^410 Byzantine messages)

### When TLC Is Better
- **Unbounded verification** of finite-state systems
- **Liveness properties** (eventually, always-eventually, fairness)
- Learning and building intuition (fast feedback for small models)
- Refinement checking
- Mature, stable, well-understood behavior

### Integration

- Works on standard TLA+ specs (with type annotations and some restrictions — no recursion, no unbounded quantifiers)
- **Quint integration is first-class**: `quint verify` auto-downloads and invokes Apalache
- Outputs counterexamples in **ITF format** (JSON) — designed for feeding into test frameworks

### Development Status — CONCERNING

**"Apalache was spun off by Informal Systems but hasn't been adopted by the TLA+ Foundation; it's quiescent"** (Jesse Davis, 2025 TLA+ Community Event notes)

After Informal Systems divested in late 2024, Apalache is maintained by original creators on volunteer/self-funded basis. Development has slowed dramatically.

### For Our Use Case
TLC is likely sufficient for a queue model with 2-5 workers and 3-10 tasks. Apalache adds value for inductive invariant verification once the spec is stable. Use both — they're complementary ("TLA+ Trifecta" paper).

Sources: [github.com/apalache-mc/apalache](https://github.com/apalache-mc/apalache), [Apalache docs](https://apalache-mc.org/docs/apalache/index.html), [Apalache vs TLC](https://mbt.informal.systems/docs/tla_basics_tutorials/apalache_vs_tlc.html), [TLA+ Trifecta paper](https://arxiv.org/abs/2211.07216), [ChonkyBFT case study](https://protocols-made-fun.com/consensus/matterlabs/quint/specification/modelchecking/2024/07/29/chonkybft.html), [Jesse Davis notes](https://emptysqua.re/blog/2025-tlaplus-community-event/)

---

## Z3 (SMT Solver)

### What It Is
Satisfiability Modulo Theories solver by Microsoft Research. Answers: "Is there any assignment of values that makes this formula true?" Supports integers, reals, strings, arrays, bitvectors, algebraic datatypes.

### Role in the Stack

**Z3 is the engine, not the interface.** For concurrent system verification, you never use Z3 directly. Instead:

| Tool | Uses Z3? | How |
|------|----------|-----|
| TLC | No | Explicit state enumeration |
| Apalache | **Yes** | Translates TLA+ specs to SMT constraints |
| Quint (via Apalache) | **Yes** | Indirectly through Apalache |
| TLAPS | Yes | One of several proof backends |
| FizzBee | No | Own Go-based BFS explorer |
| Dafny | Yes | Via Boogie intermediate language |
| CrossHair | Yes | Python symbolic execution |
| AWS Zelkova | Yes | ~1 billion SMT queries/day for IAM policy analysis |

### Would You Use Z3 Directly?

**For our use case: No.** Modeling a concurrent task queue directly in Z3 would mean reimventing Apalache poorly.

**Z3 directly makes sense for:** Policy/rule equivalence checking, constraint satisfaction (scheduling), compiler optimization verification, cryptographic implementation verification.

### Python Bindings

`z3-solver` on PyPI (v4.16.0.0, Feb 2026). Pythonic API with operator overloading. "An absolute pleasure" per practitioners. But: for concurrent system verification, use it through Apalache, not directly.

### CrossHair — Python Contract Verification

[CrossHair](https://github.com/pschanely/CrossHair) uses Z3 for symbolic execution of Python functions. Verifies pre/post-conditions on pure functions. **Cannot verify concurrent database behavior** — works on function contracts only. 1.3k GitHub stars.

`hypothesis-crosshair` integrates Z3 as a Hypothesis backend. Currently "unlikely to outperform Hypothesis' existing backend on realistic workloads."

### For Our Use Case

Z3 is useful **only as the engine powering Apalache**. We would never interact with it directly for queue verification. CrossHair is not relevant — it verifies pure function contracts, not concurrent system properties.

Sources: [github.com/Z3Prover/z3](https://github.com/Z3Prover/z3), [z3-solver on PyPI](https://pypi.org/project/z3-solver/), [CrossHair](https://github.com/pschanely/CrossHair), [AWS Zelkova](https://www.amazon.science/blog/a-billion-smt-queries-a-day), [Teleport Z3 RBAC](https://goteleport.com/blog/z3-rbac/), [PyPy Z3 JIT rules](https://pypy.org/posts/2024/07/finding-simple-rewrite-rules-jit-z3.html)

---

## Head-to-Head Comparison

### For modeling a PostgreSQL task queue with FOR UPDATE SKIP LOCKED

| Dimension | TLA+ (TLC) | Quint (Apalache) | FizzBee |
|-----------|------------|-------------------|---------|
| **Syntax** | ASCII math (`/\`, `\E`) | TypeScript-ish, typed | Python-like (Starlark) |
| **Learning curve** | 2-3 weeks (per AWS) | Days (if you know TS) | Hours (if you know Python) |
| **Liveness** ("every task finishes") | Full support | **No** (Apalache limitation) | Full support |
| **Safety** ("no double-processing") | Full support | Full support | Full support |
| **State space handling** | Disk-backed, 16+ threads | Symbolic (Z3), bounded | **In-memory only, single-thread** |
| **Existing queue specs** | boring-task-queue, BlockingQueue | None | None |
| **Ecosystem size** | 5K+ stars, 109 example specs, 24 community modules | 1.2K stars, blockchain-focused examples | 287 stars, ~10 examples |
| **Industry adoption** | AWS, Azure, MongoDB, Datadog, Confluent | Matter Labs, Informal Systems | Jack Vanlightly (one practitioner) |
| **Model-based testing** | Trace export | ITF JSON export | **Go only** (no Python) |
| **AI/LLM compat** | Best (most training data, SYSMOBENCH confirmed) | Good (simpler syntax) | Untested |
| **Performance modeling** | No | No | **Yes** (unique) |
| **Crash simulation** | Manual modeling | Manual modeling | **Built-in** (`@state(ephemeral=...)`) |
| **Stability** | Production-proven, TLA+ Foundation funded | Pre-1.0, 29 open bugs | Pre-1.0, one-person project |
| **Long-term risk** | Low | Medium (blockchain-funded priorities) | **High** (solo developer, memory limits) |
| **TLC backend** | Native | v0.31.0 (new!) | N/A |
| **Quint interop** | Quint compiles to TLA+ | Native | None |

### PoC Plan Recommendation

For the upcoming hands-on comparison, model the same system in all three:

**What to model:**
1. Task table with N tasks (status, attempts, locked_by)
2. M workers competing for tasks
3. `SELECT FOR UPDATE SKIP LOCKED` semantics (atomic claim, skip locked rows)
4. Success/failure with retry up to max_attempts
5. Worker crash and lock recovery (stale lock reaping)

**What to verify:**
- Safety: No task processed by two workers simultaneously
- Safety: Attempts never exceed max
- Safety: No task lost (all tasks reach done/failed)
- Liveness: All tasks eventually complete (TLA+ and FizzBee only — Quint/Apalache can't check this)

**What to measure:**
- Time to write the spec (learning + writing)
- Time to run model checker (2 workers, 3 tasks; then 3 workers, 5 tasks)
- Quality of error messages when invariant violations are found
- How easily Claude can read/write/modify the spec

Sources for the full document are listed in each section above.
