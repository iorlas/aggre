"""Enforce file length limits and ban non-empty __init__.py files."""

# Known violations — TODO: split into focused modules or clean up
EXEMPT = {
    # __init__.py violations — barrel files and files with logic
    "src/aggre/__init__.py",  # TODO: move __version__ to a dedicated version.py
    "src/aggre/collectors/__init__.py",  # TODO: move COLLECTORS registry to collectors/registry.py
    "src/aggre/collectors/github_trending/__init__.py",  # TODO: remove docstring
    "src/aggre/collectors/hackernews/__init__.py",  # TODO: empty or use __all__ = []
    "src/aggre/collectors/huggingface/__init__.py",  # TODO: empty or use __all__ = []
    "src/aggre/collectors/lobsters/__init__.py",  # TODO: empty or use __all__ = []
    "src/aggre/collectors/reddit/__init__.py",  # TODO: empty or use __all__ = []
    "src/aggre/collectors/rss/__init__.py",  # TODO: empty or use __all__ = []
    "src/aggre/collectors/telegram/__init__.py",  # TODO: empty or use __all__ = []
    "src/aggre/collectors/youtube/__init__.py",  # TODO: empty or use __all__ = []
    "src/aggre/utils/__init__.py",  # TODO: empty or use __all__ = []
    "src/aggre/workflows/__init__.py",  # TODO: move worker logic to workflows/worker.py
    # Long files — TODO: split into focused modules
    "tests/factories.py",  # TODO: split into per-domain factory modules
}

import pathlib  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

MAX_LINES = 500
result = subprocess.run(["git", "ls-files", "*.py", "**/*.py"], capture_output=True, text=True)
files = [pathlib.Path(f) for f in result.stdout.strip().splitlines() if f]

errors = []
for p in files:
    if str(p) in EXEMPT:
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
