"""Click CLI with commands: fetch, transcribe, backfill, status."""

from __future__ import annotations

import time
from pathlib import Path

import click
import sqlalchemy as sa

from aggre.config import load_config
from aggre.db import content_items, get_engine, sources
from aggre.logging import setup_logging


@click.group()
@click.option("--config", "config_path", default="config.yaml", help="Path to config YAML file.")
@click.pass_context
def cli(ctx: click.Context, config_path: str) -> None:
    """Aggre — Content aggregation system."""
    ctx.ensure_object(dict)
    cfg = load_config(config_path)
    ctx.obj["config"] = cfg

    Path(cfg.settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = get_engine(cfg.settings.db_path)
    ctx.obj["engine"] = engine


@cli.command()
@click.option("--source", "source_type", type=click.Choice(["rss", "reddit", "youtube"]), help="Fetch only this source type.")
@click.option("--loop", is_flag=True, help="Run continuously.")
@click.option("--interval", default=3600, type=int, help="Seconds between loop iterations.")
@click.pass_context
def fetch(ctx: click.Context, source_type: str | None, loop: bool, interval: int) -> None:
    """Poll sources and store new content."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "fetch")

    from aggre.collectors.reddit import RedditCollector
    from aggre.collectors.rss import RssCollector
    from aggre.collectors.youtube import YoutubeCollector

    collectors: dict[str, RssCollector | RedditCollector | YoutubeCollector] = {
        "rss": RssCollector(),
        "reddit": RedditCollector(),
        "youtube": YoutubeCollector(),
    }

    if source_type:
        collectors = {source_type: collectors[source_type]}

    while True:
        total = 0
        for name, collector in collectors.items():
            log.info("fetching", source=name)
            try:
                count = collector.collect(engine, cfg, log)
                total += count
                log.info("fetch_complete", source=name, new_items=count)
            except Exception:
                log.exception("fetch_error", source=name)
        log.info("fetch_cycle_complete", total_new_items=total)

        if not loop:
            break
        log.info("sleeping", seconds=interval)
        time.sleep(interval)


@cli.command()
@click.option("--batch", default=0, type=int, help="Max videos to process (0 = all pending).")
@click.option("--loop", is_flag=True, help="Run continuously.")
@click.option("--interval", default=900, type=int, help="Seconds between loop iterations.")
@click.pass_context
def transcribe(ctx: click.Context, batch: int, loop: bool, interval: int) -> None:
    """Transcribe pending YouTube videos."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "transcribe")

    from aggre.transcriber import process_pending

    while True:
        try:
            processed = process_pending(engine, cfg, log, batch_limit=batch)
            log.info("transcribe_cycle_complete", processed=processed)
        except Exception:
            log.exception("transcribe_error")

        if not loop:
            break
        log.info("sleeping", seconds=interval)
        time.sleep(interval)


@cli.command()
@click.argument("source_type", type=click.Choice(["youtube"]))
@click.pass_context
def backfill(ctx: click.Context, source_type: str) -> None:
    """Backfill full history for a source type."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "backfill")

    if source_type == "youtube":
        from aggre.collectors.youtube import YoutubeCollector

        collector = YoutubeCollector()
        count = collector.collect(engine, cfg, log, backfill=True)
        log.info("backfill_complete", source=source_type, items=count)


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show fetch times, queue sizes, and recent errors."""
    engine = ctx.obj["engine"]

    with engine.connect() as conn:
        # Sources summary
        rows = conn.execute(sa.select(sources.c.type, sources.c.name, sources.c.last_fetched_at)).fetchall()
        click.echo("\n=== Sources ===")
        for row in rows:
            click.echo(f"  [{row.type}] {row.name} — last fetched: {row.last_fetched_at or 'never'}")

        if not rows:
            click.echo("  No sources registered yet. Run 'aggre fetch' first.")

        # Content counts by type
        result = conn.execute(sa.select(content_items.c.source_type, sa.func.count()).group_by(content_items.c.source_type)).fetchall()
        click.echo("\n=== Content Items ===")
        for row in result:
            click.echo(f"  {row[0]}: {row[1]} items")

        if not result:
            click.echo("  No content items yet.")

        # Transcription queue
        pending = conn.execute(sa.select(sa.func.count()).where(content_items.c.transcription_status == "pending")).scalar()
        failed = conn.execute(sa.select(sa.func.count()).where(content_items.c.transcription_status == "failed")).scalar()
        completed = conn.execute(sa.select(sa.func.count()).where(content_items.c.transcription_status == "completed")).scalar()
        click.echo("\n=== Transcription Queue ===")
        click.echo(f"  Pending: {pending}  |  Completed: {completed}  |  Failed: {failed}")

        # Recent errors
        errors = conn.execute(
            sa.select(content_items.c.external_id, content_items.c.title, content_items.c.transcription_error)
            .where(content_items.c.transcription_status == "failed")
            .order_by(content_items.c.fetched_at.desc())
            .limit(5)
        ).fetchall()
        if errors:
            click.echo("\n=== Recent Transcription Errors ===")
            for row in errors:
                click.echo(f"  {row.external_id} ({row.title}): {row.transcription_error}")

    click.echo()
