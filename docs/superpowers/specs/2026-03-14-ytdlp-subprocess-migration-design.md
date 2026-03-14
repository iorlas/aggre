# yt-dlp Subprocess Migration

## Problem

yt-dlp's Python API fails when called from Hatchet worker threads:
- `curl-cffi` + `impersonate: "chrome"` throws `AssertionError` on `ImpersonateTarget` when run inside threads ([yt-dlp #15073](https://github.com/yt-dlp/yt-dlp/issues/15073))
- yt-dlp 2026.x requires a JavaScript runtime (Deno/QuickJS) for YouTube URL decryption — none is installed in the Docker image
- Both issues make all YouTube downloads and metadata extraction fail in production

## Solution

Replace yt-dlp Python API calls with subprocess invocations of the yt-dlp CLI. Add Deno to the Docker image. Encapsulate all yt-dlp interaction in a single utility module.

## Design

### New module: `src/aggre/utils/ytdlp.py`

Two exception classes:

```python
class YtDlpError(Exception):
    """Transient yt-dlp failure — Hatchet will retry with backoff."""

class VideoUnavailable(YtDlpError):
    """Permanent — video deleted, private, region-blocked. Safe to skip."""
```

Two public functions:

```python
def extract_channel_info(
    channel_url: str,
    *,
    proxy_url: str,
    fetch_limit: int | None = 30,
) -> list[dict]:
    """Fetch video metadata from a YouTube channel/playlist.
    Returns list of video entry dicts (id, title, duration, etc.).
    Uses --flat-playlist -J to get metadata without extracting each video.
    Pass fetch_limit=None for backfill (no limit).
    Raises VideoUnavailable or YtDlpError."""

def download_audio(video_id: str, output_dir: Path, *, proxy_url: str) -> Path:
    """Download audio and convert to opus via ffmpeg.
    Internally handles yt-dlp's output naming (glob + rename) — the returned
    Path is the final canonical file regardless of what extension yt-dlp produces.
    Raises VideoUnavailable or YtDlpError."""
```

Internal shared runner:

```python
def _run_ytdlp(args: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run yt-dlp CLI via subprocess. All calls go through here.
    Parses stderr for known error patterns.
    Raises VideoUnavailable for permanent failures, YtDlpError for transient."""
```

### CLI flags

**Always present (both functions):**

| Flag | Purpose |
|------|---------|
| `--impersonate chrome` | TLS fingerprint stealth |
| `--proxy {proxy_url}` | Route through residential SOCKS5 proxy |
| `--source-address 0.0.0.0` | Force IPv4 binding for SOCKS5 compatibility |
| `--quiet --no-warnings` | Clean stderr for error parsing |

**`extract_channel_info` additional flags:**

| Flag | Purpose |
|------|---------|
| `--flat-playlist` | Metadata only, don't extract each video |
| `-J` | Output full playlist JSON to stdout |
| `--ignore-errors` | Skip unavailable entries, don't abort entire channel |
| `--playlist-end {N}` | Limit entries (omitted when `fetch_limit=None`) |

**`download_audio` additional flags:**

| Flag | Purpose |
|------|---------|
| `-f bestaudio/best` | Best audio quality |
| `-x --audio-format opus --audio-quality 48K` | Convert to opus |
| `--no-playlist` | Defensive — don't expand playlist context |
| `-o {output_template}` | Output path |

Deno is the default JS runtime — no flag needed. yt-dlp auto-discovers it from PATH.

### Subprocess timeouts

| Function | Timeout | Rationale |
|----------|---------|-----------|
| `extract_channel_info` | 120s | Metadata extraction is lightweight |
| `download_audio` | 600s (10 min) | Long videos need download + ffmpeg conversion |

Hatchet task has 30min execution timeout — subprocess timeout is a safety net within that budget.

### Stderr error classification

| stderr pattern | Exception | Retryable |
|---|---|---|
| `Video unavailable` | `VideoUnavailable(message)` | No — skip |
| `Private video` | `VideoUnavailable(message)` | No — skip |
| `This video is not available` | `VideoUnavailable(message)` | No — skip |
| `This video has been removed` | `VideoUnavailable(message)` | No — skip |
| `Sign in to confirm` | `YtDlpError(message)` | Yes — bot detection |
| `HTTP Error 429` | `YtDlpError(message)` | Yes — rate limit |
| Any other non-zero exit | `YtDlpError(stderr)` | Yes — unknown |

Exception messages always carry the original yt-dlp stderr text for Hatchet UI visibility.

**Fragility note:** These patterns are not part of yt-dlp's stable API and may change between versions. Keep the pattern list updated when upgrading yt-dlp. Unknown errors always propagate the full stderr, so new patterns degrade to retryable rather than silent failure.

### Caller changes

**YouTube collector** (`src/aggre/collectors/youtube/collector.py`):
- Replace `yt_dlp.YoutubeDL` + `extract_info(url, download=False)` with `extract_channel_info()`
- Remove `import yt_dlp`
- Catch both `VideoUnavailable` and `YtDlpError` per-channel → log and continue to next source (matches current graceful-degradation behavior where the collector iterates over multiple channels)
- Only propagate if the exception occurs outside the per-channel loop

**Transcription workflow** (`src/aggre/workflows/transcription.py`):
- Replace `yt_dlp.YoutubeDL` + `ydl.download()` with `download_audio()`
- Remove `import yt_dlp`
- Catch `VideoUnavailable` → return `StepOutput(status="skipped", reason="video_unavailable", url=url, detail={"message": str(e)})`
- Let `YtDlpError` propagate → Hatchet retries with backoff (7 retries, backoff_factor=4)

**StepOutput in Hatchet UI** for permanent skips:
```json
{
  "status": "skipped",
  "reason": "video_unavailable",
  "url": "https://youtube.com/watch?v=abc123",
  "detail": {"message": "Private video. Sign in if you've been granted access"}
}
```

### File/S3 strategy

No change. `download_audio()` writes the opus file to the same `tmpfs` path (internally handling yt-dlp's output naming via glob + rename). The existing caller code reads the returned path, uploads to S3/garage, sends to whisper. Only the download invocation changes.

### Dockerfile changes

Add Deno (~39MB compressed, ~100MB installed) to the existing image. Multi-arch support via `TARGETARCH` build arg:

```dockerfile
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl unzip \
    && DENO_ARCH=$(if [ "$TARGETARCH" = "arm64" ]; then echo "aarch64"; else echo "x86_64"; fi) \
    && curl -fsSL "https://dl.deno.land/release/v2.3.5/deno-${DENO_ARCH}-unknown-linux-gnu.zip" -o /tmp/deno.zip \
    && unzip /tmp/deno.zip -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && rm -rf /var/lib/apt/lists/*
```

Deno is the yt-dlp recommended default JS runtime. Auto-discovered from PATH — no yt-dlp config needed.

### Testing strategy

**Unit tests for `ytdlp.py`:**
- Mock `subprocess.run` → verify CLI args include `--impersonate chrome`, `--proxy`, `--source-address 0.0.0.0`, correct per-function flags
- Mock stderr with known error patterns → verify correct exception type and message preserved
- Mock stderr with unknown error → verify `YtDlpError` with full stderr text
- Mock successful JSON output for `extract_channel_info` → verify parsed entry list
- Mock successful file output for `download_audio` → verify returned Path
- Verify `--ignore-errors` present for channel extraction
- Verify `--no-playlist` present for audio download
- Verify `--playlist-end` omitted when `fetch_limit=None`

**Updated tests for callers:**
- YouTube collector tests: mock `ytdlp.extract_channel_info` instead of `yt_dlp.YoutubeDL`
- Transcription tests: mock `ytdlp.download_audio` instead of `yt_dlp.YoutubeDL`
- Verify `VideoUnavailable` → `StepOutput(status="skipped", reason="video_unavailable", detail={"message": ...})`
- Verify collector catches both `YtDlpError` and `VideoUnavailable` per-channel and continues
- Verify transcription lets `YtDlpError` propagate

**Manual production test:**
- Deploy → trigger one transcription → verify audio downloads through proxy with impersonate + Deno
- Check Hatchet UI for structured StepOutput

## Files to modify

| File | Change |
|------|--------|
| `src/aggre/utils/ytdlp.py` | New — wrapper module |
| `src/aggre/collectors/youtube/collector.py` | Replace yt_dlp API with wrapper |
| `src/aggre/workflows/transcription.py` | Replace yt_dlp API with wrapper |
| `Dockerfile` | Add Deno installation (multi-arch) |
| `tests/utils/test_ytdlp.py` | New — unit tests for wrapper |
| `tests/collectors/test_youtube.py` | Update mocks |
| `tests/workflows/test_transcription.py` | Update mocks |
| `tests/test_s3_integration.py` | Update mocks |
| `scripts/benchmark_whisper.py` | Update to use wrapper (low priority, not production) |

## Out of scope

- Dropping `curl-cffi` dependency — still used by httpx[socks] for other HTTP clients
- yt-dlp as a sidecar service — over-engineered, no production-ready option exists
- QuickJS — no reliable aarch64 binary in Debian repos, performance warnings on older versions
