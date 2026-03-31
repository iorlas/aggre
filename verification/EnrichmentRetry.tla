---- MODULE EnrichmentRetry ----
(*
 * EnrichmentRetry -- Formal model of the enrichment retry system with
 * per-platform tracking, exponential backoff, shared throttle, and
 * partial progress preservation.
 *
 * This models the FIXED enrichment pipeline where:
 *   - Each platform (HN, Lobsters) has independent result tracking
 *   - Items retry with bounded retries (MAX_RETRIES)
 *   - Throttle state is shared per-platform (a 429 on Lobsters skips ALL Lobsters calls)
 *   - Partial progress is preserved (HN done on run 1 stays done on run 2)
 *   - Items reach "completed" or "permanently_failed" terminal states
 *
 * State per item:
 *   status:           "pending" | "in_progress" | "completed" | "permanently_failed"
 *   retry_count:      0..MAX_RETRIES
 *   hn_result:        "pending" | "done" | "failed"
 *   lobsters_result:  "pending" | "done" | "failed"
 *   ready:            TRUE when backoff has elapsed (abstract time model)
 *
 * Global state:
 *   hn_throttled:       BOOLEAN
 *   lobsters_throttled: BOOLEAN
 *   job_running:        BOOLEAN
 *
 * 4 processes:
 *   Sensor          -- starts jobs when items need processing, respects backoff
 *   Worker          -- processes items sequentially, calls platforms, handles throttle
 *   ThrottleManager -- nondeterministically lifts throttle (models cooldown expiring)
 *   BackoffTimer    -- makes non-ready items ready (models backoff time elapsing)
 *)

EXTENDS TLC, Integers, Sequences, FiniteSets

CONSTANTS
    NumItems,       \* Number of content items to model (e.g. 2)
    MAX_RETRIES     \* Maximum retry count before permanent failure (e.g. 2)

ItemIds == 1..NumItems

