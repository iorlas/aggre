"""Validate all JSON files in the project."""

from __future__ import annotations

import json
import pathlib
import sys

EXCLUDES = {"node_modules", ".venv", ".dmux", "data", ".git"}

errors = []
for p in pathlib.Path(".").rglob("*.json"):
    if any(part in EXCLUDES for part in p.parts):
        continue
    try:
        json.loads(p.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"{p}: {e}")

if errors:
    for e in errors:
        print(e, file=sys.stderr)
    sys.exit(1)
