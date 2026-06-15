# YouTube Analytics — Nike Football Pipeline

End-to-end pipeline to **search YouTube**, **fetch comments**, **analyse sentiment** with DeepSeek AI, and **visualise results** in an interactive HTML dashboard.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Full Pipeline](#full-pipeline)
3. [Scripts](#scripts)
   - [youtube_search.py](#1-youtube_searchpy--search-only-no-comments)
   - [youtube_search_comments.py](#2-youtube_search_commentspy--search--fetch-comments)
   - [youtube_broad_search.py](#3-youtube_broad_searchpy--broad-multi-query-search--comments-recommended)
   - [youtube_comments.py](#4-youtube_commentspy--single-video-comments)
   - [analyze_comments_deepseek.py](#5-analyze_comments_deepseekpy--ai-analysis)
   - [generate_dashboard.py](#6-generate_dashboardpy--html-dashboard)
4. [Output Formats](#output-formats)
5. [Dashboard Pages](#dashboard-pages)
6. [API Keys](#api-keys)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project directory:

```env
YOUTUBE_API_KEY=your_youtube_data_api_v3_key
DEEPSEEK_API_KEY=your_deepseek_api_key
```

---

## Full Pipeline

```
Step 1 — Search & fetch comments
    ↓  youtube_broad_search.py  →  yt_broad_nike_<timestamp>.json

Step 2 — AI analysis (DeepSeek)
    ↓  analyze_comments_deepseek.py  →  yt_broad_nike_<timestamp>_analyzed.json

Step 3 — HTML dashboard
    ↓  generate_dashboard.py  →  yt_broad_nike_<timestamp>_analyzed_dashboard.html
```

**Quickstart — Nike, last 30 days:**

```bash
python youtube_broad_search.py "Nike" --days 30
python analyze_comments_deepseek.py yt_broad_nike_<timestamp>.json
python generate_dashboard.py yt_broad_nike_<timestamp>_analyzed.json
```

---

## Scripts

---

### 1. `youtube_search.py` — Search only (no comments)

Searches YouTube and saves video metadata to JSON. **Does not fetch comments.**

```bash
# Last N days
python youtube_search.py "Nike" --days 30

# Explicit date range
python youtube_search.py "Nike" --after 2024-01-01 --before 2024-06-01

# Only --after, no upper bound
python youtube_search.py "Nike" --after 2025-01-01

# Custom output file
python youtube_search.py "Nike" --days 7 -o nike_week.json
```

| Argument | Description |
|----------|-------------|
| `query` | Search string (required) |
| `--days N` | Videos published in the last N days |
| `--after DATE` | Published on or after this date (YYYY-MM-DD) |
| `--before DATE` | Published on or before this date (YYYY-MM-DD) |
| `-o FILE` | Output JSON file path |

---

### 2. `youtube_search_comments.py` — Search + fetch comments

Searches YouTube and fetches **all comments** (top-level + replies) from every result video.

> ⚠️ **Limitation:** YouTube's search API caps at ~500 results per query. Use `youtube_broad_search.py` for broader coverage.

```bash
# Last 30 days
python youtube_search_comments.py "Nike" --days 30

# Explicit date range
python youtube_search_comments.py "nike football" --after 2024-01-01 --before 2024-06-01

# Skip videos with comments turned off
python youtube_search_comments.py "Nike" --days 30 --skip-disabled

# Custom output file
python youtube_search_comments.py "Nike" --days 2 -o nike_2days.json
```

| Argument | Description |
|----------|-------------|
| `query` | Search string (required) |
| `--days N` | Videos published in the last N days |
| `--after DATE` | Published on or after this date (YYYY-MM-DD) |
| `--before DATE` | Published on or before this date (YYYY-MM-DD) |
| `--skip-disabled` | Silently skip videos with comments disabled |
| `-o FILE` | Output JSON file path |

---

### 3. `youtube_broad_search.py` — Broad multi-query search + comments ✅ Recommended

Overcomes YouTube's ~500 result cap by **automatically generating 21 sub-queries** (e.g. `"Nike football"`, `"Nike Mercurial"`, `"Nike Ronaldo"`, `"Nike running"`, etc.), running each independently, **de-duplicating** results, then fetching all comments.

After **every sub-query**, you are prompted to continue, stop (jump to comment fetching), or quit — with automatic intermediate saves.

```bash
# Default — interactive, last 30 days
python youtube_broad_search.py "Nike" --days 30

# Non-interactive (fully automatic, no prompts)
python youtube_broad_search.py "Nike" --days 30 --non-interactive

# Provide your own sub-queries instead of auto-expanding
python youtube_broad_search.py "Nike" --days 30 --sub-queries "Nike football" "Nike Mercurial" "Nike ad"

# Skip auto-expansion, use base query only
python youtube_broad_search.py "Nike" --days 30 --no-auto-expand

# Skip videos with comments disabled
python youtube_broad_search.py "Nike" --days 30 --skip-disabled

# Add delay between sub-queries (gentler on API quota)
python youtube_broad_search.py "Nike" --days 30 --delay 2

# Custom output file
python youtube_broad_search.py "Nike" --days 30 -o nike_broad_30days.json
```

| Argument | Description |
|----------|-------------|
| `query` | Base search term (required) |
| `--days N` | Videos published in the last N days |
| `--after DATE` | Published on or after this date (YYYY-MM-DD) |
| `--before DATE` | Published on or before this date (YYYY-MM-DD) |
| `--sub-queries Q …` | Custom sub-queries (overrides auto-expansion) |
| `--no-auto-expand` | Use only the base query, no sub-queries |
| `--skip-disabled` | Skip videos with comments disabled |
| `--delay N` | Seconds between sub-queries (default: 1.0) |
| `--non-interactive` | Run without any prompts |
| `-o FILE` | Output JSON file path |

**Interactive prompt (shown after each sub-query):**

```
──────────────────────────────────────────────────────
  Query 3/21 complete: "Nike football boots"
  Results so far → 187 unique videos (this query: 42)
  Auto-saved to: yt_broad_nike_20260615_180000_intermediate_3.json
──────────────────────────────────────────────────────
  [p] Proceed to next query
  [s] Stop searching → fetch comments on what we have
  [q] Quit now → save findings without fetching comments
──────────────────────────────────────────────────────
```

**Auto-generated sub-queries for any base term:**

| Category | Sub-queries |
|----------|-------------|
| Football | `football`, `soccer`, `football boots`, `soccer cleats` |
| Basketball | `basketball`, `basketball shoes` |
| Running | `running`, `running shoes` |
| Nike Products | `Mercurial`, `Phantom`, `Tiempo`, `Air Max`, `Air Zoom`, `Vapor`, `Dri-FIT` |
| Campaign | `ad`, `commercial`, `campaign`, `new release` |
| Lifestyle | `training`, `lifestyle`, `sportswear` |
| Athletes | `Ronaldo`, `Mbappe` |

---

### 4. `youtube_comments.py` — Single video comments

Downloads all comments from a single video by ID or URL.

```bash
# Using a video ID
python youtube_comments.py IyZ1WIua_1s

# Using a full URL
python youtube_comments.py "https://www.youtube.com/watch?v=IyZ1WIua_1s"

# Custom output file
python youtube_comments.py IyZ1WIua_1s -o my_comments.json
```

| Argument | Description |
|----------|-------------|
| `video` | YouTube video ID or full URL (required) |
| `-o FILE` | Output file (default: `<video_id>_comments.json`) |

---

### 5. `analyze_comments_deepseek.py` — AI Analysis

Sends comments in batches to the **DeepSeek API** for sentiment analysis using the Nike football prompt (`prompt.txt`). Supports **parallel workers**, **checkpointing**, and **auto-retry** on errors.

```bash
# Default — 2 parallel workers, batch size 25
python analyze_comments_deepseek.py yt_broad_nike_<timestamp>.json

# Resume from where it was interrupted
python analyze_comments_deepseek.py yt_broad_nike_<timestamp>.json --resume

# Force restart (ignore checkpoint)
python analyze_comments_deepseek.py yt_broad_nike_<timestamp>.json --force-restart

# Increase workers for speed (watch rate limits)
python analyze_comments_deepseek.py yt_broad_nike_<timestamp>.json --workers 3

# Conservative — single worker, smaller batch (safest for rate limits)
python analyze_comments_deepseek.py yt_broad_nike_<timestamp>.json --workers 1 --batch-size 15

# Print every comment's analysis to console
python analyze_comments_deepseek.py yt_broad_nike_<timestamp>.json --verbose

# Custom output file
python analyze_comments_deepseek.py yt_broad_nike_<timestamp>.json -o nike_analyzed.json
```

| Argument | Default | Description |
|----------|---------|-------------|
| `input` | — | Input JSON file (required) |
| `-o FILE` | `<input>_analyzed.json` | Output JSON file |
| `--batch-size N` | `25` | Comments sent per API call |
| `--workers N` | `2` | Parallel API workers (1 = sequential) |
| `--delay N` | `0.0` | Seconds between batches |
| `--api-key KEY` | `.env` | DeepSeek API key (overrides `.env`) |
| `--resume` | off | Resume from last checkpoint |
| `--force-restart` | off | Ignore existing checkpoint, start fresh |
| `--verbose` | off | Print each comment's full analysis |
| `--min-length N` | `3` | Skip comments shorter than N characters |

**Fields extracted per comment (from `prompt.txt`):**

| Field | Type | Description |
|-------|------|-------------|
| `sentiment` | `positive` / `negative` / `neutral` | Overall comment tone |
| `players_or_personalities` | `list` | Footballers, coaches, celebrities mentioned |
| `players_sentiment` | `list` | Sentiment toward each player (same order) |
| `nike_products_mentioned` | `list` | Specific Nike products (Mercurial, Air Max, etc.) |
| `competitor_comparison` | `positive` / `negative` / `neutral` / `none` | Whether Nike is compared to a competitor and in whose favour |
| `competitor_brands_mentioned` | `list` | Adidas, Puma, On, New Balance, Under Armour, etc. |

**Retry / error handling:**

| Error | Behaviour |
|-------|-----------|
| HTTP 429 / 529 (rate limit) | Exponential backoff: 2s → 4s → 8s → 16s → 32s (5 retries) |
| Truncated JSON response | Auto-splits batch in half and retries each half (up to 3 levels) |
| Single comment failure | Falls back to empty analysis — never crashes the run |
| Keyboard interrupt (Ctrl+C) | Saves checkpoint — resume with `--resume` |

---

### 6. `generate_dashboard.py` — HTML Dashboard

Generates a **single self-contained HTML file** from any analyzed (or raw) YouTube JSON. Handles both single-video and multi-video files automatically.

```bash
# Multi-video analyzed file (recommended)
python generate_dashboard.py yt_broad_nike_<timestamp>_analyzed.json

# Single video analyzed file
python generate_dashboard.py rip-the-script-analysis.json

# Custom output name
python generate_dashboard.py my_analyzed.json -o nike_dashboard.html
```

| Argument | Description |
|----------|-------------|
| `input` | JSON file — single or multi-video, analyzed or raw (required) |
| `-o FILE` | Output HTML file (default: `<input>_dashboard.html`) |

**Supported input formats:**

| Format | Key | Daily axis |
|--------|-----|------------|
| Single video | `video_metadata` + `comments` | Comment `published_at` date |
| Multi video | `videos[...]` | Video `published_at` date |

**Also supports old prompt fields** (`player_name`, `country`, `theme`) alongside new Nike fields — no re-analysis needed.

---

## Output Formats

### Multi-video JSON structure

```json
{
  "query": "Nike",
  "exported_at": "2026-06-15T10:00:00Z",
  "filters": { "days": 30 },
  "summary": { "total_videos": 398, "total_comments": 15230 },
  "videos": [
    {
      "video_id": "abc123",
      "title": "Nike Mercurial 2026 Launch",
      "channel": "Nike Football",
      "published_at": "2026-06-10T14:00:00Z",
      "view_count": 1200000,
      "like_count": 45000,
      "comment_count_reported": 3200,
      "comments": [
        {
          "comment_id": "xyz",
          "text": "Best boots ever!",
          "like_count": 240,
          "published_at": "2026-06-11",
          "sentiment": "positive",
          "players_or_personalities": ["Mbappe"],
          "players_sentiment": ["positive"],
          "nike_products_mentioned": ["Mercurial"],
          "competitor_comparison": "none",
          "competitor_brands_mentioned": []
        }
      ]
    }
  ]
}
```

---

## Dashboard Pages

| Page | Single Video | Multi Video |
|------|-------------|-------------|
| **📊 Overview** | KPI cards, sentiment donut, competitor comparison donut, comments/day bar, top players bar, products / brands / themes tables | Same but videos/day bar instead |
| **📅 Daily Trends** | Comments/day stacked bar, sentiment % ratio line, daily table | Videos/day bar + videos sentiment stacked, comments/day stacked, ratio line, full daily table |
| **👤 Players** | Stacked sentiment bar per player, detail table with positive/neutral/negative counts | Same |
| **👟 Products & Brands** | Nike products bar + pie, competitor brands bar + pie | Same |
| **🎬 All Videos** | ❌ (single video shown in banner) | ✅ Searchable/filterable table — click any row for popup with sentiment, players, products, brands, and top comments |
| **💬 Top Comments** | Top 50 most-liked comments with sentiment badges | Same |

---

## API Keys

| Key | Where to get it | Set in `.env` as |
|-----|----------------|-----------------|
| YouTube Data API v3 | [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → YouTube Data API v3 | `YOUTUBE_API_KEY` |
| DeepSeek API | [platform.deepseek.com](https://platform.deepseek.com/) | `DEEPSEEK_API_KEY` |

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `❌ Batch N FAILED: Unterminated string` | DeepSeek response truncated (too many comments) | Use `--batch-size 15` or lower |
| `HTTP 429` rate limit errors | Too many parallel requests | Use `--workers 1` and/or `--delay 1` |
| Only 500 videos found | YouTube API per-query cap | Use `youtube_broad_search.py` instead |
| Sentiment fields all `null` | File not yet analysed | Run `analyze_comments_deepseek.py` first |
| Dashboard shows ⚠️ yellow warning | No analysed comments in file | Run `analyze_comments_deepseek.py` on the file |
| `YOUTUBE_API_KEY not found` | Missing `.env` file | Create `.env` with your keys |
| Interrupted mid-analysis | Ctrl+C or crash | Re-run with `--resume` flag |
