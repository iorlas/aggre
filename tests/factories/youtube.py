from __future__ import annotations

__all__ = ["youtube_entry"]


def youtube_entry(
    video_id: str = "vid001",
    title: str = "Test Video",
    url: str | None = None,
    upload_date: str = "20240115",
    duration: int = 600,
    view_count: int = 1000,
) -> dict:
    entry: dict = {
        "id": video_id,
        "title": title,
        "upload_date": upload_date,
        "duration": duration,
        "view_count": view_count,
    }
    if url is not None:
        entry["url"] = url
    else:
        entry["url"] = f"https://www.youtube.com/watch?v={video_id}"
    return entry
