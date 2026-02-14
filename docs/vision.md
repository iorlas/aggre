# Aggre — Business Vision

## What It Does

A Dockerized CLI tool that continuously polls configured sources, fetches new content, and stores it in a local queryable database. Runs as long-lived containers with built-in loop scheduling.

| Source | Input | What We Capture | Media Handling |
|--------|-------|-----------------|----------------|
| **RSS/Blogs** | Feed URLs (~100, starting with 20) | Title, author, date, full text/summary, link | Store article text directly |
| **Reddit** | Subreddit names (~100, starting with 35) | Posts (title, author, score, text, flair), comments, image/video URLs | Store URLs only (no media download) |
| **YouTube** | Channel IDs (~100, starting with 15) | Title, channel, date, description, category, duration, **transcript** | Download video → transcribe (Whisper large-v3) → delete video |

## Reddit Strategy

- Poll both **hot** and **new** for each subreddit (catches popular + emerging content)
- **Deduplicate** posts that appear in both listings (by Reddit post ID)
- Capture comments per post (throttled to avoid blocks)
- Store image/video URLs, not the files themselves
- No API auth — use Reddit's public JSON endpoints with conservative rate limiting

## YouTube Strategy

- Track all new videos from configured channels
- **On-demand backfill** of full channel history (first-time setup)
- Download video → transcribe with Whisper large-v3 (auto language detection) → delete video
- Keep all metadata + transcript permanently; videos are temporary
- Transcription fully decoupled from polling

## Querying

Must support queries like:

- "Top posts today from r/rust and r/golang"
- "All YouTube transcripts from channel X this week"
- "Latest RSS articles containing keyword Y"

## Retention

- Keep all content indefinitely (TTL cleanup is a future concern)
- YouTube videos deleted after successful transcription
- ~400GB SSD constraint — only transcription temp files use significant disk
