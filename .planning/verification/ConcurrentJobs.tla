---- MODULE ConcurrentJobs ----
(*
 * ConcurrentJobs -- Formal model proving why sensor guards and
 * default_op_concurrency_limit=1 exist in Aggre pipelines.
 *
 * Models the content pipeline scenario where two job instances can run
 * concurrently WITHOUT the sensor guard. Both query
 * WHERE text IS NULL AND error IS NULL, get overlapping batches,
 * and process the same items. update_content() is a blind UPDATE by ID --
 * last writer wins with no optimistic locking.
 *
 * With UseGuard=TRUE, the sensor waits for the current job to finish
 * before starting a new one, preventing overlap.
 *
 * State per item (SilverContent row):
 *   - text:       "null" | "extracted_by_j1" | "extracted_by_j2"
 *   - error:      "null" | "error"
 *   - fetched_at: "null" | "fetched"
 *
 * Using "extracted_by_j1" / "extracted_by_j2" (rather than just "extracted")
 * lets us detect when one job's work is silently overwritten by another.
 *
 * Two configs:
 *   ConcurrentJobs_unsafe.cfg -- UseGuard=FALSE, expects violations
 *   ConcurrentJobs_safe.cfg   -- UseGuard=TRUE, expects all pass
 *)

EXTENDS TLC, Integers, Sequences, FiniteSets

CONSTANTS
    NumItems,       \* Number of content items (e.g. 3)
    Jobs,           \* Set of job IDs (e.g. {"j1", "j2"}) -- strings for ProcSet compat
    UseGuard        \* TRUE = sensor enforces single-job-at-a-time

ItemIds == 1..NumItems

\* Map job IDs to their text tags
TextTag(j) == IF j = "j1" THEN "extracted_by_j1" ELSE "extracted_by_j2"

