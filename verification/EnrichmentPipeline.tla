---- MODULE EnrichmentPipeline ----
(*
 * EnrichmentPipeline -- Formal model of the Aggre enrichment pipeline.
 *
 * Models the enrichment process that searches HN and Lobsters for discussions:
 *   - Sequential processing: one item at a time
 *   - Multi-platform: HN search, then Lobsters search for each item
 *   - Partial failure bug: if either platform fails, enriched_at stays NULL
 *     and the item is re-queried on the next job run (infinite reprocessing)
 *
 * State per item (SilverContent row):
 *   - enriched_at: NULL | "enriched"
 *   - canonical_url: always non-null (precondition)
 *
 * The sensor starts a job when items have enriched_at IS NULL.
 * The job processes a batch sequentially.
 * For each item, it tries HN then Lobsters; if either fails, enriched_at stays NULL.
 *
 * KNOWN BUG: This creates infinite reprocessing when a platform is permanently
 * failing for a specific URL. The liveness property AllEnriched should detect this.
 *)

EXTENDS TLC, Integers, Sequences, FiniteSets

CONSTANTS
    NumItems,              \* Number of content items to model (e.g. 3)
    PermanentHNFailItem    \* Item ID for which HN always fails (0 = none)

ItemIds == 1..NumItems

