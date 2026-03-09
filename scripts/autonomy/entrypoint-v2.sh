#!/bin/bash
set -euo pipefail

# --- Inject stop hook into mounted ~/.claude/settings.json ---
SETTINGS_FILE="/root/.claude/settings.json"
SETTINGS_BAK="/root/.claude/settings.json.autonomy-bak"

if [ -f "$SETTINGS_FILE" ]; then
  cp "$SETTINGS_FILE" "$SETTINGS_BAK"
  jq '.hooks.Stop = [{"hooks":[{"type":"command","command":"/opt/autonomy/stop-hook.sh"}]}]' \
    "$SETTINGS_BAK" > "$SETTINGS_FILE"
else
  mkdir -p /root/.claude
  echo '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"/opt/autonomy/stop-hook.sh"}]}]}}' \
    > "$SETTINGS_FILE"
fi

# --- Parse args ---
MODE="${1:-execute}"
shift || true

MAX_ITERATIONS="${MAX_ITERATIONS:-50}"
COMPLETION_PROMISE="TASK COMPLETE"

case "$MODE" in
  execute)
    PLAN_FILE="${1:-PLAN.md}"
    if [[ ! -f "$PLAN_FILE" ]]; then
      echo "Error: Plan file '$PLAN_FILE' not found"
      echo "Create a plan first, or use autonomy-full-v2.sh for plan+execute mode"
      exit 1
    fi
    PROMPT="Read the plan in $PLAN_FILE and execute it step by step.

Rules:
1. Read CLAUDE.md first for project conventions
2. Execute each step sequentially
3. After each step, run tests (make test) and linters (ruff check src tests, ty check). Fix issues before continuing.
4. Log structural decisions to DECISIONS.md (format: ## [area] — chose X over Y — because Z)
5. Commit after completing each logical unit of work with a descriptive message
6. If stuck after 3 attempts on the same issue, write BLOCKED.md explaining what happened

When all steps are complete and verified (tests pass, linters clean), output:
<promise>$COMPLETION_PROMISE</promise>"
    ;;

  full)
    TASK="$*"
    if [[ -z "$TASK" ]]; then
      echo "Error: No task description provided"
      echo "Usage: autonomy-full-v2.sh <task description>"
      exit 1
    fi
    PROMPT="Your task: $TASK

Phase 1 — Plan:
- Read CLAUDE.md for project conventions
- Analyze the codebase structure and relevant source files
- Create a step-by-step plan in PLAN.md with success criteria for each step

Phase 2 — Execute:
- Execute the plan step by step
- After each step, run tests (make test) and linters (ruff check src tests, ty check). Fix issues before continuing.
- Log structural decisions to DECISIONS.md (format: ## [area] — chose X over Y — because Z)
- Commit after completing each logical unit of work

Phase 3 — Verify:
- Run full test suite and linters
- Review that plan goals in PLAN.md are met
- Check DECISIONS.md for consistency

If stuck after 3 attempts on the same issue, write BLOCKED.md explaining what happened.

When all phases are complete and verified, output:
<promise>$COMPLETION_PROMISE</promise>"
    ;;

  *)
    echo "Usage: entrypoint-v2.sh <execute|full> [args...]"
    echo "  execute [PLAN_FILE]     Execute an existing plan (default: PLAN.md)"
    echo "  full <task description> Plan and execute a task from scratch"
    exit 1
    ;;
esac

# Create ralph loop state file
mkdir -p .claude
cat > .claude/ralph-loop.local.md <<EOF
---
active: true
iteration: 1
max_iterations: $MAX_ITERATIONS
completion_promise: "$COMPLETION_PROMISE"
started_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
---

$PROMPT
EOF

# Cleanup on exit: restore original settings, remove state file
cleanup() {
  if [ -f "$SETTINGS_BAK" ]; then
    mv "$SETTINGS_BAK" "$SETTINGS_FILE"
  fi
  rm -f .claude/ralph-loop.local.md
}
trap cleanup EXIT

# Update Claude Code to latest before launching
echo "Updating Claude Code..."
npm update -g @anthropic-ai/claude-code 2>/dev/null || true

echo "=== Autonomy Mode v2 (Native TUI) ==="
echo "Mode: $MODE"
echo "Max iterations: $MAX_ITERATIONS"
echo "Completion promise: $COMPLETION_PROMISE"
echo "======================================="
echo ""

# Launch Claude Code with native TUI (interactive mode, not -p print mode)
# Positional arg sends the prompt as first message while keeping the full TUI.
# The stop hook (ralph loop) handles continuation by blocking exit and re-prompting.
set +e

claude \
  --dangerously-skip-permissions \
  --disallowedTools "AskUserQuestion" \
  --append-system-prompt "AUTONOMY MODE: You are running unattended in a Docker container. Never ask questions — decide yourself. Log structural decisions to DECISIONS.md. Run tests and linters after changes. If stuck 3x on same issue, write BLOCKED.md and output <promise>$COMPLETION_PROMISE</promise>." \
  --verbose \
  "$PROMPT"
exit $?
