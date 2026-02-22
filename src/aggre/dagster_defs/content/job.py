"""Content download and extraction job.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import dagster as dg
from dagster import OpExecutionContext

from aggre.config import load_config
from aggre.pipeline.content_downloader import download_content
from aggre.pipeline.content_extractor import extract_html_text
from aggre.utils.logging import setup_logging


@dg.op
def download_content_op(context: OpExecutionContext) -> int:
    """Download raw HTML for pending content URLs."""
    cfg = load_config()
    engine = context.resources.database.get_engine()
    log = setup_logging(cfg.settings.log_dir, "download")
    return download_content(engine, cfg, log)


@dg.op
def extract_content_op(context: OpExecutionContext, download_count: int) -> int:
    """Extract text from downloaded HTML."""
    cfg = load_config()
    engine = context.resources.database.get_engine()
    log = setup_logging(cfg.settings.log_dir, "extract")
    return extract_html_text(engine, cfg, log)


@dg.job
def content_job() -> None:
    extract_content_op(download_content_op())
