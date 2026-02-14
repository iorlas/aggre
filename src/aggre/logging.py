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

    # Root logger config
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    # Clear existing handlers to avoid duplicates on repeated calls
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stdout_handler)

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

    return structlog.get_logger(log_name)
