# Testing Guidelines

Opinionated guidelines for test coverage, exclusion pragmas, and verification workflow. Consumed by coding agents. Prescribes what to do and why.

Scope: coverage thresholds, pragma conventions, test workflow. Does NOT cover test fixtures or mocking patterns (`.planning/codebase/TESTING.md`).

## Coverage Thresholds

Two independent checks enforce coverage:

| Check | Target | What it measures |
|-------|--------|-----------------|
| `--cov-fail-under=95` | Global | Overall project coverage. Prevents slow erosion. |
| `make coverage-diff` `--fail-under=95` | Changed lines | Coverage of lines changed vs `origin/main`. Catches new untested code even when global stays high. |

Both must pass. Global catches broad neglect; diff-cover catches per-PR regressions.

## Coverage Exclusion Pragmas

coverage.py provides two inline pragmas. Use them sparingly.

### `pragma: no cover`

Excludes a line or block from coverage measurement entirely.

**When to use:**
- S3/network error branches that require mocking specific low-level error codes (e.g., `botocore.exceptions.ClientError` with specific error codes)
- Unreachable defensive code required by type narrowing (e.g., `assert_never()` branches)
- Framework entry points that only run in production (e.g., `if __name__ == "__main__"`)

### `pragma: no branch`

Marks a branch as not requiring both paths to be covered. The line itself is still covered.

**When to use:**
- Double-checked locking or thread-race branches that are non-deterministic in tests
- Guard clauses where the "else" path is tested by other test cases but coverage can't link them

### When NOT to use pragmas

- Never to skip testing business logic
- Never because "it's just a simple wrapper"
- Never to meet a deadline or pass CI faster
- Never on code paths that could actually fail in production

If you're tempted to add a pragma, first try writing a test. If the test is genuinely impractical (requires deep mocking of third-party internals), add the pragma with a comment explaining why.

## pyproject.toml Coverage Config

Global exclusions in `[tool.coverage.report].exclude_lines`:

```toml
exclude_lines = [
    "pragma: no cover",
    "if __name__ == .__main__.",
    "if TYPE_CHECKING:",
    "class .*\\bProtocol\\):",
    "\\.\\.\\.",       # Protocol method bodies (ellipsis)
]
```

These patterns are excluded project-wide because they are structurally untestable (type-checking imports, protocol definitions, ellipsis bodies).

Whole-file exclusions via `[tool.coverage.run].omit`:

```toml
omit = ["src/aggre/cli.py"]
```

Use `omit` for entire modules that are interactive entry points or otherwise structurally untestable as a whole.

## Test Workflow

Standard verification sequence after making changes:

```
make test-e2e          # Run all tests with ephemeral postgres
make coverage-diff     # Check coverage of changed lines (95% threshold)
make lint              # ruff check + format + ty check
```

Run `make test-e2e` first — it produces `coverage.xml` that `coverage-diff` reads. If coverage-diff fails, add tests for the uncovered lines before adding pragmas.
