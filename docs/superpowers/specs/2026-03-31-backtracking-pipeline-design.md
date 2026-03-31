# Backtracking Pipeline — Design Spec

## Overview

Build a system that traces popular AI coverage back to its original sources, scores those sources over time, and eventually monitors proven early sources proactively. This turns Aggre from a passive aggregator into an early-signal discovery engine.

Research reference: `~/Documents/Knowledge/Researches/091-aggre-source-expansion/backtracking-strategy.md`

**Prerequisite:** Source expansion spec (Bluesky + Twitter/X collectors) should be live first — the backtracking pipeline needs social platform data to trace provenance.

## Strategy: The Canary Method

Rather than building the full automated pipeline upfront, start with a lightweight "canary" approach that validates the concept with minimal infrastructure:

1. **Month 1:** Monitor ~30 candidate early sources alongside late commentators. Measure lead times manually/semi-automatically.
2. **Month 2:** Automate provenance chain construction. Score sources by consistency.
3. **Month 3+:** Flip to proactive monitoring of proven sources. Flag their new posts as "likely to go mainstream."

This spec covers the full pipeline but implementation should follow this phased approach.

---

## Data Model

### New Tables

#### `signal_topics`

Represents a detected topic/story that propagated across sources.

| Column | Type | Description |
|--------|------|-------------|
| `id` | int PK | |
| `name` | str | Short topic label (e.g., "Claude 4 release") |
| `entities` | JSON | Extracted entities `["Anthropic", "Claude", "Claude 4"]` |
| `topic_type` | str | `product_launch`, `research_paper`, `technique`, `opinion`, `model_release` |
| `first_seen_at` | timestamp | Earliest known mention across all sources |
| `mainstream_at` | timestamp | When a late commentator covered it (trigger event) |
| `trigger_discussion_id` | int FK | The late-commentator discussion that triggered backtracking |
| `created_at` | timestamp | |

#### `signal_mentions`

Individual mentions of a topic, forming the provenance chain.

| Column | Type | Description |
|--------|------|-------------|
| `id` | int PK | |
| `topic_id` | int FK | → signal_topics |
| `discussion_id` | int FK nullable | → silver_discussions (if mention is in Aggre) |
| `external_url` | str | URL of the mention (if not in Aggre) |
| `platform` | str | `twitter`, `bluesky`, `hackernews`, `reddit`, `arxiv`, `blog`, `youtube` |
| `author` | str | Who posted it |
| `mentioned_at` | timestamp | When this mention was published |
| `is_origin` | bool | True if this is the earliest known mention |
| `meta` | JSON | Platform-specific data |
| `created_at` | timestamp | |

Unique constraint: `(topic_id, platform, author, external_url)`

#### `signal_sources`

Scored early-signal sources, accumulated over time.

| Column | Type | Description |
|--------|------|-------------|
| `id` | int PK | |
| `platform` | str | `twitter`, `bluesky`, `blog`, etc. |
| `author` | str | Handle or name |
| `profile_url` | str | Link to profile |
| `origin_count` | int | Times this source was the origin |
| `early_count` | int | Times this source was in the first 10% of mentions |
| `total_mentions` | int | Total topic mentions tracked |
| `avg_lead_hours` | float | Average hours ahead of mainstream |
| `score` | float | Composite score (see scoring section) |
| `last_scored_at` | timestamp | |
| `created_at` | timestamp | |

Unique constraint: `(platform, author)`

### No Changes to Existing Tables

The backtracking pipeline reads from `silver_discussions` but writes only to its own tables. No column ownership conflicts.

---

## Pipeline Stages

### Stage 1: Trigger Detection

**Input:** New discussions from late commentators (YouTube channels: Fireship, ThePrimeagen, AI Explained, Matt Wolfe, Two Minute Papers).

**Trigger condition:** A new YouTube discussion appears from a monitored "late commentator" source. These sources are tagged in config:

```yaml
backtracking:
  late_commentators:
    - source_type: "youtube"
      source_names: ["Fireship", "ThePrimeagen", "AI Explained"]
  canary_sources:
    - platform: "twitter"
      authors: ["_akhaliq", "simonw", "natolambert", "Teknium1"]
    - platform: "bluesky"
      authors: ["simonwillison.net", "natolambert.bsky.social"]
```