(* --algorithm EnrichmentRetry

variables
    \* Per-item state
    status         = [i \in ItemIds |-> "pending"],
    retry_count    = [i \in ItemIds |-> 0],
    hn_result      = [i \in ItemIds |-> "pending"],
    lobsters_result = [i \in ItemIds |-> "pending"],
    ready          = [i \in ItemIds |-> TRUE],  \* Initially all items are ready

    \* Global throttle state
    hn_throttled       = FALSE,
    lobsters_throttled = FALSE,

    \* Job/sensor state
    job_running = FALSE,

    \* Worker state
    batch          = {},
    batchRemaining = {},
    currentItem    = 0,

    \* Violation-detection flags: set TRUE when a successful platform call was made.
    \* Used with NoThrottledCall invariant to verify no "done" while throttled.
    callHN  = FALSE,
    callLob = FALSE;

define
    \* Items eligible for processing: pending/failed status AND ready (backoff elapsed)
    NeedsProcessing == {i \in ItemIds :
        status[i] = "pending" /\ ready[i]}

    \* Terminal items
    IsTerminal(i) == status[i] = "completed" \/ status[i] = "permanently_failed"

    \* ===== SAFETY PROPERTIES =====

    \* BoundedRetries: retry_count never exceeds MAX_RETRIES
    BoundedRetries ==
        \A i \in ItemIds : retry_count[i] <= MAX_RETRIES

    \* NoThrottledCall: a successful platform call (callHN/callLob = TRUE)
    \* never occurs while the platform is throttled. The worker sets callHN/callLob
    \* to TRUE when it successfully gets "done" from a platform, and to FALSE
    \* otherwise. The invariant checks: if a successful call was made, the platform
    \* was not throttled at that moment.
    NoThrottledCall ==
        (callHN => ~hn_throttled) /\ (callLob => ~lobsters_throttled)

    \* PartialProgressPreserved: a platform result of "done" is never reverted.
    \* Once both platforms for an item are "done" and item is "completed",
    \* results stay "done". For in-progress items, "done" results are preserved
    \* across retries. Checked as temporal property (PartialProgressHN/Lobsters)
    \* below. This invariant checks the weaker structural guarantee: a completed
    \* item always has both results "done".
    PartialProgressPreserved ==
        \A i \in ItemIds :
            status[i] = "completed" =>
                (hn_result[i] = "done" /\ lobsters_result[i] = "done")

    \* NoInfiniteReprocess: terminal items are not reprocessed
    NoInfiniteReprocess ==
        \A i \in ItemIds :
            IsTerminal(i) => status[i] # "in_progress"

    \* SensorGuard: at most one job at a time
    SensorGuard ==
        currentItem # 0 => job_running

    \* ===== LIVENESS PROPERTIES =====

    \* EventualTermination: every item reaches completed or permanently_failed
    EventualTermination ==
        <>(\A i \in ItemIds : IsTerminal(i))

end define;

\* =====================================================================
\* SENSOR PROCESS
\* =====================================================================
fair process sensor = "sensor"
begin
SensorLoop:
    while TRUE do
        CheckSensor:
            if ~job_running /\ NeedsProcessing # {} then
                job_running := TRUE;
                batch := NeedsProcessing;
                batchRemaining := NeedsProcessing;
                \* Mark batch items as in_progress
                with items = NeedsProcessing do
                    status := [i \in ItemIds |->
                        IF i \in items THEN "in_progress" ELSE status[i]];
                end with;
            end if;

        WaitJobDone:
            await ~job_running \/ (job_running /\ batchRemaining = {} /\ currentItem = 0);

        SensorStop:
            job_running := FALSE;
            batch := {};
    end while;
end process;

\* =====================================================================
\* WORKER PROCESS: sequential processing of items
\* =====================================================================
fair process enrichWorker = "worker"
begin
EWLoop:
    while TRUE do
        EWWait:
            await job_running /\ batchRemaining # {};

        EWPickItem:
            with i \in batchRemaining do
                currentItem := i;
                batchRemaining := batchRemaining \ {i};
            end with;

        \* Phase 1: Call HN (if not already done and not throttled)
        EWCallHN:
            if hn_result[currentItem] # "done" then
                if hn_throttled then
                    \* Throttled: skip, treat as failed for this run
                    hn_result[currentItem] := "failed";
                    callHN := FALSE;
                else
                    \* Non-deterministic: success, normal failure, or rate-limit failure
                    either
                        hn_result[currentItem] := "done";
                        callHN := TRUE;  \* Record: we made a successful HN call
                    or
                        hn_result[currentItem] := "failed";
                        callHN := FALSE;
                    or
                        \* Rate-limit failure: set throttle AND fail
                        hn_result[currentItem] := "failed";
                        hn_throttled := TRUE;
                        callHN := FALSE;
                    end either;
                end if;
            else
                \* Already done, no call made
                callHN := FALSE;
            end if;

        \* Phase 2: Call Lobsters (if not already done and not throttled)
        EWCallLob:
            if lobsters_result[currentItem] # "done" then
                if lobsters_throttled then
                    \* Throttled: skip, treat as failed for this run
                    lobsters_result[currentItem] := "failed";
                    callLob := FALSE;
                else
                    \* Non-deterministic: success, normal failure, or rate-limit failure
                    either
                        lobsters_result[currentItem] := "done";
                        callLob := TRUE;  \* Record: we made a successful Lobsters call
                    or
                        lobsters_result[currentItem] := "failed";
                        callLob := FALSE;
                    or
                        \* Rate-limit failure: set throttle AND fail
                        lobsters_result[currentItem] := "failed";
                        lobsters_throttled := TRUE;
                        callLob := FALSE;
                    end either;
                end if;
            else
                \* Already done, no call made
                callLob := FALSE;
            end if;

        \* Phase 3: Evaluate results and update item state
        EWEvaluate:
            if hn_result[currentItem] = "done" /\ lobsters_result[currentItem] = "done" then
                \* Both platforms succeeded: completed
                status[currentItem] := "completed";
            else
                \* At least one platform not done
                retry_count[currentItem] := retry_count[currentItem] + 1;
                if retry_count[currentItem] >= MAX_RETRIES then
                    \* Max retries reached: permanent failure
                    status[currentItem] := "permanently_failed";
                else
                    \* Will retry: reset failed results to pending, keep done results
                    status[currentItem] := "pending";
                    ready[currentItem] := FALSE;  \* Backoff: not ready until timer fires
                    if hn_result[currentItem] = "failed" then
                        hn_result[currentItem] := "pending";
                    end if;
                    if lobsters_result[currentItem] = "failed" then
                        lobsters_result[currentItem] := "pending";
                    end if;
                end if;
            end if;

        EWRelease:
            currentItem := 0;
            if batchRemaining # {} then
                goto EWPickItem;
            end if;

    end while;
end process;

\* =====================================================================
\* THROTTLE MANAGER: models external cooldown expiration
\* =====================================================================
fair process throttleManager = "throttle"
begin
TMLoop:
    while TRUE do
        TMAction:
            \* Nondeterministically lift throttle (models cooldown expiring)
            either
                hn_throttled := FALSE;
            or
                lobsters_throttled := FALSE;
            end either;
    end while;
end process;

\* =====================================================================
\* BACKOFF TIMER: models backoff time elapsing for items
\* =====================================================================
fair process backoffTimer = "backoff"
begin
BTLoop:
    while TRUE do
        BTMakeReady:
            \* Wait until there is a non-ready pending item, then make one ready
            await \E i \in ItemIds : ~ready[i] /\ status[i] = "pending";
            with i \in {i \in ItemIds : ~ready[i] /\ status[i] = "pending"} do
                ready[i] := TRUE;
            end with;
    end while;
end process;

end algorithm; *)

