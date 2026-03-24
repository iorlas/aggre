#!/usr/bin/env python3
# ruff: noqa: C901, PLR0912, PLR0915, FURB171, SIM102, PIE810, F841, E501
"""Opinionated linter for docker-compose.prod.yml.

Enforces deployment-platform conventions. Run in pre-commit or CI.
Exit 0 = pass, Exit 1 = errors found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    # Fallback: if PyYAML not available, try ruamel or skip
    print("warning: PyYAML not installed, skipping compose lint", file=sys.stderr)
    sys.exit(0)


def lint_compose(path: Path) -> list[str]:
    """Return list of error messages. Empty = all good."""
    errors: list[str] = []
    content = path.read_text()
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]

    if not isinstance(data, dict):
        return [f"Expected a mapping at top level, got {type(data).__name__}"]

    services = data.get("services", {})
    if not services:
        return ["No services defined"]

    # Check file name
    if path.name not in ("docker-compose.prod.yml",):
        errors.append(f"File must be named 'docker-compose.prod.yml', got '{path.name}'")

    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue

        prefix = f"services.{svc_name}"
        image = svc.get("image", "")

        # ── Must use image, never build ──
        if "build" in svc:
            errors.append(f"{prefix}: has 'build:' directive — pre-build images in CI, never on the server")

        # ── Image tag rules ──
        if image:
            # Own images (ghcr.io/iorlas/*) must be SHA-pinned
            if "ghcr.io/iorlas/" in image:
                tag = image.split(":")[-1] if ":" in image else ""
                if not tag or tag in ("latest", "main", "master", "dev"):
                    errors.append(f"{prefix}: own image '{image}' must use SHA-pinned tag (main-<sha7>), not '{tag or 'latest'}'")
                elif not re.match(r"^\$\{", tag) and not re.match(r"^main-[a-f0-9]{7}", tag):
                    # Allow ${IMAGE_TAG} variable references
                    if "${" not in tag:
                        errors.append(f"{prefix}: own image tag '{tag}' doesn't look SHA-pinned (expected main-<sha7> or ${{IMAGE_TAG}})")

            # Any image with :latest or :main should have pull_policy
            if image.endswith(":latest") or image.endswith(":main"):
                if svc.get("pull_policy") != "always":
                    errors.append(f"{prefix}: mutable tag '{image}' requires 'pull_policy: always'")

        # ── Healthcheck required ──
        if "healthcheck" not in svc:
            # Skip one-shot services (migrations, init containers)
            depends = svc.get("depends_on", {})
            is_depended_as_completed = False
            for other_svc in services.values():
                if not isinstance(other_svc, dict):
                    continue
                other_deps = other_svc.get("depends_on", {})
                if isinstance(other_deps, dict) and svc_name in other_deps:
                    cond = other_deps[svc_name].get("condition", "")
                    if cond == "service_completed_successfully":
                        is_depended_as_completed = True

            if not is_depended_as_completed and "restart" in svc:
                errors.append(f"{prefix}: missing 'healthcheck:' — every long-running service must have one")

        # ── Restart policy ──
        if "restart" not in svc:
            # One-shot services (no restart) are ok if they're depended on with service_completed_successfully
            # Otherwise they should have a restart policy
            is_one_shot = False
            for other_svc in services.values():
                if not isinstance(other_svc, dict):
                    continue
                other_deps = other_svc.get("depends_on", {})
                if isinstance(other_deps, dict) and svc_name in other_deps:
                    cond = other_deps[svc_name].get("condition", "")
                    if cond == "service_completed_successfully":
                        is_one_shot = True
            if not is_one_shot and image:
                errors.append(f"{prefix}: missing 'restart:' policy — use 'unless-stopped' for long-running services")

        # ── Network rules ──
        labels = svc.get("labels", {})
        # Normalize labels — can be list or dict
        if isinstance(labels, list):
            label_str = " ".join(labels)
        elif isinstance(labels, dict):
            label_str = " ".join(f"{k}={v}" for k, v in labels.items())
        else:
            label_str = ""

        if "traefik" in label_str.lower():
            networks = svc.get("networks", [])
            if isinstance(networks, dict):
                network_names = list(networks.keys())
            else:
                network_names = networks
            if "dokploy-network" not in network_names:
                errors.append(f"{prefix}: has Traefik labels but is not on 'dokploy-network' — Traefik can't route to it")

        # ── No 0.0.0.0 port binding ──
        ports = svc.get("ports", [])
        for port in ports:
            port_str = str(port)
            if port_str.startswith("0.0.0.0:"):
                errors.append(
                    f"{prefix}: binds to 0.0.0.0 ({port_str}) — Docker bypasses UFW. Use ${{TAILSCALE_IP}} for private or remove for internal-only"
                )

    # ── Env interpolation: bare $ in compose (not $$ or ${) ──
    # Scan raw content for patterns like $2a$12$ (bcrypt) that should be $$
    for i, line in enumerate(content.splitlines(), 1):
        line_stripped = line.strip()
        if line_stripped.startswith("#"):
            continue
        # Find $ not followed by { or $ or end-of-line
        bare_dollars = re.findall(r"\$(?!\{)(?!\$)(?!$)", line_stripped)
        if bare_dollars and "$$" not in line_stripped:
            # Heuristic: if line has = and $, it's likely an env value with unescaped $
            if "=" in line_stripped or ":" in line_stripped:
                # Skip lines that are just variable references like ${VAR}
                if "${" not in line_stripped:
                    errors.append(f"line {i}: bare '$' may be interpolated by Docker Compose — escape as '$$' if literal")

    return errors


def main() -> None:
    path = Path("docker-compose.prod.yml")
    if not path.exists():
        # Not a deployable project — skip silently
        sys.exit(0)

    errors = lint_compose(path)
    if errors:
        print(f"docker-compose.prod.yml: {len(errors)} issue(s):\n", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
