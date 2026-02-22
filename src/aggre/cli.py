"""Click CLI with commands: status, telegram-auth, run-once."""

from __future__ import annotations

import inspect

import click
import sqlalchemy as sa

from aggre.collectors import COLLECTORS
from aggre.config import load_config
from aggre.db import SilverContent, SilverDiscussion, Source, get_engine
from aggre.statuses import TranscriptionStatus
from aggre.utils.logging import setup_logging


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

    async def _auth() -> str:
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.start()
        session_str = client.session.save()
        await client.disconnect()
        return session_str

    session = asyncio.run(_auth())
    click.echo(f"\nAdd this to your .env file:\nAGGRE_TELEGRAM_SESSION={session}")


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
        content_stats = conn.execute(sa.select(SilverContent.fetch_status, sa.func.count()).group_by(SilverContent.fetch_status)).fetchall()
        if content_stats:
            click.echo("\n=== Content Status ===")
            for row in content_stats:
                click.echo(f"  {row[0]}: {row[1]}")

        # Transcription queue (on SilverContent)
        pending = conn.execute(sa.select(sa.func.count()).where(SilverContent.transcription_status == TranscriptionStatus.PENDING)).scalar()
        failed = conn.execute(sa.select(sa.func.count()).where(SilverContent.transcription_status == TranscriptionStatus.FAILED)).scalar()
        completed = conn.execute(
            sa.select(sa.func.count()).where(SilverContent.transcription_status == TranscriptionStatus.COMPLETED)
        ).scalar()
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


_MAX_DRAIN_ITERATIONS = 100  # Safety cap; prevents infinite loops if a stage never drains


@cli.command("run-once")
@click.option("--source-ttl", default=0, type=int, help="Skip sources fetched within this many minutes (0 = always collect).")
@click.option("--source", "source_type", type=click.Choice(list(COLLECTORS.keys())), help="Collect only this source type.")
@click.option("--skip-transcribe", is_flag=True, help="Skip the transcription stage.")
@click.option("--comment-batch", default=10, type=int, help="Max comments to fetch per source per cycle (0 = skip).")
@click.option("--enrich-batch", default=10000, type=int, help="Max URLs to enrich per run (0 = skip enrichment).")
@click.pass_context
def run_once_cmd(
    ctx: click.Context, source_ttl: int, source_type: str | None, skip_transcribe: bool, comment_batch: int, enrich_batch: int
) -> None:
    """Run the full pipeline once and exit."""
    from aggre.collectors.base import all_sources_recent
    from aggre.collectors.hackernews.collector import HackernewsCollector
    from aggre.collectors.lobsters.collector import LobstersCollector
    from aggre.content_fetcher import download_content, extract_html_text
    from aggre.enrichment import enrich_content_discussions
    from aggre.transcriber import transcribe as do_transcribe

    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "run-once")

    collectors = {name: cls() for name, cls in COLLECTORS.items()}

    active_collectors = collectors
    if source_type:
        active_collectors = {source_type: collectors[source_type]}

    # ---- Stage 1: Collect ----
    sources_checked = 0
    sources_collected = 0
    sources_skipped = 0
    sources_failed = 0
    total_new_discussions = 0

    for name, collector in active_collectors.items():
        sources_checked += 1
        if source_ttl > 0 and all_sources_recent(engine, collector.source_type, ttl_minutes=source_ttl):
            sources_skipped += 1
            log.info("run_once.source_skipped", source=name, reason="recent")
            continue
        try:
            source_config = getattr(cfg, name)
            collect_kwargs = {}
            if source_ttl > 0 and "source_ttl_minutes" in inspect.signature(collector.collect).parameters:
                collect_kwargs["source_ttl_minutes"] = source_ttl
            count = collector.collect(engine, source_config, cfg.settings, log, **collect_kwargs)
            sources_collected += 1
            total_new_discussions += count
            log.info("run_once.source_collected", source=name, new_discussions=count)
        except Exception:
            log.exception("run_once.collect_error", source=name)
            sources_failed += 1

    # Fetch comments (same pattern as original collect)
    for src_name in ("reddit", "hackernews", "lobsters"):
        if source_type in (None, src_name) and comment_batch > 0:
            coll = active_collectors.get(src_name) or collectors.get(src_name)
            if coll and hasattr(coll, "collect_comments"):
                try:
                    coll.collect_comments(engine, getattr(cfg, src_name), cfg.settings, log, batch_limit=comment_batch)
                except Exception:
                    log.exception("run_once.comments_error", source=src_name)

    # ---- Stage 2: Download (drain loop) ----
    total_downloaded = 0
    for _ in range(_MAX_DRAIN_ITERATIONS):
        n = download_content(engine, cfg, log)
        if n == 0:
            break
        total_downloaded += n

    # ---- Stage 3: Extract (drain loop) ----
    total_extracted = 0
    for _ in range(_MAX_DRAIN_ITERATIONS):
        n = extract_html_text(engine, cfg, log)
        if n == 0:
            break
        total_extracted += n

    # ---- Stage 4: Enrich (drain loop) ----
    total_enriched = 0
    hn_coll = HackernewsCollector()
    lob_coll = LobstersCollector()
    for _ in range(_MAX_DRAIN_ITERATIONS):
        result = enrich_content_discussions(
            engine,
            cfg,
            log,
            hn_collector=hn_coll,
            lobsters_collector=lob_coll,
        )
        processed = result.get("processed", 0)
        if processed == 0:
            break
        total_enriched += processed
        if total_enriched >= enrich_batch:
            break

    # ---- Stage 5: Transcribe (drain loop, skippable) ----
    total_transcribed = 0
    if not skip_transcribe:
        for _ in range(_MAX_DRAIN_ITERATIONS):
            n = do_transcribe(engine, cfg, log)
            if n == 0:
                break
            total_transcribed += n

    # ---- Summary ----
    transcribe_line = "skipped" if skip_transcribe else f"{total_transcribed} transcribed"
    click.echo("")
    click.echo("=== Run Complete ===")
    total_checked = sources_collected + sources_skipped + sources_failed
    sources_line = f"Sources:  {total_checked} checked, {sources_collected} collected, {sources_skipped} skipped (recent)"
    if sources_failed:
        sources_line += f", {sources_failed} failed"
    click.echo(sources_line)
    click.echo(f"Discuss:  {total_new_discussions} new")
    click.echo(f"Content:  {total_downloaded} downloaded")
    click.echo(f"Extract:  {total_extracted} extracted")
    click.echo(f"Transcr:  {transcribe_line}")
    click.echo(f"Enrich:   {total_enriched} enriched")
