# 008: Pydantic BaseModel over Any for collector configs

**Status:** Active
**Date:** 2026-02-22

## Why
The `Collector` protocol used `config: Any`, hiding the fact that all collector configs are Pydantic `BaseModel` subclasses. This violated the no-Any typing guideline and lost type safety at protocol boundaries.

## Decision
Changed protocol to `config: BaseModel`. Every concrete collector config (`HackernewsConfig`, `RedditConfig`, etc.) inherits from `BaseModel`, so this is semantically correct. Protocol variance isn't strictly enforced by ty/mypy for this pattern, so concrete implementations with more specific config types work fine.

## Not chosen
- Keep `Any` -- loses type safety, violates typing guidelines
- Generic protocol `Collector[C: BaseModel]` -- over-engineered; protocol variance issues make this harder than it looks with no practical benefit

## Consequence
New collector configs must inherit from Pydantic `BaseModel`. The protocol enforces this at the type level.
