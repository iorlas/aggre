# Modal Serverless Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Modal as primary serverless GPU transcription backend with automatic fallback to existing Whisper servers.

**Architecture:** A `Transcriber` protocol with `__call__` supports pluggable backends. `ModalTranscriber` calls the deployed Modal app via SDK. `WhisperTranscriber` wraps existing `whisper_client.py`. A `transcribe_with_fallback` function loops backends in priority order, falling back on quota/connection errors only.

**Tech Stack:** Modal SDK, faster-whisper, existing whisper_client.py

**Spec:** `docs/superpowers/specs/2026-03-19-modal-transcription-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/aggre/transcriber.py` | Create | Protocol, TranscriptResult, exceptions, ModalTranscriber, WhisperTranscriber, transcribe_with_fallback, build_transcribers |
| `src/aggre/settings.py` | Modify (line 24) | Add `modal_app_name` setting |
| `src/aggre/workflows/transcription.py` | Modify (lines 21, 104-120, 155-156) | Replace whisper_client call with transcribe_with_fallback |
| `tests/test_transcriber.py` | Create | Tests for all transcriber module code |
| `tests/workflows/test_transcription.py` | Modify | Update mocks from whisper_client to transcriber |
| `tests/factories.py` | Modify (line 555) | Add `modal_app_name` param to `make_config` |
| `src/aggre/modal_apps/transcription.py` | Already exists | POC deployed, no changes needed |

---

### Task 1: Create transcriber module — types and exceptions

**Files:**
- Create: `src/aggre/transcriber.py`
- Test: `tests/test_transcriber.py`

- [ ] **Step 1: Write tests for TranscriptResult and exceptions**

```python
"""Tests for the transcriber abstraction layer."""

from __future__ import annotations

import pytest

from aggre.transcriber import (
    AllTranscribersFailedError,
    QuotaExceededError,
    TranscriptResult,
)

pytestmark = pytest.mark.unit


class TestTranscriptResult:
    def test_frozen(self):
        r = TranscriptResult(text="hello", language="en", transcribed_by="test")
        with pytest.raises(AttributeError):
            r.text = "changed"

    def test_fields(self):
        r = TranscriptResult(text="hello", language="en", transcribed_by="modal-a10g")
        assert r.text == "hello"
        assert r.language == "en"
        assert r.transcribed_by == "modal-a10g"


class TestExceptions:
    def test_quota_exceeded_is_exception(self):
        assert issubclass(QuotaExceededError, Exception)

    def test_all_transcribers_failed_is_exception(self):
        assert issubclass(AllTranscribersFailedError, Exception)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber.py -v`
Expected: FAIL — module `aggre.transcriber` not found

- [ ] **Step 3: Write the types and exceptions**

```python
"""Transcription abstraction — pluggable backends with priority-based fallback."""

from __future__ import annotations

import dataclasses
from typing import Protocol


@dataclasses.dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str
    transcribed_by: str


class Transcriber(Protocol):
    def __call__(self, audio: bytes, format_hint: str = "opus") -> TranscriptResult: ...


class QuotaExceededError(Exception):
    """Backend has exhausted its quota (e.g. Modal free credits)."""


class AllTranscribersFailedError(Exception):
    """Every configured backend failed with a fallback-eligible error."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aggre/transcriber.py tests/test_transcriber.py
git commit -m "feat(transcriber): add TranscriptResult, Transcriber protocol, and exceptions"
```

---

### Task 2: Implement transcribe_with_fallback

**Files:**
- Modify: `src/aggre/transcriber.py`
- Modify: `tests/test_transcriber.py`

- [ ] **Step 1: Write tests for transcribe_with_fallback**

Append to `tests/test_transcriber.py`:

