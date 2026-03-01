"""Transcription job -- download and transcribe YouTube videos.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import json
import logging

import dagster as dg
import sqlalchemy as sa
import yt_dlp
from dagster import OpExecutionContext
from faster_whisper import WhisperModel

from aggre.config import AppConfig, load_config
from aggre.db import SilverContent, SilverObservation, update_content
from aggre.utils.bronze import bronze_exists, bronze_path, read_bronze, write_bronze

logger = logging.getLogger(__name__)


def _mark_transcribed(engine: sa.engine.Engine, content_id: int, *, text: str, detected_language: str) -> None:
    """Set transcript text and language on content."""
    update_content(engine, content_id, text=text, detected_language=detected_language)


def _mark_transcription_failed(engine: sa.engine.Engine, content_id: int, *, error: str) -> None:
    """Transcription failed — set error."""
    update_content(engine, content_id, error=error)


def create_whisper_model(config: AppConfig) -> WhisperModel:
    """Create a WhisperModel from app config settings."""
    return WhisperModel(
        config.settings.whisper_model,
        device="cpu",
        download_root=config.settings.whisper_model_cache,
    )


def transcribe(
    engine: sa.engine.Engine,
    config: AppConfig,
    batch_limit: int = 0,
    *,
    model: WhisperModel | None = None,
) -> int:
    # Query SilverContent needing transcription: text IS NULL, error IS NULL, YouTube domain
    query = (
        sa.select(
            SilverContent.id,
            SilverContent.canonical_url,
            SilverObservation.external_id,
            SilverObservation.title,
        )
        .join(SilverObservation, SilverObservation.content_id == SilverContent.id)
        .where(
            SilverContent.text.is_(None),
            SilverContent.error.is_(None),
            SilverObservation.source_type == "youtube",
        )
        .order_by(SilverContent.created_at.asc())
    )
    if batch_limit > 0:
        query = query.limit(batch_limit)

    with engine.connect() as conn:
        pending = conn.execute(query).fetchall()

    processed = 0

    for item in pending:
        content_id = item.id
        external_id = item.external_id
        logger.info("transcribing_video external_id=%s title=%s", external_id, item.title)

        # Cache check: if whisper.json exists in bronze, skip transcription
        if bronze_exists("youtube", external_id, "whisper", "json"):
            logger.info("transcription_cached external_id=%s", external_id)
            cached = json.loads(read_bronze("youtube", external_id, "whisper", "json"))
            transcript = cached["transcript"] if isinstance(cached, dict) else ""
            language = cached.get("language", "unknown") if isinstance(cached, dict) else "unknown"
            _mark_transcribed(engine, content_id, text=transcript, detected_language=language)
            processed += 1
            continue

        try:
            # Check if audio already exists in bronze (from a previous partial run)
            audio_dest = bronze_path("youtube", external_id, "audio", "opus")
            if audio_dest.exists():
                logger.info("audio_cached external_id=%s", external_id)
            else:
                # Download audio to bronze
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

                # Find the downloaded file and move to bronze path
                candidates = list(audio_dest.parent.glob(f"{external_id}.*"))
                if not candidates:
                    raise FileNotFoundError(f"No downloaded file found for {external_id}")
                audio_file = candidates[0]

                # If the downloaded file isn't already at the target path, rename it
                if audio_file != audio_dest:
                    audio_file.rename(audio_dest)

            # Check audio file size (500MB limit)
            file_size = audio_dest.stat().st_size
            if file_size > 500 * 1024 * 1024:
                logger.warning("audio_file_too_large external_id=%s size_mb=%s", external_id, file_size / (1024 * 1024))
                _mark_transcription_failed(engine, content_id, error="Audio file exceeds 500MB limit")
                continue

            # Transcribe — create model on first use if not provided
            if model is None:
                model = create_whisper_model(config)
            segments, info = model.transcribe(str(audio_dest))
            transcript = " ".join(seg.text for seg in segments)

            # Write full whisper output to bronze
            whisper_output = {
                "transcript": transcript,
                "language": info.language,
                "language_probability": info.language_probability,
            }
            write_bronze("youtube", external_id, "whisper", json.dumps(whisper_output, ensure_ascii=False), "json")

            # Store result on SilverContent
            _mark_transcribed(engine, content_id, text=transcript, detected_language=info.language)

            logger.info("transcription_complete external_id=%s", external_id)
            processed += 1

        except Exception as exc:
            logger.exception("transcription_failed external_id=%s", external_id)
            _mark_transcription_failed(engine, content_id, error=str(exc))

    return processed


# -- Dagster ops and job -------------------------------------------------------


@dg.op(required_resource_keys={"database"})
def transcribe_videos_op(context: OpExecutionContext) -> int:
    """Download and transcribe pending YouTube videos."""
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return transcribe(engine, cfg)


@dg.job
def transcribe_job() -> None:
    transcribe_videos_op()
