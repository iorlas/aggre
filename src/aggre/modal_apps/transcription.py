"""Modal serverless app for audio transcription using faster-whisper."""

import modal

app = modal.App("aggre-transcription")

image = modal.Image.debian_slim(python_version="3.12").apt_install("ffmpeg").pip_install("faster-whisper>=1.1")


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
    def transcribe(self, audio_bytes: bytes) -> dict:
        """Transcribe audio bytes, return {"text": ..., "language": ...}."""
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".opus", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            segments, info = self.model.transcribe(
                tmp_path,
                beam_size=5,
                temperature=0.0,
            )
            text = " ".join(seg.text.strip() for seg in segments)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return {"text": text, "language": info.language}
