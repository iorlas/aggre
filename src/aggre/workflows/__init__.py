"""Hatchet workflow orchestration for Aggre."""

from __future__ import annotations

import importlib
import logging
import pkgutil

from hatchet_sdk import Hatchet

_hatchet: Hatchet | None = None


def get_hatchet() -> Hatchet:
    """Return the shared Hatchet client, creating it on first call."""
    global _hatchet  # noqa: PLW0603
    if _hatchet is None:
        _hatchet = Hatchet()
    return _hatchet


def start_worker() -> None:  # pragma: no cover — entry point
    """Start the Hatchet worker with all registered workflows."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    import aggre.workflows as pkg

    h = get_hatchet()
    workflows: list = []

    # Auto-discover workflow modules — each exports register(h).
    # Justified over explicit imports: failure is loud (Hatchet startup crash),
    # no static typing concern for workflow objects.
    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        if name.startswith("_"):
            continue
        mod = importlib.import_module(f"aggre.workflows.{name}")
        if hasattr(mod, "register"):
            result = mod.register(h)
            if isinstance(result, list):
                workflows.extend(result)
            else:
                workflows.append(result)

    worker = h.worker("aggre-worker", slots=40, workflows=workflows)
    worker.start()
