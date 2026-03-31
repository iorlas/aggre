# Documentation System

How we author, maintain, and evaluate documentation in this project.

## Ontology

Four document types:

| Type | Folder | Purpose | When to create | Naming |
|------|--------|---------|---------------|--------|
| **Guideline** | `docs/guidelines/` | Non-inferable standards -- how we do X and why | Pattern survives 2+ implementations | `{topic}.md` |
| **Decision** | `docs/decisions/` | Architectural choices + rejections -- why X not Y | Non-obvious architectural choice is made | `NNN-{topic}.md` |
| **Reference** | `docs/reference/` | Project-specific lookup data the agent can't infer from code | Agent repeatedly needs info not in code | `{topic}.md` |
| **Spec** | `docs/superpowers/` | Working design documents | Born during brainstorming (managed by superpowers skill) | `YYYY-MM-DD-{topic}-design.md` |

**Archive:** `docs/archive/` -- historical documents. Never referenced in CLAUDE.md.

## When to write a doc

Three reasons:

1. **Non-inferable knowledge** -- code can't tell the agent this (domain semantics, constraints, operational gotchas).
2. **Rejection records** -- prevent agents from re-proposing alternatives already evaluated and rejected.
3. **Agent shortcuts** -- observed the agent struggling repeatedly; short-circuit the struggle.

**Quality gate:** "Would an agent make a concrete mistake without this doc?" If no, don't write it.

## Decision record format

~100-200 tokens each:

    # NNN: Short title

    **Status:** Active | Superseded by NNN
    **Date:** YYYY-MM-DD

    ## Why
    1-3 sentences: what problem forced this decision.

    ## Decision
    1-3 sentences: what we chose.

    ## Not chosen
    - Option X -- one-line rejection reason

    ## Consequence
    Key tradeoff or constraint this creates.

The "Not chosen" section is the highest-value field -- it prevents agents from re-proposing rejected alternatives.

## File naming

- Guidelines: lowercase kebab-case, no dates -- `python.md`
- Decisions: numbered prefix, kebab-case -- `001-medallion-architecture.md`
- References: lowercase kebab-case, no dates -- `semantic-model.md`
- Specs: date-prefixed per superpowers convention -- `2026-03-29-topic-design.md`

## Lifecycle

| Event | Action |
|-------|--------|
| Pattern survives 2+ implementations | Extract into guideline |
| Non-obvious architectural choice made | Write decision record |
| Agent struggles repeatedly | Write shortcut doc (guideline or reference) |
| Practice changes | Update existing guideline |
| Decision superseded | Mark old "Superseded by NNN", write new |
| Doc no longer relevant | Move to `docs/archive/` |

## Periodic review

**Every 2 weeks:** Scan Claude Code transcripts to assess documentation effectiveness.

1. Grep transcripts for Read/Edit operations on `docs/` files.
2. Count genuine reads per doc (exclude bulk scans).
3. Identify: heavily used (keep), never read (archive candidate), read but stale (update).
4. Identify agent struggle patterns -- repeated wrong approaches, re-proposed rejected alternatives.
5. Create shortcut docs for observed struggles.

## References & Evidence

- **ETH Zurich (2026):** AI-generated context files degrade agent performance 3%, increase cost 20%+. Limit context to non-inferable details. (InfoQ: "New Research Reassesses the Value of AGENTS.md Files for AI Coding")
- **Anthropic:** "Find the smallest set of high-signal tokens that maximize likelihood of desired outcome." Just-in-time retrieval over pre-loading. (anthropic.com/engineering: "Effective Context Engineering for AI Agents")
- **Martin Fowler:** Layered context -- always-loaded (CLAUDE.md), path-scoped, lazy-loaded (skills), isolated (subagents). (martinfowler.com: "Context Engineering for Coding Agents")
- **GSD:** Spec-driven development, atomic context windows, phased docs with archival. (github.com/gsd-build/get-shit-done)
- **ADR "Not Chosen":** Projects with decision records show fewer agent-generated bugs. "Not chosen" section prevents re-proposing rejected alternatives. (7tonshark.com, agents.md)
- **Transcript analysis (2026-03-30):** 146 sessions, 62MB. Only 5-6 of 48 docs genuinely consulted. 9 research files: zero reads. 4 CLAUDE.md-listed guidelines: never opened.
