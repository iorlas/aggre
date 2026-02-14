"""YouTube video transcription pipeline using faster-whisper."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
import structlog
import yt_dlp
from faster_whisper import WhisperModel

from aggre.config import AppConfig
from aggre.db import SilverPost

_model_cache: WhisperModel | None = None


def _get_model(config: AppConfig) -> WhisperModel:
    global _model_cache
    if _model_cache is None:
        _model_cache = WhisperModel(
            config.settings.whisper_model,
            device="cpu",
            download_root=config.settings.whisper_model_cache,
        )
    return _model_cache


def process_pending(
    engine: sa.engine.Engine,
    config: AppConfig,
    log: structlog.stdlib.BoundLogger,
    batch_limit: int = 0,
) -> int:
    query = sa.select(SilverPost).where(SilverPost.transcription_status == "pending").order_by(SilverPost.fetched_at.asc())
    if batch_limit > 0:
        query = query.limit(batch_limit)

    with engine.connect() as conn:
        pending = conn.execute(query).fetchall()

    processed = 0

    for item in pending:
        external_id = item.external_id
        log.info("transcribing_video", external_id=external_id, title=item.title)

        audio_path: Path | None = None

        try:
            # Mark as downloading
            with engine.begin() as conn:
                conn.execute(sa.update(SilverPost).where(SilverPost.id == item.id).values(transcription_status="downloading"))

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

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={external_id}"])

            # Find the downloaded file
            candidates = list(temp_dir.glob(f"{external_id}.*"))
            if not candidates:
                raise FileNotFoundError(f"No downloaded file found for {external_id}")
            audio_path = candidates[0]

            # Mark as transcribing
            with engine.begin() as conn:
                conn.execute(sa.update(SilverPost).where(SilverPost.id == item.id).values(transcription_status="transcribing"))

            # Transcribe
            model = _get_model(config)
            segments, info = model.transcribe(str(audio_path))
            transcript = " ".join(seg.text for seg in segments)

            # Store result
            with engine.begin() as conn:
                conn.execute(
                    sa.update(SilverPost)
                    .where(SilverPost.id == item.id)
                    .values(
                        content_text=transcript,
                        transcription_status="completed",
                        detected_language=info.language,
                    )
                )

            log.info("transcription_complete", external_id=external_id)
            processed += 1

        except Exception as exc:
            log.exception("transcription_failed", external_id=external_id)
            with engine.begin() as conn:
                conn.execute(
                    sa.update(SilverPost)
                    .where(SilverPost.id == item.id)
                    .values(
                        transcription_status="failed",
                        transcription_error=str(exc),
                    )
                )

        finally:
            if audio_path and audio_path.exists():
                audio_path.unlink()

    return processed
