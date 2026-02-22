"""YouTube video transcription pipeline using faster-whisper."""

from __future__ import annotations

import json

import sqlalchemy as sa
import structlog
import yt_dlp
from faster_whisper import WhisperModel

from aggre.config import AppConfig
from aggre.db import SilverContent, SilverDiscussion, update_content
from aggre.statuses import TranscriptionStatus
from aggre.utils.bronze import bronze_exists, bronze_path, read_bronze, write_bronze

# -- Transcription state transitions -------------------------------------------


def transcription_downloading(engine: sa.engine.Engine, content_id: int) -> None:
    """PENDING → DOWNLOADING"""
    update_content(engine, content_id, transcription_status=TranscriptionStatus.DOWNLOADING)


def transcription_transcribing(engine: sa.engine.Engine, content_id: int) -> None:
    """DOWNLOADING → TRANSCRIBING"""
    update_content(engine, content_id, transcription_status=TranscriptionStatus.TRANSCRIBING)


def transcription_completed(engine: sa.engine.Engine, content_id: int, *, body_text: str, detected_language: str) -> None:
    """TRANSCRIBING → COMPLETED"""
    update_content(
        engine, content_id, body_text=body_text, transcription_status=TranscriptionStatus.COMPLETED, detected_language=detected_language
    )


def transcription_failed(engine: sa.engine.Engine, content_id: int, *, error: str) -> None:
    """any → FAILED"""
    update_content(engine, content_id, transcription_status=TranscriptionStatus.FAILED, transcription_error=error)


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
    log: structlog.stdlib.BoundLogger,
    batch_limit: int = 0,
    *,
    model: WhisperModel | None = None,
) -> int:
    # Query SilverContent with pending transcription, JOIN to get the YouTube video ID
    query = (
        sa.select(
            SilverContent.id,
            SilverContent.canonical_url,
            SilverContent.transcription_status,
            SilverDiscussion.external_id,
            SilverDiscussion.title,
        )
        .join(SilverDiscussion, SilverDiscussion.content_id == SilverContent.id)
        .where(
            SilverContent.transcription_status.in_(
                (
                    TranscriptionStatus.PENDING,
                    TranscriptionStatus.DOWNLOADING,
                    TranscriptionStatus.TRANSCRIBING,
                )
            ),
            SilverDiscussion.source_type == "youtube",
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
        log.info("transcribing_video", external_id=external_id, title=item.title)

        # Cache check: if whisper.json exists in bronze, skip transcription
        if bronze_exists("youtube", external_id, "whisper", "json"):
            log.info("transcription_cached", external_id=external_id)
            cached = json.loads(read_bronze("youtube", external_id, "whisper", "json"))
            transcript = cached["transcript"] if isinstance(cached, dict) else ""
            language = cached.get("language", "unknown") if isinstance(cached, dict) else "unknown"
            transcription_completed(engine, content_id, body_text=transcript, detected_language=language)
            processed += 1
            continue

        try:
            # Mark as downloading
            transcription_downloading(engine, content_id)

            # Download audio to bronze
            audio_dest = bronze_path("youtube", external_id, "audio", "opus")
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
                log.warning("audio_file_too_large", external_id=external_id, size_mb=file_size / (1024 * 1024))
                transcription_failed(engine, content_id, error="Audio file exceeds 500MB limit")
                continue

            # Mark as transcribing
            transcription_transcribing(engine, content_id)

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

            # Store result on SilverContent (body_text holds the transcript)
            transcription_completed(engine, content_id, body_text=transcript, detected_language=info.language)

            log.info("transcription_complete", external_id=external_id)
            processed += 1

        except Exception as exc:
            log.exception("transcription_failed", external_id=external_id)
            transcription_failed(engine, content_id, error=str(exc))

    return processed
