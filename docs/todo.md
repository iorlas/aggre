# S3/Garage Remote Performance — Review Findings

> **Note:** Path references below use pre-migration `dagster_defs/` paths. Business logic now lives in `src/aggre/workflows/`.

## Critical (Must Fix Before Remote Deployment)

- [x] **C1: Reprocess job broken on S3** — `src/aggre/dagster_defs/reprocess/job.py` uses `Path.glob()` to scan bronze directory. Completely non-functional with S3 backend (silently returns 0). Fix: add `list_keys(prefix)` to `BronzeStore`, implement with `list_objects_v2` for S3, rewrite reprocess to use it.

- [x] **C2: Transcription audio not persisted to S3** — `src/aggre/dagster_defs/transcription/job.py:83-127`. Audio downloaded by yt_dlp only saved to local temp dir, never uploaded to S3. Lost on cleanup, must re-download from YouTube. Fix: add `write_bytes()`/`read_bytes()` to `BronzeStore`, upload audio after download, check S3 before re-downloading.

## Important (Should Fix Soon)

- [x] **I1: Double round-trip in bronze_http.py** — `src/aggre/utils/bronze_http.py:62-63`. `bronze_exists()` (HEAD) + `read_bronze()` (GET) = 2 requests per cache hit. Fix: add `read_or_none()` to `BronzeStore`.

- [x] **I2: Double round-trip in transcription cache** — `src/aggre/dagster_defs/transcription/job.py:74-77`. Same exists+read pattern as I1. Fix: use `read_or_none()`.

- [x] **I3: No boto3 timeouts/retries** — `src/aggre/utils/bronze.py:61-73`. Default boto3 config, no timeouts. A hung Garage connection blocks thread indefinitely. Fix: add `botocore.config.Config` with `connect_timeout=5`, `read_timeout=30`, `retries={"max_attempts": 3, "mode": "adaptive"}`, `max_pool_connections=20`.

- [x] **I4: Thread-unsafe get_store() singleton** — `src/aggre/utils/bronze.py:106-125`. Check-then-act without lock. Concurrent threads in webpage pipeline can race. Fix: `threading.Lock` with double-checked locking.

- [ ] **I5: Redundant bronze writes every collection run** — All collectors via `BaseCollector._write_bronze()` in `src/aggre/collectors/base.py:79-81`. Every item written to S3 every run, even if unchanged. ~4,320 unnecessary PUTs/day for HN alone. Fix: only write for genuinely new items (use DB upsert result).

## Minor (Nice to Have)

- [x] **M1: BronzeStore protocol text-only** — `src/aggre/utils/bronze.py:20-32`. `read()`/`write()` only support `str`. Audio is binary. Fix: add `read_bytes()`/`write_bytes()`.

- [ ] **M2: `bronze_path()` misleading for S3** — `src/aggre/utils/bronze.py:160-182`. Returns local path that doesn't exist on S3 backend. Fix: deprecate or assert on S3 backend.

- [ ] **M3: No S3 connection health check at startup** — If Tailscale/Garage down, first S3 op fails with opaque error. Fix: add health check in Dagster resource init.
