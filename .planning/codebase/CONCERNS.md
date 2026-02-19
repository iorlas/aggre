# Codebase Concerns

**Analysis Date:** 2026-02-20

## Tech Debt

**Global Model Cache in Transcriber:**
- Issue: The Whisper model is cached globally as a module-level variable (`_model_cache`) without thread safety or cleanup mechanism. This prevents model reloading and could cause issues if the model needs to be swapped or if memory leaks occur.
- Files: `src/aggre/transcriber.py:16`, `src/aggre/transcriber.py:44-52`
- Impact: Model updates require process restart; potential memory leaks in long-running processes; no way to unload model or switch between models
- Fix approach: Use a class-based context manager or dependency injection pattern; implement proper lifecycle management for WhisperModel

**Generic Exception Handlers Without Specificity:**
- Issue: Multiple locations catch bare `Exception` without distinguishing transient failures from fatal errors. This makes retry logic and error recovery strategies unclear.
- Files: `src/aggre/cli.py:80-81`, `src/aggre/cli.py:90-91`, `src/aggre/content_fetcher.py:74-77`, `src/aggre/transcriber.py:146-148`, `src/aggre/enrichment.py:58-60`, `src/aggre/enrichment.py:65-67`, `src/aggre/collectors/youtube.py:59-61`, `src/aggre/collectors/reddit.py:96-98`
- Impact: Cannot distinguish between retryable errors (429 rate limits, timeouts) and non-retryable errors (bad data, missing fields); retry logic may be ineffective or wasteful
- Fix approach: Create exception hierarchies; catch specific exception types; use separate handlers for transient vs permanent failures

**Complex URL Normalization with Brittle Domain Checks:**
- Issue: The `normalize_url()` function has overlapping conditional checks that could miss edge cases or process URLs incorrectly. Lines 100-105 perform redundant checks after domain-specific logic.
- Files: `src/aggre/urls.py:19-114`
- Impact: URLs may not normalize consistently; domain-specific logic could be bypassed; maintenance difficult when adding new domain rules
- Fix approach: Refactor to use a domain-based dispatcher pattern; add comprehensive test cases for each domain; flatten conditional logic

**Race Condition in ensure_content():**
- Issue: The function acknowledges a race condition (line 147 comment) where concurrent inserts could fail. While the workaround exists, it requires a second lookup that could still fail if timing is unfortunate.
- Files: `src/aggre/urls.py:128-152`
- Impact: Under high concurrency, content_id could be None even after checking; discussions linked to wrong or missing content
- Fix approach: Use a database-level RETURNING clause or move to application-level UUID generation; add test for concurrent inserts

**Upsert Logic Complexity in BaseCollector:**
- Issue: The `_upsert_discussion()` method queries for existing records, then performs an upsert, then queries again to get the ID. This is inefficient and the logic for detecting "new" vs "existing" is fragile.
- Files: `src/aggre/collectors/base.py:102-135`
- Impact: Three database round-trips per upsert; if the record is inserted between first check and upsert, the return value becomes unreliable; unnecessary SELECT queries during bulk operations
- Fix approach: Use PostgreSQL RETURNING clause with INSERT...ON CONFLICT directly; simplify by always returning the ID; profile query performance

**Transcription Status Spread Across Two Tables:**
- Issue: Transcription status and error live on `SilverContent` (lines 58-60 in `db.py`), but the transcriber joins `SilverDiscussion` to find what to transcribe. This creates a split concern: the content owns the state, but discussions own the reference.
- Files: `src/aggre/db.py:57-60`, `src/aggre/transcriber.py:61-74`, `src/aggre/collectors/youtube.py:84-90`
- Impact: Status updates require updates to SilverContent while querying SilverDiscussion; if a content is linked to multiple discussions (cross-source), status visibility is ambiguous; risk of transcription status inconsistency
- Fix approach: Clarify whether transcription is per-discussion or per-content; if per-content, move filtering logic to identify unique content_ids; add test for multi-discussion scenarios

**Enrichment Partial Failure Handling:**
- Issue: In `enrich_content_discussions()`, if one search (HN) fails but the other (Lobsters) succeeds, the `enriched_at` timestamp is NOT set. However, the failed search will be retried on next cycle, leading to repeated partial processing.
- Files: `src/aggre/enrichment.py:55-71`
- Impact: Content items bounce between "enriched" and "not enriched" states; unnecessary repeated searches; platform-specific failures block enrichment marking
- Fix approach: Track which platforms have been successfully searched; set enriched_at only when both succeed or max retries exceeded; add telemetry for failed searches