```python
from aggre.transcriber import transcribe_with_fallback


class TestTranscribeWithFallback:
    def test_first_backend_succeeds(self):
        def backend(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            return TranscriptResult(text="ok", language="en", transcribed_by="first")

        result = transcribe_with_fallback([backend], b"audio")
        assert result.text == "ok"
        assert result.transcribed_by == "first"

    def test_falls_back_on_quota_exceeded(self):
        def failing(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            raise QuotaExceededError("out of credits")

        def fallback(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            return TranscriptResult(text="fallback", language="en", transcribed_by="second")

        result = transcribe_with_fallback([failing, fallback], b"audio")
        assert result.transcribed_by == "second"

    def test_falls_back_on_connection_error(self):
        def failing(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            raise ConnectionError("network down")

        def fallback(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            return TranscriptResult(text="ok", language="en", transcribed_by="backup")

        result = transcribe_with_fallback([failing, fallback], b"audio")
        assert result.transcribed_by == "backup"

    def test_does_not_fall_back_on_transcription_error(self):
        """Non-fallback errors (bad audio, model failure) propagate immediately."""
        def failing(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            raise ValueError("bad audio format")

        def fallback(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            return TranscriptResult(text="ok", language="en", transcribed_by="backup")

        with pytest.raises(ValueError, match="bad audio"):
            transcribe_with_fallback([failing, fallback], b"audio")

    def test_all_fail_raises_all_transcribers_failed(self):
        def failing(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            raise QuotaExceededError("out")

        with pytest.raises(AllTranscribersFailedError):
            transcribe_with_fallback([failing], b"audio")

    def test_empty_list_raises_all_transcribers_failed(self):
        with pytest.raises(AllTranscribersFailedError):
            transcribe_with_fallback([], b"audio")

    def test_passes_format_hint(self):
        received = {}

        def backend(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            received["format_hint"] = format_hint
            return TranscriptResult(text="ok", language="en", transcribed_by="test")

        transcribe_with_fallback([backend], b"audio", format_hint="wav")
        assert received["format_hint"] == "wav"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber.py::TestTranscribeWithFallback -v`
Expected: FAIL — `transcribe_with_fallback` not importable

- [ ] **Step 3: Implement transcribe_with_fallback**

Add to `src/aggre/transcriber.py`:

```python
import logging
from collections.abc import Sequence

logger = logging.getLogger(__name__)


def transcribe_with_fallback(
    transcribers: Sequence[Transcriber],
    audio: bytes,
    format_hint: str = "opus",
) -> TranscriptResult:
    """Try each transcriber in order. Fall back on quota/connection errors only."""
    last_error: Exception | None = None
    for transcriber in transcribers:
        try:
            return transcriber(audio, format_hint)
        except (QuotaExceededError, ConnectionError) as exc:
            logger.warning("transcriber.fallback backend=%s error=%s", type(transcriber).__name__, exc)
            last_error = exc
    raise AllTranscribersFailedError(
        f"All {len(transcribers)} transcription backends failed"
    ) from last_error
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/aggre/transcriber.py tests/test_transcriber.py
git commit -m "feat(transcriber): add transcribe_with_fallback with priority-based fallback"
```

---

### Task 3: Implement WhisperTranscriber

**Files:**
- Modify: `src/aggre/transcriber.py`
- Modify: `tests/test_transcriber.py`

- [ ] **Step 1: Write tests for WhisperTranscriber**

Append to `tests/test_transcriber.py`:

```python
from unittest.mock import patch, MagicMock
from aggre.transcriber import WhisperTranscriber
from aggre.utils.whisper_client import Endpoint, TranscriptionResult


class TestWhisperTranscriber:
    def _make_endpoint(self) -> Endpoint:
        return Endpoint(url="http://test:8090", weight=1, api_format="whisper-cpp", name="test-whisper", max_concurrent=1)

    @patch("aggre.transcriber.transcribe_audio")
    def test_transcribes_and_maps_result(self, mock_transcribe, tmp_path):
        mock_transcribe.return_value = TranscriptionResult(
            text="Hello world", language="en", server_name="test-whisper"
        )
        endpoints = [self._make_endpoint()]
        whisper = WhisperTranscriber(endpoints=endpoints, model="large-v3-turbo", timeout=300.0)

        result = whisper(b"fake audio", "opus")

        assert result.text == "Hello world"
        assert result.language == "en"
        assert result.transcribed_by == "test-whisper"

        # Verify temp file was created and passed to transcribe_audio
        call_args = mock_transcribe.call_args
        audio_path = call_args[0][0]
        assert audio_path.suffix == ".opus"
        assert call_args[1]["endpoints"] == endpoints
        assert call_args[1]["model"] == "large-v3-turbo"

    @patch("aggre.transcriber.transcribe_audio")
    def test_format_hint_used_as_extension(self, mock_transcribe):
        mock_transcribe.return_value = TranscriptionResult(
            text="ok", language="en", server_name="test"
        )
        whisper = WhisperTranscriber(
            endpoints=[self._make_endpoint()], model="large-v3-turbo", timeout=300.0
        )

        whisper(b"fake", "wav")

        audio_path = mock_transcribe.call_args[0][0]
        assert audio_path.suffix == ".wav"

    @patch("aggre.transcriber.transcribe_audio")
    def test_connection_error_propagates(self, mock_transcribe):
        mock_transcribe.side_effect = ConnectionError("All endpoints failed")
        whisper = WhisperTranscriber(
            endpoints=[self._make_endpoint()], model="large-v3-turbo", timeout=300.0
        )

        with pytest.raises(ConnectionError):
            whisper(b"fake", "opus")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber.py::TestWhisperTranscriber -v`
