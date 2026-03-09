#!/usr/bin/env bash
# Claude Code statusline — sexy edition
# Receives session JSON on stdin

input=$(cat)

# ── Colors ──
R="\033[0m"
DIM="\033[2m"
BOLD="\033[1m"
BLINK="\033[5m"
GREEN="\033[32m"
YELLOW="\033[33m"
ORANGE="\033[38;5;208m"
RED="\033[31m"
CYAN="\033[36m"
MAGENTA="\033[35m"
BLUE="\033[34m"
SEP="${DIM} │ ${R}"

# ── Parse all JSON fields in one jq call ──
eval "$(echo "$input" | jq -r '
  @sh "workdir=\(.workspace.current_dir // ".")",
  @sh "projdir=\(.workspace.project_dir // ".")",
  @sh "model=\(.model.display_name // "Claude")",
  @sh "used=\(.context_window.used_percentage // "")",
  @sh "cost=\(.cost.total_cost_usd // "")",
  @sh "duration_ms=\(.cost.total_duration_ms // "")",
  @sh "added=\(.cost.total_lines_added // "0")",
  @sh "removed=\(.cost.total_lines_removed // "0")"
')"

# ── Model ──
model_str="${DIM}${model}${R}"

# ── Folder name ──
dir_str="${BLUE}${workdir##*/}${R}"

# ── Git branch + dirty ──
branch=$(git -C "$workdir" rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ -n "$branch" ]; then
    dirty=""
    if [ -n "$(git -C "$workdir" status --porcelain 2>/dev/null | head -1)" ]; then
        dirty="${YELLOW}*${R}"
    fi
    branch_str="${MAGENTA} ${branch}${dirty}${R}"
else
    branch_str=""
fi

# ── Context usage with mini bar ──
if [ -n "$used" ]; then
    used_int=${used%.*}
    if [ "$used_int" -lt 50 ]; then
        cc="$GREEN"; icon=""
    elif [ "$used_int" -lt 65 ]; then
        cc="$YELLOW"; icon=""
    elif [ "$used_int" -lt 75 ]; then
        cc="$ORANGE"; icon="◆ "
    elif [ "$used_int" -lt 85 ]; then
        cc="${BOLD}${ORANGE}"; icon="▲ COMPACT "
    else
        cc="${BLINK}${RED}"; icon="⚠ COMPACT "
    fi
    filled=$((used_int / 20))
    empty=$((5 - filled))
    bar=""
    for ((i=0; i<filled; i++)); do bar+="▮"; done
    for ((i=0; i<empty; i++)); do bar+="▯"; done
    ctx_str="${cc}${icon}${bar} ${used_int}%${R}"
else
    ctx_str="${DIM}▯▯▯▯▯ --%${R}"
fi

# ── MCP servers ──
settings_file="${projdir}/.claude/settings.json"
if [ -f "$settings_file" ]; then
    mcp_count=$(jq '.enabledMcpjsonServers | if type == "array" then length else 0 end' \
        "$settings_file" 2>/dev/null)
    mcp_count=${mcp_count:-0}
else
    mcp_count=0
fi
mcp_str="${CYAN}⚡${mcp_count}${R}"

# ── Session cost (color-coded) ──
if [ -n "$cost" ]; then
    # Pure bash: multiply by 100 via string manipulation to avoid bc dependency
    cost_fmt=$(printf '%.2f' "$cost")
    cost_int=${cost_fmt%.*}
    cost_frac=${cost_fmt#*.}
    cost_cents=$((10#${cost_int} * 100 + 10#${cost_frac}))
    if [ "$cost_cents" -lt 100 ]; then
        cost_color="$GREEN"
    elif [ "$cost_cents" -lt 300 ]; then
        cost_color="$YELLOW"
    elif [ "$cost_cents" -lt 500 ]; then
        cost_color="$ORANGE"
    else
        cost_color="$RED"
    fi
    cost_str="${cost_color}\$${cost_fmt}${R}"
else
    cost_str=""
fi

# ── Wall clock ──
if [ -n "$duration_ms" ]; then
    total_sec=$((duration_ms / 1000))
    mins=$((total_sec / 60))
    secs=$((total_sec % 60))
    if [ "$mins" -gt 0 ]; then
        time_str="${DIM}${mins}m${secs}s${R}"
    else
        time_str="${DIM}${secs}s${R}"
    fi
else
    time_str=""
fi

# ── Code churn ──
if [ "$added" -gt 0 ] || [ "$removed" -gt 0 ]; then
    churn_str="${GREEN}+${added}${R} ${RED}-${removed}${R}"
else
    churn_str=""
fi

# ── Active Claude Code sessions ──
session_count=$(pgrep -x 'claude' 2>/dev/null | wc -l | tr -d ' ')
if [ "$session_count" -gt 1 ]; then
    session_str="${DIM}sess:${session_count}${R}"
else
    session_str=""
fi

# ── Assemble ──
out="${model_str}${SEP}${dir_str}${SEP}${branch_str}${SEP}${ctx_str}${SEP}${mcp_str}"
[ -n "$cost_str" ] && out+="${SEP}${cost_str}"
[ -n "$time_str" ] && out+="${SEP}${time_str}"
[ -n "$churn_str" ] && out+="${SEP}${churn_str}"
[ -n "$session_str" ] && out+="${SEP}${session_str}"

printf '%b' "$out"
