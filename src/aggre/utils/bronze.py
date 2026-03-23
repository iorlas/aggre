"""Bronze storage — immutable raw data layer with pluggable backends."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Kept for backward compat — callers import this as their default parameter.
DEFAULT_BRONZE_ROOT = Path("data/bronze")


class BronzeS3Error(Exception):
    """S3 operation failed with non-retriable error. Wraps botocore ClientError with context."""

    def __init__(self, operation: str, bucket: str, key: str, code: str, message: str) -> None:  # pragma: no cover — only non-404 S3 errors
        super().__init__(f"S3 {operation} failed: bucket={bucket} key={key} code={code} message={message}")


# -- Store protocol and implementations ---------------------------------------


class BronzeStore(Protocol):
    """Backend-agnostic bronze storage."""

    def exists(self, key: str) -> bool: ...
    def read(self, key: str) -> str: ...
    def read_or_none(self, key: str) -> str | None: ...
    def write(self, key: str, data: str) -> None: ...
    def read_bytes(self, key: str) -> bytes: ...
    def write_bytes(self, key: str, data: bytes) -> None: ...
    def list_keys(self, prefix: str) -> list[str]: ...

    def local_path(self, key: str) -> Path | None:
        """Return local filesystem path if available (filesystem only).

        Returns None for remote backends — caller must handle.
        """
        ...


class FilesystemStore:
    """Bronze storage backed by local filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def exists(self, key: str) -> bool:
        return (self._root / key).exists()

    def read(self, key: str) -> str:
        path = self._root / key
        if not path.exists():
            raise FileNotFoundError(f"Bronze artifact not found: {path}")
        return path.read_text()

    def read_or_none(self, key: str) -> str | None:
        path = self._root / key
        if not path.exists():
            return None
        return path.read_text()

    def write(self, key: str, data: str) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data)
        tmp.rename(path)

    def read_bytes(self, key: str) -> bytes:
        path = self._root / key
        if not path.exists():
            raise FileNotFoundError(f"Bronze artifact not found: {path}")
        return path.read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.rename(path)

    def list_keys(self, prefix: str) -> list[str]:
        base = self._root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self._root)) for p in base.rglob("*") if p.is_file())

    def local_path(self, key: str) -> Path | None:
        return self._root / key


class S3Store:
    """Bronze storage backed by S3-compatible object storage (Garage, MinIO, AWS)."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, region: str) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                connect_timeout=10,
                read_timeout=300,
                retries={"max_attempts": 3, "mode": "adaptive"},
                max_pool_connections=20,
            ),
        )

    def _raise_s3_error(self, operation: str, key: str, e: Exception) -> None:  # pragma: no cover — only non-404 S3 errors
        """Re-raise botocore ClientError as BronzeS3Error with full context."""
        code = e.response["Error"]["Code"]  # type: ignore[union-attr]
        msg = e.response["Error"].get("Message", "")  # type: ignore[union-attr]
        raise BronzeS3Error(operation, self._bucket, key, code, msg) from e

    def exists(self, key: str) -> bool:
        import botocore.exceptions

        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            self._raise_s3_error("HeadObject", key, e)  # pragma: no cover

    def read(self, key: str) -> str:
        import botocore.exceptions

        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read().decode("utf-8")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise FileNotFoundError(f"Bronze artifact not found: {key}") from e
            self._raise_s3_error("GetObject", key, e)  # pragma: no cover

    def read_or_none(self, key: str) -> str | None:
        import botocore.exceptions

        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read().decode("utf-8")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            self._raise_s3_error("GetObject", key, e)  # pragma: no cover

    def write(self, key: str, data: str) -> None:
        import botocore.exceptions

        try:
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=data.encode("utf-8"))
        except botocore.exceptions.ClientError as e:  # pragma: no cover
            self._raise_s3_error("PutObject", key, e)  # pragma: no cover

    def read_bytes(self, key: str) -> bytes:
        import botocore.exceptions

        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise FileNotFoundError(f"Bronze artifact not found: {key}") from e
            self._raise_s3_error("GetObject", key, e)  # pragma: no cover

    def write_bytes(self, key: str, data: bytes) -> None:
        import botocore.exceptions

        try:
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=data)
        except botocore.exceptions.ClientError as e:  # pragma: no cover
            self._raise_s3_error("PutObject", key, e)  # pragma: no cover

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        continuation_token = None
        while True:
            kwargs: dict[str, str] = {"Bucket": self._bucket, "Prefix": prefix}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            resp = self._s3.list_objects_v2(**kwargs)
            keys.extend(obj["Key"] for obj in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            continuation_token = resp["NextContinuationToken"]
        return keys

    def local_path(self, key: str) -> Path | None:  # noqa: ARG002 — required by BronzeStore interface
        return None


# -- Store initialization -----------------------------------------------------

_store: BronzeStore | None = None
_store_lock = threading.Lock()


def get_store() -> BronzeStore:
    """Return the configured bronze store (lazily initialized from settings)."""
    global _store  # noqa: PLW0603
    if _store is None:
        with _store_lock:
            if _store is None:  # pragma: no branch — double-checked locking; race path untestable without thread contention
                from aggre.settings import Settings

                settings = Settings()
                if settings.bronze_backend == "s3":
                    _store = S3Store(
                        endpoint=settings.bronze_s3_endpoint,
                        bucket=settings.bronze_s3_bucket,
                        access_key=settings.bronze_s3_access_key,
                        secret_key=settings.bronze_s3_secret_key,
                        region=settings.bronze_s3_region,
                    )
                else:
                    _store = FilesystemStore(Path(settings.bronze_root))
    return _store


def _reset_store() -> None:
    """Reset the module-level store instance (for testing)."""
    global _store  # noqa: PLW0603
    with _store_lock:
        _store = None


# -- Key helpers ---------------------------------------------------------------


def _make_key(source_type: str, external_id: str, artifact_type: str, ext: str) -> str:
    return f"{source_type}/{external_id}/{artifact_type}.{ext}"


def url_hash(url: str) -> str:
    """Create a stable hash of a URL for request-keyed bronze storage."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# -- Backward-compatible module-level functions --------------------------------
