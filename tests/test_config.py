"""Tests for YAML config loading and AppConfig defaults."""

from __future__ import annotations

import os

import pytest
import yaml

from aggre.config import AppConfig, load_config
from aggre.settings import Settings

pytestmark = pytest.mark.unit


class TestLoadConfig:
    def test_loads_valid_yaml(self, tmp_path, monkeypatch):
        """Write a tmp config.yaml with hackernews sources, verify AppConfig fields."""
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "hackernews": {
                        "fetch_limit": 42,
                        "sources": [{"name": "HN Test"}],
                    },
                }
            )
        )

        cfg = load_config(str(config_file))

        assert cfg.hackernews.fetch_limit == 42
        assert len(cfg.hackernews.sources) == 1
        assert cfg.hackernews.sources[0].name == "HN Test"
        # Other collectors should still have defaults
        assert cfg.reddit.fetch_limit == 100
        assert cfg.youtube.sources == []

    def test_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        """No file -> all collector defaults."""
        monkeypatch.chdir(tmp_path)

        cfg = load_config(str(tmp_path / "nonexistent.yaml"))

        assert cfg.hackernews.fetch_limit == 1000
        assert cfg.reddit.fetch_limit == 100
        assert cfg.youtube.fetch_limit == 10
        assert cfg.lobsters.fetch_limit == 50
        assert cfg.rss.fetch_limit == 50
        assert cfg.huggingface.fetch_limit == 100
        assert cfg.telegram.fetch_limit == 100

    def test_empty_file_returns_defaults(self, tmp_path, monkeypatch):
        """Empty YAML file -> all defaults."""
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        cfg = load_config(str(config_file))

        assert cfg.hackernews.fetch_limit == 1000
        assert cfg.reddit.sources == []
        assert cfg.youtube.sources == []

    def test_strips_settings_from_yaml(self, tmp_path, monkeypatch):
        """Settings block in YAML is ignored (env vars only)."""
        monkeypatch.chdir(tmp_path)
        for key in list(os.environ):
            if key.startswith("AGGRE_"):
                monkeypatch.delenv(key, raising=False)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "settings": {
                        "database_url": "postgresql://yaml-should-be-ignored/db",
                    },
                    "hackernews": {"fetch_limit": 7},
                }
            )
        )

        cfg = load_config(str(config_file))

        # The YAML settings block must be stripped; Settings uses env vars / defaults
        assert cfg.settings.database_url != "postgresql://yaml-should-be-ignored/db"
        assert cfg.settings.database_url == "postgresql+psycopg://localhost/aggre"
        # The rest of the config should still load
        assert cfg.hackernews.fetch_limit == 7

    def test_partial_config_fills_defaults(self, tmp_path, monkeypatch):
        """Only hackernews in YAML -> rest default."""
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "hackernews": {"fetch_limit": 55},
                }
            )
        )

        cfg = load_config(str(config_file))

        assert cfg.hackernews.fetch_limit == 55
        # All other collectors should be at defaults
        assert cfg.reddit.fetch_limit == 100
        assert cfg.youtube.fetch_limit == 10
        assert cfg.lobsters.fetch_limit == 50
        assert cfg.rss.fetch_limit == 50
        assert cfg.huggingface.fetch_limit == 100
        assert cfg.telegram.fetch_limit == 100

    def test_invalid_yaml_raises(self, tmp_path, monkeypatch):
        """Malformed YAML -> yaml.YAMLError."""
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "config.yaml"
        config_file.write_text("{{invalid: yaml: [unterminated")

        with pytest.raises(yaml.YAMLError):
            load_config(str(config_file))


class TestAppConfig:
    def test_all_collectors_have_defaults(self):
        """AppConfig() with no args -> valid instance with all collector defaults."""
        cfg = AppConfig()

        assert cfg.youtube.sources == []
        assert cfg.reddit.sources == []
        assert cfg.hackernews.sources == []
        assert cfg.lobsters.sources == []
        assert cfg.rss.sources == []
        assert cfg.huggingface.sources == []
        assert cfg.telegram.sources == []
        # Settings field should be present (default is evaluated at class definition
        # time and may reflect the host .env, so we only check it's a Settings instance)
        assert isinstance(cfg.settings, Settings)