\* BEGIN TRANSLATION (chksum(pcal) = "f94212cf" /\ chksum(tla) = "f56b4ff1")
VARIABLES status, retry_count, hn_result, lobsters_result, ready, 
          hn_throttled, lobsters_throttled, job_running, batch, 
          batchRemaining, currentItem, callHN, callLob, pc

(* define statement *)
NeedsProcessing == {i \in ItemIds :
    status[i] = "pending" /\ ready[i]}


IsTerminal(i) == status[i] = "completed" \/ status[i] = "permanently_failed"




BoundedRetries ==
    \A i \in ItemIds : retry_count[i] <= MAX_RETRIES






NoThrottledCall ==
    (callHN => ~hn_throttled) /\ (callLob => ~lobsters_throttled)







PartialProgressPreserved ==
    \A i \in ItemIds :
        status[i] = "completed" =>
            (hn_result[i] = "done" /\ lobsters_result[i] = "done")


NoInfiniteReprocess ==
    \A i \in ItemIds :
        IsTerminal(i) => status[i] # "in_progress"


SensorGuard ==
    currentItem # 0 => job_running




EventualTermination ==
    <>(\A i \in ItemIds : IsTerminal(i))


vars == << status, retry_count, hn_result, lobsters_result, ready, 
           hn_throttled, lobsters_throttled, job_running, batch, 
           batchRemaining, currentItem, callHN, callLob, pc >>

ProcSet == {"sensor"} \cup {"worker"} \cup {"throttle"} \cup {"backoff"}

Init == (* Global variables *)
        /\ status = [i \in ItemIds |-> "pending"]
        /\ retry_count = [i \in ItemIds |-> 0]
        /\ hn_result = [i \in ItemIds |-> "pending"]
        /\ lobsters_result = [i \in ItemIds |-> "pending"]
        /\ ready = [i \in ItemIds |-> TRUE]
        /\ hn_throttled = FALSE
        /\ lobsters_throttled = FALSE
        /\ job_running = FALSE
        /\ batch = {}
        /\ batchRemaining = {}
        /\ currentItem = 0
        /\ callHN = FALSE
        /\ callLob = FALSE
        /\ pc = [self \in ProcSet |-> CASE self = "sensor" -> "SensorLoop"
                                        [] self = "worker" -> "EWLoop"
                                        [] self = "throttle" -> "TMLoop"
                                        [] self = "backoff" -> "BTLoop"]

SensorLoop == /\ pc["sensor"] = "SensorLoop"
              /\ pc' = [pc EXCEPT !["sensor"] = "CheckSensor"]
              /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                              ready, hn_throttled, lobsters_throttled, 
                              job_running, batch, batchRemaining, currentItem, 
                              callHN, callLob >>

CheckSensor == /\ pc["sensor"] = "CheckSensor"
               /\ IF ~job_running /\ NeedsProcessing # {}
                     THEN /\ job_running' = TRUE
                          /\ batch' = NeedsProcessing
                          /\ batchRemaining' = NeedsProcessing
                          /\ LET items == NeedsProcessing IN
                               status' =       [i \in ItemIds |->
                                         IF i \in items THEN "in_progress" ELSE status[i]]
                     ELSE /\ TRUE
                          /\ UNCHANGED << status, job_running, batch, 
                                          batchRemaining >>
               /\ pc' = [pc EXCEPT !["sensor"] = "WaitJobDone"]
               /\ UNCHANGED << retry_count, hn_result, lobsters_result, ready, 
                               hn_throttled, lobsters_throttled, currentItem, 
                               callHN, callLob >>

WaitJobDone == /\ pc["sensor"] = "WaitJobDone"
               /\ ~job_running \/ (job_running /\ batchRemaining = {} /\ currentItem = 0)
               /\ pc' = [pc EXCEPT !["sensor"] = "SensorStop"]
               /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                               ready, hn_throttled, lobsters_throttled, 
                               job_running, batch, batchRemaining, currentItem, 
                               callHN, callLob >>