**Output:** A candidate topic to backtrack.

**Implementation:** Hatchet workflow triggered by `item.new` event from late-commentator sources.

### Stage 2: Topic Extraction

**Input:** Discussion from Stage 1 (title, description/content_text, linked URLs).

**Method:** LLM extraction (Claude Haiku or equivalent — cheap, fast, sufficient for structured extraction).

**Prompt pattern:**
```
Extract the core topic from this content:
Title: {title}
Description: {first 500 chars of content_text}
URLs: {urls from description}

Return JSON:
{
  "topic_name": "short label",
  "entities": ["list", "of", "key", "entities"],
  "topic_type": "product_launch|research_paper|technique|model_release|opinion",
  "search_terms": ["terms", "to", "search", "for"]
}
```

**Deduplication:** Before creating a new `signal_topics` row, check if an existing topic matches (same entities within a 14-day window). If so, link to existing topic.

**Output:** `signal_topics` row.

### Stage 3: Backtrack Search (Fan-Out)

**Input:** Topic record with entities and search terms.

**Parallel searches across platforms:**

| Search Target | Method | Window |
|---------------|--------|--------|
| Aggre's own DB | SQL query on `silver_discussions` matching entities in title/content_text | topic.mainstream_at - 14 days |
| HN Algolia | `GET http://hn.algolia.com/api/v1/search_by_date?query={terms}&numericFilters=created_at_i>{start}` | 14 days |
| Reddit | Reddit API search on r/MachineLearning, r/LocalLLaMA, r/artificial | 14 days |
| ArXiv | ArXiv API query for paper titles/abstracts matching entities | 30 days |
| Twitter/X | TwitterAPI.io search endpoint with date filters | 14 days |
| Bluesky | AT Protocol search endpoint | 14 days |
| Description URLs | Follow URLs from Stage 2, extract their publication dates | Direct |

Each search runs as a parallel Hatchet child workflow step.

**Output:** List of `(url, author, platform, timestamp)` tuples.

### Stage 4: Provenance Chain Construction

**Input:** All discovered mentions from Stage 3.

**Process:**
1. Sort by timestamp (oldest first)
2. For each mention, check if it cites/links to an earlier mention (recursive, max depth 3)
3. Deduplicate (same author + same platform + same day = one mention)
4. Mark the earliest mention as `is_origin = true`
5. Insert all as `signal_mentions` rows

**Lead time calculation:** `lead_hours = mainstream_at - mention.mentioned_at`

### Stage 5: Source Scoring

**Input:** Accumulated `signal_mentions` over time.

**Scoring formula (per source):**

```
score = (origin_weight * origin_count + early_weight * early_count) / total_mentions * log(total_mentions + 1) * recency_factor
```

Where:
- `origin_weight = 3.0` (was the absolute first)
- `early_weight = 1.0` (was in first 10% of mentions)
- `total_mentions` normalization prevents gaming (posting about everything)
- `recency_factor` decays old contributions (half-life: 90 days)

**Anti-gaming:** The `/ total_mentions` term penalizes sources that mention many topics but are only early on a few. A source that mentions 100 topics but is early on 5 scores lower than one that mentions 10 topics and is early on 5.

**Update frequency:** Re-score all sources weekly (cron job), or after each new provenance chain is completed.

### Stage 6: Proactive Monitoring (Future)

Once `signal_sources` has high-confidence entries (score > threshold, origin_count >= 5), flag new posts from those sources in the Aggre UI/digest as "early signal — likely to go mainstream."

This stage is out of scope for initial implementation. It requires:
- A notification/flagging mechanism in Aggre
- Confidence thresholds tuned from real data
- Integration with the digest/output layer

---

## Hatchet Workflow Design

### `backtrack_topic` Workflow

Triggered by: `item.new` event where source matches `late_commentators` config.