Expected: FAIL — `WhisperTranscriber` not importable

- [ ] **Step 3: Implement WhisperTranscriber**

Add to `src/aggre/transcriber.py`:

```python
import tempfile
from pathlib import Path
from aggre.utils.whisper_client import Endpoint, transcribe_audio


class WhisperTranscriber:
    """Wraps the existing whisper HTTP client as a Transcriber backend."""

    def __init__(self, *, endpoints: list[Endpoint], model: str, timeout: float = 300.0) -> None:
        self._endpoints = endpoints
        self._model = model
        self._timeout = timeout

    def __call__(self, audio: bytes, format_hint: str = "opus") -> TranscriptResult:
        with tempfile.NamedTemporaryFile(suffix=f".{format_hint}", delete=False) as f:
            f.write(audio)
            tmp_path = Path(f.name)
        try:
            result = transcribe_audio(
                tmp_path,
                endpoints=self._endpoints,
                model=self._model,
                timeout=self._timeout,
            )
            return TranscriptResult(
                text=result.text,
                language=result.language,
                transcribed_by=result.server_name,
            )
        finally:
            tmp_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/aggre/transcriber.py tests/test_transcriber.py
git commit -m "feat(transcriber): add WhisperTranscriber wrapping existing HTTP client"
```

---

### Task 4: Implement ModalTranscriber

**Files:**
- Modify: `src/aggre/transcriber.py`
- Modify: `tests/test_transcriber.py`

- [ ] **Step 1: Write tests for ModalTranscriber**

Append to `tests/test_transcriber.py`:

```python
from aggre.transcriber import ModalTranscriber


class TestModalTranscriber:
    @patch("aggre.transcriber.modal")
    def test_transcribes_via_modal_sdk(self, mock_modal):
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_modal.Cls.from_name.return_value = mock_cls
        mock_cls.return_value = mock_instance
        mock_instance.transcribe.remote.return_value = {"text": "Hello", "language": "en"}

        transcriber = ModalTranscriber(app_name="aggre-transcription")
        result = transcriber(b"fake audio", "opus")

        assert result.text == "Hello"
        assert result.language == "en"
        assert result.transcribed_by == "modal"
        mock_modal.Cls.from_name.assert_called_once_with("aggre-transcription", "Transcriber")
        mock_instance.transcribe.remote.assert_called_once_with(b"fake audio", format_hint="opus")

    @patch("aggre.transcriber.modal")
    def test_quota_error_mapped(self, mock_modal):
        """Modal billing/quota errors are mapped to QuotaExceededError."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_modal.Cls.from_name.return_value = mock_cls
        mock_cls.return_value = mock_instance
        mock_modal.exception.InvalidError = type("InvalidError", (Exception,), {})
        mock_instance.transcribe.remote.side_effect = mock_modal.exception.InvalidError("quota exceeded")

        transcriber = ModalTranscriber(app_name="aggre-transcription")
        with pytest.raises(QuotaExceededError):
            transcriber(b"fake audio", "opus")

    @patch("aggre.transcriber.modal")
    def test_connection_error_on_network_failure(self, mock_modal):
        """Modal connection failures raise ConnectionError for fallback."""
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_modal.Cls.from_name.return_value = mock_cls
        mock_cls.return_value = mock_instance
        mock_modal.exception.ConnectionError = type("ConnectionError", (Exception,), {})
        mock_instance.transcribe.remote.side_effect = mock_modal.exception.ConnectionError("timeout")

        transcriber = ModalTranscriber(app_name="aggre-transcription")
        with pytest.raises(ConnectionError):
            transcriber(b"fake audio", "opus")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber.py::TestModalTranscriber -v`
