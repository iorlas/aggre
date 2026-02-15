"""Click CLI with commands: collect, transcribe, backfill, status."""

from __future__ import annotations

import concurrent.futures

import click
import sqlalchemy as sa

from aggre.config import load_config
from aggre.db import SilverContent, SilverDiscussion, Source, get_engine
from aggre.statuses import TranscriptionStatus
from aggre.logging import setup_logging
from aggre.worker import run_loop, worker_options


@click.group()
@click.option("--config", "config_path", default="config.yaml", help="Path to config YAML file.")
@click.pass_context
def cli(ctx: click.Context, config_path: str) -> None:
    """Aggre — Content aggregation system."""
    ctx.ensure_object(dict)
    cfg = load_config(config_path)
    ctx.obj["config"] = cfg

    engine = get_engine(cfg.settings.database_url)
    ctx.obj["engine"] = engine


@cli.command("collect")
@click.option(
    "--source", "source_type",
    type=click.Choice(["rss", "reddit", "youtube", "hackernews", "lobsters", "huggingface", "telegram"]),
    help="Collect only this source type.",
)
@click.option("--comment-batch", default=10, type=int, help="Max comments to fetch per source per cycle (0 = skip).")
@worker_options(default_interval=3600, include_batch=False)
@click.pass_context
def collect_cmd(ctx: click.Context, source_type: str | None, comment_batch: int, loop: bool, interval: int) -> None:
    """Collect discussions from configured sources."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "collect")

    from aggre.collectors.hackernews import HackernewsCollector
    from aggre.collectors.huggingface import HuggingfaceCollector
    from aggre.collectors.lobsters import LobstersCollector
    from aggre.collectors.reddit import RedditCollector
    from aggre.collectors.rss import RssCollector
    from aggre.collectors.telegram import TelegramCollector
    from aggre.collectors.youtube import YoutubeCollector

    collectors = {
        "rss": RssCollector(),
        "reddit": RedditCollector(),
        "youtube": YoutubeCollector(),
        "hackernews": HackernewsCollector(),
        "lobsters": LobstersCollector(),
        "huggingface": HuggingfaceCollector(),
        "telegram": TelegramCollector(),
    }

    active_collectors = collectors
    if source_type:
        active_collectors = {source_type: collectors[source_type]}

    def _cycle():
        total = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_collectors)) as executor:
            futures = {
                executor.submit(collector.collect, engine, cfg, log): name
                for name, collector in active_collectors.items()
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    count = future.result()
                    total += count
                    log.info("collect.source_complete", source=name, new_discussions=count)
                except Exception:
                    log.exception("collect.source_error", source=name)

        for src_name in ("reddit", "hackernews", "lobsters"):
            if source_type in (None, src_name) and comment_batch > 0:
                coll = active_collectors.get(src_name) or collectors.get(src_name)
                if coll and hasattr(coll, "collect_comments"):
                    try:
                        comments_fetched = coll.collect_comments(engine, cfg, log, batch_limit=comment_batch)
                        log.info("collect.comments_complete", source=src_name, comments_fetched=comments_fetched)
                    except Exception:
                        log.exception("collect.comments_error", source=src_name)

        return total

    run_loop(fn=_cycle, loop=loop, interval=interval, log=log, name="collect")


@cli.command("telegram-auth")
@click.pass_context
def telegram_auth(ctx: click.Context) -> None:
    """Generate a Telegram session string for AGGRE_TELEGRAM_SESSION."""
    import asyncio

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    cfg = ctx.obj["config"]
    api_id = cfg.settings.telegram_api_id
    api_hash = cfg.settings.telegram_api_hash

    if not api_id or not api_hash:
        click.echo("Set AGGRE_TELEGRAM_API_ID and AGGRE_TELEGRAM_API_HASH first.")
        raise SystemExit(1)

    async def _auth():
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.start()
        session_str = client.session.save()
        await client.disconnect()
        return session_str

    session = asyncio.run(_auth())
    click.echo(f"\nAdd this to your .env file:\nAGGRE_TELEGRAM_SESSION={session}")


@cli.command("download")
@worker_options(default_interval=10, default_batch=50)
@click.option("--workers", default=5, type=int, help="Concurrent download threads.")
@click.pass_context
def download_cmd(ctx, batch, workers, loop, interval):
    """Download pending content URLs."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "download")
    from aggre.content_fetcher import download_content
    run_loop(
        fn=lambda: download_content(engine, cfg, log, batch_limit=batch, max_workers=workers),
        loop=loop, interval=interval, log=log, name="download",
    )


@cli.command("extract-html-text")
@worker_options(default_interval=10, default_batch=50)
@click.pass_context
def extract_html_text_cmd(ctx, batch, loop, interval):
    """Extract text from downloaded HTML content."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "extract-html-text")
    from aggre.content_fetcher import extract_html_text
    run_loop(
        fn=lambda: extract_html_text(engine, cfg, log, batch_limit=batch),
        loop=loop, interval=interval, log=log, name="extract_html_text",
    )


@cli.command("enrich-content-discussions")
@worker_options(default_interval=60, default_batch=50)
@click.pass_context
def enrich_content_discussions_cmd(ctx, batch, loop, interval):
    """Discover cross-source discussions for content URLs."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "enrich-content-discussions")
    from aggre.enrichment import enrich_content_discussions
    run_loop(
        fn=lambda: enrich_content_discussions(engine, cfg, log, batch_limit=batch),
        loop=loop, interval=interval, log=log, name="enrich",
    )


