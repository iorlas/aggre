"""yt-dlp subprocess wrapper — encapsulates all yt-dlp CLI interaction.

All YouTube downloads and metadata extraction go through this module.
Uses subprocess to avoid curl-cffi threading bugs (yt-dlp #15073).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# -- Exceptions ---------------------------------------------------------------


class YtDlpError(Exception):
    """Transient yt-dlp failure — Hatchet will retry with backoff."""


class VideoUnavailableError(YtDlpError):
    """Permanent — video deleted, private, region-blocked. Safe to skip."""


# Stderr patterns that indicate a permanent (non-retryable) failure
_PERMANENT_PATTERNS = [
    re.compile(r"Video unavailable", re.IGNORECASE),
    re.compile(r"Private video", re.IGNORECASE),
    re.compile(r"This video is not available", re.IGNORECASE),
    re.compile(r"This video has been removed", re.IGNORECASE),
    re.compile(r"This live event will begin", re.IGNORECASE),
    re.compile(r"Premieres in", re.IGNORECASE),
]


# -- Internal runner ----------------------------------------------------------


def _run_ytdlp(args: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """Run yt-dlp CLI via subprocess.

    Parses stderr for known error patterns.
    Raises VideoUnavailableError for permanent failures, YtDlpError for transient.
    """
    cmd = ["uv", "run", "yt-dlp", *args]
    logger.debug("ytdlp.run cmd=%s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode == 0:
        return result

    stderr = result.stderr.strip()
    logger.warning("ytdlp.failed exit=%d stderr=%s", result.returncode, stderr[:500])

    for pattern in _PERMANENT_PATTERNS:
        if pattern.search(stderr):
            raise VideoUnavailableError(stderr)

    raise YtDlpError(stderr)


# -- Public API ---------------------------------------------------------------


def extract_channel_info(
    channel_url: str,
    *,
    proxy_url: str,
    fetch_limit: int | None = 30,
) -> list[dict]:
    """Fetch video metadata from a YouTube channel/playlist.

    Returns list of video entry dicts (id, title, duration, etc.).
    Uses --flat-playlist to get metadata without extracting each video.
    Pass fetch_limit=None for backfill (no limit).
    """
    args = [
        "--impersonate",
        "chrome",
        "--proxy",
        proxy_url,
        "--source-address",
        "0.0.0.0",
        "--quiet",
        "--no-warnings",
        "--ignore-errors",
        "--flat-playlist",
        "-J",
    ]
    if fetch_limit is not None:
        args.extend(["--playlist-end", str(fetch_limit)])
    args.append(channel_url)

    result = _run_ytdlp(args, timeout=120)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise YtDlpError(f"Failed to parse yt-dlp JSON output: {e}") from e

    return data.get("entries", []) or []


def download_audio(
    video_id: str,
    output_dir: Path,
    *,
    proxy_url: str,
) -> Path:
    """Download audio and convert to opus via ffmpeg.

    Handles yt-dlp's output naming internally (glob + rename).
    Returns the final canonical path to the opus file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / f"{video_id}.%(ext)s")

    args = [
        "--impersonate",
        "chrome",
        "--proxy",
        proxy_url,
        "--source-address",
        "0.0.0.0",
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        "-f",
        "bestaudio/best",
        "-x",
        "--audio-format",
        "opus",
        "--audio-quality",
        "48K",
        "-o",
        output_template,
        f"https://www.youtube.com/watch?v={video_id}",
    ]

    _run_ytdlp(args, timeout=600)

    # Find the downloaded file — yt-dlp may produce various extensions
    candidates = list(output_dir.glob(f"{video_id}.*"))
    if not candidates:
        raise YtDlpError(f"No downloaded file found for {video_id}")

    audio_file = candidates[0]
    target = output_dir / "audio.opus"
    if audio_file != target:
        audio_file.rename(target)

    return target
