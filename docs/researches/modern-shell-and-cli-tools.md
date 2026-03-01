# Modern Shell & CLI Tools — Research (2026-03-01)

## Context

Long-time bash user on macOS, where Apple switched the default to zsh years ago. Pain points: broken keybindings (Fn+arrows for Home/End don't work in zsh out of the box), slow shell startup with oh-my-zsh plugins, tedious directory navigation (`cd a/b/c/d` chains), and primitive ctrl+r history search.

Researched: modern shell options, community opinions (HN, Lobsters, Reddit), and the current ecosystem of CLI tool replacements.

## Shell Comparison

| Shell | Philosophy | POSIX | Out-of-box UX | Ecosystem | Daily-driver ready |
|-------|-----------|-------|---------------|-----------|-------------------|
| **bash** | Conservative, universal | Yes | Minimal | Everything assumes it | Yes (boring) |
| **zsh** | Extensible bash superset | Yes | Bad (needs oh-my-zsh/zinit) | Massive (plugins, themes) | Yes (with config work) |
| **fish** | Batteries-included | No | Excellent (autosuggestions, syntax highlighting, completions — zero config) | Smaller but sufficient (Fisher, Tide) | Yes |
| **nushell** | Structured data pipelines | No | Good for data work | Small, pre-1.0 | Not yet |

### Community consensus (HN, Lobsters, 2024-2025)

- **Fish is the practical winner** for interactive use. "Awesome and works out of the box — barely need any configuration."
- **Nushell is exciting but not ready.** Pre-1.0, breaking changes, LLMs and tutorials don't speak it. One maintainer admits "breaking changes fairly often." People who tried daily-driving it [switched back to zsh](https://ryanxcharles.com/blog/2025-05-26-nushell-to-zsh) because every tutorial/LLM assumes POSIX.
- **Most pros' actual setup:** fish or zsh interactively, bash for scripts, Python for complex automation.
- **The POSIX gravity is real** — every StackOverflow answer, every README, every LLM assumes bash/zsh syntax.

### The zsh keybinding problem

Not a philosophy issue — Apple ships zsh as default but with no `.zshrc`. Fn+Left/Right sends Home/End escape codes that zsh doesn't bind by default. Fix: `bindkey "^[[H" beginning-of-line` / `bindkey "^[[F" end-of-line` in `.zshrc`. But this is symptomatic of zsh's "configure everything yourself" approach.

### Fish caveats

- **Not POSIX:** can't paste bash one-liners directly (e.g., `VAR=x command` doesn't work, need `env VAR=x command`).
- **Different syntax:** no aliases in the bash sense — uses `abbr` (abbreviations) or functions.
- **Framework:** skip Oh My Fish (unmaintained). Use **Fisher** (lightweight plugin manager) + **Tide** (async prompt, like powerlevel10k).
- **Fish 4.x** (Feb 2025) was rewritten from C++ to Rust — fast.

### Claude Code compatibility

