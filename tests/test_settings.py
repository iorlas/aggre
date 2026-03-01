"""Tests for Settings loaded from environment variables via pydantic-settings."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from aggre.settings import Settings

pytestmark = pytest.mark.unit


def _clear_aggre_env(monkeypatch):
    """Remove all AGGRE_* env vars to isolate Settings from host."""
    for key in list(os.environ):
        if key.startswith("AGGRE_"):
            monkeypatch.delenv(key, raising=False)


class TestSettings:
    def test_defaults_without_env(self, tmp_path, monkeypatch):
        """Settings() -> database_url has default value."""
        monkeypatch.chdir(tmp_path)
        _clear_aggre_env(monkeypatch)

        s = Settings()

        assert s.database_url == "postgresql+psycopg2://localhost/aggre"
        assert s.log_dir == "./data/logs"
        assert s.whisper_model == "large-v3-turbo"
        assert s.telegram_api_id == 0
        assert s.telegram_api_hash == ""

    def test_env_prefix(self, tmp_path, monkeypatch):
        """AGGRE_DATABASE_URL=x -> Settings().database_url == x."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AGGRE_DATABASE_URL", "postgresql+psycopg2://test/mydb")

        s = Settings()

        assert s.database_url == "postgresql+psycopg2://test/mydb"

    def test_ignores_extra_env_vars(self, tmp_path, monkeypatch):
        """AGGRE_UNKNOWN=x -> no error (extra='ignore')."""
        monkeypatch.chdir(tmp_path)
        _clear_aggre_env(monkeypatch)
        monkeypatch.setenv("AGGRE_UNKNOWN", "should_be_ignored")

        s = Settings()

        assert not hasattr(s, "unknown")
        # Should still work with defaults
        assert s.database_url == "postgresql+psycopg2://localhost/aggre"

    def test_telegram_api_id_rejects_str(self, tmp_path, monkeypatch):
        """AGGRE_TELEGRAM_API_ID=abc -> ValidationError (int field)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AGGRE_TELEGRAM_API_ID", "abc")

        with pytest.raises(ValidationError):
            Settings()

    def test_proxy_url_default_empty(self, tmp_path, monkeypatch):
        """Settings().proxy_url == '' by default."""
        monkeypatch.chdir(tmp_path)
        _clear_aggre_env(monkeypatch)

        s = Settings()

        assert s.proxy_url == ""