**Content Fetcher Timeouts Without Backoff:**
- Issue: The `extract_html_text()` function applies a 90-second timeout per article using ThreadPoolExecutor, but if timeout occurs, the content is marked FAILED immediately without retry capability.
- Files: `src/aggre/content_fetcher.py:150-156`
- Impact: Long but valid extraction jobs are lost; no exponential backoff for flaky extraction; difficult to distinguish timeout from actual failure
- Fix approach: Implement retry with longer timeout on second attempt; log timeout separately from other errors; consider async I/O for better resource utilization

**Bare Global Variable in CLI Context Passing:**
- Issue: Click context object used as a bare dictionary (`ctx.obj = dict()`, `ctx.obj["config"]`, `ctx.obj["engine"]`) with no type hints or structure. This is fragile and error-prone.
- Files: `src/aggre/cli.py:22, 24, 27, 41-42, 132-133, etc.`
- Impact: Easy to typo keys; no IDE autocomplete; unclear what context contains; changes to context structure require manual updates across all commands
- Fix approach: Create a `CliContext` dataclass with type hints; use it throughout; add validation on cli() to ensure engine/config are set

## Known Bugs

**YouTube Comments Collection Not Implemented:**
- Symptoms: The CLI has `--comment-batch` option and there's logic to skip YouTube in `collect_comments()` flow, but YouTube never appears in the comment-collection loop (only reddit, hackernews, lobsters at lines 83-91 in `cli.py`)
- Files: `src/aggre/cli.py:83-91`
- Trigger: Run `aggre collect --comment-batch=50`; YouTube discussions with `comments_status=PENDING` are never processed
- Workaround: Currently must manually update YouTube discussion comments_status to DONE or leave as NULL

**Transcriber Only Processes YouTube via JOIN:**
- Symptoms: Only YouTube videos can be transcribed because the transcriber queries `SilverDiscussion` with source_type filter. If another source (RSS, HN) links to video content, that content is never transcribed.
- Files: `src/aggre/transcriber.py:71-74`
- Trigger: Add RSS feed with video links; fetch content; run transcriber; videos won't be transcribed
- Workaround: Manually set `transcription_status='pending'` on SilverContent; manually run transcriber against those IDs (not currently possible without code change)

**Domain Extraction Doesn't Handle Subdomains Consistently:**
- Symptoms: `extract_domain()` removes www. prefix but doesn't normalize other subdomains. Calls to `extract_domain()` in content fetcher may store "api.example.com" vs "example.com" inconsistently.
- Files: `src/aggre/urls.py:117-125`, `src/aggre/content_fetcher.py:110`
- Trigger: Collect content from API subdomains vs main domain
- Workaround: None; content fetching will work but domain grouping will be fragmented

## Security Considerations

**Proxy URL Passed Directly to yt-dlp and httpx:**
- Risk: The proxy_url from config is passed directly to yt-dlp and httpx without validation. Malformed proxy URLs could cause connection errors or be logged in error messages.
- Files: `src/aggre/config.py:33`, `src/aggre/transcriber.py:112-114`, `src/aggre/collectors/youtube.py:51-53`, `src/aggre/content_fetcher.py:102-105`
- Current mitigation: Assumes proxy_url is set by administrator in .env; runtime errors would fail the collection cycle
- Recommendations: Validate proxy URL format on config load (check for valid scheme, host); add proxy connection test on startup; sanitize proxy URLs in error logs

**Raw HTML Storage Without Sanitization:**
- Risk: Downloaded HTML is stored as-is in raw_html column without any sanitization. If this data is later displayed or processed without escaping, it could be a vector for injection or XSS if exposed via API.
- Files: `src/aggre/content_fetcher.py:70`, `src/aggre/db.py:52`
- Current mitigation: raw_html is extracted into body_text by trafilatura (which strips HTML); raw_html field is not currently exposed
- Recommendations: Document that raw_html must never be returned via API; add runtime assertion if API is added; consider not storing raw_html at all (only body_text)

**JSON Parsing Without Size Limits:**
- Risk: Comments are stored as raw JSON strings. If a discussion has thousands of comments, the JSON could be very large and cause memory pressure during parsing.
- Files: `src/aggre/db.py:83`
- Current mitigation: None; relies on source API rate limits
- Recommendations: Add maximum comment count check before storing; compress comments_json; consider storing comments in a separate table

