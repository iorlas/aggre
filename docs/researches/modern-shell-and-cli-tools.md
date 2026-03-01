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

## Decision: What We Installed

**Shell:** fish 4.5.0 as interactive shell, zsh remains login shell.

**Fish config:** Fisher + Tide v6. No framework (Oh My Fish is unmaintained).

**Tools:** zoxide, fd, atuin (with `--disable-up-arrow`), btop, lazygit. jq was pre-existing. ripgrep, bat, eza skipped for now (can add later).

**Config location:** `~/.config/fish/config.fish` — zoxide init + atuin init.

## Sources

- [Lobsters: Bash vs Fish vs Zsh vs Nushell](https://lobste.rs/s/qoccbl/bash_vs_fish_vs_zsh_vs_nushell)
- [HN: Are alternative shells usable as daily drivers?](https://news.ycombinator.com/item?id=34722208)
- [Why I Switched Back to Zsh from Nushell](https://ryanxcharles.com/blog/2025-05-26-nushell-to-zsh)
- [macOS fix Zsh Home and End keys](https://www.sindastra.de/p/2004/macos-fix-zsh-home-and-end-keys)
- [Claude Code shell config issue #7490](https://github.com/anthropics/claude-code/issues/7490)
- [Fisher vs Oh My Fish](https://github.com/jorgebucaran/fisher/issues/481)
- [Better Shell History Search](https://tratt.net/laurie/blog/2025/better_shell_history_search.html)
- [Rise of Terminal Tools](https://tduyng.com/blog/rise-of-terminal/)
- [Building the Ultimate Developer Shell](https://corti.com/building-the-ultimate-developer-shell/)
