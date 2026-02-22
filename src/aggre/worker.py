"""Reusable worker helpers for CLI commands."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import click
import structlog

F = TypeVar("F", bound=Callable)


def worker_options(
    default_interval: int = 10,
    default_batch: int = 50,
    include_batch: bool = True,
) -> Callable[[F], F]:
    """Click decorator that adds --loop, --interval, and optionally --batch."""

    def decorator(f: F) -> F:
        f = click.option("--loop", is_flag=True, help="Run continuously.")(f)
        f = click.option("--interval", default=default_interval, type=int, help="Seconds between loop iterations.")(f)
        if include_batch:
            f = click.option("--batch", default=default_batch, type=int, help="Max items per batch.")(f)
        return f

    return decorator


def run_loop(
    fn: Callable[[], object],
    *,
    loop: bool,
    interval: int,
    log: structlog.stdlib.BoundLogger,
    name: str,
) -> None:
    """Run fn in a loop with sleep/retry. Logs cycle completion and errors."""
    while True:
        try:
            result = fn()
            log.info(f"{name}.cycle_complete", result=result)
        except Exception:
            log.exception(f"{name}.error")

        if not loop:
            break
        log.info("sleeping", seconds=interval)
        time.sleep(interval)
