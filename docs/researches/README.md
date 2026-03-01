# Research Index

## Formal Verification for Concurrent System Design

Research into using formal methods to catch concurrency/integration bugs that AI coding agents miss. Motivated by the observation that Claude Code lacks context to systematically enumerate concurrent interleavings.

### Documents

1. **[postgresql-as-queue.md](postgresql-as-queue.md)** — Core PostgreSQL queue pattern (`FOR UPDATE SKIP LOCKED`, `LISTEN/NOTIFY`, retries, caveats). This is the system we want to formally verify.

2. **[formal-verification-for-concurrency.md](formal-verification-for-concurrency.md)** — Initial landscape survey. Covers the AI + formal methods convergence, spec-first workflows with LLMs (Bhatti, Terzian), Specula controversy, existing PostgreSQL TLA+ specs (boring-task-queue), and the same queue modeled in FizzBee/TLA+/Quint side by side.

3. **[formal-verification-ecosystem.md](formal-verification-ecosystem.md)** — Deep ecosystem research from 5 parallel agents. Covers TLA+ (TLC, TLAPS, CommunityModules, 109 example specs, industry adoption), Quint (tooling, TLA+ compatibility gaps, no liveness checking), FizzBee (memory dealbreaker, MBT Go-only), Apalache (Z3-powered, development slowing), Z3 (backend only, not direct use). Head-to-head comparison table.

4. **[formal-verification-poc-results.md](formal-verification-poc-results.md)** — Hands-on PoC: modeled the actual Aggre content + enrichment pipelines in TLA+, Quint, and FizzBee. All three detected the enrichment partial failure bug. Comparison was not fully fair (different model sizes/granularity). TLA+ won: fastest per-state, full liveness, most efficient LLM authoring.

### Key Findings Summary

**The thesis is confirmed:** A formal spec gives Claude a condensed, unambiguous system description. Practitioners report: "The 10% I write by hand is not code but specification" — Claude implements correctly when spec is precise.

**Tool verdict:**

| Tool | Verdict | Key Risk |
|------|---------|----------|
| **TLA+ (PlusCal + TLC)** | Safest choice. Full liveness, largest ecosystem, existing queue specs, best LLM compatibility | Learning curve (2-3 weeks) |
| **Quint** | Best syntax, but **no liveness checking** (cannot verify "every task eventually completes"). TLC backend just landed (v0.31.0) which may fix this | Pre-1.0, 29 bugs, blockchain-skewed priorities |
| **FizzBee** | Easiest to learn, but **memory is a dealbreaker** for non-trivial models. 180GB swap killed after 5h where TLC did 23s | One-person project, no Python MBT |
| **Apalache** | Great for inductive invariants (4s vs TLC's 3h), but development slowing after Informal Systems divestiture | Volunteer-maintained since late 2024 |
| **Z3** | Backend engine only. Never used directly for system verification | N/A |

### PoC Outcome

All three tools modeled the Aggre pipeline and found the enrichment bug. TLA+ is the recommended tool — full liveness, fastest checker, best LLM authoring efficiency. The comparison exposed that FizzBee's "easy syntax" advantage doesn't help LLMs (2.2x more tool calls than TLA+), and Quint's broken liveness checking is a dealbreaker. Specs and results live in `.planning/verification/`.

---

## Modern Shell & CLI Tools

Research into replacing zsh/bash workflow with modern alternatives. Motivated by broken macOS zsh keybindings, slow oh-my-zsh startup, and primitive directory navigation / history search.

### Documents

1. **[modern-shell-and-cli-tools.md](modern-shell-and-cli-tools.md)** — Shell comparison (bash/zsh/fish/nushell), community opinions from HN/Lobsters, Claude Code compatibility analysis, and tiered catalog of modern CLI replacements (atuin, zoxide, ripgrep, fd, bat, eza, lazygit, etc.).

### Key Findings Summary

**Shell verdict:** Fish for interactive use, zsh as login shell (Claude Code compatibility). Skip nushell (pre-1.0, ecosystem too thin). Skip Oh My Fish (unmaintained) — use Fisher + Tide instead.

**Biggest CLI wins:** atuin (shell history with fuzzy search, directory/exit code filtering) and zoxide (frecency-based `cd` — `z aggre` from anywhere). Both solve daily pain points immediately.

**Installed:** fish 4.5.0, zoxide, fd, atuin, btop, lazygit, Fisher, Tide v6.
