---- MODULE ContentPipeline ----
(*
 * ContentPipeline -- Formal model of the Aggre content download + extraction pipeline.
 *
 * Models the two-phase content processing:
 *   Phase 1 (Download): Query WHERE text IS NULL AND error IS NULL AND fetched_at IS NULL
 *                        Parallel workers (ThreadPoolExecutor, max_workers modeled as NumWorkers)
 *                        Each worker: download HTML, set fetched_at (or set error)
 *
 *   Phase 2 (Extract):  Query WHERE text IS NULL AND error IS NULL AND fetched_at IS NOT NULL
 *                        Sequential: extract text, set text (or set error)
 *
 * The job runs both phases sequentially: download_content_op -> extract_content_op
 * The sensor ensures at most one job at a time (singleton guard).
 *
 * State per item (SilverContent row):
 *   - text:      NULL | "extracted" | <not modeled: actual text>
 *   - error:     NULL | "error"
 *   - fetched_at: NULL | "fetched"
 *
 * Terminal states:
 *   - text != NULL (success path)
 *   - error != NULL (failure path)
 *)

EXTENDS TLC, Integers, Sequences, FiniteSets

CONSTANTS
    NumItems,        \* Number of content items to model (e.g. 3)
    Workers          \* Set of worker IDs (e.g. {"w1", "w2"}) -- strings to match sensor/extractor

ItemIds == 1..NumItems