(* --algorithm EnrichmentPipeline

variables
    \* Per-item state
    enriched_at = [i \in ItemIds |-> "null"],

    \* Per-item: how many times this item has been processed (for detecting re-query loops)
    processCount = [i \in ItemIds |-> 0],

    \* Job/sensor state
    jobRunning = FALSE,

    \* Current batch being processed
    batch = {},
    batchRemaining = {},
    currentItem = 0,

    \* Per-item platform results for current processing
    hnResult = "pending",     \* "pending" | "ok" | "failed"
    lobstersResult = "pending";

define
    \* Items needing enrichment
    NeedsEnrichment == {i \in ItemIds : enriched_at[i] = "null"}

    \* ===== SAFETY PROPERTIES =====

    \* Sensor guard: if processing, job must be running
    SensorGuard ==
        currentItem # 0 => jobRunning

    \* ===== LIVENESS / BUG DETECTION =====

    \* AllEnriched: every item eventually gets enriched_at set.
    \* This should FAIL for PermanentHNFailItem because partial failure
    \* leaves enriched_at as null, causing infinite reprocessing.
    AllEnriched ==
        <>(\A i \in ItemIds : enriched_at[i] # "null")

    \* NoInfiniteReprocess: no item is processed more than twice.
    \* This is an invariant that should FAIL, demonstrating the bug.
    NoInfiniteReprocess ==
        \A i \in ItemIds : processCount[i] <= 2

end define;

\* =====================================================================
\* SENSOR PROCESS
\* =====================================================================
fair process sensor = "sensor"
begin
SensorLoop:
    while TRUE do
        CheckSensor:
            if ~jobRunning /\ NeedsEnrichment # {} then
                jobRunning := TRUE;
                batch := NeedsEnrichment;
                batchRemaining := NeedsEnrichment;
            end if;

        WaitJobDone:
            await ~jobRunning \/ (jobRunning /\ batchRemaining = {} /\ currentItem = 0);

        SensorStop:
            jobRunning := FALSE;
            batch := {};
    end while;
end process;

\* =====================================================================
\* ENRICHMENT WORKER: sequential processing
\* =====================================================================
fair process enrichWorker = "worker"
begin
EWLoop:
    while TRUE do
        EWWait:
            await jobRunning /\ batchRemaining # {};

        EWPickItem:
            with i \in batchRemaining do
                currentItem := i;
                batchRemaining := batchRemaining \ {i};
                hnResult := "pending";
                lobstersResult := "pending";
                processCount[i] := processCount[i] + 1;
            end with;

        \* Phase 1: Try HN search
        EWSearchHN:
            if currentItem = PermanentHNFailItem then
                \* This item always fails on HN (models permanent API failure)
                hnResult := "failed";
            else
                \* Non-deterministic: HN can succeed or fail
                either
                    hnResult := "ok";
                or
                    hnResult := "failed";
                end either;
            end if;

        \* Phase 2: Try Lobsters search
        EWSearchLobsters:
            \* Non-deterministic: Lobsters can succeed or fail
            either
                lobstersResult := "ok";
            or
                lobstersResult := "failed";
            end either;

        \* Phase 3: Update enriched_at only if both succeeded
        EWUpdate:
            if hnResult = "ok" /\ lobstersResult = "ok" then
                \* Both succeeded: mark as enriched
                enriched_at[currentItem] := "enriched";
            end if;
            \* BUG: if failed=TRUE, enriched_at stays "null"
            \* This item will be re-queried on next sensor tick

        EWRelease:
            currentItem := 0;
            hnResult := "pending";
            lobstersResult := "pending";

            \* If more items in batch, continue; otherwise signal done
            if batchRemaining # {} then
                goto EWPickItem;
            end if;

    end while;
end process;

end algorithm; *)

\* BEGIN TRANSLATION -- generated by TLA+ (PlusCal translator will fill this in)
VARIABLES enriched_at, processCount, jobRunning, batch, batchRemaining, 
          currentItem, hnResult, lobstersResult, pc

(* define statement *)
NeedsEnrichment == {i \in ItemIds : enriched_at[i] = "null"}




SensorGuard ==
    currentItem # 0 => jobRunning






AllEnriched ==
    <>(\A i \in ItemIds : enriched_at[i] # "null")



NoInfiniteReprocess ==
    \A i \in ItemIds : processCount[i] <= 2


vars == << enriched_at, processCount, jobRunning, batch, batchRemaining, 
           currentItem, hnResult, lobstersResult, pc >>

ProcSet == {"sensor"} \cup {"worker"}

Init == (* Global variables *)
        /\ enriched_at = [i \in ItemIds |-> "null"]
        /\ processCount = [i \in ItemIds |-> 0]
        /\ jobRunning = FALSE
        /\ batch = {}
        /\ batchRemaining = {}
        /\ currentItem = 0
        /\ hnResult = "pending"
        /\ lobstersResult = "pending"
        /\ pc = [self \in ProcSet |-> CASE self = "sensor" -> "SensorLoop"
                                        [] self = "worker" -> "EWLoop"]

SensorLoop == /\ pc["sensor"] = "SensorLoop"
              /\ pc' = [pc EXCEPT !["sensor"] = "CheckSensor"]
              /\ UNCHANGED << enriched_at, processCount, jobRunning, batch, 
                              batchRemaining, currentItem, hnResult, 
                              lobstersResult >>

CheckSensor == /\ pc["sensor"] = "CheckSensor"
               /\ IF ~jobRunning /\ NeedsEnrichment # {}
                     THEN /\ jobRunning' = TRUE
                          /\ batch' = NeedsEnrichment
                          /\ batchRemaining' = NeedsEnrichment
                     ELSE /\ TRUE
                          /\ UNCHANGED << jobRunning, batch, batchRemaining >>
               /\ pc' = [pc EXCEPT !["sensor"] = "WaitJobDone"]
               /\ UNCHANGED << enriched_at, processCount, currentItem, 
                               hnResult, lobstersResult >>

WaitJobDone == /\ pc["sensor"] = "WaitJobDone"
               /\ ~jobRunning \/ (jobRunning /\ batchRemaining = {} /\ currentItem = 0)
               /\ pc' = [pc EXCEPT !["sensor"] = "SensorStop"]
               /\ UNCHANGED << enriched_at, processCount, jobRunning, batch, 
                               batchRemaining, currentItem, hnResult, 
                               lobstersResult >>

SensorStop == /\ pc["sensor"] = "SensorStop"
              /\ jobRunning' = FALSE
              /\ batch' = {}
              /\ pc' = [pc EXCEPT !["sensor"] = "SensorLoop"]
              /\ UNCHANGED << enriched_at, processCount, batchRemaining, 
                              currentItem, hnResult, lobstersResult >>

sensor == SensorLoop \/ CheckSensor \/ WaitJobDone \/ SensorStop

EWLoop == /\ pc["worker"] = "EWLoop"
          /\ pc' = [pc EXCEPT !["worker"] = "EWWait"]
          /\ UNCHANGED << enriched_at, processCount, jobRunning, batch, 
                          batchRemaining, currentItem, hnResult, 
                          lobstersResult >>

EWWait == /\ pc["worker"] = "EWWait"
          /\ jobRunning /\ batchRemaining # {}
          /\ pc' = [pc EXCEPT !["worker"] = "EWPickItem"]
          /\ UNCHANGED << enriched_at, processCount, jobRunning, batch, 
                          batchRemaining, currentItem, hnResult, 
                          lobstersResult >>

EWPickItem == /\ pc["worker"] = "EWPickItem"
              /\ \E i \in batchRemaining:
                   /\ currentItem' = i
                   /\ batchRemaining' = batchRemaining \ {i}
                   /\ hnResult' = "pending"
                   /\ lobstersResult' = "pending"
                   /\ processCount' = [processCount EXCEPT ![i] = processCount[i] + 1]
              /\ pc' = [pc EXCEPT !["worker"] = "EWSearchHN"]
              /\ UNCHANGED << enriched_at, jobRunning, batch >>

EWSearchHN == /\ pc["worker"] = "EWSearchHN"
              /\ IF currentItem = PermanentHNFailItem
                    THEN /\ hnResult' = "failed"
                    ELSE /\ \/ /\ hnResult' = "ok"
                            \/ /\ hnResult' = "failed"
              /\ pc' = [pc EXCEPT !["worker"] = "EWSearchLobsters"]
              /\ UNCHANGED << enriched_at, processCount, jobRunning, batch, 
                              batchRemaining, currentItem, lobstersResult >>

EWSearchLobsters == /\ pc["worker"] = "EWSearchLobsters"
                    /\ \/ /\ lobstersResult' = "ok"
                       \/ /\ lobstersResult' = "failed"
                    /\ pc' = [pc EXCEPT !["worker"] = "EWUpdate"]
                    /\ UNCHANGED << enriched_at, processCount, jobRunning, 
                                    batch, batchRemaining, currentItem, 
                                    hnResult >>

EWUpdate == /\ pc["worker"] = "EWUpdate"
            /\ IF hnResult = "ok" /\ lobstersResult = "ok"
                  THEN /\ enriched_at' = [enriched_at EXCEPT ![currentItem] = "enriched"]
                  ELSE /\ TRUE
                       /\ UNCHANGED enriched_at
            /\ pc' = [pc EXCEPT !["worker"] = "EWRelease"]
            /\ UNCHANGED << processCount, jobRunning, batch, batchRemaining, 
                            currentItem, hnResult, lobstersResult >>

EWRelease == /\ pc["worker"] = "EWRelease"
             /\ currentItem' = 0
             /\ hnResult' = "pending"
             /\ lobstersResult' = "pending"
             /\ IF batchRemaining # {}
                   THEN /\ pc' = [pc EXCEPT !["worker"] = "EWPickItem"]
                   ELSE /\ pc' = [pc EXCEPT !["worker"] = "EWLoop"]
             /\ UNCHANGED << enriched_at, processCount, jobRunning, batch, 
                             batchRemaining >>

enrichWorker == EWLoop \/ EWWait \/ EWPickItem \/ EWSearchHN
                   \/ EWSearchLobsters \/ EWUpdate \/ EWRelease

Next == sensor \/ enrichWorker

Spec == /\ Init /\ [][Next]_vars
        /\ WF_vars(sensor)
        /\ WF_vars(enrichWorker)

\* END TRANSLATION

\* Monotonic enrichment: once enriched, stays enriched
MonotonicEnriched ==
    \A i \in ItemIds : [](enriched_at[i] # "null" => [](enriched_at[i] # "null"))

\* Bug detection: at least one item gets reprocessed (processCount > 1)
\* This is a liveness property that should HOLD (confirming the bug exists)
SomeItemReprocessed ==
    <>(\E i \in ItemIds : processCount[i] > 1)

\* State constraint: bound processCount to prevent infinite state space exploration
\* Used with CONSTRAINT in cfg to make model finite while still detecting the bug
StateConstraint ==
    \A i \in ItemIds : processCount[i] <= 4

====
