"""Quick POC: send a local audio file to the Modal transcription app."""

import sys
import time
from pathlib import Path

import modal


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/test_modal_transcription.py <audio_file>")
        print("Example: uv run python scripts/test_modal_transcription.py whisper-server-20260316-232202-397014158.wav")
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    if not audio_path.exists():
        print(f"File not found: {audio_path}")
        sys.exit(1)

    audio_bytes = audio_path.read_bytes()
    print(f"Audio file: {audio_path} ({len(audio_bytes) / 1024 / 1024:.1f} MB)")

    # Look up the deployed Modal app by name
    transcriber = modal.Cls.from_name("aggre-transcription", "Transcriber")()

    print("Calling Modal transcription (includes cold start if first request)...")
    t0 = time.perf_counter()
    result = transcriber.transcribe.remote(audio_bytes)
    elapsed = time.perf_counter() - t0

    print(f"\n--- Result ({elapsed:.1f}s) ---")
    print(f"Language: {result['language']}")
    print(f"Text: {result['text'][:500]}{'...' if len(result['text']) > 500 else ''}")


if __name__ == "__main__":
    main()
