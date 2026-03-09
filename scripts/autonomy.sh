#!/bin/bash
# Autonomy Mode — Execute a plan
# Usage: ./scripts/autonomy.sh [PLAN_FILE]
# Default: PLAN.md
#
# Env vars:
#   ANTHROPIC_API_KEY  — required
#   MAX_ITERATIONS     — max Ralph iterations (default: 50)

set -euo pipefail

PLAN_FILE="${1:-PLAN.md}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$PROJECT_DIR/$PLAN_FILE" ]]; then
  echo "Error: $PLAN_FILE not found in $PROJECT_DIR"
  exit 1
fi

echo "Building autonomy image..."
docker build -q -t claude-autonomy "$SCRIPT_DIR/autonomy/"

echo "Launching autonomy mode: execute $PLAN_FILE"
echo "Max iterations: ${MAX_ITERATIONS:-50}"
echo ""

# Extract OAuth credentials from macOS Keychain for container auth
security find-generic-password -s "Claude Code-credentials" -a "$(whoami)" -w \
  > "$HOME/.claude/.credentials.json" 2>/dev/null || true

# Copy .claude.json for container (avoid corruption on kill)
cp "$HOME/.claude.json" "$HOME/.claude/.claude.json.container" 2>/dev/null || true

docker run --rm -it \
  --network host \
  -v "$PROJECT_DIR:/work" \
  -v autonomy-venv:/work/.venv \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$HOME/.claude:/root/.claude" \
  -v "$HOME/.claude/.claude.json.container:/root/.claude.json" \
  -e "MAX_ITERATIONS=${MAX_ITERATIONS:-50}" \
  claude-autonomy \
  execute "$PLAN_FILE"