Claude Code does **not** officially support fish. The Bash tool always uses bash/zsh internally regardless of login shell. Key issues:
- [#7490](https://github.com/anthropics/claude-code/issues/7490) — cannot configure which shell the Bash tool uses (77 upvotes, open)
- [#25779](https://github.com/anthropics/claude-code/issues/25779) — SSH remote mode breaks with fish login shell
- PATH configured only in fish config won't be visible to Claude Code

**Workaround:** Keep zsh as macOS login shell. Set fish as terminal startup command (iTerm2 > Profiles > General > Command > `/opt/homebrew/bin/fish`). Claude Code uses zsh internally and is happy. PATH must be configured in both shells or via `/etc/paths.d/`.

## Modern CLI Tool Replacements

Researched Rust/Go rewrites of classic Unix tools. Sorted by impact.

### Tier 1: Immediate productivity gains

| Replaces | Tool | What changes | Install |
|----------|------|-------------|---------|
| `ctrl+r` | **[atuin](https://github.com/atuinsh/atuin)** | SQLite shell history. Fuzzy search, filter by directory/exit code/duration, cross-machine sync (encrypted). Biggest single QoL win. | `brew install atuin` |
| `cd a/b/c` | **[zoxide](https://github.com/ajeetdsouza/zoxide)** (`z`) | Frecency-based directory jumping. `z aggre` from anywhere. Learns from usage. | `brew install zoxide` |
| `grep -r` | **[ripgrep](https://github.com/BurntSushi/ripgrep)** (`rg`) | 10-100x faster, respects `.gitignore`, better defaults. | `brew install ripgrep` |
| `find` | **[fd](https://github.com/sharkdp/fd)** | `fd pattern` instead of `find . -name '*pattern*'`. Parallel, colorized, `.gitignore`-aware. | `brew install fd` |
| `cat` | **[bat](https://github.com/sharkdp/bat)** | Syntax highlighting, line numbers, git diff markers. | `brew install bat` |
| `ls` | **[eza](https://github.com/eza-community/eza)** | Colors, icons, git status column, tree view. Maintained fork of exa. | `brew install eza` |

### Tier 2: Valuable workflow tools

| Tool | Purpose | Install |
|------|---------|---------|
| **[fzf](https://github.com/junegunn/fzf)** | Universal fuzzy finder. Pipe anything: `vim $(fzf)`, `git checkout $(git branch \| fzf)`. | `brew install fzf` |
| **[lazygit](https://github.com/jesseduffield/lazygit)** | Git TUI. Staging hunks, rebasing, cherry-picking — all keyboard-driven. Faster than `git add -p`. | `brew install lazygit` |
| **[delta](https://github.com/dandavid0x/delta)** | Git diff pager. Syntax highlighting, line numbers, side-by-side. | `brew install git-delta` |
| **[tldr](https://tldr.sh/)** | Community man pages with examples. `tldr tar` shows 5 useful examples instead of 2000 lines. | `brew install tlrc` |
| **[btop](https://github.com/aristocratos/btop)** | htop replacement. Prettier, more informative. | `brew install btop` |

### Tier 3: Nice to have

| Tool | Purpose |
|------|---------|
| **[yazi](https://github.com/sxyazi/yazi)** | Terminal file manager (Rust). Image previews, syntax highlighting, bulk rename. Successor to ranger. |
| **[dust](https://github.com/bootandy/dust)** | Visual `du` — disk usage as tree with bars. |
| **[hyperfine](https://github.com/sharkdp/hyperfine)** | CLI benchmarking with warmup, statistical analysis. |
| **[jq](https://github.com/jqlang/jq)** | JSON CLI processor. Likely already installed. |

## Terminal Emulators

Researched GPU-accelerated and AI-native terminal emulators as potential iTerm2 replacements.

### Comparison

| Terminal | Engine | Rendering | Philosophy | Best for |
|----------|--------|-----------|-----------|----------|
| **iTerm2** | Obj-C | CPU | Feature-rich, mature | tmux -CC integration, triggers, Python API, macOS power users |
| **Ghostty** | Zig | Metal (GPU) | Fast + Mac-native + minimal config | Speed + polish with zero fuss. "Fish of terminals" |
| **WezTerm** | Rust | WebGPU/Metal | Programmable (Lua), built-in mux | Power users who script their terminal, remote work |
| **Kitty** | C/Python | OpenGL | "Terminal as a platform" | Image-heavy TUIs, neovim users, own graphics protocol |
| **Alacritty** | Rust | OpenGL | Minimal, performance-only | Purists — no tabs, no splits, pair with tmux |
| **cmux** | Swift | libghostty | Agent-session management | Multiple Claude Code / AI agent sessions |
| **Warp** | Rust | Metal | AI-native, block-based output | Natural language → shell commands, built-in AI agent |

### Key findings

**Performance:** GPU-accelerated terminals (Ghostty, WezTerm, Kitty) deliver sub-10ms latency and smoother scrolling vs iTerm2's CPU rendering. On M-series Macs, [the difference is noticeable but not dramatic](https://news.ycombinator.com/item?id=42518591). Memory: Ghostty ~129MB vs iTerm2 ~207MB for similar workloads.

**iTerm2's unique strength:** tmux -CC mode renders tmux panes as native macOS tabs/windows. No other terminal does this. Critical for remote work.

**Ghostty's appeal:** Built in Zig, uses Metal on macOS, feels like a native Apple app. Sensible defaults, minimal config. The community's "just switch and forget" recommendation. Created by HashiCorp co-founder Mitchell Hashimoto.

**cmux's niche:** Designed specifically for multi-agent workflows. Vertical sidebar shows git branch, working directory, ports, notification text per tab. Blue ring indicator when Claude Code needs attention. Built on libghostty. [3,000+ GitHub stars](https://github.com/manaflow-ai/cmux). AGPL-3.0.

### Should you migrate from iTerm2?

**No strong reason for existing users on M-series Macs.** GPU rendering improvements are real but marginal on Apple Silicon. iTerm2's maturity, tmux -CC, and ecosystem are hard to replace.

**Exception:** cmux solves the "which Claude Code tab needs me?" problem that no other terminal addresses. Worth running alongside iTerm2 specifically for Claude Code sessions.

### AI-Native Terminals

**Warp** is the main "AI terminal" — block-based output, built-in AI agent (Claude Sonnet, GPT-4o), natural language command suggestions. Has [official Claude Code integration](https://github.com/warpdotdev/claude-code-warp). However:

- **Redundant with Claude Code.** Warp's AI features overlap with what Claude Code already does, but worse.
- **Privacy concerns.** [Telemetry controversy](https://github.com/warpdotdev/Warp/issues/1346) — opt-out available but data collection is on by default. VC-funded, closed source.
- **Block-based UI** is genuinely nice but cosmetic, not a workflow change.

Other AI CLI tools (shell-gpt, aichat, aider) are all redundant if you use Claude Code as your primary coding agent.

**Verdict:** AI terminals are solving a problem that's already solved by running Claude Code in any terminal. The AI belongs in the agent, not the terminal chrome.

## Decision: What We Installed

**Shell:** fish 4.5.0 as interactive shell, zsh remains macOS login shell (Claude Code compatibility).

**Fish config:** Fisher + Tide v6. No framework (Oh My Fish is unmaintained).

**CLI tools:** zoxide, fd, atuin (with `--disable-up-arrow`), btop, lazygit. jq was pre-existing. ripgrep, bat, eza skipped for now (can add later).

**Terminal:** cmux v0.61.0 installed alongside iTerm2. cmux for Claude Code multi-session work (sidebar, notifications), iTerm2 as fallback and for future remote/tmux work.

**Config location:** `~/.config/fish/config.fish` — zoxide init + atuin init.

**Skipped:** Warp (AI features redundant with Claude Code, privacy concerns), Ghostty standalone (cmux already uses libghostty), dotfile manager (not needed yet), direnv (manual .env workflow is sufficient for now).

## Sources

### Shells & CLI tools
- [Lobsters: Bash vs Fish vs Zsh vs Nushell](https://lobste.rs/s/qoccbl/bash_vs_fish_vs_zsh_vs_nushell)
- [HN: Are alternative shells usable as daily drivers?](https://news.ycombinator.com/item?id=34722208)
- [Why I Switched Back to Zsh from Nushell](https://ryanxcharles.com/blog/2025-05-26-nushell-to-zsh)
- [macOS fix Zsh Home and End keys](https://www.sindastra.de/p/2004/macos-fix-zsh-home-and-end-keys)
- [Claude Code shell config issue #7490](https://github.com/anthropics/claude-code/issues/7490)
- [Fisher vs Oh My Fish](https://github.com/jorgebucaran/fisher/issues/481)
- [Better Shell History Search](https://tratt.net/laurie/blog/2025/better_shell_history_search.html)
- [Rise of Terminal Tools](https://tduyng.com/blog/rise-of-terminal/)
- [Building the Ultimate Developer Shell](https://corti.com/building-the-ultimate-developer-shell/)

### Terminal emulators
- [Choosing a Terminal on macOS (2025)](https://medium.com/@dynamicy/choosing-a-terminal-on-macos-2025-iterm2-vs-ghostty-vs-wezterm-vs-kitty-vs-alacritty-d6a5e42fd8b3)
- [HN: Reasons to switch from iTerm2](https://news.ycombinator.com/item?id=42518591)
- [cmux GitHub](https://github.com/manaflow-ai/cmux)
- [HN: cmux — Ghostty-based terminal with vertical tabs](https://news.ycombinator.com/item?id=47079718)
- [Parallel work with Claude Code in iTerm2](https://dev.to/kamilbuksakowski/parallel-work-with-claude-code-in-iterm2-a-workflow-inspired-by-boris-cherny-5940)
- [Claude Code terminal tab title issues: #18326](https://github.com/anthropics/claude-code/issues/18326), [#20441](https://github.com/anthropics/claude-code/issues/20441), [#15802](https://github.com/anthropics/claude-code/issues/15802)

### AI terminals
- [Warp: Claude Code integration](https://github.com/warpdotdev/claude-code-warp)
- [Warp telemetry concerns](https://github.com/warpdotdev/Warp/issues/1346)
- [AI coding tools shifting to the terminal (TechCrunch)](https://techcrunch.com/2025/07/15/ai-coding-tools-are-shifting-to-a-surprising-place-the-terminal/)
- [AI terminal coding tools that actually work (Augment)](https://www.augmentcode.com/guides/ai-terminal-coding-tools-that-actually-work-in-2025)