Expected: FAIL — `ModalTranscriber` not importable

- [ ] **Step 3: Implement ModalTranscriber**

Add to `src/aggre/transcriber.py`:

```python
import modal


class ModalTranscriber:
    """Calls the deployed Modal transcription app via SDK."""

    def __init__(self, *, app_name: str) -> None:
        self._app_name = app_name
        self._cls = modal.Cls.from_name(app_name, "Transcriber")

    def __call__(self, audio: bytes, format_hint: str = "opus") -> TranscriptResult:
        try:
            instance = self._cls()
            result = instance.transcribe.remote(audio, format_hint=format_hint)
        except modal.exception.InvalidError as exc:
            raise QuotaExceededError(str(exc)) from exc
        except modal.exception.ConnectionError as exc:
            raise ConnectionError(str(exc)) from exc
        return TranscriptResult(
            text=result["text"],
            language=result["language"],
            transcribed_by="modal",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/aggre/transcriber.py tests/test_transcriber.py
git commit -m "feat(transcriber): add ModalTranscriber calling deployed Modal app via SDK"
```

---

### Task 5: Add build_transcribers and settings

**Files:**
- Modify: `src/aggre/settings.py` (line 24)
- Modify: `src/aggre/transcriber.py`
- Modify: `tests/test_transcriber.py`
- Modify: `tests/factories.py` (line 555)

- [ ] **Step 1: Add modal_app_name to Settings**

In `src/aggre/settings.py`, add after line 23 (`whisper_server_timeout`):

```python
    modal_app_name: str = ""
```

- [ ] **Step 2: Add modal_app_name to make_config in factories.py**

In `tests/factories.py`, add `modal_app_name: str = ""` parameter to `make_config` and pass it to `Settings(...)`:

After `whisper_endpoints` param (line 555):
```python
    modal_app_name: str = "",
```

In the `Settings(...)` constructor (after `whisper_endpoints=whisper_endpoints,`):
```python
            modal_app_name=modal_app_name,
```

- [ ] **Step 3: Write tests for build_transcribers**

Append to `tests/test_transcriber.py`:

```python
from aggre.transcriber import build_transcribers, ModalTranscriber, WhisperTranscriber
from tests.factories import make_config


class TestBuildTranscribers:
    def test_both_configured(self):
        config = make_config(
            modal_app_name="aggre-transcription",
            whisper_endpoints="http://test:8090:1:whisper-cpp:test:1",
        )
        transcribers = build_transcribers(config.settings)
        assert len(transcribers) == 2
        assert isinstance(transcribers[0], ModalTranscriber)
        assert isinstance(transcribers[1], WhisperTranscriber)

    def test_only_whisper(self):
        config = make_config(
            modal_app_name="",
            whisper_endpoints="http://test:8090:1:whisper-cpp:test:1",
        )
        transcribers = build_transcribers(config.settings)
        assert len(transcribers) == 1
        assert isinstance(transcribers[0], WhisperTranscriber)

    def test_only_modal(self):
        config = make_config(modal_app_name="aggre-transcription", whisper_endpoints="")
        transcribers = build_transcribers(config.settings)
        assert len(transcribers) == 1
        assert isinstance(transcribers[0], ModalTranscriber)

    def test_nothing_configured(self):
        config = make_config(modal_app_name="", whisper_endpoints="")
        transcribers = build_transcribers(config.settings)
        assert len(transcribers) == 0
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber.py::TestBuildTranscribers -v`
Expected: FAIL — `build_transcribers` not importable

- [ ] **Step 5: Implement build_transcribers**

Add to `src/aggre/transcriber.py`:

