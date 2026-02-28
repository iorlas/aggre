# MCP Code Understanding Tools — Comparison (2026-02-24)

## Context

Claude Code agents working on this codebase sometimes produce code that violates architectural guidelines. We evaluated 4 MCP tools to see if any help the agent understand structural relationships better than built-in grep/glob/read.

**Codebase size:** ~50 Python files, ~4K LOC, in `src/aggre/`.

## Summary

| Criteria | RepoMapper | code-index-mcp | CodeGraphContext | Repomix |
|---|---|---|---|---|
| **Install** | `git clone` + `uv sync` (73 deps) | `uv tool install` (40 deps) | `uv tool install` (63 deps) | `npx repomix` (0 deps) |
| **Setup time** | ~3 min (needs Python 3.13) | ~3 sec | ~3 sec | ~3 sec |
| **Disk footprint** | ~200 MB | ~100 KB index | 450 MB + 328 KB DB | 0 (no persistent index) |
| **Index time** | N/A (on-demand) | 5.6 sec | 1.86 sec | N/A (packs on demand) |
| **Shows relationships** | No (all ranks = 1.0) | Yes (`called_by` data) | Yes (call chains, partial inheritance) | No (file concatenation) |
| **Token cost** | ~2,900 tokens (full map) | ~200-500 per query | ~200-500 per query | 22,690 uncompressed / 11,155 compressed |
| **Better than grep** | No | Marginally | Marginally | No |
| **MCP server** | Yes (FastMCP, 2 tools) | Yes (stdio, 14 tools) | Yes (stdio, 19 tools) | Yes (stdio, 6 tools) |
| **Verdict** | DISCARD | DISCARD | DISCARD | DISCARD |

## Detailed Findings

### RepoMapper (Go→actually Python, 101 stars)

**What it claims:** Token-budget-optimized text map with PageRank-ranked symbols.

**What we found:**
- PageRank is **broken** — every file gets identical rank (1.0000)
- Output is a flat list of class/function definitions per file — no cross-file relationships shown
- CLI has multiple bugs: prints raw Python tuples, `--verbose` crashes, path resolution broken
- Output is functionally identical to `grep -n "^class \|^def " **/*.py`
- Heavy: 73 dependencies for grep-equivalent output

**Verdict: DISCARD.** Core feature (intelligent ranking) does not work.

### code-index-mcp (Python/tree-sitter, 787 stars)

**What it claims:** Searchable tree-sitter index with symbol resolution.

**What we found:**
- **Best feature:** `called_by` data — for any function, shows exactly which functions in which files call it. Example: `update_content` is called by 8 functions across 3 job files. This is genuinely structured data that grep doesn't provide directly.
- `get_file_summary` gives structured overview (functions, classes, imports, docstrings) in one call
- `search_code_advanced` is literally just a grep wrapper (detected `grep` as backend)
- Index is ephemeral (`/tmp/`), requires rebuild each session (~5.6s)
- Type annotations stripped from signatures
- Python parsing uses `ast` module, not tree-sitter despite the marketing

**Verdict: DISCARD.** The `called_by` feature is useful but not transformative for a 50-file codebase. Claude Code can achieve the same with 1-2 grep calls. Would reconsider for a 500+ file project.

### CodeGraphContext (Python/FalkorDB, 783 stars)

**What it claims:** Graph database of code relationships — callers, callees, class hierarchies.

**What we found:**
- **Best feature:** Call chain analysis — correctly mapped all 6 collector paths to `ensure_content`, distinguishing direct calls (RSS, YouTube) from indirect ones (via `_store_discussion`)
- Built a real graph: 778 nodes, 1,084 edges, including 206 imports, 139 calls, 4 inherits
- **Critical gap:** Missed 6 of 10 expected inheritance relationships (all `BaseCollector` subclasses!)
- Decorator detection broken (`@op`, `@job`, `@sensor` — none found)
- Full-text search returns zero results
- Cross-layer analysis requires hand-written Cypher queries against undocumented schema
- Complexity analysis works well (identified most complex functions)

**Verdict: DISCARD.** Incomplete inheritance tracking is disqualifying for a tool whose value proposition is "understand code structure." 450MB footprint for marginal benefit.

### Repomix (Node.js packer, 15K+ stars)

**What it claims:** Pack entire codebase into a single AI-friendly file with token compression.

**What we found:**
- **Easiest setup** — `npx repomix` with zero config, no persistent state
- Uncompressed output: 22,690 tokens — full file contents in XML format with directory tree
- Compressed output: 11,155 tokens (~49% reduction) — but **strips all 95 internal import statements**, destroying the dependency information we need
- MCP server tools (`read_file`, `read_directory`, `grep`) duplicate Claude Code's built-in Glob/Grep/Read
- Nice CLI feature: `--token-count-tree` shows token cost per directory
- Solves a **different problem** — designed for stateless LLMs (paste into ChatGPT) where there's no file access. Claude Code already has targeted Read/Grep/Glob tools that are more token-efficient

**Verdict: DISCARD.** Packer, not analyzer. Compressed mode strips imports (fatal for our use case). Uncompressed mode is just file concatenation — Claude Code's on-demand file access is strictly better.

## Why All Four Failed

1. **Codebase is too small.** At 50 files / 4K LOC, Claude Code's built-in grep/glob/read are fast and sufficient. The overhead of maintaining an MCP server, index, and additional tool selection complexity isn't justified.

2. **Accuracy gaps destroy trust.** RepoMapper's broken PageRank, CodeGraphContext's missing inheritance links, and code-index-mcp's grep-wrapper search all mean the agent can't rely on the output. Partial correctness is worse than no tool at all — it gives false confidence.

3. **Wrong abstraction level.** Repomix dumps everything (too much), code-map tools surface symbols (too granular). None produce the architectural summary we actually need: "these are the layers, this is what goes where, these are the conventions."

4. **The actual problem isn't navigation.** The architectural violations (domain logic in shared files, layer violations) come from the agent not understanding *conventions*, not from inability to find code. A code map shows "what exists" but not "what should exist where." The solution is better documentation in CLAUDE.md and `.planning/` docs, not a code graph.

## Recommendation

**Don't add any MCP code tools now.** Instead:

1. Keep investing in `.planning/codebase/` documentation (ARCHITECTURE.md, CONVENTIONS.md, STRUCTURE.md) — these directly encode architectural intent that no tool can infer
2. Revisit when the codebase grows past ~200 files, at which point code-index-mcp's `called_by` feature and CodeGraphContext's call chain analysis become more valuable
3. Monitor the MCP ecosystem — these tools are all <1 year old and improving rapidly

## No Changes to Settings

No tools are being added to `.claude/settings.local.json`. The current setup (Context7 for library docs) remains unchanged.
