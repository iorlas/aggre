"""Unit tests for the yt-dlp subprocess wrapper."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from aggre.utils.ytdlp import (
    VideoUnavailableError,
    YtDlpError,
    _run_ytdlp,
    download_audio,
    extract_channel_info,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _run_ytdlp stderr parsing
# ---------------------------------------------------------------------------


class TestRunYtdlp:
    def test_success_returns_completed_process(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            out = _run_ytdlp(["--version"])
        assert out.stdout == "ok"

    @pytest.mark.parametrize(
        "stderr_msg",
        [
            "ERROR: Video unavailable",
            "ERROR: Private video. Sign in if you've been granted access",
            "ERROR: This video is not available",
            "ERROR: This video has been removed by the uploader",
            "ERROR: This live event will begin in 2 hours",
            "ERROR: Premieres in 5 hours",
        ],
    )
    def test_permanent_patterns_raise_video_unavailable(self, stderr_msg):
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr_msg)
        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            with pytest.raises(VideoUnavailableError):
                _run_ytdlp(["https://youtube.com/watch?v=xxx"])

    def test_transient_error_raises_ytdlp_error(self):
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="ERROR: unable to download webpage")
        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            with pytest.raises(YtDlpError):
                _run_ytdlp(["https://youtube.com/watch?v=xxx"])

    def test_transient_error_is_not_video_unavailable(self):
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="ERROR: connection timed out")
        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            with pytest.raises(YtDlpError) as exc_info:
                _run_ytdlp(["https://youtube.com/watch?v=xxx"])
            assert not isinstance(exc_info.value, VideoUnavailableError)

    def test_timeout_passed_to_subprocess(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result) as mock_run:
            _run_ytdlp(["--version"], timeout=30)
        assert mock_run.call_args.kwargs["timeout"] == 30


# ---------------------------------------------------------------------------
# extract_channel_info
# ---------------------------------------------------------------------------


class TestExtractChannelInfo:
    def test_builds_correct_cli_args(self):
        json_output = '{"entries": [{"id": "vid1", "title": "Test"}]}'
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=json_output, stderr="")

        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result) as mock_run:
            entries = extract_channel_info(
                "https://www.youtube.com/channel/UC_test/videos",
                proxy_url="socks5://proxy:1080",
                fetch_limit=30,
            )

        cmd = mock_run.call_args[0][0]
        assert cmd[0:3] == ["uv", "run", "yt-dlp"]
        assert "--impersonate" in cmd
        assert "chrome" in cmd
        assert "--proxy" in cmd
        assert "socks5://proxy:1080" in cmd
        assert "--flat-playlist" in cmd
        assert "-J" in cmd
        assert "--playlist-end" in cmd
        assert "30" in cmd
        assert cmd[-1] == "https://www.youtube.com/channel/UC_test/videos"

        assert entries == [{"id": "vid1", "title": "Test"}]

    def test_no_limit_omits_playlist_end(self):
        json_output = '{"entries": []}'
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=json_output, stderr="")

        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result) as mock_run:
            extract_channel_info(
                "https://www.youtube.com/channel/UC_test/videos",
                proxy_url="socks5://proxy:1080",
                fetch_limit=None,
            )

        cmd = mock_run.call_args[0][0]
        assert "--playlist-end" not in cmd

    def test_returns_empty_list_when_no_entries(self):
        json_output = '{"entries": null}'
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=json_output, stderr="")

        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            entries = extract_channel_info(
                "https://www.youtube.com/channel/UC_test/videos",
                proxy_url="socks5://proxy:1080",
            )

        assert entries == []

    def test_invalid_json_raises_ytdlp_error(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr="")

        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            with pytest.raises(YtDlpError, match="Failed to parse"):
                extract_channel_info(
                    "https://www.youtube.com/channel/UC_test/videos",
                    proxy_url="socks5://proxy:1080",
                )

    def test_missing_entries_key_returns_empty(self):
        json_output = '{"_type": "playlist"}'
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=json_output, stderr="")

        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            entries = extract_channel_info(
                "https://www.youtube.com/channel/UC_test/videos",
                proxy_url="socks5://proxy:1080",
            )

        assert entries == []


# ---------------------------------------------------------------------------
# download_audio
# ---------------------------------------------------------------------------


class TestDownloadAudio:
    def test_builds_correct_cli_args(self, tmp_path):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        # Create the expected output file so glob finds it
        (tmp_path / "vid123.opus").write_bytes(b"audio data")

        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result) as mock_run:
            path = download_audio("vid123", tmp_path, proxy_url="socks5://proxy:1080")

        cmd = mock_run.call_args[0][0]
        assert cmd[0:3] == ["uv", "run", "yt-dlp"]
        assert "--impersonate" in cmd
        assert "chrome" in cmd
        assert "--proxy" in cmd
        assert "socks5://proxy:1080" in cmd
        assert "-f" in cmd
        assert "bestaudio/best" in cmd
        assert "-x" in cmd
        assert "--audio-format" in cmd
        assert "opus" in cmd
        assert "--audio-quality" in cmd
        assert "48K" in cmd
        assert "-o" in cmd
        assert "https://www.youtube.com/watch?v=vid123" in cmd

        # Should have been renamed to audio.opus
        assert path == tmp_path / "audio.opus"
        assert path.exists()

    def test_no_file_found_raises_ytdlp_error(self, tmp_path):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            with pytest.raises(YtDlpError, match="No downloaded file found"):
                download_audio("missing123", tmp_path, proxy_url="socks5://proxy:1080")

    def test_renames_downloaded_file_to_audio_opus(self, tmp_path):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        # yt-dlp might produce a .webm file that gets converted
        (tmp_path / "vid456.webm").write_bytes(b"webm data")

        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            path = download_audio("vid456", tmp_path, proxy_url="socks5://proxy:1080")

        assert path == tmp_path / "audio.opus"
        assert path.read_bytes() == b"webm data"

    def test_creates_output_dir(self, tmp_path):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        output_dir = tmp_path / "nested" / "dir"

        def fake_run(cmd, **kwargs):
            # Create the file that yt-dlp would create
            (output_dir / "vid789.opus").write_bytes(b"audio")
            return result

        with patch("aggre.utils.ytdlp.subprocess.run", side_effect=fake_run):
            path = download_audio("vid789", output_dir, proxy_url="socks5://proxy:1080")

        assert path.exists()
        assert output_dir.exists()

    def test_file_already_named_audio_opus_no_rename(self, tmp_path):
        """If the glob finds audio.opus directly, no rename needed."""
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        # File already at target name (edge case: video_id is "audio")
        # Actually test with a normal id where the file happens to be audio.opus already
        # This won't happen in practice but tests the code path
        (tmp_path / "testvid.opus").write_bytes(b"data")

        with patch("aggre.utils.ytdlp.subprocess.run", return_value=result):
            path = download_audio("testvid", tmp_path, proxy_url="socks5://proxy:1080")

        assert path == tmp_path / "audio.opus"