```python
from aggre.settings import Settings
from aggre.utils.whisper_client import parse_endpoints


def build_transcribers(settings: Settings) -> list[Transcriber]:
    """Build transcriber list from settings. Modal first (if configured), then Whisper."""
    transcribers: list[Transcriber] = []
    if settings.modal_app_name:
        transcribers.append(ModalTranscriber(app_name=settings.modal_app_name))
    endpoints = parse_endpoints(settings.whisper_endpoints)
    if endpoints:
        transcribers.append(
            WhisperTranscriber(
                endpoints=endpoints,
                model=settings.whisper_model,
                timeout=settings.whisper_server_timeout,
            )
        )
    return transcribers
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/aggre/settings.py src/aggre/transcriber.py tests/test_transcriber.py tests/factories.py
git commit -m "feat(transcriber): add build_transcribers and modal_app_name setting"
```

---

### Task 6: Wire into transcription workflow

**Files:**
- Modify: `src/aggre/workflows/transcription.py` (lines 21, 104-120, 155-156)
- Modify: `tests/workflows/test_transcription.py`

- [ ] **Step 1: Update workflow imports and _transcribe_one**

In `src/aggre/workflows/transcription.py`:

Replace line 21:
```python
from aggre.utils.whisper_client import parse_endpoints, transcribe_audio
```
with:
```python
from aggre.transcriber import build_transcribers, transcribe_with_fallback
```

Replace lines 104-126 (the whisper transcription block through the detail dict):
```python
        # Transcribe via whisper server
        endpoints = parse_endpoints(config.settings.whisper_endpoints)
        result = transcribe_audio(
            audio_dest,
            endpoints=endpoints,
            model=config.settings.whisper_model,
            timeout=config.settings.whisper_server_timeout,
        )
        transcript = result.text
        language = result.language

        # Write full whisper output to bronze
        whisper_output = {"transcript": transcript, "language": language}
        write_bronze("youtube", external_id, "whisper", json.dumps(whisper_output, ensure_ascii=False), "json")

        # Store result on SilverContent
        update_content(engine, content_id, text=transcript, detected_language=language, transcribed_by=result.server_name)

        logger.info("transcription.transcribed external_id=%s", external_id)
        detail = {"transcriber": result.server_name, "language": language}
```

with:
```python
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
```

- [ ] **Step 2: Update the guard clause in transcribe_one**

Replace lines 155-156:
```python
    if not config.settings.whisper_endpoints:
        raise RuntimeError("AGGRE_WHISPER_ENDPOINTS not configured")
```

with:
```python
    if not config.settings.whisper_endpoints and not config.settings.modal_app_name:
        raise RuntimeError("No transcription backend configured (set AGGRE_WHISPER_ENDPOINTS or AGGRE_MODAL_APP_NAME)")
```

- [ ] **Step 3: Update test mocks**

In `tests/workflows/test_transcription.py`:

Replace line 16:
```python
from aggre.utils.whisper_client import TranscriptionResult
```
with:
```python
from aggre.transcriber import TranscriptResult
```

**Changes to apply across all affected tests:**

1. Replace import (line 16):
   - Old: `from aggre.utils.whisper_client import TranscriptionResult`
   - New: `from aggre.transcriber import TranscriptResult, AllTranscribersFailedError`
   - Also remove `import httpx` (line 10) — no longer needed after migration

2. Replace all `@patch("aggre.workflows.transcription.transcribe_audio")` with `@patch("aggre.workflows.transcription.transcribe_with_fallback")`

3. Replace all `TranscriptionResult(text=..., language=..., server_name=...)` with `TranscriptResult(text=..., language=..., transcribed_by=...)`

4. Replace `row.transcribed_by == "test-whisper"` assertions — these stay the same, just the mock return type changes

Here's the template for `test_transcribes_and_stores_text` — apply the same pattern to `test_uses_cached_audio` and `test_writes_whisper_output_to_bronze`:

