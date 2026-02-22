# Plan: Code File Audit & Status Column Analysis

## Problem
1. Every code file needs its location and purpose challenged
2. Question: are state machines and status columns necessary when Dagster handles orchestration?
   - User's thesis: "if failure = no data saved, next run resyncs"

## Audit Summary

### File Placement Verdict: All Well-Placed
After the recent restructuring (pipeline/, utils/ extraction), every file has a clear single responsibility at the correct layer:
- **Layer 1 (infrastructure):** db.py, statuses.py, urls.py, settings.py, utils/*
- **Layer 2 (business):** pipeline/*, collectors/*/collector.py
- **Layer 3 (composition):** config.py, cli.py, collectors/__init__.py, dagster_defs/*

No files need relocation. One known layer-2 cross-import (content_extractor → content_downloader.content_fetch_failed) was a deliberate consolidation decision documented in DECISIONS.md.

### Status Column Verdict: Essential, But TranscriptionStatus Can Be Simplified

**FetchStatus — KEEP AS-IS:**
- PENDING → DOWNLOADED → FETCHED | SKIPPED | FAILED
- Two-stage pipeline (download → extract) needs DOWNLOADED as handoff state
- SKIPPED/FAILED prevent infinite retries on YouTube URLs, PDFs, 404s
- Sensor polls for PENDING/DOWNLOADED to trigger jobs
- Cannot be replaced by "no data = retry" because some content is permanently unfetchable

**TranscriptionStatus — SIMPLIFY:**
- Current: PENDING → DOWNLOADING → TRANSCRIBING → COMPLETED | FAILED
- The transcriber queries ALL THREE intermediate states identically: `.in_((PENDING, DOWNLOADING, TRANSCRIBING))`
- Bronze cache (audio.opus, whisper.json) provides actual crash recovery, not the intermediate states
- Simplify to: PENDING → COMPLETED | FAILED
- Removes 2 enum values, 2 transition functions, 2 status update calls

**CommentsStatus — KEEP AS-IS:**
- NULL = source doesn't support comments (RSS, HuggingFace, Telegram)
- PENDING = comments available but not yet fetched
- DONE = comments fetched
- Three-value semantic is essential; can't use comments_json IS NULL because NULL is ambiguous

**enriched_at — KEEP AS-IS:**
- Already minimal — NULL vs non-NULL timestamp

## Steps

### Step 1: Simplify TranscriptionStatus enum
- Remove DOWNLOADING and TRANSCRIBING from `statuses.py`
- **Success:** Enum has only PENDING, COMPLETED, FAILED

### Step 2: Simplify transcriber.py
- Remove `transcription_downloading()` and `transcription_transcribing()` functions
- Remove intermediate status update calls during processing
- Simplify query to `transcription_status == TranscriptionStatus.PENDING`
- **Success:** Transcriber processes PENDING items, transitions directly to COMPLETED or FAILED

### Step 3: Add Alembic migration
- Convert any existing "downloading" or "transcribing" rows to "pending"
- **Success:** Legacy data is compatible with simplified enum

### Step 4: Run tests and linters
- `make test`, `ruff check src tests`, `ty check`
- **Success:** All pass

### Step 5: Update documentation
- Document status column analysis decision in DECISIONS.md
- Update ARCHITECTURE.md and semantic-model.md if needed
- **Success:** Docs match code

## After — TranscriptionStatus
```
PENDING → COMPLETED | FAILED
```
(Down from PENDING → DOWNLOADING → TRANSCRIBING → COMPLETED | FAILED)
