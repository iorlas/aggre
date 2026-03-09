#!/bin/bash
# Autonomy Mode — Plan and execute from task description
# Usage: ./scripts/autonomy-full.sh "build feature X because Y"
#
# Env vars:
#   ANTHROPIC_API_KEY  — required
#   MAX_ITERATIONS     — max Ralph iterations (default: 50)

set -euo pipefail

TASK="$*"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "$TASK" ]]; then
  echo "Usage: autonomy-full.sh <task description>"
  echo 'Example: ./scripts/autonomy-full.sh "add caching to the MCP server"'
  exit 1
fi

echo "Building autonomy image..."
docker build -q -t claude-autonomy "$SCRIPT_DIR/autonomy/"

echo "Launching autonomy mode: plan + execute"
echo "Task: $TASK"
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
  full "$TASK"
