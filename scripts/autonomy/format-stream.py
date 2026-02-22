#!/usr/bin/env python3
"""Real-time formatter for Claude Code stream-json output."""
import json
import sys

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

last_kind = None  # "action" | "text" | None

def sep(new_kind):
    """Print blank line on action↔text transitions."""
    global last_kind
    if last_kind and last_kind != new_kind:
        print(flush=True)
    last_kind = new_kind

def truncate(text, max_len=300):
    text = text.strip()
    if len(text) > max_len:
        return text[:max_len - 3] + "..."
    return text

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue

    etype = event.get("type")

    if etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text":
                sep("text")
                print(block["text"], flush=True)
            elif block.get("type") == "tool_use":
                sep("action")
                name = block.get("name", "?")
                inp = block.get("input", {})
                if name == "Bash":
                    print(f"  {CYAN}${RESET} {inp.get('command', '?')}", flush=True)
                elif name == "Read":
                    print(f"  {DIM}📖 {inp.get('file_path', '?')}{RESET}", flush=True)
                elif name in ("Write", "Edit"):
                    print(f"  {YELLOW}✏️  {inp.get('file_path', '?')}{RESET}", flush=True)
                elif name in ("Glob", "Grep"):
                    print(f"  {DIM}🔍 {name.lower()}: {inp.get('pattern', '?')}{RESET}", flush=True)
                elif name == "Task":
                    desc = inp.get("description", inp.get("prompt", "?")[:60])
                    print(f"  {BOLD}🤖 {desc}{RESET}", flush=True)
                elif name == "Skill":
                    print(f"  {BOLD}⚡ {inp.get('skill', '?')}{RESET}", flush=True)
                else:
                    summary = json.dumps(inp)[:120] if inp else ""
                    print(f"  {DIM}🔧 {name}{RESET} {summary}", flush=True)

    elif etype == "user":
        result = event.get("tool_use_result")
        if result is None:
            continue
        sep("action")
        if isinstance(result, str):
            text = result
        elif isinstance(result, dict):
            if result.get("type") == "text" and "file" in result:
                f = result["file"]
                text = f"({f.get('totalLines', '?')} lines)"
            else:
                text = result.get("stdout", "") or result.get("content", "")
                stderr = result.get("stderr", "")
                if stderr and not text:
                    text = stderr
        else:
            text = str(result)
        text = truncate(text)
        if text:
            is_error = isinstance(result, dict) and result.get("is_error") is True
            color = RED if is_error else DIM
            print(f"    {color}{text}{RESET}", flush=True)

    elif etype == "result":
        last_kind = None
        cost = event.get("total_cost_usd", 0)
        turns = event.get("num_turns", "?")
        duration_s = event.get("duration_ms", 0) / 1000
        print(flush=True)
        print(f"{BOLD}{'=' * 50}{RESET}", flush=True)
        print(f"{GREEN}✅ Done{RESET} | {turns} turns | {duration_s:.0f}s | ${cost:.4f}", flush=True)
        print(f"{BOLD}{'=' * 50}{RESET}", flush=True)

    elif etype == "system":
        last_kind = None
        subtype = event.get("subtype", "")
        if subtype == "init":
            print(f"{DIM}⚙️  [init] model={event.get('model', '?')}{RESET}", flush=True)
        elif subtype == "hook_response" and event.get("exit_code", 0) != 0:
            hook = event.get("hook_name", "?")
            stderr = event.get("stderr", "") or event.get("output", "")
            detail = truncate(stderr, 200) if stderr else ""
            print(f"{RED}❌ [hook error] {hook}{RESET}", flush=True)
            if detail:
                print(f"    {RED}{detail}{RESET}", flush=True)