**No Input Validation on External IDs:**
- Risk: External IDs from Reddit, HN, YouTube etc. are stored directly in the database. Malicious data could contain SQL injection attempts (though parameterized queries mitigate this) or very long strings.
- Files: `src/aggre/db.py:74`, `src/aggre/collectors/base.py:53-64`
- Current mitigation: SQLAlchemy parameterized queries prevent SQL injection
- Recommendations: Add length validation on external_id before storing; sanitize external_id values that might be used in URLs

## Performance Bottlenecks

**ThreadPoolExecutor with Max Workers Per Collector:**
- Problem: CLI creates one ThreadPoolExecutor with max_workers=len(active_collectors) (e.g., 7 workers for 7 collectors). Each collector then makes blocking HTTP calls. This can lead to thread starvation or too many concurrent connections.
- Files: `src/aggre/cli.py:69-73`
- Cause: Naive threading model without connection pooling coordination; no backpressure mechanism
- Improvement path: Use asyncio instead of threads; or limit total concurrent connections; or use a shared connection pool with per-collector rate limiting

**Single-Threaded Content Extraction:**
- Problem: `extract_html_text()` processes articles sequentially with 90-second timeout per article. A batch of 50 articles could take 75+ minutes.
- Files: `src/aggre/content_fetcher.py:144-172`
- Cause: CPU-bound trafilatura extraction; ThreadPoolExecutor per item would add overhead
- Improvement path: Batch process via multi-processing; profile trafilatura to identify hot paths; consider alternative extraction library; implement streaming extraction for large documents

**No Query Pagination in Enrichment:**
- Problem: `enrich_content_discussions()` retrieves batch_limit rows from SilverContent, but for each row, it makes two separate API calls to HN and Lobsters. With batch_limit=50, that's 100 external API calls per cycle.
- Files: `src/aggre/enrichment.py:25-34`, `src/aggre/enrichment.py:56-64`
- Cause: No batching of URL searches; rate limiting handled per request
- Improvement path: Batch search requests if APIs support it; implement persistent search queue; add metrics for API call overhead

**Inefficient Database Lookups in _ensure_source:**
- Problem: Every collector call to `_ensure_source()` makes a database round-trip, even though source configs are static.
- Files: `src/aggre/collectors/base.py:39-51`
- Cause: No caching; source_id needed immediately for discussion inserts
- Improvement path: Pre-load sources at app startup; cache source_id by (type, name); invalidate on config changes

**JOIN Between SilverContent and SilverDiscussion in Transcriber:**
- Problem: The transcriber JOINs SilverContent to SilverDiscussion to find pending videos. If there are 100k discussions but only 10k YouTube ones, the JOIN still scans both tables.
- Files: `src/aggre/transcriber.py:61-74`
- Cause: No index on (source_type, transcription_status) to efficiently filter
- Improvement path: Add composite index; consider denormalization if needed; or query SilverContent with status=PENDING first, then check for associated discussions

## Fragile Areas

**Collector Comment Fetching with Implicit hasattr Check:**
- Files: `src/aggre/cli.py:86-91`
- Why fragile: The code checks `hasattr(coll, "collect_comments")` and assumes it exists on dynamic lookup from dictionary. If collector dict is stale or missing, the check passes but call fails later.
- Safe modification: Create a protocol/interface that all collectors must implement (even if no-op); validate at startup; add type hints
- Test coverage: No unit tests for comment collection flow; only tested via acceptance tests

**BaseCollector._upsert_discussion Double Query:**
- Files: `src/aggre/collectors/base.py:109-135`
- Why fragile: Logic relies on state from first query to decide what was inserted, but the upsert could have been done by another process. The second query is a fallback that could also fail in edge cases.
- Safe modification: Refactor to use RETURNING clause; add transaction isolation level validation; test under concurrent load
- Test coverage: No concurrency tests; only single-threaded scenarios

**URL Normalization Domain Dispatch:**
- Files: `src/aggre/urls.py:45-105`
- Why fragile: Adding a new domain requires modifying the nested if-elif chain and possibly duplicating query cleaning logic. Easy to miss a domain in the query cleaning at lines 100-102.
- Safe modification: Use a domain dispatcher dict; move domain logic to separate functions; test each domain independently
- Test coverage: Test file exists but may not cover all edge cases (e.g., subdomains, mixed case)

