"""Transcription job -- download and transcribe YouTube videos.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import dagster as dg
from dagster import OpExecutionContext

from aggre.config import load_config
from aggre.transcriber import transcribe
from aggre.utils.logging import setup_logging


@dg.op
def transcribe_videos_op(context: OpExecutionContext) -> int:
    """Download and transcribe pending YouTube videos."""
    cfg = load_config()
    engine = context.resources.database.get_engine()
    log = setup_logging(cfg.settings.log_dir, "transcribe")
    return transcribe(engine, cfg, log)


@dg.job
def transcribe_job() -> None:
    transcribe_videos_op()
