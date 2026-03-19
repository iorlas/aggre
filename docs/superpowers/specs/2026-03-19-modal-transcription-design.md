# Modal Serverless Transcription

## Problem

Transcription currently runs on self-hosted Whisper servers (MacBook + zep) via HTTP. This is slow, unreliable (machines sleep, go offline), and ties up local resources. We need a faster, more reliable primary transcription backend while keeping self-hosted as a fallback.

## Solution

Add Modal as a serverless GPU transcription backend with automatic fallback to existing Whisper servers when Modal credits are exhausted.

## POC Results (validated 2026-03-19)

| Test | File size | Time | Notes |
|------|-----------|------|-------|
| Russian, short | 1.3 MB | 32.1s | Cold start (~26s overhead) |
| Russian, short (repeat) | 1.3 MB | 5.5s | Warm container |
| English, medium | 7.9 MB | 27.0s | Warm, real transcription time |

Modal Starter plan: $30/month free credits (~27 hrs A10G time, ~800+ hrs of audio).

## Data Contract

```python
@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str
    transcribed_by: str  # e.g. "modal-a10g", "macbook-whisper"

class Transcriber(Protocol):
    def __call__(self, audio: bytes, format_hint: str = "opus") -> TranscriptResult: ...
```

`Transcriber` is a `Protocol` with `__call__` — supports both classes (stateful backends like Modal) and plain functions (stateless backends). **Sync, not async** — the entire transcription call chain (Hatchet task, whisper_client, Modal SDK `.remote()`) is synchronous.

Replaces the existing `whisper_client.TranscriptionResult` (fields: `text`, `language`, `server_name`). The old type is no longer used outside `whisper_client.py` internals.

## Backend Implementations

**ModalTranscriber (class):**
- Calls deployed Modal app via `modal.Cls.from_name(app_name, "Transcriber")`
- Configured with app name from settings
- Raises `QuotaExceededError` on billing/quota errors to trigger fallback

**WhisperTranscriber (class):**
- Wraps existing `whisper_client.py` logic (weighted load balancer, multi-endpoint, fallover)
- No changes to `whisper_client.py` internals — accepts bytes, writes to a temp file, passes path to `transcribe_audio()`
- Maps `whisper_client.TranscriptionResult` to `TranscriptResult`
- Configured via existing `AGGRE_WHISPER_ENDPOINTS` setting
- `format_hint` parameter is ignored (whisper_client sends as `audio/ogg` regardless)

## Fallback

```python
def transcribe_with_fallback(
    transcribers: Sequence[Transcriber],
    audio: bytes,
    format_hint: str = "opus",
) -> TranscriptResult:
```

A sync function that loops through backends in priority order. Falls back on `QuotaExceededError` and connection errors. Does NOT fall back on transcription errors (bad audio fails everywhere).

Backend list is built at worker startup from settings — Modal first (if configured), then Whisper (if endpoints configured). The guard clause in `_transcribe_one()` must be updated to check that at least one backend is configured (Modal or Whisper), replacing the current whisper-only check.

## Error Handling

- `QuotaExceededError` — raised by `ModalTranscriber` on billing/quota errors, triggers fallback
- Connection errors from Modal — also trigger fallback
- Transcription errors (bad audio, model failure) — re-raised, no fallback
- `AllTranscribersFailedError` — raised when every backend is exhausted

## Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `AGGRE_MODAL_APP_NAME` | `""` | Modal app name. Empty = skip Modal backend |
| `AGGRE_WHISPER_ENDPOINTS` | `""` | Existing setting, unchanged |

If both set: Modal primary, Whisper fallback. If only Whisper: no change from today. If only Modal: no fallback.

## File Layout

**New files:**
- `src/aggre/transcriber.py` — Protocol, TranscriptResult, ModalTranscriber, WhisperTranscriber, transcribe_with_fallback
- `src/aggre/modal_apps/transcription.py` — Modal app (already deployed from POC). This is a **deployment artifact only** — never imported at runtime by the main application. The Hatchet worker calls it via `modal.Cls.from_name()` string lookup.

**Modified files:**
- `src/aggre/settings.py` — add `AGGRE_MODAL_APP_NAME`
- `src/aggre/workflows/transcription.py` — swap whisper_client call for transcribe_with_fallback

**Unchanged:**
- `src/aggre/utils/whisper_client.py` — used internally by WhisperTranscriber

## Deployment

- `modal deploy src/aggre/modal_apps/transcription.py` — runs in CI parallel with Hatchet worker deploy
- Idempotent, rolling update, no downtime
- Requires `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` env vars in CI and on shen

## Design Notes

- **Whisper model is hardcoded in the Modal app** (`deepdml/faster-whisper-large-v3-turbo-ct2`). This is intentional — the Modal container image bakes in the model at deploy time. To change the model, update `modal_apps/transcription.py` and redeploy.
- **Memory: audio bytes are held in memory** for the duration of the transcription call. With the existing 500MB size limit and typical opus files (1–50MB), this is acceptable.
- **Bronze cache `transcribed_by`:** not stored in cache. On cache hit, `transcribed_by` is set to `"cache"`. This is acceptable — the field is informational.
- **Rollback:** setting `AGGRE_MODAL_APP_NAME=""` cleanly reverts to whisper-only behavior. No code changes needed.

## Tests

- `transcribe_with_fallback`: fallback on QuotaExceededError, no fallback on transcription error, AllTranscribersFailedError when empty/all fail
- `ModalTranscriber`: maps Modal SDK errors to QuotaExceededError, returns correct TranscriptResult
- `WhisperTranscriber`: writes temp file, delegates to whisper_client, maps TranscriptionResult to TranscriptResult
- Existing `test_whisper_client.py` unchanged
