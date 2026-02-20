"""Benchmark faster-whisper models on real audio to compare speed and quality."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path


DEFAULT_MODELS = ["large-v3", "medium", "small", "base"]
MODEL_CACHE = "./data/models"


def pick_random_video(database_url: str) -> str:
    """Pick a random YouTube video ID from silver_discussions."""
    import sqlalchemy as sa

    from aggre.db import SilverDiscussion, get_engine

    engine = get_engine(database_url)
    query = (
        sa.select(SilverDiscussion.external_id)
        .where(SilverDiscussion.source_type == "youtube")
        .order_by(sa.func.random())
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(query).fetchone()
    if row is None:
        sys.exit("No YouTube videos found in silver_discussions")
    return row.external_id


def get_database_url() -> str:
    """Load database URL from env / .env via project settings."""
    from aggre.config import Settings

    return Settings().database_url


def download_audio(video_id: str, dest_dir: Path, proxy_url: str = "") -> Path:
    """Download audio for a YouTube video, return path to the file."""
    import yt_dlp

    output_path = str(dest_dir / f"{video_id}.%(ext)s")
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
    if proxy_url:
        ydl_opts["proxy"] = proxy_url
        ydl_opts["source_address"] = "0.0.0.0"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

    candidates = list(dest_dir.glob(f"{video_id}.*"))
    if not candidates:
        sys.exit(f"No downloaded file found for {video_id}")
    return candidates[0]


def fmt_duration(seconds: float) -> str:
    """Format seconds as 'Xm YYs' or 'X.Xs'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def benchmark_model(
    model_name: str, audio_path: Path
) -> dict:
    """Load a model, transcribe, and return timing + results."""
    from faster_whisper import WhisperModel

    print(f"  [{model_name}] Loading model...", end="", flush=True)
    t0 = time.monotonic()
    model = WhisperModel(model_name, device="cpu", download_root=MODEL_CACHE)
    load_time = time.monotonic() - t0
    print(f" {fmt_duration(load_time)}")

    print(f"  [{model_name}] Transcribing...", end="", flush=True)
    t0 = time.monotonic()
    segments, info = model.transcribe(str(audio_path))
    transcript = " ".join(seg.text for seg in segments)
    transcribe_time = time.monotonic() - t0
    print(f" {fmt_duration(transcribe_time)}")

    return {
        "model": model_name,
        "load_time": load_time,
        "transcribe_time": transcribe_time,
        "audio_duration": info.duration,
        "language": info.language,
        "transcript": transcript,
    }


def print_results(results: list[dict], audio_path: Path) -> None:
    """Print a summary table and transcript previews."""
    if not results:
        return

    audio_dur = results[0]["audio_duration"]
    print()
    print(f"Audio: {audio_path.name} (duration: {fmt_duration(audio_dur)})")
    print(f"Model cache: {MODEL_CACHE}")
    print()

    # Table header
    header = f"{'Model':<12}  {'Load Time':>10}  {'Transcribe Time':>16}  {'RT Factor':>10}  {'Language':>8}"
    print(header)
    print("-" * len(header))

    for r in results:
        rt_factor = r["transcribe_time"] / r["audio_duration"] if r["audio_duration"] > 0 else 0
        print(
            f"{r['model']:<12}  {fmt_duration(r['load_time']):>10}  "
            f"{fmt_duration(r['transcribe_time']):>16}  "
            f"{rt_factor:>9.2f}x  {r['language']:>8}"
        )

    print()
    print("Transcript preview (first 200 chars):")
    for r in results:
        preview = r["transcript"][:200]
        print(f"  [{r['model']}] {preview}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark faster-whisper models")
    parser.add_argument("--audio", type=Path, help="Local audio file to use")
    parser.add_argument("--video-id", help="YouTube video ID to download")
    parser.add_argument(
        "--models",
        help=f"Comma-separated models to test (default: {','.join(DEFAULT_MODELS)})",
    )
    args = parser.parse_args()

    models = args.models.split(",") if args.models else DEFAULT_MODELS

    audio_path: Path | None = None
    cleanup_audio = False

    try:
        if args.audio:
            audio_path = args.audio.resolve()
            if not audio_path.exists():
                sys.exit(f"Audio file not found: {audio_path}")
            print(f"Using local audio: {audio_path}")
        else:
            video_id = args.video_id
            if not video_id:
                print("No --audio or --video-id given, picking random video from DB...")
                db_url = get_database_url()
                video_id = pick_random_video(db_url)

            print(f"Downloading audio for video: {video_id}")
            tmp_dir = Path(tempfile.mkdtemp(prefix="whisper_bench_"))
            from aggre.config import Settings

            proxy_url = Settings().proxy_url
            audio_path = download_audio(video_id, tmp_dir, proxy_url)
            cleanup_audio = True
            print(f"Downloaded: {audio_path}")

        print(f"\nBenchmarking models: {', '.join(models)}\n")

        results = []
        for model_name in models:
            result = benchmark_model(model_name, audio_path)
            results.append(result)

        print_results(results, audio_path)

    finally:
        if cleanup_audio and audio_path and audio_path.exists():
            audio_path.unlink()
            parent = audio_path.parent
            if parent.exists() and not list(parent.iterdir()):
                parent.rmdir()


if __name__ == "__main__":
    main()
