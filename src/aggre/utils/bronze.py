"""Bronze storage — immutable raw data layer with pluggable backends."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Kept for backward compat — callers import this as their default parameter.
DEFAULT_BRONZE_ROOT = Path("data/bronze")


# -- Store protocol and implementations ---------------------------------------


class BronzeStore(Protocol):
    """Backend-agnostic bronze storage."""

    def exists(self, key: str) -> bool: ...
    def read(self, key: str) -> str: ...
    def write(self, key: str, data: str) -> None: ...

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

    def write(self, key: str, data: str) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data)
        tmp.rename(path)

    def local_path(self, key: str) -> Path | None:
        return self._root / key


class S3Store:
    """Bronze storage backed by S3-compatible object storage (Garage, MinIO, AWS)."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str) -> None:
        import boto3

        self._bucket = bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def exists(self, key: str) -> bool:
        import botocore.exceptions

        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def read(self, key: str) -> str:
        import botocore.exceptions

        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read().decode("utf-8")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise FileNotFoundError(f"Bronze artifact not found: {key}") from e
            raise

    def write(self, key: str, data: str) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data.encode("utf-8"))

    def local_path(self, key: str) -> Path | None:
        return None


# -- Store initialization -----------------------------------------------------

_store: BronzeStore | None = None


def get_store() -> BronzeStore:
    """Return the configured bronze store (lazily initialized from settings)."""
    global _store  # noqa: PLW0603
    if _store is None:
        from aggre.settings import Settings

        settings = Settings()
        if settings.bronze_backend == "s3":
            _store = S3Store(
                endpoint=settings.bronze_s3_endpoint,
                bucket=settings.bronze_s3_bucket,
                access_key=settings.bronze_s3_access_key,
                secret_key=settings.bronze_s3_secret_key,
            )
        else:
            _store = FilesystemStore(Path(settings.bronze_root))
    return _store


def _reset_store() -> None:
    """Reset the module-level store instance (for testing)."""
    global _store  # noqa: PLW0603
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
    # S3 backend — return a path relative to the configured root for temp use
    from aggre.settings import Settings

    return Path(Settings().bronze_root) / key


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
