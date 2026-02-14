"""Click CLI with commands: fetch, transcribe, backfill, status."""

from __future__ import annotations

import time
from pathlib import Path

import click
import sqlalchemy as sa

from aggre.config import load_config
from aggre.db import SilverPost, Source, get_engine
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
@click.option(
    "--source", "source_type",
    type=click.Choice(["rss", "reddit", "youtube", "hackernews", "lobsters", "huggingface"]),
    help="Fetch only this source type.",
)
@click.option("--comment-batch", default=10, type=int, help="Max comments to fetch per source per cycle (0 = skip).")
@click.option("--enrich-batch", default=50, type=int, help="Max posts to enrich per cycle (0 = skip).")
@click.option("--loop", is_flag=True, help="Run continuously.")
@click.option("--interval", default=3600, type=int, help="Seconds between loop iterations.")
@click.pass_context
def fetch(ctx: click.Context, source_type: str | None, comment_batch: int, enrich_batch: int, loop: bool, interval: int) -> None:
    """Poll sources and store new content."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "fetch")

    from aggre.collectors.hackernews import HackernewsCollector
    from aggre.collectors.huggingface import HuggingfaceCollector
    from aggre.collectors.lobsters import LobstersCollector
    from aggre.collectors.reddit import RedditCollector
    from aggre.collectors.rss import RssCollector
    from aggre.collectors.youtube import YoutubeCollector

    collectors = {
        "rss": RssCollector(),
        "reddit": RedditCollector(),
        "youtube": YoutubeCollector(),
        "hackernews": HackernewsCollector(),
        "lobsters": LobstersCollector(),
        "huggingface": HuggingfaceCollector(),
    }

    active_collectors = collectors
    if source_type:
        active_collectors = {source_type: collectors[source_type]}

    while True:
        # Step 1: Collect posts
        total = 0
        for name, collector in active_collectors.items():
            log.info("fetching", source=name)
            try:
                count = collector.collect(engine, cfg, log)
                total += count
                log.info("fetch_complete", source=name, new_items=count)
            except Exception:
                log.exception("fetch_error", source=name)

        # Step 2: Collect comments for sources that support it
        for src_name in ("reddit", "hackernews", "lobsters"):
            if source_type in (None, src_name) and comment_batch > 0:
                coll = active_collectors.get(src_name) or collectors.get(src_name)
                if coll and hasattr(coll, "collect_comments"):
                    try:
                        comments_fetched = coll.collect_comments(engine, cfg, log, batch_limit=comment_batch)
                        log.info("comments_complete", source=src_name, comments_fetched=comments_fetched)
                    except Exception:
                        log.exception("comments_error", source=src_name)

        # Step 3: Enrich — only when no --source filter (cross-source step)
        if source_type is None and enrich_batch > 0:
            try:
                from aggre.enrichment import enrich_posts

                results = enrich_posts(engine, cfg, log, batch_limit=enrich_batch)
                log.info("enrich_complete", results=results)
            except Exception:
                log.exception("enrich_error")

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
        rows = conn.execute(sa.select(Source.type, Source.name, Source.last_fetched_at)).fetchall()
        click.echo("\n=== Sources ===")
        for row in rows:
            click.echo(f"  [{row.type}] {row.name} — last fetched: {row.last_fetched_at or 'never'}")

        if not rows:
            click.echo("  No sources registered yet. Run 'aggre fetch' first.")

        # Content counts by type
        result = conn.execute(sa.select(SilverPost.source_type, sa.func.count()).group_by(SilverPost.source_type)).fetchall()
        click.echo("\n=== Content Items ===")
        for row in result:
            click.echo(f"  {row[0]}: {row[1]} items")

        if not result:
            click.echo("  No content items yet.")

        # Transcription queue
        pending = conn.execute(sa.select(sa.func.count()).where(SilverPost.transcription_status == "pending")).scalar()
        failed = conn.execute(sa.select(sa.func.count()).where(SilverPost.transcription_status == "failed")).scalar()
        completed = conn.execute(sa.select(sa.func.count()).where(SilverPost.transcription_status == "completed")).scalar()
        click.echo("\n=== Transcription Queue ===")
        click.echo(f"  Pending: {pending}  |  Completed: {completed}  |  Failed: {failed}")

        # Recent errors
        errors = conn.execute(
            sa.select(SilverPost.external_id, SilverPost.title, SilverPost.transcription_error)
            .where(SilverPost.transcription_status == "failed")
            .order_by(SilverPost.fetched_at.desc())
            .limit(5)
        ).fetchall()
        if errors:
            click.echo("\n=== Recent Transcription Errors ===")
            for row in errors:
                click.echo(f"  {row.external_id} ({row.title}): {row.transcription_error}")

    click.echo()