@cli.command()
@worker_options(default_interval=10, default_batch=0)
@click.pass_context
def transcribe(ctx: click.Context, batch: int, loop: bool, interval: int) -> None:
    """Transcribe pending YouTube videos."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "transcribe")

    from aggre.transcriber import transcribe as do_transcribe

    run_loop(
        fn=lambda: do_transcribe(engine, cfg, log, batch_limit=batch),
        loop=loop, interval=interval, log=log, name="transcribe",
    )


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
        log.info("backfill.complete", source=source_type, discussions=count)


@cli.command("backfill-content")
@click.option("--batch", default=50, type=int, help="Max content items to fetch per batch.")
@click.pass_context
def backfill_content(ctx: click.Context, batch: int) -> None:
    """Backfill content for existing discussions."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "backfill-content")

    import json

    import sqlalchemy as sa

    from aggre.db import SilverDiscussion
    from aggre.urls import ensure_content

    # Step 1: Link existing discussions to SilverContent
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(SilverDiscussion.id, SilverDiscussion.url, SilverDiscussion.meta)
            .where(
                SilverDiscussion.content_id.is_(None),
                SilverDiscussion.url.isnot(None),
            )
        ).fetchall()

    linked = 0
    for row in rows:
        with engine.begin() as conn:
            content_id = ensure_content(conn, row.url)
            if content_id:
                conn.execute(
                    sa.update(SilverDiscussion)
                    .where(SilverDiscussion.id == row.id)
                    .values(content_id=content_id)
                )
                linked += 1

                # Extract score/comment_count from meta if available
                if row.meta:
                    meta = json.loads(row.meta)
                    updates = {}
                    if "score" in meta:
                        updates["score"] = meta["score"]
                    elif "points" in meta:
                        updates["score"] = meta["points"]
                    if "num_comments" in meta:
                        updates["comment_count"] = meta["num_comments"]
                    elif "comment_count" in meta:
                        updates["comment_count"] = meta["comment_count"]
                    if updates:
                        conn.execute(
                            sa.update(SilverDiscussion)
                            .where(SilverDiscussion.id == row.id)
                            .values(**updates)
                        )

    log.info("backfill.linked", linked=linked, total=len(rows))

    click.echo(f"Linked {linked} discussions to content.")


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show collection times, queue sizes, and recent errors."""
    engine = ctx.obj["engine"]

    with engine.connect() as conn:
        # Sources summary
        rows = conn.execute(sa.select(Source.type, Source.name, Source.last_fetched_at)).fetchall()
        click.echo("\n=== Sources ===")
        for row in rows:
            click.echo(f"  [{row.type}] {row.name} — last fetched: {row.last_fetched_at or 'never'}")

        if not rows:
            click.echo("  No sources registered yet. Run 'aggre collect' first.")

        # Discussion counts by type
        result = conn.execute(sa.select(SilverDiscussion.source_type, sa.func.count()).group_by(SilverDiscussion.source_type)).fetchall()
        click.echo("\n=== Discussions by Source ===")
        for row in result:
            click.echo(f"  {row[0]}: {row[1]} discussions")

        if not result:
            click.echo("  No discussions yet.")

        # Content status
        content_stats = conn.execute(
            sa.select(SilverContent.fetch_status, sa.func.count())
            .group_by(SilverContent.fetch_status)
        ).fetchall()
        if content_stats:
            click.echo("\n=== Content Status ===")
            for row in content_stats:
                click.echo(f"  {row[0]}: {row[1]}")

        # Transcription queue (on SilverContent)
        pending = conn.execute(sa.select(sa.func.count()).where(SilverContent.transcription_status == TranscriptionStatus.PENDING)).scalar()
        failed = conn.execute(sa.select(sa.func.count()).where(SilverContent.transcription_status == TranscriptionStatus.FAILED)).scalar()
        completed = conn.execute(sa.select(sa.func.count()).where(SilverContent.transcription_status == TranscriptionStatus.COMPLETED)).scalar()
        click.echo("\n=== Transcription Queue ===")
        click.echo(f"  Pending: {pending}  |  Completed: {completed}  |  Failed: {failed}")

        # Recent errors
        errors = conn.execute(
            sa.select(SilverContent.canonical_url, SilverContent.title, SilverContent.transcription_error)
            .where(SilverContent.transcription_status == TranscriptionStatus.FAILED)
            .order_by(SilverContent.created_at.desc())
            .limit(5)
        ).fetchall()
        if errors:
            click.echo("\n=== Recent Transcription Errors ===")
            for row in errors:
                click.echo(f"  {row.canonical_url} ({row.title}): {row.transcription_error}")

    click.echo()