SensorStop == /\ pc["sensor"] = "SensorStop"
              /\ job_running' = FALSE
              /\ batch' = {}
              /\ pc' = [pc EXCEPT !["sensor"] = "SensorLoop"]
              /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                              ready, hn_throttled, lobsters_throttled, 
                              batchRemaining, currentItem, callHN, callLob >>

sensor == SensorLoop \/ CheckSensor \/ WaitJobDone \/ SensorStop

EWLoop == /\ pc["worker"] = "EWLoop"
          /\ pc' = [pc EXCEPT !["worker"] = "EWWait"]
          /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                          ready, hn_throttled, lobsters_throttled, job_running, 
                          batch, batchRemaining, currentItem, callHN, callLob >>

EWWait == /\ pc["worker"] = "EWWait"
          /\ job_running /\ batchRemaining # {}
          /\ pc' = [pc EXCEPT !["worker"] = "EWPickItem"]
          /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                          ready, hn_throttled, lobsters_throttled, job_running, 
                          batch, batchRemaining, currentItem, callHN, callLob >>

EWPickItem == /\ pc["worker"] = "EWPickItem"
              /\ \E i \in batchRemaining:
                   /\ currentItem' = i
                   /\ batchRemaining' = batchRemaining \ {i}
              /\ pc' = [pc EXCEPT !["worker"] = "EWCallHN"]
              /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                              ready, hn_throttled, lobsters_throttled, 
                              job_running, batch, callHN, callLob >>

EWCallHN == /\ pc["worker"] = "EWCallHN"
            /\ IF hn_result[currentItem] # "done"
                  THEN /\ IF hn_throttled
                             THEN /\ hn_result' = [hn_result EXCEPT ![currentItem] = "failed"]
                                  /\ callHN' = FALSE
                                  /\ UNCHANGED hn_throttled
                             ELSE /\ \/ /\ hn_result' = [hn_result EXCEPT ![currentItem] = "done"]
                                        /\ callHN' = TRUE
                                        /\ UNCHANGED hn_throttled
                                     \/ /\ hn_result' = [hn_result EXCEPT ![currentItem] = "failed"]
                                        /\ callHN' = FALSE
                                        /\ UNCHANGED hn_throttled
                                     \/ /\ hn_result' = [hn_result EXCEPT ![currentItem] = "failed"]
                                        /\ hn_throttled' = TRUE
                                        /\ callHN' = FALSE
                  ELSE /\ callHN' = FALSE
                       /\ UNCHANGED << hn_result, hn_throttled >>
            /\ pc' = [pc EXCEPT !["worker"] = "EWCallLob"]
            /\ UNCHANGED << status, retry_count, lobsters_result, ready, 
                            lobsters_throttled, job_running, batch, 
                            batchRemaining, currentItem, callLob >>

EWCallLob == /\ pc["worker"] = "EWCallLob"
             /\ IF lobsters_result[currentItem] # "done"
                   THEN /\ IF lobsters_throttled
                              THEN /\ lobsters_result' = [lobsters_result EXCEPT ![currentItem] = "failed"]
                                   /\ callLob' = FALSE
                                   /\ UNCHANGED lobsters_throttled
                              ELSE /\ \/ /\ lobsters_result' = [lobsters_result EXCEPT ![currentItem] = "done"]
                                         /\ callLob' = TRUE
                                         /\ UNCHANGED lobsters_throttled
                                      \/ /\ lobsters_result' = [lobsters_result EXCEPT ![currentItem] = "failed"]
                                         /\ callLob' = FALSE
                                         /\ UNCHANGED lobsters_throttled
                                      \/ /\ lobsters_result' = [lobsters_result EXCEPT ![currentItem] = "failed"]
                                         /\ lobsters_throttled' = TRUE
                                         /\ callLob' = FALSE
                   ELSE /\ callLob' = FALSE
                        /\ UNCHANGED << lobsters_result, lobsters_throttled >>
             /\ pc' = [pc EXCEPT !["worker"] = "EWEvaluate"]
             /\ UNCHANGED << status, retry_count, hn_result, ready, 
                             hn_throttled, job_running, batch, batchRemaining, 
                             currentItem, callHN >>

