"""Validate all JSON files in the project."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

# Use git ls-files to find tracked JSON files (fast, respects .gitignore)
result = subprocess.run(["git", "ls-files", "*.json", "**/*.json"], capture_output=True, text=True)
files = [pathlib.Path(f) for f in result.stdout.strip().splitlines() if f]

errors = []
for p in files:
    try:
        json.loads(p.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"{p}: {e}")

if errors:
    for e in errors:
        print(e, file=sys.stderr)
    sys.exit(1)