(* --algorithm ConcurrentJobs

variables
    \* Per-item state (models SilverContent columns)
    text      = [i \in ItemIds |-> "null"],
    error     = [i \in ItemIds |-> "null"],
    fetched_at = [i \in ItemIds |-> "null"],

    \* Per-job state
    jobActive    = [j \in Jobs |-> FALSE],     \* is this job currently running?
    jobBatch     = [j \in Jobs |-> {}],         \* snapshot of items to process
    jobCurrent   = [j \in Jobs |-> 0],          \* item currently being processed
    jobDone      = [j \in Jobs |-> FALSE],      \* job finished all items?

    \* Track which items are being processed by which jobs (for NoDoubleProcessing)
    processing   = [i \in ItemIds |-> "none"],   \* "none" or job ID string

    \* Track who last wrote each item (for NoLostUpdate)
    lastWriter   = [i \in ItemIds |-> "none"],   \* "none" or job ID string

    \* Sensor state: set of jobs remaining to start
    jobsRemaining = Jobs,                         \* jobs left to start
    sensorDone    = FALSE;                        \* sensor has started all jobs

define
    \* Items needing processing: text=null AND error=null
    NeedsProcessing == {i \in ItemIds : text[i] = "null" /\ error[i] = "null"}

    \* Terminal items
    IsTerminal(i) == text[i] # "null" \/ error[i] # "null"

    \* ===== SAFETY PROPERTIES =====

    \* No two jobs process the same item simultaneously
    NoDoubleProcessing ==
        \A i \in ItemIds : processing[i] # "none" =>
            ~(\E j \in Jobs : j # processing[i] /\ jobCurrent[j] = i)

    \* Once a job writes a result for an item, it is not overwritten by a different job
    \* with a potentially different result
    NoLostUpdate ==
        \A i \in ItemIds :
            (lastWriter[i] # "none" /\ text[i] # "null") =>
                text[i] = TextTag(lastWriter[i])

    \* ===== LIVENESS PROPERTIES =====

    \* Every item eventually reaches a terminal state
    AllComplete ==
        <>(\A i \in ItemIds : IsTerminal(i))

end define;

\* =====================================================================
\* SENSOR PROCESS: starts jobs, optionally enforcing single-job guard
\* =====================================================================
fair process sensor = "sensor"
begin
SensorLoop:
    while jobsRemaining # {} do
        SensorCheck:
            if UseGuard then
                \* Safe mode: wait until no other job is active
                await \A j \in Jobs : ~jobActive[j];
            end if;

        SensorStart:
            \* Start the next job -- snapshot current NeedsProcessing as its batch
            with j \in jobsRemaining do
                jobActive[j] := TRUE;
                jobBatch[j]  := NeedsProcessing;
                jobDone[j]   := FALSE;
                jobsRemaining := jobsRemaining \ {j};
            end with;
    end while;

SensorFinished:
    sensorDone := TRUE;
end process;

\* =====================================================================
\* WORKER PROCESSES: one per job, processes items sequentially
\* =====================================================================
fair process worker \in Jobs
variable myItem = 0;
begin
WLoop:
    while TRUE do
        WWait:
            \* Wait for this job to be activated
            await jobActive[self];

        WPick:
            \* Pick an item from our batch that still needs processing
            \* (re-check current state, but we only pick from our snapshot batch)
            if \E i \in jobBatch[self] : text[i] = "null" /\ error[i] = "null" then
                with i \in {i \in jobBatch[self] : text[i] = "null" /\ error[i] = "null"} do
                    myItem := i;
                    jobCurrent[self] := i;
                    processing[i] := self;
                end with;
            else
                goto WFinish;
            end if;

        WFetch:
            \* Simulate fetching content (set fetched_at)
            fetched_at[myItem] := "fetched";

        WWrite:
            \* Simulate writing result -- nondeterministic success or failure
            either
                \* Success: write text tagged with our job ID
                text[myItem] := TextTag(self);
                lastWriter[myItem] := self;
            or
                \* Failure: write error
                error[myItem] := "error";
                lastWriter[myItem] := self;
            end either;

        WRelease:
            \* Release the item
            processing[myItem] := "none";
            jobCurrent[self] := 0;
            myItem := 0;
            goto WPick;

        WFinish:
            \* Job complete: mark as inactive
            jobActive[self] := FALSE;
            jobDone[self] := TRUE;
            jobCurrent[self] := 0;
    end while;
end process;

end algorithm; *)

\* BEGIN TRANSLATION -- the translator will fill this in
VARIABLES text, error, fetched_at, jobActive, jobBatch, jobCurrent, jobDone, 
          processing, lastWriter, jobsRemaining, sensorDone, pc

(* define statement *)
NeedsProcessing == {i \in ItemIds : text[i] = "null" /\ error[i] = "null"}


IsTerminal(i) == text[i] # "null" \/ error[i] # "null"




NoDoubleProcessing ==
    \A i \in ItemIds : processing[i] # "none" =>
        ~(\E j \in Jobs : j # processing[i] /\ jobCurrent[j] = i)



NoLostUpdate ==
    \A i \in ItemIds :
        (lastWriter[i] # "none" /\ text[i] # "null") =>
            text[i] = TextTag(lastWriter[i])




AllComplete ==
    <>(\A i \in ItemIds : IsTerminal(i))

VARIABLE myItem

vars == << text, error, fetched_at, jobActive, jobBatch, jobCurrent, jobDone, 
           processing, lastWriter, jobsRemaining, sensorDone, pc, myItem >>

ProcSet == {"sensor"} \cup (Jobs)

Init == (* Global variables *)
        /\ text = [i \in ItemIds |-> "null"]
        /\ error = [i \in ItemIds |-> "null"]
        /\ fetched_at = [i \in ItemIds |-> "null"]
        /\ jobActive = [j \in Jobs |-> FALSE]
        /\ jobBatch = [j \in Jobs |-> {}]
        /\ jobCurrent = [j \in Jobs |-> 0]
        /\ jobDone = [j \in Jobs |-> FALSE]
        /\ processing = [i \in ItemIds |-> "none"]
        /\ lastWriter = [i \in ItemIds |-> "none"]
        /\ jobsRemaining = Jobs
        /\ sensorDone = FALSE
        (* Process worker *)
        /\ myItem = [self \in Jobs |-> 0]
        /\ pc = [self \in ProcSet |-> CASE self = "sensor" -> "SensorLoop"
                                        [] self \in Jobs -> "WLoop"]

SensorLoop == /\ pc["sensor"] = "SensorLoop"
              /\ IF jobsRemaining # {}
                    THEN /\ pc' = [pc EXCEPT !["sensor"] = "SensorCheck"]
                    ELSE /\ pc' = [pc EXCEPT !["sensor"] = "SensorFinished"]
              /\ UNCHANGED << text, error, fetched_at, jobActive, jobBatch, 
                              jobCurrent, jobDone, processing, lastWriter, 
                              jobsRemaining, sensorDone, myItem >>

SensorCheck == /\ pc["sensor"] = "SensorCheck"
               /\ IF UseGuard
                     THEN /\ \A j \in Jobs : ~jobActive[j]
                     ELSE /\ TRUE
               /\ pc' = [pc EXCEPT !["sensor"] = "SensorStart"]
               /\ UNCHANGED << text, error, fetched_at, jobActive, jobBatch, 
                               jobCurrent, jobDone, processing, lastWriter, 
                               jobsRemaining, sensorDone, myItem >>

SensorStart == /\ pc["sensor"] = "SensorStart"
               /\ \E j \in jobsRemaining:
                    /\ jobActive' = [jobActive EXCEPT ![j] = TRUE]
                    /\ jobBatch' = [jobBatch EXCEPT ![j] = NeedsProcessing]
                    /\ jobDone' = [jobDone EXCEPT ![j] = FALSE]
                    /\ jobsRemaining' = jobsRemaining \ {j}
               /\ pc' = [pc EXCEPT !["sensor"] = "SensorLoop"]
               /\ UNCHANGED << text, error, fetched_at, jobCurrent, processing, 
                               lastWriter, sensorDone, myItem >>

SensorFinished == /\ pc["sensor"] = "SensorFinished"
                  /\ sensorDone' = TRUE
                  /\ pc' = [pc EXCEPT !["sensor"] = "Done"]
                  /\ UNCHANGED << text, error, fetched_at, jobActive, jobBatch, 
                                  jobCurrent, jobDone, processing, lastWriter, 
                                  jobsRemaining, myItem >>

sensor == SensorLoop \/ SensorCheck \/ SensorStart \/ SensorFinished

WLoop(self) == /\ pc[self] = "WLoop"
               /\ pc' = [pc EXCEPT ![self] = "WWait"]
               /\ UNCHANGED << text, error, fetched_at, jobActive, jobBatch, 
                               jobCurrent, jobDone, processing, lastWriter, 
                               jobsRemaining, sensorDone, myItem >>

WWait(self) == /\ pc[self] = "WWait"
               /\ jobActive[self]
               /\ pc' = [pc EXCEPT ![self] = "WPick"]
               /\ UNCHANGED << text, error, fetched_at, jobActive, jobBatch, 
                               jobCurrent, jobDone, processing, lastWriter, 
                               jobsRemaining, sensorDone, myItem >>

WPick(self) == /\ pc[self] = "WPick"
               /\ IF \E i \in jobBatch[self] : text[i] = "null" /\ error[i] = "null"
                     THEN /\ \E i \in {i \in jobBatch[self] : text[i] = "null" /\ error[i] = "null"}:
                               /\ myItem' = [myItem EXCEPT ![self] = i]
                               /\ jobCurrent' = [jobCurrent EXCEPT ![self] = i]
                               /\ processing' = [processing EXCEPT ![i] = self]
                          /\ pc' = [pc EXCEPT ![self] = "WFetch"]
                     ELSE /\ pc' = [pc EXCEPT ![self] = "WFinish"]
                          /\ UNCHANGED << jobCurrent, processing, myItem >>
               /\ UNCHANGED << text, error, fetched_at, jobActive, jobBatch, 
                               jobDone, lastWriter, jobsRemaining, sensorDone >>

WFetch(self) == /\ pc[self] = "WFetch"
                /\ fetched_at' = [fetched_at EXCEPT ![myItem[self]] = "fetched"]
                /\ pc' = [pc EXCEPT ![self] = "WWrite"]
                /\ UNCHANGED << text, error, jobActive, jobBatch, jobCurrent, 
                                jobDone, processing, lastWriter, jobsRemaining, 
                                sensorDone, myItem >>

WWrite(self) == /\ pc[self] = "WWrite"
                /\ \/ /\ text' = [text EXCEPT ![myItem[self]] = TextTag(self)]
                      /\ lastWriter' = [lastWriter EXCEPT ![myItem[self]] = self]
                      /\ error' = error
                   \/ /\ error' = [error EXCEPT ![myItem[self]] = "error"]
                      /\ lastWriter' = [lastWriter EXCEPT ![myItem[self]] = self]
                      /\ text' = text
                /\ pc' = [pc EXCEPT ![self] = "WRelease"]
                /\ UNCHANGED << fetched_at, jobActive, jobBatch, jobCurrent, 
                                jobDone, processing, jobsRemaining, sensorDone, 
                                myItem >>

WRelease(self) == /\ pc[self] = "WRelease"
                  /\ processing' = [processing EXCEPT ![myItem[self]] = "none"]
                  /\ jobCurrent' = [jobCurrent EXCEPT ![self] = 0]
                  /\ myItem' = [myItem EXCEPT ![self] = 0]
                  /\ pc' = [pc EXCEPT ![self] = "WPick"]
                  /\ UNCHANGED << text, error, fetched_at, jobActive, jobBatch, 
                                  jobDone, lastWriter, jobsRemaining, 
                                  sensorDone >>

WFinish(self) == /\ pc[self] = "WFinish"
                 /\ jobActive' = [jobActive EXCEPT ![self] = FALSE]
                 /\ jobDone' = [jobDone EXCEPT ![self] = TRUE]
                 /\ jobCurrent' = [jobCurrent EXCEPT ![self] = 0]
                 /\ pc' = [pc EXCEPT ![self] = "WLoop"]
                 /\ UNCHANGED << text, error, fetched_at, jobBatch, processing, 
                                 lastWriter, jobsRemaining, sensorDone, myItem >>

worker(self) == WLoop(self) \/ WWait(self) \/ WPick(self) \/ WFetch(self)
                   \/ WWrite(self) \/ WRelease(self) \/ WFinish(self)

Next == sensor
           \/ (\E self \in Jobs: worker(self))

Spec == /\ Init /\ [][Next]_vars
        /\ WF_vars(sensor)
        /\ \A self \in Jobs : WF_vars(worker(self))

\* END TRANSLATION

====
