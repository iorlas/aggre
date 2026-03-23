"""Tests for workflow data contracts."""

from __future__ import annotations

import pytest

from aggre.db import SilverContent, SilverDiscussion
from aggre.workflows.models import SilverContentRef


@pytest.mark.unit
class TestSilverContentRefSync:
    """SilverContentRef fields must stay in sync with DB columns."""

    def test_content_id_matches_silver_content_pk(self) -> None:
        assert hasattr(SilverContent, "id")

    def test_discussion_id_matches_silver_discussions_pk(self) -> None:
        assert hasattr(SilverDiscussion, "id")

    def test_domain_matches_silver_content_column(self) -> None:
        assert hasattr(SilverContent, "domain")

    def test_source_matches_silver_discussions_column(self) -> None:
        assert hasattr(SilverDiscussion, "source_type")

    def test_text_provided_derivable_from_text_column(self) -> None:
        assert hasattr(SilverContent, "text")

    def test_ref_fields_are_expected_set(self) -> None:
        """Guard against accidental field additions/removals."""
        expected = {"content_id", "discussion_id", "source", "domain", "text_provided"}
        actual = set(SilverContentRef.model_fields.keys())
        assert actual == expected
