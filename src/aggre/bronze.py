"""Bronze filesystem storage — immutable raw data layer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Default bronze root — overridden by config in production
DEFAULT_BRONZE_ROOT = Path("data/bronze")


def bronze_path(
    source_type: str,
    external_id: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> Path:
    """Return the filesystem path for a bronze artifact.

    Path: {bronze_root}/{source_type}/{external_id}/{artifact_type}.{ext}
    """
    return bronze_root / source_type / external_id / f"{artifact_type}.{ext}"


def bronze_exists(
    source_type: str,
    external_id: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> bool:
    """Check if a bronze artifact exists."""
    return bronze_path(source_type, external_id, artifact_type, ext, bronze_root=bronze_root).exists()


def read_bronze(
    source_type: str,
    external_id: str,
    artifact_type: str,
    ext: str,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> str:
    """Read a bronze artifact as text. Raises FileNotFoundError if missing."""
    return bronze_path(source_type, external_id, artifact_type, ext, bronze_root=bronze_root).read_text()


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
    """Write a bronze artifact. Creates directories as needed.

    Atomic write via .tmp rename to prevent partial files.
    Returns the path written to.
    """
    path = bronze_path(source_type, external_id, artifact_type, ext, bronze_root=bronze_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(data)
    tmp_path.rename(path)
    return path


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


def url_hash(url: str) -> str:
    """Create a stable hash of a URL for request-keyed bronze storage."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


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
