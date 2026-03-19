"""Modal serverless app for audio transcription using faster-whisper."""

import modal

app = modal.App("aggre-transcription")

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04", add_python="3.12")
    .apt_install("ffmpeg")
    .pip_install("faster-whisper>=1.1")
)


@app.cls(image=image, gpu="A10G", timeout=600)
class Transcriber:
    @modal.enter()
    def load_model(self) -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            "deepdml/faster-whisper-large-v3-turbo-ct2",
            device="cuda",
            compute_type="float16",
        )

    @modal.method()
    def transcribe(self, audio_bytes: bytes, format_hint: str = "opus") -> dict:
        """Transcribe audio bytes, return {"text": ..., "language": ...}."""
        import subprocess
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / f"input.{format_hint}"
            wav_path = Path(tmp_dir) / "audio.wav"

            input_path.write_bytes(audio_bytes)

            # Convert any audio format to 16kHz mono wav (what Whisper expects)
            subprocess.run(
                ["ffmpeg", "-i", str(input_path), "-ar", "16000", "-ac", "1", str(wav_path)],
                check=True,
                capture_output=True,
            )

            segments, info = self.model.transcribe(
                str(wav_path),
                beam_size=5,
                temperature=0.0,
            )
            text = " ".join(seg.text.strip() for seg in segments)

        return {"text": text, "language": info.language}
