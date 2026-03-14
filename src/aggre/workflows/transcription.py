"""Transcription workflow -- download and transcribe YouTube videos.

Single-task workflow triggered per-item via "item.new" event.
Hatchet manages concurrency and retry.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import sqlalchemy as sa
import yt_dlp
from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, DefaultFilter

from aggre.config import AppConfig, load_config
from aggre.db import SilverContent, SilverDiscussion, update_content
from aggre.utils.bronze import get_store, read_bronze_or_none, write_bronze
from aggre.utils.db import get_engine
from aggre.utils.whisper_client import parse_endpoints, transcribe_audio
from aggre.workflows.models import ItemEvent

logger = logging.getLogger(__name__)


def _transcribe_one(
    engine: sa.engine.Engine,
    config: AppConfig,
    item: sa.engine.Row,
) -> str:
    """Transcribe a single video. Returns status string.

    Raises on transient failure (Hatchet handles retry).
    """
    content_id = item.id
    external_id = item.external_id
    is_remote = False
    audio_dest = None

    try:
        duration_meta = json.loads(item.meta).get("duration") if item.meta else None
        duration_str = f" duration={duration_meta // 60}m{duration_meta % 60}s" if duration_meta else ""
        logger.info("transcription.transcribing external_id=%s%s title=%s", external_id, duration_str, item.title)

        # Cache check: if whisper.json exists in bronze, skip transcription
        cached_whisper = read_bronze_or_none("youtube", external_id, "whisper", "json")
        if cached_whisper is not None:
            logger.info("transcription.cached external_id=%s", external_id)
            cached = json.loads(cached_whisper)
            transcript = cached["transcript"] if isinstance(cached, dict) else ""
            language = cached.get("language", "unknown") if isinstance(cached, dict) else "unknown"
            update_content(engine, content_id, text=transcript, detected_language=language)
            return "cached"

        # Resolve audio location — filesystem store uses local path, S3 uses temp dir
        store = get_store()
        audio_key = f"youtube/{external_id}/audio.opus"
        audio_local = store.local_path(audio_key)
        is_remote = audio_local is None
        if is_remote:
            audio_dest = Path(config.settings.youtube_temp_dir) / external_id / "audio.opus"
        else:
            audio_dest = audio_local

        if audio_dest.exists():
            logger.info("transcription.audio_cached external_id=%s", external_id)
        elif is_remote:
            # Try fetching cached audio from S3 before downloading from YouTube
            try:
                audio_data = store.read_bytes(audio_key)
                audio_dest.parent.mkdir(parents=True, exist_ok=True)
                audio_dest.write_bytes(audio_data)
                logger.info("transcription.audio_from_s3 external_id=%s", external_id)
            except FileNotFoundError:
                pass  # Not in S3 either — will download from YouTube below

        if not audio_dest.exists():
            # Download audio from YouTube
            audio_dest.parent.mkdir(parents=True, exist_ok=True)
            output_path = str(audio_dest.parent / f"{external_id}.%(ext)s")

            ydl_opts = {
                "format": "bestaudio/best",
                "impersonate": "chrome",
                "outtmpl": output_path,
                "quiet": True,
                "no_warnings": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "opus",
                        "preferredquality": "48",
                    }
                ],
            }
            if config.settings.proxy_url:
                ydl_opts["proxy"] = config.settings.proxy_url
                ydl_opts["source_address"] = "0.0.0.0"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={external_id}"])

            # Find the downloaded file and move to target path
            candidates = list(audio_dest.parent.glob(f"{external_id}.*"))
            if not candidates:
                raise FileNotFoundError(f"No downloaded file found for {external_id}")
            audio_file = candidates[0]

            if audio_file != audio_dest:
                audio_file.rename(audio_dest)

            # Upload audio to S3 so it survives temp dir cleanup
            if is_remote:
                store.write_bytes(audio_key, audio_dest.read_bytes())
                logger.info("transcription.audio_uploaded external_id=%s", external_id)

        # Check audio file size (500MB limit)
        file_size = audio_dest.stat().st_size
        if file_size > 500 * 1024 * 1024:
            logger.warning("transcription.audio_too_large external_id=%s size_mb=%s", external_id, file_size / (1024 * 1024))
            raise ValueError(f"Audio file exceeds 500MB limit ({file_size / (1024 * 1024):.0f}MB)")

        # Transcribe via whisper server
        endpoints = parse_endpoints(config.settings.whisper_endpoints)
        result = transcribe_audio(
            audio_dest,
            endpoints=endpoints,
            model=config.settings.whisper_model,
            timeout=config.settings.whisper_server_timeout,
        )
        transcript = result.text
        language = result.language

        # Write full whisper output to bronze
        whisper_output = {"transcript": transcript, "language": language}
        write_bronze("youtube", external_id, "whisper", json.dumps(whisper_output, ensure_ascii=False), "json")

        # Store result on SilverContent
        update_content(engine, content_id, text=transcript, detected_language=language, transcribed_by=result.server_name)

        logger.info("transcription.transcribed external_id=%s", external_id)
        return "transcribed"

    finally:
        # Clean up temp audio when using remote storage (S3)
        try:
            if is_remote and audio_dest and audio_dest.exists():
                audio_dest.unlink()
                logger.info("transcription.audio_cleaned external_id=%s", external_id)
        except NameError:  # pragma: no cover — only if early failure before variable assignment
            pass


# -- Per-item function (tested directly) ------------------------------------


def transcribe_one(
    engine: sa.engine.Engine,
    config: AppConfig,
    content_id: int,
) -> str:
    """Transcribe a single YouTube video by content_id. Returns status string."""
    if not config.settings.whisper_endpoints:
        raise RuntimeError("AGGRE_WHISPER_ENDPOINTS not configured")

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(
                SilverContent.id,
                SilverContent.canonical_url,
                SilverContent.text,
                SilverDiscussion.external_id,
                SilverDiscussion.title,
                SilverDiscussion.meta,
            )
            .join(SilverDiscussion, SilverDiscussion.content_id == SilverContent.id)
            .where(SilverContent.id == content_id, SilverDiscussion.source_type == "youtube")
        ).first()

    if not row:
        return "skipped"

    if row.text is not None:
        return "already_done"

    return _transcribe_one(engine, config, row)


# -- Hatchet workflow ----------------------------------------------------------


def register(h):  # pragma: no cover — Hatchet wiring
    """Register the transcription workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-transcription",
        on_events=["item.new"],
        concurrency=ConcurrencyExpression(
            expression="'youtube'",
            max_runs=20,
            limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
        ),
        input_validator=ItemEvent,
        default_filters=[DefaultFilter(expression="input.domain == 'youtube.com'", scope="default")],
    )

    @wf.task(execution_timeout="30m", schedule_timeout="720h", retries=7, backoff_factor=4, backoff_max_seconds=3600)
    def transcribe_task(input: ItemEvent, ctx):
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        status = transcribe_one(engine, cfg, input.content_id)
        ctx.log(f"Transcription: {status} for content_id={input.content_id}")
        return {"status": status}

    return wf