#
# When bronze_root is the default (DEFAULT_BRONZE_ROOT), these delegate to the
# configured store (filesystem or S3). When an explicit bronze_root is passed
# (tests use tmp_path), they use a temporary FilesystemStore with that root.


def _store_for(bronze_root: Path) -> BronzeStore:
    """Return the appropriate store for a given bronze_root parameter."""
    if bronze_root is not DEFAULT_BRONZE_ROOT:
        return FilesystemStore(bronze_root)
    return get_store()


def bronze_path(
    source_type: str,
    external_id: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> Path:
    """Return the filesystem path for a bronze artifact.

    For filesystem backend, returns the actual path.
    For S3 backend with default bronze_root, returns a path under settings.bronze_root
    (useful only as a temp location — actual data lives in S3).
    """
    store = _store_for(bronze_root)
    key = _make_key(source_type, external_id, artifact_type, ext)
    path = store.local_path(key)
    if path is not None:
        return path
    # S3 backend — return a path relative to the configured root for temp use  # pragma: no cover — S3 fallback
    from aggre.settings import Settings  # pragma: no cover

    return Path(Settings().bronze_root) / key  # pragma: no cover


def bronze_exists(
    source_type: str,
    external_id: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> bool:
    """Check if a bronze artifact exists."""
    return _store_for(bronze_root).exists(_make_key(source_type, external_id, artifact_type, ext))


def read_bronze(
    source_type: str,
    external_id: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> str:
    """Read a bronze artifact as text. Raises FileNotFoundError if missing."""
    return _store_for(bronze_root).read(_make_key(source_type, external_id, artifact_type, ext))


def read_bronze_or_none(
    source_type: str,
    external_id: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> str | None:
    """Read a bronze artifact as text. Returns None if missing."""
    return _store_for(bronze_root).read_or_none(_make_key(source_type, external_id, artifact_type, ext))


def read_bronze_json(
    source_type: str,
    external_id: str,
    artifact_type: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> object:
    """Read a bronze JSON artifact. Returns parsed JSON."""
    text = read_bronze(source_type, external_id, artifact_type, "json", bronze_root=bronze_root)
    return json.loads(text)


def write_bronze(
    source_type: str,
    external_id: str,
    artifact_type: str,
    data: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> Path:
    """Write a bronze artifact. Returns the path written to."""
    key = _make_key(source_type, external_id, artifact_type, ext)
    store = _store_for(bronze_root)
    store.write(key, data)
    path = store.local_path(key)
    if path is not None:
        return path
    return Path(key)


def write_bronze_json(
    source_type: str,
    external_id: str,
    data: object,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> Path:
    """Write a JSON object to bronze as raw.json."""
    return write_bronze(
        source_type,
        external_id,
        "raw",
        json.dumps(data, ensure_ascii=False),
        "json",
        bronze_root=bronze_root,
    )


def write_bronze_by_url(
    source_type: str,
    url: str,
    artifact_type: str,
    data: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> Path:
    """Write a request-keyed bronze artifact using URL hash as directory name."""
    hashed = url_hash(url)
    return write_bronze(source_type, hashed, artifact_type, data, ext, bronze_root=bronze_root)


def read_bronze_by_url(
    source_type: str,
    url: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> str:
    """Read a request-keyed bronze artifact."""
    hashed = url_hash(url)
    return read_bronze(source_type, hashed, artifact_type, ext, bronze_root=bronze_root)


def read_bronze_or_none_by_url(
    source_type: str,
    url: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> str | None:
    """Read a request-keyed bronze artifact. Returns None if missing."""
    hashed = url_hash(url)
    return read_bronze_or_none(source_type, hashed, artifact_type, ext, bronze_root=bronze_root)


def bronze_exists_by_url(
    source_type: str,
    url: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> bool:
    """Check if a request-keyed bronze artifact exists."""
    hashed = url_hash(url)
    return bronze_exists(source_type, hashed, artifact_type, ext, bronze_root=bronze_root)
