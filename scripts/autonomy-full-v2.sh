#!/bin/bash
# Autonomy Mode v2 — Plan and execute from task description (native Claude Code TUI)
# Usage: ./scripts/autonomy-full-v2.sh "build feature X because Y"
#
# Env vars:
#   ANTHROPIC_API_KEY  — required
#   MAX_ITERATIONS     — max Ralph iterations (default: 50)

set -euo pipefail

TASK="$*"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "$TASK" ]]; then
  echo "Usage: autonomy-full-v2.sh <task description>"
  echo 'Example: ./scripts/autonomy-full-v2.sh "add caching to the MCP server"'
  exit 1
fi

echo "Building autonomy v2 image..."
docker build -q -f "$SCRIPT_DIR/autonomy/Dockerfile-v2" -t claude-autonomy-v2 "$SCRIPT_DIR/autonomy/"

echo "Launching autonomy mode v2 (native TUI): plan + execute"
echo "Task: $TASK"
echo "Max iterations: ${MAX_ITERATIONS:-50}"
echo ""

# Extract OAuth credentials from macOS Keychain for container auth
security find-generic-password -s "Claude Code-credentials" -a "$(whoami)" -w \
  > "$HOME/.claude/.credentials.json" 2>/dev/null || true

# Copy .claude.json for container (avoid corruption on kill)
# Patch: add /work project entry so Claude doesn't trigger project onboarding
cp "$HOME/.claude.json" "$HOME/.claude/.claude.json.container" 2>/dev/null || true
jq '.projects["/work"] = {
  "allowedTools": [], "mcpContextUris": [], "mcpServers": {},
  "enabledMcpjsonServers": [], "disabledMcpjsonServers": [],
  "hasTrustDialogAccepted": true, "projectOnboardingSeenCount": 1,
  "hasClaudeMdExternalIncludesApproved": false,
  "hasClaudeMdExternalIncludesWarningShown": false,
  "hasCompletedProjectOnboarding": true
}' "$HOME/.claude/.claude.json.container" > "$HOME/.claude/.claude.json.container.tmp" \
  && mv "$HOME/.claude/.claude.json.container.tmp" "$HOME/.claude/.claude.json.container"

docker run --rm -it \
  --network host \
  -v "$PROJECT_DIR:/work" \
  -v autonomy-venv:/work/.venv \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$HOME/.claude:/root/.claude" \
  -v "$HOME/.claude/.claude.json.container:/root/.claude.json" \
  -e "MAX_ITERATIONS=${MAX_ITERATIONS:-50}" \
  claude-autonomy-v2 \
  full "$TASK"