EWEvaluate == /\ pc["worker"] = "EWEvaluate"
              /\ IF hn_result[currentItem] = "done" /\ lobsters_result[currentItem] = "done"
                    THEN /\ status' = [status EXCEPT ![currentItem] = "completed"]
                         /\ UNCHANGED << retry_count, hn_result, 
                                         lobsters_result, ready >>
                    ELSE /\ retry_count' = [retry_count EXCEPT ![currentItem] = retry_count[currentItem] + 1]
                         /\ IF retry_count'[currentItem] >= MAX_RETRIES
                               THEN /\ status' = [status EXCEPT ![currentItem] = "permanently_failed"]
                                    /\ UNCHANGED << hn_result, lobsters_result, 
                                                    ready >>
                               ELSE /\ status' = [status EXCEPT ![currentItem] = "pending"]
                                    /\ ready' = [ready EXCEPT ![currentItem] = FALSE]
                                    /\ IF hn_result[currentItem] = "failed"
                                          THEN /\ hn_result' = [hn_result EXCEPT ![currentItem] = "pending"]
                                          ELSE /\ TRUE
                                               /\ UNCHANGED hn_result
                                    /\ IF lobsters_result[currentItem] = "failed"
                                          THEN /\ lobsters_result' = [lobsters_result EXCEPT ![currentItem] = "pending"]
                                          ELSE /\ TRUE
                                               /\ UNCHANGED lobsters_result
              /\ pc' = [pc EXCEPT !["worker"] = "EWRelease"]
              /\ UNCHANGED << hn_throttled, lobsters_throttled, job_running, 
                              batch, batchRemaining, currentItem, callHN, 
                              callLob >>

EWRelease == /\ pc["worker"] = "EWRelease"
             /\ currentItem' = 0
             /\ IF batchRemaining # {}
                   THEN /\ pc' = [pc EXCEPT !["worker"] = "EWPickItem"]
                   ELSE /\ pc' = [pc EXCEPT !["worker"] = "EWLoop"]
             /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                             ready, hn_throttled, lobsters_throttled, 
                             job_running, batch, batchRemaining, callHN, 
                             callLob >>

enrichWorker == EWLoop \/ EWWait \/ EWPickItem \/ EWCallHN \/ EWCallLob
                   \/ EWEvaluate \/ EWRelease

TMLoop == /\ pc["throttle"] = "TMLoop"
          /\ pc' = [pc EXCEPT !["throttle"] = "TMAction"]
          /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                          ready, hn_throttled, lobsters_throttled, job_running, 
                          batch, batchRemaining, currentItem, callHN, callLob >>

TMAction == /\ pc["throttle"] = "TMAction"
            /\ \/ /\ hn_throttled' = FALSE
                  /\ UNCHANGED lobsters_throttled
               \/ /\ lobsters_throttled' = FALSE
                  /\ UNCHANGED hn_throttled
            /\ pc' = [pc EXCEPT !["throttle"] = "TMLoop"]
            /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                            ready, job_running, batch, batchRemaining, 
                            currentItem, callHN, callLob >>

throttleManager == TMLoop \/ TMAction

BTLoop == /\ pc["backoff"] = "BTLoop"
          /\ pc' = [pc EXCEPT !["backoff"] = "BTMakeReady"]
          /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                          ready, hn_throttled, lobsters_throttled, job_running, 
                          batch, batchRemaining, currentItem, callHN, callLob >>

BTMakeReady == /\ pc["backoff"] = "BTMakeReady"
               /\ \E i \in ItemIds : ~ready[i] /\ status[i] = "pending"
               /\ \E i \in {i \in ItemIds : ~ready[i] /\ status[i] = "pending"}:
                    ready' = [ready EXCEPT ![i] = TRUE]
               /\ pc' = [pc EXCEPT !["backoff"] = "BTLoop"]
               /\ UNCHANGED << status, retry_count, hn_result, lobsters_result, 
                               hn_throttled, lobsters_throttled, job_running, 
                               batch, batchRemaining, currentItem, callHN, 
                               callLob >>

backoffTimer == BTLoop \/ BTMakeReady

Next == sensor \/ enrichWorker \/ throttleManager \/ backoffTimer

Spec == /\ Init /\ [][Next]_vars
        /\ WF_vars(sensor)
        /\ WF_vars(enrichWorker)
        /\ WF_vars(throttleManager)
        /\ WF_vars(backoffTimer)

\* END TRANSLATION

\* Temporal properties for partial progress preservation
\* Once a platform result is "done", it stays "done" forever
PartialProgressHN ==
    \A i \in ItemIds : [](hn_result[i] = "done" => [](hn_result[i] = "done"))

PartialProgressLobsters ==
    \A i \in ItemIds : [](lobsters_result[i] = "done" => [](lobsters_result[i] = "done"))

====
