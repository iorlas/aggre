"""Transcription job -- download and transcribe YouTube videos.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import concurrent.futures
import json
import logging
import threading
import traceback
from pathlib import Path

import dagster as dg
import sqlalchemy as sa
import yt_dlp
from dagster import OpExecutionContext, Output
from faster_whisper import WhisperModel
from sqlalchemy.dialects.postgresql import JSONB

from aggre.config import AppConfig
from aggre.db import SilverContent, SilverDiscussion, update_content
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter, upsert_done, upsert_failed, upsert_skipped
from aggre.tracking.status import Stage
from aggre.utils.bronze import get_store, read_bronze_or_none, write_bronze

logger = logging.getLogger(__name__)

_thread_local = threading.local()


def _get_model(config: AppConfig, provided_model: WhisperModel | None) -> WhisperModel:
    """Return the provided model or a thread-local one (created lazily)."""
    if provided_model is not None:
        return provided_model
    if not hasattr(_thread_local, "whisper_model"):
        _thread_local.whisper_model = create_whisper_model(config)
    return _thread_local.whisper_model


def create_whisper_model(config: AppConfig) -> WhisperModel:
    """Create a WhisperModel from app config settings."""
    return WhisperModel(
        config.settings.whisper_model,
        device="cpu",
        download_root=config.settings.whisper_model_cache,
    )


def _transcribe_one(
    engine: sa.engine.Engine,
    config: AppConfig,
    item: sa.engine.Row,
    *,
    model: WhisperModel | None = None,
) -> int:
    """Transcribe a single video. Returns 1 on success, 0 on failure/skip."""
    content_id = item.id
    external_id = item.external_id

    try:
        # Skip videos longer than 30 minutes
        meta = json.loads(item.meta) if item.meta else {}
        duration = meta.get("duration")
        if duration is not None and duration > 1800:
            duration_min = duration / 60
            logger.info("transcription.skipped_long external_id=%s duration_min=%.0f", external_id, duration_min)
            upsert_skipped(engine, "youtube", external_id, Stage.TRANSCRIBE, f"video_too_long: {duration_min:.0f}min (limit 30min)")
            return 0

        logger.info("transcription.transcribing external_id=%s title=%s", external_id, item.title)

        # Cache check: if whisper.json exists in bronze, skip transcription
        cached_whisper = read_bronze_or_none("youtube", external_id, "whisper", "json")
        if cached_whisper is not None:
            logger.info("transcription.cached external_id=%s", external_id)
            cached = json.loads(cached_whisper)
            transcript = cached["transcript"] if isinstance(cached, dict) else ""
            language = cached.get("language", "unknown") if isinstance(cached, dict) else "unknown"
            update_content(engine, content_id, text=transcript, detected_language=language)
            upsert_done(engine, "youtube", external_id, Stage.TRANSCRIBE)
            return 1
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
            upsert_failed(engine, "youtube", external_id, Stage.TRANSCRIBE, "Audio file exceeds 500MB limit")
            return 0

        # Transcribe — get thread-local or provided model
        whisper_model = _get_model(config, model)
        segments, info = whisper_model.transcribe(str(audio_dest))
        transcript = " ".join(seg.text for seg in segments)

        # Write full whisper output to bronze
        whisper_output = {
            "transcript": transcript,
            "language": info.language,
            "language_probability": info.language_probability,
        }
        write_bronze("youtube", external_id, "whisper", json.dumps(whisper_output, ensure_ascii=False), "json")

        # Store result on SilverContent
        update_content(engine, content_id, text=transcript, detected_language=info.language)
        upsert_done(engine, "youtube", external_id, Stage.TRANSCRIBE)

        logger.info("transcription.transcribed external_id=%s", external_id)
        return 1

    except Exception as exc:
        logger.exception("transcription.failed external_id=%s", external_id)
        upsert_failed(engine, "youtube", external_id, Stage.TRANSCRIBE, str(exc))
        return 0

    finally:
        # Clean up temp audio when using remote storage (S3)
        try:
            if is_remote and audio_dest.exists():
                audio_dest.unlink()
                logger.info("transcription.audio_cleaned external_id=%s", external_id)
        except NameError:  # pragma: no cover — only if early failure before variable assignment
            pass


def transcribe(
    engine: sa.engine.Engine,
    config: AppConfig,
    batch_limit: int = 0,
    *,
    model: WhisperModel | None = None,
    max_workers: int = 1,
) -> dict[str, int]:
    # Query SilverContent needing transcription: text IS NULL, YouTube domain, stage not done
    query = (
        sa.select(
            SilverContent.id,
            SilverContent.canonical_url,
            SilverDiscussion.external_id,
            SilverDiscussion.title,
            SilverDiscussion.meta,
        )
        .join(SilverDiscussion, SilverDiscussion.content_id == SilverContent.id)
        .outerjoin(
            StageTracking,
            sa.and_(
                StageTracking.source == "youtube",
                StageTracking.external_id == SilverDiscussion.external_id,
                StageTracking.stage == Stage.TRANSCRIBE,
            ),
        )
        .where(
            SilverContent.text.is_(None),
            SilverDiscussion.source_type == "youtube",
            sa.or_(
                StageTracking.id.is_(None),
                retry_filter(StageTracking, Stage.TRANSCRIBE),
            ),
        )
        .order_by(
            sa.func.coalesce(
                sa.cast(sa.cast(SilverDiscussion.meta, JSONB)["duration"].as_string(), sa.Integer),
                999999,
            ).asc(),
            SilverContent.created_at.asc(),
        )
    )
    if batch_limit > 0:
        query = query.limit(batch_limit)

    with engine.connect() as conn:
        pending = conn.execute(query).fetchall()

    if not pending:
        logger.info("transcription.no_pending")
        return {"succeeded": 0, "failed": 0, "total": 0}

    logger.info("transcription.starting pending=%d", len(pending))

    if max_workers <= 1:
        processed = 0
        for item in pending:
            processed += _transcribe_one(engine, config, item, model=model)
    else:
        processed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {executor.submit(_transcribe_one, engine, config, item, model=model): item.external_id for item in pending}
            for future in concurrent.futures.as_completed(future_to_id):
                ext_id = future_to_id[future]
                try:
                    processed += future.result()
                except Exception:
                    logger.exception("transcription.worker_exception external_id=%s", ext_id)
                    upsert_failed(engine, "youtube", ext_id, Stage.TRANSCRIBE, traceback.format_exc())

    failed = len(pending) - processed
    logger.info("transcription.complete succeeded=%d failed=%d total=%d", processed, failed, len(pending))
    return {"succeeded": processed, "failed": failed, "total": len(pending)}


# -- Dagster ops and job -------------------------------------------------------


@dg.op(required_resource_keys={"database", "app_config"})
def transcribe_videos_op(context: OpExecutionContext) -> Output[int]:  # pragma: no cover — Dagster op wiring
    """Download and transcribe pending YouTube videos."""
    cfg = context.resources.app_config.get_config()
    engine = context.resources.database.get_engine()
    stats = transcribe(engine, cfg, batch_limit=30, max_workers=2)
    return Output(
        stats["total"],
        metadata={
            "succeeded": stats["succeeded"],
            "failed": stats["failed"],
            "total": stats["total"],
        },
    )


@dg.job
def transcribe_job() -> None:
    transcribe_videos_op()
