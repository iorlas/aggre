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
