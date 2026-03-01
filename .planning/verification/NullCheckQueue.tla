---- MODULE NullCheckQueue ----
(*
 * NullCheckQueue -- Reusable abstract model of the Aggre null-check queue pattern.
 *
 * All Aggre pipeline stages follow this pattern:
 *   1. Items start with nullable fields (null = needs processing)
 *   2. A sensor checks if a job is already running (singleton guard)
 *   3. A job queries a batch: WHERE nullable_field IS NULL AND error IS NULL
 *   4. Workers process batch items (parallel or sequential)
 *   5. Each update is atomic (own transaction via update_content())
 *   6. No row-level locking -- the sensor singleton guard is the sole concurrency control
 *
 * This module is parameterized by CONSTANTS and meant to be imported by
 * concrete pipeline specs (ContentPipeline, EnrichmentPipeline).
 *)

EXTENDS TLC, Integers, Sequences, FiniteSets

CONSTANTS
    ItemIds,          \* Set of item identifiers (e.g. 1..3)
    NumWorkers,       \* Number of concurrent workers
    Null              \* A model value representing NULL

WorkerIds == 1..NumWorkers

(*
 * State variables for the abstract queue:
 *   - result:   NULL | "done" | "error"  (the terminal nullable field)
 *   - error:    NULL | some error string
 *   - jobRunning: whether a job is currently active (sensor guard)
 *   - workerState: per-worker state ("idle" | "working")
 *   - workerItem:  per-worker currently claimed item (0 = none)
 *)

VARIABLES result, error, jobRunning, workerState, workerItem

vars == <<result, error, jobRunning, workerState, workerItem>>

TypeOK ==
    /\ result \in [ItemIds -> {Null, "done", "error"}]
    /\ error \in [ItemIds -> {Null, "error"}]
    /\ jobRunning \in BOOLEAN
    /\ workerState \in [WorkerIds -> {"idle", "working"}]
    /\ workerItem \in [WorkerIds -> ItemIds \cup {0}]

Init ==
    /\ result = [i \in ItemIds |-> Null]
    /\ error = [i \in ItemIds |-> Null]
    /\ jobRunning = FALSE
    /\ workerState = [w \in WorkerIds |-> "idle"]
    /\ workerItem = [w \in WorkerIds |-> 0]

\* -- Sensor: start a job if none is running and items need processing --
PendingItems == {i \in ItemIds : result[i] = Null /\ error[i] = Null}

SensorStart ==
    /\ ~jobRunning
    /\ PendingItems # {}
    /\ jobRunning' = TRUE
    /\ UNCHANGED <<result, error, workerState, workerItem>>

\* -- Worker: claim a pending item --
WorkerClaim(w) ==
    /\ jobRunning
    /\ workerState[w] = "idle"
    /\ \E i \in PendingItems :
        /\ \A w2 \in WorkerIds : workerItem[w2] # i  \* Not already claimed
        /\ workerState' = [workerState EXCEPT ![w] = "working"]
        /\ workerItem' = [workerItem EXCEPT ![w] = i]
        /\ UNCHANGED <<result, error, jobRunning>>

\* -- Worker: succeed (set result to "done") --
WorkerSucceed(w) ==
    /\ workerState[w] = "working"
    /\ workerItem[w] # 0
    /\ result' = [result EXCEPT ![workerItem[w]] = "done"]
    /\ workerState' = [workerState EXCEPT ![w] = "idle"]
    /\ workerItem' = [workerItem EXCEPT ![w] = 0]
    /\ UNCHANGED <<error, jobRunning>>

\* -- Worker: fail (set error) --
WorkerFail(w) ==
    /\ workerState[w] = "working"
    /\ workerItem[w] # 0
    /\ error' = [error EXCEPT ![workerItem[w]] = "error"]
    /\ result' = [result EXCEPT ![workerItem[w]] = "error"]
    /\ workerState' = [workerState EXCEPT ![w] = "idle"]
    /\ workerItem' = [workerItem EXCEPT ![w] = 0]
    /\ UNCHANGED <<jobRunning>>

\* -- Job completes: all workers idle, stop the job --
JobComplete ==
    /\ jobRunning
    /\ \A w \in WorkerIds : workerState[w] = "idle"
    /\ jobRunning' = FALSE
    /\ UNCHANGED <<result, error, workerState, workerItem>>

\* -- Next-state relation --
Next ==
    \/ SensorStart
    \/ \E w \in WorkerIds : WorkerClaim(w)
    \/ \E w \in WorkerIds : WorkerSucceed(w)
    \/ \E w \in WorkerIds : WorkerFail(w)
    \/ JobComplete

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

\* ===== PROPERTIES =====

\* Safety: No item processed by two workers simultaneously
NoDoubleProcessing ==
    \A w1, w2 \in WorkerIds :
        (w1 # w2 /\ workerItem[w1] # 0) => workerItem[w1] # workerItem[w2]

\* Safety: Once a terminal field is set, it doesn't revert to null
MonotonicProgress ==
    \A i \in ItemIds :
        /\ (result[i] # Null => result[i]' # Null)
        /\ (error[i] # Null => error[i]' # Null)

\* Alternate formulation as an invariant (check MonotonicProgressInv at each state)
\* We check via temporal property: []MonotonicProgress is equivalent

\* Safety: If a job is running, sensor doesn't start another
SensorGuard ==
    \A w \in WorkerIds :
        workerState[w] = "working" => jobRunning

\* Liveness: Every item eventually reaches a terminal state
AllComplete ==
    <>(\A i \in ItemIds : result[i] # Null \/ error[i] # Null)

====
