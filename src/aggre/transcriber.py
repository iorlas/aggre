"""YouTube video transcription pipeline using faster-whisper."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
import structlog
import yt_dlp
from faster_whisper import WhisperModel

from aggre.config import AppConfig
from aggre.db import SilverContent, SilverDiscussion, _update_content
from aggre.statuses import TranscriptionStatus

_model_cache: WhisperModel | None = None


# -- Transcription state transitions -------------------------------------------

def transcription_downloading(engine: sa.engine.Engine, content_id: int) -> None:
    """PENDING → DOWNLOADING"""
    _update_content(engine, content_id, transcription_status=TranscriptionStatus.DOWNLOADING)


def transcription_transcribing(engine: sa.engine.Engine, content_id: int) -> None:
    """DOWNLOADING → TRANSCRIBING"""
    _update_content(engine, content_id, transcription_status=TranscriptionStatus.TRANSCRIBING)


def transcription_completed(engine: sa.engine.Engine, content_id: int, *, body_text: str, detected_language: str) -> None:
    """TRANSCRIBING → COMPLETED"""
    _update_content(engine, content_id,
        body_text=body_text, transcription_status=TranscriptionStatus.COMPLETED,
        detected_language=detected_language)


def transcription_failed(engine: sa.engine.Engine, content_id: int, *, error: str) -> None:
    """any → FAILED"""
    _update_content(engine, content_id,
        transcription_status=TranscriptionStatus.FAILED, transcription_error=error)


def _get_model(config: AppConfig) -> WhisperModel:
    global _model_cache
    if _model_cache is None:
        _model_cache = WhisperModel(
            config.settings.whisper_model,
            device="cpu",
            download_root=config.settings.whisper_model_cache,
        )
    return _model_cache


def transcribe(
    engine: sa.engine.Engine,
    config: AppConfig,
    log: structlog.stdlib.BoundLogger,
    batch_limit: int = 0,
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
            SilverContent.transcription_status.in_((TranscriptionStatus.PENDING, TranscriptionStatus.DOWNLOADING, TranscriptionStatus.TRANSCRIBING)),
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

        audio_path: Path | None = None

        try:
            # Mark as downloading
            transcription_downloading(engine, content_id)

            # Download audio
            temp_dir = Path(config.settings.youtube_temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(temp_dir / f"{external_id}.%(ext)s")

            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": output_path,
                "quiet": True,
                "no_warnings": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "opus",
                    "preferredquality": "48",
                }],
            }
            if config.settings.proxy_url:
                ydl_opts["proxy"] = config.settings.proxy_url
                ydl_opts["source_address"] = "0.0.0.0"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={external_id}"])

            # Find the downloaded file
            candidates = list(temp_dir.glob(f"{external_id}.*"))
            if not candidates:
                raise FileNotFoundError(f"No downloaded file found for {external_id}")
            audio_path = candidates[0]

            # Check audio file size (500MB limit)
            file_size = audio_path.stat().st_size
            if file_size > 500 * 1024 * 1024:
                log.warning("audio_file_too_large", external_id=external_id, size_mb=file_size / (1024 * 1024))
                transcription_failed(engine, content_id, error="Audio file exceeds 500MB limit")
                continue

            # Mark as transcribing
            transcription_transcribing(engine, content_id)

            # Transcribe
            model = _get_model(config)
            segments, info = model.transcribe(str(audio_path))
            transcript = " ".join(seg.text for seg in segments)

            # Store result on SilverContent (body_text holds the transcript)
            transcription_completed(engine, content_id, body_text=transcript, detected_language=info.language)

            log.info("transcription_complete", external_id=external_id)
            processed += 1

        except Exception as exc:
            log.exception("transcription_failed", external_id=external_id)
            transcription_failed(engine, content_id, error=str(exc))

        finally:
            if audio_path and audio_path.exists():
                audio_path.unlink()

    return processed


# Backward compatibility alias
process_pending = transcribe
