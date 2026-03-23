"""Enforce file length limits and ban non-empty __init__.py files."""

# Known violations — barrel re-exports kept for test convenience
EXEMPT = {
    "tests/factories/__init__.py",  # re-exports from per-domain factory modules
}

import pathlib  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

MAX_LINES = 500
result = subprocess.run(["git", "ls-files", "*.py", "**/*.py"], capture_output=True, text=True)
files = [pathlib.Path(f) for f in result.stdout.strip().splitlines() if f]

errors = []
for p in files:
    if str(p) in EXEMPT or not p.exists():
        continue
    # Ban __init__.py — no barrel files, no re-exports, no circular import traps
    if p.name == "__init__.py":
        content = p.read_text().strip()
        if content and content != "__all__ = []":
            errors.append(f"{p}: __init__.py must be empty or contain only __all__ = [] (no re-exports, no logic)")
        continue
    count = len(p.read_text().splitlines())
    if count > MAX_LINES:
        errors.append(f"{p}: {count} lines (max {MAX_LINES})")

if errors:
    for e in errors:
        print(e, file=sys.stderr)
    sys.exit(1)
