"""Transcription workflow -- download and transcribe YouTube videos.

Single-task workflow triggered per-item via "item.new" event.
Hatchet manages concurrency and retry.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import sqlalchemy as sa
from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, DefaultFilter

from aggre.config import AppConfig, load_config
from aggre.db import SilverContent, SilverDiscussion, update_content
from aggre.transcriber import build_transcribers, transcribe_with_fallback
from aggre.utils.bronze import get_store, read_bronze_or_none, write_bronze
from aggre.utils.db import get_engine
from aggre.utils.ytdlp import VideoUnavailableError, download_audio
from aggre.workflows.models import SilverContentRef, StepOutput

logger = logging.getLogger(__name__)


def _transcribe_one(
    engine: sa.engine.Engine,
    config: AppConfig,
    item: sa.engine.Row,
) -> StepOutput:
    """Transcribe a single video. Returns StepOutput.

    Raises on transient failure (Hatchet handles retry).
    """
    content_id = item.id
    external_id = item.external_id
    url = item.canonical_url
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
            detail: dict[str, str] = {}
            if duration_meta:
                detail["duration"] = f"{duration_meta // 60}m{duration_meta % 60}s"
            return StepOutput(status="cached", url=url, detail=detail or None)

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
            # Download audio from YouTube via subprocess wrapper
            downloaded = download_audio(
                external_id,
                audio_dest.parent,
                proxy_url=config.settings.proxy_url,
            )
            if downloaded != audio_dest:
                downloaded.rename(audio_dest)

            # Upload audio to S3 so it survives temp dir cleanup
            if is_remote:
                store.write_bytes(audio_key, audio_dest.read_bytes())
                logger.info("transcription.audio_uploaded external_id=%s", external_id)

        # Check audio file size (500MB limit)
        file_size = audio_dest.stat().st_size
        if file_size > 500 * 1024 * 1024:
            logger.warning("transcription.audio_too_large external_id=%s size_mb=%s", external_id, file_size / (1024 * 1024))
            raise ValueError(f"Audio file exceeds 500MB limit ({file_size / (1024 * 1024):.0f}MB)")

        # Transcribe via configured backends (Modal → Whisper fallback)
        transcribers = build_transcribers(config.settings)
        result = transcribe_with_fallback(transcribers, audio_dest.read_bytes(), format_hint="opus")
        transcript = result.text
        language = result.language

        # Write full whisper output to bronze
        whisper_output = {"transcript": transcript, "language": language}
        write_bronze("youtube", external_id, "whisper", json.dumps(whisper_output, ensure_ascii=False), "json")

        # Store result on SilverContent
        update_content(engine, content_id, text=transcript, detected_language=language, transcribed_by=result.transcribed_by)

        logger.info("transcription.transcribed external_id=%s", external_id)
        detail = {"transcriber": result.transcribed_by, "language": language}
        if duration_meta:
            detail["duration"] = f"{duration_meta // 60}m{duration_meta % 60}s"
        return StepOutput(status="transcribed", url=url, detail=detail)

    finally:
        # Clean up temp audio when using remote storage (S3)
        try:
            if is_remote and audio_dest and audio_dest.exists():
                audio_dest.unlink()
                logger.info("transcription.audio_cleaned external_id=%s", external_id)
        except NameError:  # pragma: no cover — only if early failure before variable assignment
            pass


# -- Per-item function (tested directly) ------------------------------------


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from canonical URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    v = params.get("v")
    return v[0] if v else None


def transcribe_one(
    engine: sa.engine.Engine,
    config: AppConfig,
    content_id: int,
) -> StepOutput:
    """Transcribe a single YouTube video by content_id. Returns StepOutput."""
    if not config.settings.whisper_endpoints and not config.settings.modal_app_name:
        raise RuntimeError("No transcription backend configured (set AGGRE_WHISPER_ENDPOINTS or AGGRE_MODAL_APP_NAME)")

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(
                SilverContent.id,
                SilverContent.canonical_url,
                SilverContent.text,
                SilverContent.domain,
            ).where(SilverContent.id == content_id, SilverContent.domain == "youtube.com")
        ).first()

    if not row:
        return StepOutput(status="skipped", reason="not_found")

    if row.text is not None:
        return StepOutput(status="skipped", reason="already_done", url=row.canonical_url)

    video_id = _extract_video_id(row.canonical_url)
    if not video_id:
        return StepOutput(status="skipped", reason="no_video_id", url=row.canonical_url)

    # Fetch title and meta from any associated discussion (for logging/duration)
    with engine.connect() as conn:
        disc = conn.execute(
            sa.select(SilverDiscussion.title, SilverDiscussion.meta).where(SilverDiscussion.content_id == content_id).limit(1)
        ).first()

    # Build a lightweight row-like object for _transcribe_one
    from types import SimpleNamespace

    item = SimpleNamespace(
        id=row.id,
        canonical_url=row.canonical_url,
        text=row.text,
        external_id=video_id,
        title=disc.title if disc else None,
        meta=disc.meta if disc else None,
    )

    try:
        return _transcribe_one(engine, config, item)
    except VideoUnavailableError as e:
        return StepOutput(status="skipped", reason="video_unavailable", url=item.canonical_url, detail={"message": str(e)})


# -- Hatchet workflow ----------------------------------------------------------


def register(h):  # pragma: no cover — Hatchet wiring
    """Register the transcription workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-transcription",
        on_events=["item.new"],
        # Two-layer concurrency:
        # 1. GROUP_ROUND_ROBIN with static key — global max 20 concurrent transcriptions
        # 2. CANCEL_NEWEST by content_id — dedup safety net, see event-dedup-design.md
        concurrency=[
            ConcurrencyExpression(
                expression="'youtube'",
                max_runs=20,
                limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
            ),
            ConcurrencyExpression(
                expression="string(input.content_id)",
                max_runs=1,
                limit_strategy=ConcurrencyLimitStrategy.CANCEL_NEWEST,
            ),
        ],
        input_validator=SilverContentRef,
        # Only YouTube content that doesn't already have text from the collector
        default_filters=[DefaultFilter(expression="input.domain == 'youtube.com' && !input.text_provided", scope="default")],
    )

    @wf.task(execution_timeout="30m", schedule_timeout="720h", retries=7, backoff_factor=4, backoff_max_seconds=3600)
    def transcribe_task(input: SilverContentRef, ctx) -> StepOutput:
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        result = transcribe_one(engine, cfg, input.content_id)
        ctx.log(f"Transcription: {result.status} for content_id={input.content_id}")
        return result

    return wf
