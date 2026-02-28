"""Structured logging setup: JSON to file, human-readable to stdout."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog


def setup_logging(log_dir: str, log_name: str = "aggre") -> structlog.stdlib.BoundLogger:
    """Configure structlog with dual output: human-readable stdout + JSON file."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # File handler — JSON lines, 10 MB per file, keep 5 backups
    file_handler = RotatingFileHandler(
        log_path / f"{log_name}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    # Stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)

    # Parent logger — Dagster attaches its DagsterLogHandler here via
    # managed_python_loggers: [aggre].  We only ensure the level is set so
    # messages from child loggers propagate through.
    parent_logger = logging.getLogger("aggre")
    parent_logger.setLevel(logging.DEBUG)

    # Child logger for OUR handlers.  Python's callHandlers() processes
    # handlers at the originating logger first, then walks up the hierarchy.
    # By placing our ProcessorFormatter handlers on "aggre.out", they see
    # the original dict record.msg *before* Dagster's handler on "aggre"
    # mutates it to a string.
    out_logger = logging.getLogger("aggre.out")
    out_logger.setLevel(logging.DEBUG)

    # Remove only OUR handlers from previous calls; preserve third-party
    for h in out_logger.handlers[:]:
        if getattr(h, "_aggre_managed", False):
            out_logger.removeHandler(h)

    file_handler._aggre_managed = True  # type: ignore[attr-defined]
    stdout_handler._aggre_managed = True  # type: ignore[attr-defined]
    out_logger.addHandler(file_handler)
    out_logger.addHandler(stdout_handler)

    # structlog config
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Set formatters per handler
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
        )
    )
    stdout_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(),
        )
    )

    return structlog.get_logger(f"aggre.out.{log_name}")
