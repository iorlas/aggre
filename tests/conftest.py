"""Shared test fixtures for PostgreSQL-based tests."""

from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import sqlalchemy as sa

from aggre.db import Base
from aggre.utils.db import get_engine


@pytest.fixture(scope="session")
def engine():
    """Session-scoped PostgreSQL test engine."""
    url = os.environ.get("AGGRE_TEST_DATABASE_URL", "postgresql+psycopg://aggre:aggre@localhost/aggre_test")
    eng = get_engine(url)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_tables(request):
    """Truncate all tables before each test.

    Skipped for contract tests (they don't use a database).
    """
    if "contract" in {m.name for m in request.node.iter_markers()}:
        yield
        return
    engine = request.getfixturevalue("engine")
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(sa.text(f"TRUNCATE TABLE {table.name} CASCADE"))
    yield


@pytest.fixture(scope="module")
def vcr_config():
    """VCR.py configuration for contract tests.

    Record mode is controlled by CLI: ``--record-mode=once`` to record,
    default ``none`` to replay in CI.
    """
    return {}


@pytest.fixture()
def mock_http():
    """Transport-layer httpx mocking via respx."""
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as rsps:
        yield rsps


@pytest.fixture()
def tmp_bronze(tmp_path):
    """Temporary bronze directory for filesystem tests."""
    bronze = tmp_path / "bronze"
    bronze.mkdir()
    return bronze


@contextmanager
def dummy_http_client(**kwargs):
    """A mock HTTP client context manager that returns 200 for any GET."""
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = ""
    resp.raise_for_status = MagicMock()
    client.get.return_value = resp
    yield client