**Content Fetcher HTTP Client Not Reused Between Calls:**
- Files: `src/aggre/cli.py:136-139`, `src/aggre/content_fetcher.py:102-119`
- Why fragile: Each `download_content()` call creates a new HTTP client. If called in a loop, this opens/closes connections repeatedly.
- Safe modification: Pass client as parameter or use dependency injection; consider thread-local storage for per-worker clients
- Test coverage: Mocked in tests; no integration tests with real HTTP

**Transcriber Audio File Cleanup:**
- Files: `src/aggre/transcriber.py:150-152`
- Why fragile: Audio cleanup is in finally block, but if transcription completes successfully, the file should already be deleted by yt-dlp postprocessor. This is a redundant cleanup that could mask real issues.
- Safe modification: Verify yt-dlp actually deletes files; add explicit deletion of .opus file after transcription; add logging for cleanup actions
- Test coverage: Audio cleanup not tested; could leave orphaned files in temp dir

## Scaling Limits

**Database Connection Pool Not Configured:**
- Current capacity: Default SQLAlchemy connection pool has small size; under concurrent load, connections will be queued or rejected
- Limit: If running multiple worker processes (collect, download, transcribe simultaneously), they will contend for pool connections
- Files: `src/aggre/db.py:116-118`
- Scaling path: Configure `pool_size`, `max_overflow`, and `pool_pre_ping` in `get_engine()`; add connection pooling middleware; monitor connection usage

**No Rate Limiting Coordination Between Collectors:**
- Current capacity: Each collector has individual rate limits (reddit_rate_limit, hn_rate_limit, etc.)
- Limit: If collecting all sources concurrently, total API load could exceed platform limits; no global circuit breaker
- Files: `src/aggre/config.py:25-27`, `src/aggre/collectors/reddit.py:79`
- Scaling path: Implement global rate limiter; add circuit breaker pattern; track cumulative API call counts

**Whisper Model Loading Per Process:**
- Current capacity: Model loads once per process on first transcription request
- Limit: If running 10 transcriber processes, each loads a 3GB Whisper model (30GB total); no shared model server
- Files: `src/aggre/transcriber.py:44-52`
- Scaling path: Move model to shared inference server (GPU); implement model caching layer; or use smaller model variant

**No Database Connection Limits in CLI:**
- Current capacity: ThreadPoolExecutor can spawn many threads, each making DB connections
- Limit: Default pool_size ~5; 10 threads competing for connections will queue
- Files: `src/aggre/cli.py:69`
- Scaling path: Use bounded queue for submissions; set pool_size >= max_workers; switch to async/await pattern

## Dependencies at Risk

**yt-dlp Version Pinning:**
- Risk: YouTube constantly changes its API/infrastructure; yt-dlp must track these changes. An outdated version will fail to fetch videos.
- Impact: Collectors silently fail or get incomplete data; no clear error signal
- Files: `src/aggre/collectors/youtube.py:9`, `src/aggre/transcriber.py:9` (indirect via collectors)
- Migration plan: Add version constraint in requirements; set up CI to test against latest yt-dlp weekly; implement graceful degradation if yt-dlp fails

**trafilatura Extraction Fragility:**
- Risk: Web page structures change; trafilatura heuristics may not work on custom layouts. Content extraction could silently return empty or truncated text.
- Impact: body_text is empty for many articles; users see no content; no signal that extraction failed vs page had no content
- Files: `src/aggre/content_fetcher.py:152`
- Migration plan: Add fallback extraction method (readability, newspaper3k); validate extraction quality (e.g., min word count); log extraction length for monitoring

**faster-whisper Dependency on FFmpeg:**
- Risk: faster-whisper requires FFmpeg for audio processing. If FFmpeg is not installed or wrong version, transcription fails.
- Impact: Transcription fails with unhelpful error; no fallback to other transcription services
- Files: `src/aggre/transcriber.py:10`, `src/aggre/transcriber.py:106-110`
- Migration plan: Add FFmpeg version check on startup; provide docker image with FFmpeg pre-installed; implement fallback to cloud transcription API (AWS Transcribe, GCP Speech)

