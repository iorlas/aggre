# Plan: Analyze Conflicting Concepts with Dagster Ecosystem & Medallion Guidelines

## Goal
Identify and document all conceptual conflicts between the current codebase and Dagster ecosystem idioms + medallion-guidelines.md. Write findings to SUGGESTIONS.md. No code changes.

## Steps

### Step 1: Analyze sensor anti-patterns
- **What**: Sensors poll status columns on parent silver rows (fetch_status, transcription_status, enriched_at, comments_status). This creates tight coupling between Dagster orchestration and silver data model — silver statuses serve double duty as both data state AND orchestration triggers.
- **Success criteria**: Document each sensor, what it polls, and why this conflicts with Dagster's asset/sensor cursor model.

### Step 2: Analyze Dagster definition organization
- **What**: All sensors + schedule in one file. Jobs split into files but all in same `jobs/` directory. No domain separation.
- **Success criteria**: Document current layout and propose domain-aligned alternatives.

### Step 3: Identify dead/redundant code from pre-Dagster era
- **What**: CLI `run-once` with drain loops, `status` command, orchestration helpers that duplicate Dagster capabilities.
- **Success criteria**: List each piece of code and whether it's fully redundant, partially redundant, or still needed.

### Step 4: Identify helpers that could be generic/reusable
- **What**: bronze.py, bronze_http.py, urls.py utilities, logging.py, http.py — classify as domain-specific or generic-reusable.
- **Success criteria**: Classify each helper module.

### Step 5: Analyze domain separation issues
- **What**: Do module boundaries align with Dagster job boundaries? Are they separated enough for parallel agent work?
- **Success criteria**: Map current coupling points and propose cleaner separation.

### Step 6: Analyze medallion guideline violations
- **What**: Compare actual code patterns against docs/medallion-guidelines.md prescriptions.
- **Success criteria**: Document each violation with file/line references.

### Step 7: Write SUGGESTIONS.md
- **What**: Compile all findings into structured document.
- **Success criteria**: Document is complete, actionable, and references specific files/lines.

### Step 8: Log structural decisions to DECISIONS.md
- **What**: Document analytical framework and key judgment calls.
- **Success criteria**: DECISIONS.md exists with clear rationale.

### Step 9: Verify
- **What**: Ensure no code was accidentally changed.
- **Success criteria**: `git diff` shows only new/modified .md files.
