"""Pydantic models for validation and serialization."""

from __future__ import annotations

from pydantic import BaseModel


class Source(BaseModel):
    id: int | None = None
    type: str
    name: str
    config: dict
    enabled: bool = True
    created_at: str | None = None
    last_fetched_at: str | None = None


class RawItem(BaseModel):
    id: int | None = None
    source_type: str
    external_id: str
    raw_data: str
    fetched_at: str | None = None


class ContentItem(BaseModel):
    id: int | None = None
    source_id: int | None = None
    raw_item_id: int | None = None
    source_type: str
    external_id: str
    title: str | None = None
    author: str | None = None
    url: str | None = None
    content_text: str | None = None
    published_at: str | None = None
    fetched_at: str | None = None
    metadata: dict | None = None
    transcription_status: str | None = None
    transcription_error: str | None = None
    detected_language: str | None = None


class RedditComment(BaseModel):
    id: int | None = None
    content_item_id: int | None = None
    raw_comment_id: int | None = None
    external_id: str
    author: str | None = None
    body: str | None = None
    score: int | None = None
    parent_id: str | None = None
    depth: int | None = None
    created_at: str | None = None