**Tenacity Retry Configuration for Rate Limiting:**
- Risk: The custom retry predicate in Reddit collector only retries on 429/503, but other transient errors (timeout, connection reset) are not retried.
- Impact: Temporary network issues cause hard failures; retried collections don't recover
- Files: `src/aggre/collectors/reddit.py:28-30`, `src/aggre/collectors/reddit.py:52-56`
- Migration plan: Expand retry conditions to include timeout/connection errors; use tenacity for all API calls; add metrics for retry success rate

## Missing Critical Features

**No Query Performance Monitoring:**
- Problem: Slow queries will silently degrade collection speed. No EXPLAIN ANALYZE output or query logging enabled.
- Blocks: Identifying bottlenecks; capacity planning; detecting N+1 query bugs
- Fix: Enable PostgreSQL slow query log; add query timing via SQLAlchemy events; expose metrics via /metrics endpoint if API exists

**No Idempotency Guarantees for Collectors:**
- Problem: If a collector is interrupted mid-run (e.g., network timeout, process killed), restarting could insert duplicate data (if dedup logic doesn't cover all cases) or miss data (if checkpoint wasn't set).
- Blocks: Safe restarts; safe horizontal scaling; guaranteed at-least-once semantics
- Fix: Implement checkpoint-based collection (track last_fetched_at per source and use for delta collection); add upsert-everywhere pattern; test recovery scenarios

**No Webhook Callback System:**
- Problem: Enrichment waits for batch processing cycles. If a new discussion is posted about an old article, it won't be found until the enrichment cycle runs again.
- Blocks: Real-time cross-source discussion discovery; reactive content updates
- Fix: Add webhook listener for Reddit/HN API changes; implement message queue (SQS, Kafka) for event-driven enrichment

**No Content Quality Assessment:**
- Problem: No way to know if extracted content is useful. Some articles return empty body_text; some are paywalled; some are auto-generated spam.
- Blocks: Preventing low-quality content from inflating database; prioritizing extraction for good content
- Fix: Add content quality score (based on word count, language model, domain reputation); implement content deduplication

**No Duplicate Content Detection:**
- Problem: Same article published by multiple users or in multiple sources is stored as separate SilverContent rows.
- Blocks: Unified view of content; accurate discussion aggregation per unique article
- Fix: Implement content hashing (MD5 of body_text); add similarity detection for near-duplicates; consolidate duplicate content rows

## Test Coverage Gaps

**No Tests for Concurrent Database Writes:**
- What's not tested: Race conditions in `ensure_content()`, `_upsert_discussion()` under concurrent inserts
- Files: `src/aggre/urls.py:128-152`, `src/aggre/collectors/base.py:102-135`
- Risk: Bug will only surface under real-world concurrency (multiple collectors running); hard to reproduce locally
- Priority: High — impacts data integrity

**No Tests for Comment Collection:**
- What's not tested: The entire `collect_comments()` flow for Reddit, HN, Lobsters; comment_count accuracy; comments_json structure
- Files: `src/aggre/cli.py:83-91`, and collector implementations (not shown completely)
- Risk: Comments silently not collected; incorrect comment_count in search results
- Priority: High — core feature

**No Tests for URL Normalization Edge Cases:**
- What's not tested: Subdomains (api.github.com, en.wikipedia.org), international domains, URLs with unicode, very long URLs, malformed URLs
- Files: `src/aggre/urls.py:19-114`
- Risk: URL normalization could fail or produce inconsistent results for edge cases
- Priority: Medium — affects deduplication quality

**No Integration Tests for Content Extraction:**
- What's not tested: End-to-end download + extraction with real HTML; trafilatura extraction on various page types; timeout handling; large file handling
- Files: `src/aggre/content_fetcher.py`
- Risk: Extraction could fail silently on certain content types in production
- Priority: Medium — helps validate extraction quality

**No Tests for Transcription with Multiple Discussions:**
- What's not tested: When a content_id is linked to multiple source_type='youtube' discussions, does transcriber handle it correctly?
- Files: `src/aggre/transcriber.py:61-74`
- Risk: Status inconsistency if same video discussed on multiple platforms
- Priority: Medium — edge case but impacts consistency

**No Tests for Enrichment Partial Failures:**
- What's not tested: Behavior when one search (HN) succeeds but other (Lobsters) fails; retrying failed enrichments
- Files: `src/aggre/enrichment.py:55-71`
- Risk: Enrichment status becomes unreliable; content bounces between enriched/unenriched
- Priority: Medium — affects enrichment reliability

---

*Concerns audit: 2026-02-20*
