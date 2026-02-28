# Formal Verification for Concurrency Bugs

AI coding agents (Claude Code, etc.) lack the ability to systematically enumerate concurrent interleavings. A formal spec gives the agent a condensed, machine-checkable representation of the system — and catches bugs neither humans nor LLMs can find by code review alone.

## The Landscape

### Quint — modern language on top of TLA+

[Quint](https://github.com/informalsystems/quint) by Informal Systems. Compiles down to TLA (the logic) but with TypeScript/functional-style syntax instead of TLA+'s ASCII math.

**What it adds over TLA+:**
- Type system — all variables and constants must have types
- REPL — interactive exploration
- Simulator — random execution before full model checking
- Testing framework — `run` blocks as concrete test scenarios
- IDE support — go-to-definition, etc.

**Community opinion:**
- Positive: "First thing I've seen that appears approachable from a developer's perspective." Used in production by Matter Labs (ZKSync consensus), Informal Systems (Cosmos/IBC)
- Negative: Tooling still maturing — "risk of writing half a spec and realizing tools are broken." Less expressive than TLA+ by design. TLA+ ecosystem is much larger
- Igor Konnov (TLA+ expert, Quint maintainer): "The real problem isn't syntax — it's lacking a fast feedback loop"

**Sources:**
- https://github.com/informalsystems/quint
- https://quint-lang.org/docs/lang
- https://news.ycombinator.com/item?id=38694278
- https://lobste.rs/s/pn9v0r/quint_executable_specification
- https://protocols-made-fun.com/specification/modelchecking/tlaplus/quint/2024/10/05/tla-and-not-tla.html

### TLA+ — the gold standard

Decades of tooling, community specs, battle-tested at AWS/Azure. PlusCal provides a more procedural syntax on top.

**Strengths:** Largest ecosystem (TLC model checker, TLAPS proof system, Specula). Disk-backed state exploration handles large models. Most research tooling targets TLA+.

**Weakness:** ASCII math syntax (`/\`, `\/`, `\E`) feels alien to most programmers. Steep learning curve (~days to weeks).

### FizzBee — Python-like, quick exploration

[FizzBee](https://fizzbee.io/) uses Python-like syntax. Learn in ~10 minutes if you know Python.

**Critical limitation:** Model checker keeps entire state space in RAM, doesn't spill to disk. Even with 100GB, runs out of memory on non-trivial models. Single-threaded, distributed checking not yet implemented.

**Best for:** Quick throwaway exploration of small designs.

**Sources:**
- https://fizzbee.io/
- https://news.ycombinator.com/item?id=39943717
- https://materializedview.io/p/fizzbee-tla-and-formal-software-verification

### Stateright — embedded in Rust

[Stateright](https://www.stateright.rs/) — model checker embedded in Rust. Write model AND implementation in the same language. Includes linearizability tester. Not applicable for Python projects.

### P Language — Microsoft Research

[P](https://p-org.github.io/P/) — state machine-based DSL for async distributed systems. Used at AWS (S3 consistency) and Microsoft (Windows USB drivers). Compiles to C#/C.

## AI + Formal Methods: The Convergence

### Spec-first workflow with LLMs (confirmed by practitioners)

**Shahzad Bhatti** ("Beyond Vibe Coding"):
1. Write TLA+ spec defining states, transitions, safety properties
2. Run TLC model checker — finds edge cases automatically
3. Feed the spec to Claude — Claude implements code matching the spec
4. Generate property-based tests from execution traces

Key quote: edge cases are "defined upfront" — model checking finds bugs manual testing misses.

**Gregory Terzian** ("TLA+ in Support of AI Code Generation"):
- Wrote TLA+ specs first, then had Claude generate 100% of Rust code
- TLA+ served as "ground truth" preventing misinterpretation
- "The 10% I'm writing by hand is not code but specification"
- AI struggled with architectural/concurrent design — all major decisions were human-driven
- Implementation details were flawless when spec was precise

**Sources:**
- https://shahbhat.medium.com/beyond-vibe-coding-using-tla-and-executable-specifications-with-claude-51df2a9460ff
- https://medium.com/@polyglot_factotum/tla-in-support-of-ai-code-generation-9086fc9715c4

### Specula — extracting specs from code

[Specula](https://github.com/specula-org/Specula) won the 2025 GenAI TLA+ Challenge. It's a CLI tool:
1. Feeds source code to an LLM → gets raw TLA+ translation
2. Runs Control Flow Analysis → transforms to declarative TLA+
3. Runs TLC model checker → auto-fixes errors via RAG
4. Instruments code, captures execution traces → validates against spec

Cost for etcd Raft: ~$3.54 in LLM fees + ~1.5h manual effort.

**Community opinion (divided):**
- Supporters: "Any tool that makes formal verification more accessible should be welcome"
- Critics: "LLM generation of TLA+ is intellectual masturbation" — the point of formal methods is to improve human thinking. Generating specs from code inverts the value
- Core tension: who verifies the AI's spec matches human intent?

**Sources:**
- https://github.com/specula-org/Specula
- https://foundation.tlapl.us/challenge/index.html
- https://news.ycombinator.com/item?id=43907850

### Martin Kleppmann's thesis (Dec 2025)

AI will make formal verification mainstream because LLMs can write proofs, and proof checkers are deterministic verifiers — invalid proofs get rejected, forcing retry. Envisioned workflow: human writes spec → AI generates code + proof → checker validates.

**Lobsters pushback:**
- LLMs "absolutely eat dirt" on Agda type checkers — proof generation isn't reliable yet
- "The P=NP problem of formal verification" — is writing a spec easier than writing code?
- Model checking (finite state) is practical today. Theorem proving still needs heavy human oversight

**Sources:**
- https://martin.kleppmann.com/2025/12/08/ai-formal-verification.html
- https://benjamincongdon.me/blog/2025/12/12/The-Coming-Need-for-Formal-Specification/
- https://lobste.rs/s/zsgdbg/prediction_ai_will_make_formal

## Existing PostgreSQL Queue TLA+ Spec

**[boring-task-queue](https://github.com/arnaudbos/boring-task-queue)** by Arnaud Bos — a complete TLA+ spec for a PostgreSQL-based task queue.

Models:
- `Enqueue` — producer adds jobs (with `MaxJobs` bound)
- `Lock(w)` — worker claims up to `MaxJobsPerWorker` unlocked jobs (`FOR UPDATE SKIP LOCKED` equivalent)
- `Compute(w)` — worker completes and removes jobs
- `Timeout(w)` — worker dies, marks locked jobs as timed out
- `Healthcheck(w)` — worker refreshes its locks (heartbeat)

Safety invariants:
- `TypeOK` — no rollback on produced count, jobs only held by known workers
- `AtMostMaxJobsPerWorker` — parallelism bound respected

Liveness:
- `QueueDrained` — `<>[](queue = <<>>)` — queue eventually empties and stays empty

Config: 2 workers, 5 max jobs, 2 jobs per worker.

## Comparison: Same Queue in Three Languages

### FizzBee

```python
MAX_ATTEMPTS = 3
NUM_TASKS = 2
NUM_WORKERS = 2

action Init:
    tasks = [{"status": "pending", "attempts": 0, "locked_by": None} for _ in range(NUM_TASKS)]

atomic action ClaimTask:
    any w in range(NUM_WORKERS):
        any t in range(NUM_TASKS):
            if tasks[t]["status"] == "pending" and tasks[t]["locked_by"] is None and tasks[t]["attempts"] < MAX_ATTEMPTS:
                tasks[t]["status"] = "processing"
                tasks[t]["locked_by"] = w

atomic action ProcessSuccess:
    any t in range(NUM_TASKS):
        if tasks[t]["status"] == "processing":
            tasks[t]["status"] = "done"
            tasks[t]["locked_by"] = None

atomic action ProcessFailure:
    any t in range(NUM_TASKS):
        if tasks[t]["status"] == "processing":
            tasks[t]["attempts"] += 1
            if tasks[t]["attempts"] >= MAX_ATTEMPTS:
                tasks[t]["status"] = "failed"
            else:
                tasks[t]["status"] = "pending"
            tasks[t]["locked_by"] = None

always eventually assertion AllTasksComplete:
    return all(tasks[t]["status"] in ("done", "failed") for t in range(NUM_TASKS))
```

### TLA+ (PlusCal)

```tla
---- MODULE PostgresQueue ----
EXTENDS TLC, Integers, Sequences, FiniteSets

CONSTANTS NumTasks, NumWorkers, MaxAttempts

TaskIds == 1..NumTasks
WorkerIds == 1..NumWorkers

(* --algorithm PostgresQueue

variables
    status   = [t \in TaskIds |-> "pending"],
    attempts = [t \in TaskIds |-> 0],
    locked   = [t \in TaskIds |-> 0];

define
    NoDoubleLock == \A t \in TaskIds:
        locked[t] # 0 => Cardinality({w \in WorkerIds : locked[t] = w}) = 1

    AttemptsInRange == \A t \in TaskIds: attempts[t] <= MaxAttempts

    AllComplete == <>(\A t \in TaskIds: status[t] \in {"done", "failed"})
end define;

fair process worker \in WorkerIds
variables claimed = 0;
begin
    WorkLoop:
        claimed := 0;
    Claim:
        with t \in {t \in TaskIds :
                    status[t] = "pending" /\
                    locked[t] = 0 /\
                    attempts[t] < MaxAttempts} do
            status[t] := "processing";
            locked[t] := self;
            claimed := t;
        end with;
    Process:
        either
            status[claimed] := "done";
            locked[claimed] := 0;
        or
            attempts[claimed] := attempts[claimed] + 1;
            if attempts[claimed] >= MaxAttempts then
                status[claimed] := "failed";
            else
                status[claimed] := "pending";
            end if;
            locked[claimed] := 0;
        end either;
        goto WorkLoop;
end process;

end algorithm; *)
====
```

### Quint

```quint
module PostgresQueue {
  const NUM_TASKS: int
  const NUM_WORKERS: int
  const MAX_ATTEMPTS: int

  type Status = Pending | Processing | Done | Failed
  type Task = { status: Status, attempts: int, locked_by: int }

  var tasks: int -> Task

  action init = {
    tasks' = 0.to(NUM_TASKS - 1).mapBy(
      t => { status: Pending, attempts: 0, locked_by: -1 }
    )
  }

  action claimTask(w: int, t: int): bool = all {
    tasks.get(t).status == Pending,
    tasks.get(t).locked_by == -1,
    tasks.get(t).attempts < MAX_ATTEMPTS,
    tasks' = tasks.set(t, { ...tasks.get(t), status: Processing, locked_by: w })
  }

  action processSuccess(t: int): bool = all {
    tasks.get(t).status == Processing,
    tasks' = tasks.set(t, { ...tasks.get(t), status: Done, locked_by: -1 })
  }

  action processFailure(t: int): bool = all {
    tasks.get(t).status == Processing,
    val newAttempts = tasks.get(t).attempts + 1,
    val newStatus = if (newAttempts >= MAX_ATTEMPTS) Failed else Pending,
    tasks' = tasks.set(t, { status: newStatus, attempts: newAttempts, locked_by: -1 })
  }

  action step = {
    nondet w = oneOf(0.to(NUM_WORKERS - 1))
    nondet t = oneOf(0.to(NUM_TASKS - 1))
    any {
      claimTask(w, t),
      processSuccess(t),
      processFailure(t),
    }
  }

  val noDoubleLock = 0.to(NUM_TASKS - 1).forall(
    t => tasks.get(t).locked_by != -1 implies
      0.to(NUM_TASKS - 1).forall(
        t2 => t2 != t implies tasks.get(t2).locked_by != tasks.get(t).locked_by
      )
  )

  temporal allComplete = eventually(
    0.to(NUM_TASKS - 1).forall(t =>
      tasks.get(t).status == Done or tasks.get(t).status == Failed
    )
  )

  run claimAndSucceed = {
    init.then(claimTask(0, 0)).then(processSuccess(0))
      .expect(tasks.get(0).status == Done)
  }
}
```

## Recommended Approach for Aggre

| Approach | What | Why |
|----------|------|-----|
| Write specs in Quint | Model PG queue + pipeline stages | Readable for humans AND Claude. Typed. Testable via REPL |
| Use boring-task-queue as reference | Adapt its TLA+ patterns | Already models PG queue with locking, timeouts, healthchecks |
| Feed specs to Claude when coding | Spec-first workflow | Claude implements correctly when spec is precise. Without spec, it misses concurrency bugs |
| Run model checker | TLC via Quint's backend | Catches interleavings neither humans nor AI can enumerate manually |
| Skip Specula for now | Valid criticism that it inverts the value | Writing the spec IS the thinking. Extracting from code misses the point |

**The workflow: You think → Quint spec → model check → Claude implements against the spec.**

The spec is both verification and documentation.