```python
    @patch("aggre.workflows.transcription.transcribe_with_fallback")
    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.get_store")
    @patch("aggre.workflows.transcription.read_bronze_or_none", return_value=None)
    @patch("aggre.workflows.transcription.download_audio")
    def test_transcribes_and_stores_text(
        self,
        mock_download,
        mock_read_or_none,
        mock_get_store,
        mock_write,
        mock_transcribe,
        engine,
        tmp_path,
    ):
        """Downloads audio, transcribes, stores text + detected_language on SilverContent."""
        content_id = _seed_youtube(engine, external_id="vid001")
        config = make_config()

        mock_transcribe.return_value = TranscriptResult(text="This is the transcript", language="en", transcribed_by="test-whisper")

        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")
        mock_store = MagicMock()
        mock_store.local_path.return_value = audio_file
        mock_get_store.return_value = mock_store

        mock_download.return_value = audio_file

        result = transcribe_one(engine, config, content_id)
        assert result.status == "transcribed"

        row = _get_content(engine, content_id)
        assert row.text == "This is the transcript"
        assert row.detected_language == "en"
        assert row.transcribed_by == "test-whisper"
```

For `test_transcription_server_error_propagates`, change the expected exception:
```python
    @patch("aggre.workflows.transcription.transcribe_with_fallback")
    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.get_store")
    @patch("aggre.workflows.transcription.read_bronze_or_none", return_value=None)
    @patch("aggre.workflows.transcription.download_audio")
    def test_transcription_all_backends_fail_propagates(
        self,
        mock_download,
        mock_read_or_none,
        mock_get_store,
        mock_write,
        mock_transcribe,
        engine,
        tmp_path,
    ):
        """All transcription backends failing propagates for Hatchet retry."""
        content_id = _seed_youtube(engine, external_id="terr01")
        config = make_config()

        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio")
        mock_store = MagicMock()
        mock_store.local_path.return_value = audio_file
        mock_get_store.return_value = mock_store
        mock_download.return_value = audio_file

        mock_transcribe.side_effect = AllTranscribersFailedError("All 1 transcription backends failed")

        with pytest.raises(AllTranscribersFailedError):
            transcribe_one(engine, config, content_id)
```

Update `test_empty_whisper_endpoints_raises` to test the new guard clause:
```python
    def test_no_backend_configured_raises(self, engine):
        """When neither whisper_endpoints nor modal_app_name is set, raises RuntimeError."""
        content_id = _seed_youtube(engine, external_id="nourl01")
        config = make_config(whisper_endpoints="", modal_app_name="")

        with pytest.raises(RuntimeError, match="No transcription backend configured"):
            transcribe_one(engine, config, content_id)
```

- [ ] **Step 4: Run all transcription tests**

Run: `uv run pytest tests/workflows/test_transcription.py tests/test_transcriber.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full test suite**

Run: `make test-e2e`
Expected: all tests PASS, no regressions

- [ ] **Step 6: Run lint**

Run: `make lint`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/aggre/workflows/transcription.py tests/workflows/test_transcription.py
git commit -m "feat(transcription): wire transcriber abstraction into workflow with fallback"
```

---

### Task 7: Final cleanup and verification

**Files:**
- No new files

- [ ] **Step 1: Run full test suite with coverage**

Run: `make test-e2e`
Expected: all tests pass

- [ ] **Step 2: Check diff coverage**

Run: `make coverage-diff`
Expected: >= 95% coverage on changed lines

- [ ] **Step 3: Run lint**

Run: `make lint`
Expected: clean

- [ ] **Step 4: Verify the guard clause works with modal-only config**

Add a quick test in `tests/workflows/test_transcription.py` if not already covered:
```python
    @patch("aggre.workflows.transcription.read_bronze_or_none")
    def test_modal_only_config_does_not_raise(self, mock_read_or_none, engine):
        """When only modal_app_name is set (no whisper), guard clause passes."""
        content_id = _seed_youtube(engine, external_id="modal01")
        config = make_config(whisper_endpoints="", modal_app_name="aggre-transcription")

        cached_data = json.dumps({"transcript": "Modal transcript", "language": "en"})
        mock_read_or_none.return_value = cached_data

        result = transcribe_one(engine, config, content_id)
        assert result.status == "cached"
```

- [ ] **Step 5: Final commit if any changes**

```bash
git add -A
git commit -m "test(transcription): add modal-only config test, verify coverage"
```
