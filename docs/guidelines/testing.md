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

## Test Layers

| Layer | Marker | DB? | HTTP? | Purpose |
|-------|--------|-----|-------|---------|
| Unit | `@pytest.mark.unit` | No | No | Pure functions, data transforms |
| Integration | `@pytest.mark.integration` | Real PG | Mocked (respx) | Single component: one collector or one workflow |
| Invariant | `@pytest.mark.integration` | Real PG | Mocked | State machine queries, architectural constraints |
| Acceptance | `@pytest.mark.acceptance` | Real PG | Mocked | Multi-component flows crossing workflow boundaries |
| Contract | `@pytest.mark.contract` | No | VCR cassettes | External API response shape |

## HTTP Mocking Stack

| Tool | Package | Used by | Purpose |
|------|---------|---------|---------|
| **respx** | `respx>=0.22` | Integration tests (130+ tests) | Transport-layer httpx mock. Responses defined via factory functions. Tests code logic against controlled inputs. |
| **VCR.py** | `pytest-recording>=0.13.4` (wraps `vcrpy`) | Contract tests (10 tests) | Records real HTTP to YAML cassettes, replays in CI. `@pytest.mark.vcr()` on test methods. Tests that external APIs haven't changed shape. |
| **moto** | `moto[s3]>=5.0` | S3 tests (2 files) | AWS service mock for bronze storage tests. |

Why both respx and VCR:
- respx tests *your code* against *your factories* — catches logic bugs
- VCR tests *your factories' assumptions* against *real API responses* — catches API drift

Contract test maintenance:
- Record: `pytest tests/collectors/test_contract_*.py --record-mode=once`
- Cassettes: `tests/collectors/cassettes/{module_name}/`
- Re-record when adding new contract tests or when API changes break existing ones

### When NOT to add acceptance tests

A test belongs at acceptance level ONLY if it:
1. Exercises multiple components in sequence (e.g., collect -> download -> extract)
2. Verifies cross-component properties (e.g., two collectors sharing one SilverContent)
3. Cannot be expressed as a single-component integration test

If the test calls one function and verifies its output, it's an integration test.

## Directory Convention

Test directories mirror code directories:

| Code | Tests |
|------|-------|
| `src/aggre/collectors/{source}/` | `tests/collectors/test_{source}.py` |
| `src/aggre/workflows/{name}.py` | `tests/workflows/test_{name}.py` |
| `src/aggre/tracking/` | `tests/tracking/` |
| `src/aggre/utils/` | `tests/utils/` |
| `src/aggre/{module}.py` (root) | `tests/test_{module}.py` |
| Cross-cutting acceptance | `tests/test_acceptance_*.py` |

Shared infrastructure stays at `tests/` root: `conftest.py`, `factories.py`, `helpers.py`.