```
Step 1: extract_topic
  - Input: discussion_id
  - LLM call for topic extraction
  - Dedup check against existing topics
  - Output: topic_id

Step 2: fan_out_search (parallel children)
  - search_aggre_db(topic_id)
  - search_hn_algolia(topic_id)
  - search_reddit(topic_id)
  - search_arxiv(topic_id)
  - search_twitter(topic_id)
  - search_bluesky(topic_id)
  - follow_description_urls(topic_id)

Step 3: build_provenance_chain
  - Input: all search results
  - Sort, deduplicate, mark origin
  - Insert signal_mentions

Step 4: update_source_scores
  - Recalculate scores for all sources mentioned in this chain
```

### `rescore_sources` Workflow

Triggered by: weekly cron (`0 0 * * 0`).

Recomputes all `signal_sources.score` values with current recency decay.

---

## Column Ownership (Concurrency Safety)

| Stage | Table | Columns Written |
|-------|-------|----------------|
| extract_topic | signal_topics | all columns |
| fan_out_search + build_chain | signal_mentions | all columns |
| update_source_scores | signal_sources | all columns |

No overlap with existing pipeline stages. The backtracking pipeline has its own tables.

---

## Configuration

```yaml
backtracking:
  enabled: false  # Start disabled, enable after source expansion is live

  late_commentators:
    - source_type: "youtube"
      source_names:
        - "Fireship"
        - "ThePrimeagen"
        - "AI Explained"
        - "Matt Wolfe"
        - "Two Minute Papers"

  canary_sources:
    - platform: "twitter"
      authors:
        - "_akhaliq"
        - "simonw"
        - "natolambert"
        - "Teknium1"
        - "BlancheMinerva"
        - "swyx"
        - "dylan522p"
        - "ClaborevD"
    - platform: "bluesky"
      authors:
        - "simonwillison.net"

  search_window_days: 14
  max_search_depth: 3
  min_source_score: 0.5

  llm:
    model: "claude-haiku-4-5-20251001"  # Cheap, fast, sufficient
    max_tokens: 500
```

Settings additions:
```python
backtracking_enabled: bool = False
backtracking_llm_api_key: str = ""  # Anthropic API key for topic extraction
```

---

## Migration

One Alembic migration to create `signal_topics`, `signal_mentions`, `signal_sources` tables.

---

## Testing

- **Unit tests:** Topic extraction prompt → structured output parsing
- **Unit tests:** Provenance chain construction (sorting, dedup, origin marking)
- **Unit tests:** Source scoring formula (verify anti-gaming properties)
- **Integration tests:** Full workflow with VCR cassettes for HN Algolia, Reddit, ArXiv searches
- **Manual validation:** Run on 5-10 real YouTube videos, verify provenance chains make sense

---

## Implementation Phases

### Phase 1 (Week 1): Schema + Topic Extraction
- Alembic migration for new tables
- Topic extraction step (LLM call + dedup)
- Config structure

### Phase 2 (Week 2): Search Fan-Out
- Implement all search adapters (Aggre DB, HN, Reddit, ArXiv, Twitter, Bluesky)
- VCR cassettes for each

### Phase 3 (Week 3): Provenance + Scoring
- Chain construction logic
- Source scoring with anti-gaming
- Weekly rescore cron

### Phase 4 (Week 4): Integration + Validation
- Wire into Hatchet workflows
- Manual validation on real data
- Tune scoring parameters

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| LLM topic extraction is noisy | Use structured output, validate JSON schema, log failures |
| Search APIs change/break | Each search adapter is independent, graceful degradation |
| TwitterAPI.io goes down | Search fan-out continues without Twitter results |
| Scoring rewards prolific over prescient | Anti-gaming term in formula, tune after real data |
| Cold start (no data for months) | Start with manual canary approach while pipeline builds up data |
| Cost creep from LLM calls | Haiku is cheap (~$0.001/extraction), cap at 50 extractions/day initially |

---

## Success Criteria

After 3 months of operation:
- 50+ topics traced with provenance chains
- 10+ sources identified with score > 0.5
- Demonstrated average lead time of proven sources > 12 hours ahead of late commentators
- System runs unattended with < 5% error rate on search fan-out