(* --algorithm ContentPipeline

variables
    \* Per-item state (models SilverContent columns)
    text      = [i \in ItemIds |-> "null"],
    error     = [i \in ItemIds |-> "null"],
    fetched_at = [i \in ItemIds |-> "null"],

    \* Job/sensor state
    jobRunning = FALSE,
    jobPhase   = "idle",       \* "idle" | "downloading" | "extracting" | "done"

    \* Worker state for download phase
    workerBusy = [w \in Workers |-> FALSE],
    workerItem = [w \in Workers |-> 0],

    \* Batch snapshots (items selected at start of each phase)
    downloadBatch = {},
    extractBatch  = {};

define
    \* Items needing download: text=null, error=null, fetched_at=null
    NeedsDownload == {i \in ItemIds : text[i] = "null" /\ error[i] = "null" /\ fetched_at[i] = "null"}

    \* Items needing extraction: text=null, error=null, fetched_at != null
    NeedsExtract == {i \in ItemIds : text[i] = "null" /\ error[i] = "null" /\ fetched_at[i] # "null"}

    \* Terminal items: either text set or error set
    IsTerminal(i) == text[i] # "null" \/ error[i] # "null"

    \* ===== SAFETY PROPERTIES =====

    \* No item processed by two download workers simultaneously
    NoDoubleProcessing ==
        \A w1, w2 \in Workers :
            (w1 # w2 /\ workerItem[w1] # 0) => workerItem[w1] # workerItem[w2]

    \* Phase ordering: text can only be non-null if fetched_at is also non-null
    PhaseOrder ==
        \A i \in ItemIds :
            text[i] # "null" => fetched_at[i] # "null"

    \* Mutual exclusion: text="extracted" and error="error" never both set simultaneously
    MutualExclusion ==
        \A i \in ItemIds :
            ~(text[i] = "extracted" /\ error[i] = "error")

    \* Sensor guard: if workers are busy, job must be running
    SensorGuard ==
        (\E w \in Workers : workerBusy[w]) => jobRunning

    \* ===== LIVENESS PROPERTIES =====

    \* Every item eventually reaches a terminal state
    AllComplete ==
        <>(\A i \in ItemIds : IsTerminal(i))

end define;

\* =====================================================================
\* SENSOR PROCESS: starts the job when items need processing
\* =====================================================================
fair process sensor = "sensor"
begin
SensorLoop:
    while TRUE do
        CheckSensor:
            if ~jobRunning /\ (NeedsDownload # {} \/ NeedsExtract # {}) then
                jobRunning := TRUE;
                jobPhase := "downloading";
                downloadBatch := NeedsDownload;
            end if;

        WaitJobDone:
            await jobPhase = "done" \/ ~jobRunning;

        SensorReset:
            if jobPhase = "done" then
                jobRunning := FALSE;
                jobPhase := "idle";
            end if;
    end while;
end process;

\* =====================================================================
\* DOWNLOAD WORKERS: parallel download phase
\* =====================================================================
fair process download_worker \in Workers
variable myItem = 0;
begin
DWLoop:
    while TRUE do
        DWWait:
            await jobPhase = "downloading";

        DWClaim:
            if \E i \in downloadBatch :
                (\A w2 \in Workers : workerItem[w2] # i) /\
                fetched_at[i] = "null" /\ error[i] = "null" then
                with i \in {i \in downloadBatch :
                    (\A w2 \in Workers : workerItem[w2] # i) /\
                    fetched_at[i] = "null" /\ error[i] = "null"} do
                    myItem := i;
                    workerItem[self] := i;
                    workerBusy[self] := TRUE;
                end with;
            else
                goto DWIdle;
            end if;

        DWProcess:
            either
                \* Success: mark as downloaded (set fetched_at)
                fetched_at[myItem] := "fetched";
            or
                \* Failure: mark error (and set fetched_at too, matching real code)
                error[myItem] := "error";
                fetched_at[myItem] := "fetched";
            end either;

        DWRelease:
            workerBusy[self] := FALSE;
            workerItem[self] := 0;
            myItem := 0;
            goto DWClaim;

        DWIdle:
            skip;
    end while;
end process;

\* =====================================================================
\* EXTRACTION PROCESS: sequential extraction after download completes
\* =====================================================================
fair process extractor = "extractor"
variable eItem = 0;
begin
EXLoop:
    while TRUE do
        EXWaitPhase:
            await jobPhase = "downloading" /\
                  (\A w \in Workers : ~workerBusy[w]) /\
                  (\A i \in downloadBatch :
                    fetched_at[i] # "null" \/ error[i] # "null" \/
                    ~(\A w2 \in Workers : workerItem[w2] # i));

        EXStartPhase:
            jobPhase := "extracting";
            extractBatch := NeedsExtract;

        EXProcess:
            if extractBatch # {} then
                with i \in extractBatch do
                    eItem := i;
                end with;

                EXDoExtract:
                    either
                        text[eItem] := "extracted";
                    or
                        error[eItem] := "error";
                    end either;

                EXNext:
                    extractBatch := extractBatch \ {eItem};
                    eItem := 0;
                    goto EXProcess;
            end if;

        EXDone:
            jobPhase := "done";
    end while;
end process;

end algorithm; *)

\* BEGIN TRANSLATION -- the translator will fill this in
VARIABLES text, error, fetched_at, jobRunning, jobPhase, workerBusy, 
          workerItem, downloadBatch, extractBatch, pc

(* define statement *)
NeedsDownload == {i \in ItemIds : text[i] = "null" /\ error[i] = "null" /\ fetched_at[i] = "null"}


NeedsExtract == {i \in ItemIds : text[i] = "null" /\ error[i] = "null" /\ fetched_at[i] # "null"}


IsTerminal(i) == text[i] # "null" \/ error[i] # "null"




NoDoubleProcessing ==
    \A w1, w2 \in Workers :
        (w1 # w2 /\ workerItem[w1] # 0) => workerItem[w1] # workerItem[w2]


PhaseOrder ==
    \A i \in ItemIds :
        text[i] # "null" => fetched_at[i] # "null"


MutualExclusion ==
    \A i \in ItemIds :
        ~(text[i] = "extracted" /\ error[i] = "error")


SensorGuard ==
    (\E w \in Workers : workerBusy[w]) => jobRunning




AllComplete ==
    <>(\A i \in ItemIds : IsTerminal(i))

VARIABLES myItem, eItem

vars == << text, error, fetched_at, jobRunning, jobPhase, workerBusy, 
           workerItem, downloadBatch, extractBatch, pc, myItem, eItem >>

ProcSet == {"sensor"} \cup (Workers) \cup {"extractor"}

Init == (* Global variables *)
        /\ text = [i \in ItemIds |-> "null"]
        /\ error = [i \in ItemIds |-> "null"]
        /\ fetched_at = [i \in ItemIds |-> "null"]
        /\ jobRunning = FALSE
        /\ jobPhase = "idle"
        /\ workerBusy = [w \in Workers |-> FALSE]
        /\ workerItem = [w \in Workers |-> 0]
        /\ downloadBatch = {}
        /\ extractBatch = {}
        (* Process download_worker *)
        /\ myItem = [self \in Workers |-> 0]
        (* Process extractor *)
        /\ eItem = 0
        /\ pc = [self \in ProcSet |-> CASE self = "sensor" -> "SensorLoop"
                                        [] self \in Workers -> "DWLoop"
                                        [] self = "extractor" -> "EXLoop"]

SensorLoop == /\ pc["sensor"] = "SensorLoop"
              /\ pc' = [pc EXCEPT !["sensor"] = "CheckSensor"]
              /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                              workerBusy, workerItem, downloadBatch, 
                              extractBatch, myItem, eItem >>

CheckSensor == /\ pc["sensor"] = "CheckSensor"
               /\ IF ~jobRunning /\ (NeedsDownload # {} \/ NeedsExtract # {})
                     THEN /\ jobRunning' = TRUE
                          /\ jobPhase' = "downloading"
                          /\ downloadBatch' = NeedsDownload
                     ELSE /\ TRUE
                          /\ UNCHANGED << jobRunning, jobPhase, downloadBatch >>
               /\ pc' = [pc EXCEPT !["sensor"] = "WaitJobDone"]
               /\ UNCHANGED << text, error, fetched_at, workerBusy, workerItem, 
                               extractBatch, myItem, eItem >>

WaitJobDone == /\ pc["sensor"] = "WaitJobDone"
               /\ jobPhase = "done" \/ ~jobRunning
               /\ pc' = [pc EXCEPT !["sensor"] = "SensorReset"]
               /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                               workerBusy, workerItem, downloadBatch, 
                               extractBatch, myItem, eItem >>

SensorReset == /\ pc["sensor"] = "SensorReset"
               /\ IF jobPhase = "done"
                     THEN /\ jobRunning' = FALSE
                          /\ jobPhase' = "idle"
                     ELSE /\ TRUE
                          /\ UNCHANGED << jobRunning, jobPhase >>
               /\ pc' = [pc EXCEPT !["sensor"] = "SensorLoop"]
               /\ UNCHANGED << text, error, fetched_at, workerBusy, workerItem, 
                               downloadBatch, extractBatch, myItem, eItem >>

sensor == SensorLoop \/ CheckSensor \/ WaitJobDone \/ SensorReset

DWLoop(self) == /\ pc[self] = "DWLoop"
                /\ pc' = [pc EXCEPT ![self] = "DWWait"]
                /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                                workerBusy, workerItem, downloadBatch, 
                                extractBatch, myItem, eItem >>

DWWait(self) == /\ pc[self] = "DWWait"
                /\ jobPhase = "downloading"
                /\ pc' = [pc EXCEPT ![self] = "DWClaim"]
                /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                                workerBusy, workerItem, downloadBatch, 
                                extractBatch, myItem, eItem >>

DWClaim(self) == /\ pc[self] = "DWClaim"
                 /\ IF \E i \in downloadBatch :
                        (\A w2 \in Workers : workerItem[w2] # i) /\
                        fetched_at[i] = "null" /\ error[i] = "null"
                       THEN /\ \E i \in        {i \in downloadBatch :
                                        (\A w2 \in Workers : workerItem[w2] # i) /\
                                        fetched_at[i] = "null" /\ error[i] = "null"}:
                                 /\ myItem' = [myItem EXCEPT ![self] = i]
                                 /\ workerItem' = [workerItem EXCEPT ![self] = i]
                                 /\ workerBusy' = [workerBusy EXCEPT ![self] = TRUE]
                            /\ pc' = [pc EXCEPT ![self] = "DWProcess"]
                       ELSE /\ pc' = [pc EXCEPT ![self] = "DWIdle"]
                            /\ UNCHANGED << workerBusy, workerItem, myItem >>
                 /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                                 downloadBatch, extractBatch, eItem >>

DWProcess(self) == /\ pc[self] = "DWProcess"
                   /\ \/ /\ fetched_at' = [fetched_at EXCEPT ![myItem[self]] = "fetched"]
                         /\ error' = error
                      \/ /\ error' = [error EXCEPT ![myItem[self]] = "error"]
                         /\ fetched_at' = [fetched_at EXCEPT ![myItem[self]] = "fetched"]
                   /\ pc' = [pc EXCEPT ![self] = "DWRelease"]
                   /\ UNCHANGED << text, jobRunning, jobPhase, workerBusy, 
                                   workerItem, downloadBatch, extractBatch, 
                                   myItem, eItem >>

DWRelease(self) == /\ pc[self] = "DWRelease"
                   /\ workerBusy' = [workerBusy EXCEPT ![self] = FALSE]
                   /\ workerItem' = [workerItem EXCEPT ![self] = 0]
                   /\ myItem' = [myItem EXCEPT ![self] = 0]
                   /\ pc' = [pc EXCEPT ![self] = "DWClaim"]
                   /\ UNCHANGED << text, error, fetched_at, jobRunning, 
                                   jobPhase, downloadBatch, extractBatch, 
                                   eItem >>

DWIdle(self) == /\ pc[self] = "DWIdle"
                /\ TRUE
                /\ pc' = [pc EXCEPT ![self] = "DWLoop"]
                /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                                workerBusy, workerItem, downloadBatch, 
                                extractBatch, myItem, eItem >>

download_worker(self) == DWLoop(self) \/ DWWait(self) \/ DWClaim(self)
                            \/ DWProcess(self) \/ DWRelease(self)
                            \/ DWIdle(self)

EXLoop == /\ pc["extractor"] = "EXLoop"
          /\ pc' = [pc EXCEPT !["extractor"] = "EXWaitPhase"]
          /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                          workerBusy, workerItem, downloadBatch, extractBatch, 
                          myItem, eItem >>

EXWaitPhase == /\ pc["extractor"] = "EXWaitPhase"
               /\ jobPhase = "downloading" /\
                  (\A w \in Workers : ~workerBusy[w]) /\
                  (\A i \in downloadBatch :
                    fetched_at[i] # "null" \/ error[i] # "null" \/
                    ~(\A w2 \in Workers : workerItem[w2] # i))
               /\ pc' = [pc EXCEPT !["extractor"] = "EXStartPhase"]
               /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                               workerBusy, workerItem, downloadBatch, 
                               extractBatch, myItem, eItem >>

EXStartPhase == /\ pc["extractor"] = "EXStartPhase"
                /\ jobPhase' = "extracting"
                /\ extractBatch' = NeedsExtract
                /\ pc' = [pc EXCEPT !["extractor"] = "EXProcess"]
                /\ UNCHANGED << text, error, fetched_at, jobRunning, 
                                workerBusy, workerItem, downloadBatch, myItem, 
                                eItem >>

EXProcess == /\ pc["extractor"] = "EXProcess"
             /\ IF extractBatch # {}
                   THEN /\ \E i \in extractBatch:
                             eItem' = i
                        /\ pc' = [pc EXCEPT !["extractor"] = "EXDoExtract"]
                   ELSE /\ pc' = [pc EXCEPT !["extractor"] = "EXDone"]
                        /\ eItem' = eItem
             /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                             workerBusy, workerItem, downloadBatch, 
                             extractBatch, myItem >>

EXDoExtract == /\ pc["extractor"] = "EXDoExtract"
               /\ \/ /\ text' = [text EXCEPT ![eItem] = "extracted"]
                     /\ error' = error
                  \/ /\ error' = [error EXCEPT ![eItem] = "error"]
                     /\ text' = text
               /\ pc' = [pc EXCEPT !["extractor"] = "EXNext"]
               /\ UNCHANGED << fetched_at, jobRunning, jobPhase, workerBusy, 
                               workerItem, downloadBatch, extractBatch, myItem, 
                               eItem >>

EXNext == /\ pc["extractor"] = "EXNext"
          /\ extractBatch' = extractBatch \ {eItem}
          /\ eItem' = 0
          /\ pc' = [pc EXCEPT !["extractor"] = "EXProcess"]
          /\ UNCHANGED << text, error, fetched_at, jobRunning, jobPhase, 
                          workerBusy, workerItem, downloadBatch, myItem >>

EXDone == /\ pc["extractor"] = "EXDone"
          /\ jobPhase' = "done"
          /\ pc' = [pc EXCEPT !["extractor"] = "EXLoop"]
          /\ UNCHANGED << text, error, fetched_at, jobRunning, workerBusy, 
                          workerItem, downloadBatch, extractBatch, myItem, 
                          eItem >>

extractor == EXLoop \/ EXWaitPhase \/ EXStartPhase \/ EXProcess
                \/ EXDoExtract \/ EXNext \/ EXDone

Next == sensor \/ extractor
           \/ (\E self \in Workers: download_worker(self))

Spec == /\ Init /\ [][Next]_vars
        /\ WF_vars(sensor)
        /\ \A self \in Workers : WF_vars(download_worker(self))
        /\ WF_vars(extractor)

\* END TRANSLATION

\* Monotonic progress: temporal property -- once set, never reverts
MonotonicText ==
    \A i \in ItemIds : [](text[i] # "null" => [](text[i] # "null"))

MonotonicError ==
    \A i \in ItemIds : [](error[i] # "null" => [](error[i] # "null"))

MonotonicFetchedAt ==
    \A i \in ItemIds : [](fetched_at[i] # "null" => [](fetched_at[i] # "null"))

====
