"""
youtube_broad_search.py
------------------------
Broad-coverage YouTube search that circumvents the ~500 result API limit
by running multiple narrower sub-queries, deduplicating, and combining.

Also fetches ALL comments from every video found.
Output format is identical to youtube_search_comments.py — fully compatible
with analyze_comments_deepseek.py.

Usage
-----
  # Auto-generate sub-queries from a base term
  python youtube_broad_search.py "Nike" --days 30

  # Provide your own sub-queries
  python youtube_broad_search.py "Nike" --days 30 \\
      --sub-queries "Nike football" "Nike basketball" "Nike running" "Nike Mercurial"

  # Custom output file
  python youtube_broad_search.py "Nike" --days 30 -o nike_broad_30d.json

  # Skip videos with disabled comments
  python youtube_broad_search.py "Nike" --days 30 --skip-disabled
"""

import json
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from collections import OrderedDict
from typing import Optional
from requests.exceptions import HTTPError

from youtube_api import YouTubeAPI
from youtube_search import resolve_date_range, parse_date_arg, rfc3339
from youtube_comments import parse_comment_thread


# ---------------------------------------------------------------------------
# Auto-generate sub-queries from a base term
# ---------------------------------------------------------------------------

def generate_sub_queries(base: str) -> list[str]:
    """
    Expand a broad search term into narrower queries to maximize coverage.
    Always includes the base term itself as the first query.
    """
    expanders = [
        # Football / soccer
        "football", "soccer", "football boots", "soccer cleats",
        # Basketball
        "basketball", "basketball shoes",
        # Running
        "running", "running shoes",
        # Products
        "Mercurial", "Phantom", "Tiempo", "Air Max", "Air Zoom",
        "Nike Pro", "Nike Tech", "Nike Dri-FIT", "Nike Vapor",
        # Campaigns / content
        "ad", "commercial", "campaign", "new release",
        # Training / lifestyle
        "training", "lifestyle", "sportswear",
        # Athletes
        "Ronaldo", "Mbappe",
    ]

    sub_queries = [base]  # always include the base term
    for suffix in expanders:
        sub_queries.append(f"{base} {suffix}")

    return sub_queries


# ---------------------------------------------------------------------------
# Interactive prompt
# ---------------------------------------------------------------------------

def prompt_continue(
    idx: int,
    total: int,
    query: str,
    results_so_far: int,
    unique_so_far: int,
    auto_save_path: str | None = None,
) -> str:
    """
    Ask the user whether to continue after a sub-query completes.
    Returns one of: 'proceed', 'stop_save', 'stop_nosave', 'quit'
    """
    print(f"\n{'─' * 55}")
    print(f"  Query {idx}/{total} complete: \"{query}\"")
    print(f"  Results so far → {unique_so_far} unique videos (this query: {results_so_far})")
    if auto_save_path:
        print(f"  Auto-saved to: {auto_save_path}")
    print(f"{'─' * 55}")
    print(f"  [p] Proceed to next query")
    print(f"  [s] Stop searching → fetch comments on what we have (recommended)")
    print(f"  [q] Quit now → save current findings without comments")
    print(f"{'─' * 55}")

    while True:
        try:
            choice = input("  Your choice [p/s/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "stop_save"

        if choice in ("p", "proceed"):
            return "proceed"
        elif choice in ("s", "stop", "stop_save"):
            return "stop_save"
        elif choice in ("q", "quit", "stop_nosave"):
            return "stop_nosave"
        else:
            print(f"  ⚠ Invalid choice '{choice}'. Enter p, s, or q.")

# ---------------------------------------------------------------------------

def search_with_retry(
    api: YouTubeAPI,
    query: str,
    published_after: Optional[str],
    published_before: Optional[str],
    max_retries: int = 3,
) -> list[dict]:
    """
    Fetch all search results for a single query with retry logic.
    Returns enriched video dicts.
    """
    for attempt in range(max_retries):
        try:
            return _search_single(api, query, published_after, published_before)
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 2
                print(f"    ⚠ Attempt {attempt+1} failed ({e}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"    ❌ All {max_retries} attempts failed for '{query}': {e}")
                return []

    return []


def _search_single(
    api: YouTubeAPI,
    query: str,
    published_after: Optional[str],
    published_before: Optional[str],
) -> list[dict]:
    """Single-query search: page through all results and enrich."""
    search_items = []
    next_page_token = None
    page = 0

    while True:
        page += 1
        print(f"      Page {page}...", end="\r")

        data = api.search_videos(
            query=query,
            published_after=published_after,
            published_before=published_before,
            page_token=next_page_token,
        )
        search_items.extend(data.get("items", []))

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    total = len(search_items)
    print(f"      Found {total} results{' ' * 10}")

    if not search_items:
        return []

    # Extract video IDs
    video_ids = [
        item["id"]["videoId"]
        for item in search_items
        if item.get("id", {}).get("kind") == "youtube#video"
    ]

    # Batch-enrich with full metadata
    enriched = []
    batch_size = 50
    for i in range(0, len(video_ids), batch_size):
        batch = video_ids[i : i + batch_size]
        enriched.extend(api.get_videos_metadata(batch))
        print(f"      Enriching {min(i + batch_size, len(video_ids))}/{len(video_ids)}...", end="\r")

    print(f"      Enriched {len(enriched)}/{len(video_ids)}{' ' * 10}")
    return enriched


# ---------------------------------------------------------------------------
# Fetch comments for a video
# ---------------------------------------------------------------------------

def fetch_comments_for_video(
    api: YouTubeAPI,
    video: dict,
    skip_disabled: bool,
) -> Optional[dict]:
    """Fetch all comments for one video. Returns None if skipped."""
    video_id = video["video_id"]
    comments = []
    next_page_token = None
    page = 0
    disabled = False

    while True:
        page += 1
        try:
            data = api.get_comment_page(video_id, next_page_token)
        except HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 403:
                disabled = True
                break
            elif status == 404:
                return None
            else:
                return None

        comments.extend(parse_comment_thread(item) for item in data.get("items", []))
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    if disabled:
        if skip_disabled:
            return None
        else:
            pass  # include with empty comment list

    total_replies = sum(len(c["replies"]) for c in comments)
    return {
        "video_id": video_id,
        "title": video["title"],
        "channel": video["channel"],
        "channel_id": video.get("channel_id", ""),
        "description": video.get("description", ""),
        "published_at": video["published_at"],
        "thumbnail": video.get("thumbnail", ""),
        "duration": video.get("duration", ""),
        "view_count": video["view_count"],
        "like_count": video["like_count"],
        "comment_count_reported": video["comment_count"],
        "counts": {
            "top_level_fetched": len(comments),
            "replies_fetched": total_replies,
            "total_fetched": len(comments) + total_replies,
            "comments_disabled": disabled,
        },
        "comments": comments,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def broad_search_and_fetch(
    base_query: str,
    sub_queries: list[str],
    published_after: Optional[str],
    published_before: Optional[str],
    skip_disabled: bool,
    delay_between_queries: float = 1.0,
    interactive: bool = True,
    auto_save_path: Optional[str] = None,
) -> tuple[list[dict], dict]:
    """
    Run multiple sub-queries, deduplicate, fetch comments for all unique videos.
    After each sub-query, prompts the user to proceed/stop/quit (if interactive=True).
    Returns (results, summary).
    """
    api = YouTubeAPI()

    # ── Step 1: Search all sub-queries (interactive) ──
    all_videos = OrderedDict()  # video_id → video dict (preserves order, dedupes)
    stop_early = False
    stopped_after_query = 0

    print(f"\n{'='*60}")
    print(f"🔍 PHASE 1: Broad search across {len(sub_queries)} queries")
    print(f"   Interactive mode: {'ON' if interactive else 'OFF'}")
    print(f"{'='*60}\n")

    for idx, q in enumerate(sub_queries, start=1):
        print(f"[{idx}/{len(sub_queries)}] Searching: \"{q}\"")
        results = search_with_retry(api, q, published_after, published_before)
        new_count = 0
        for video in results:
            vid = video["video_id"]
            if vid not in all_videos:
                all_videos[vid] = video
                new_count += 1
        print(f"    ✅ {len(results)} found, {new_count} new (total unique: {len(all_videos)})\n")

        # Auto-save intermediate results
        if auto_save_path:
            intermediate = {
                "exported_at": rfc3339(datetime.now(tz=timezone.utc)),
                "query": base_query,
                "search_method": "broad_multi_query",
                "intermediate": True,
                "queries_completed": idx,
                "queries_total": len(sub_queries),
                "filters": {
                    "published_after": published_after,
                    "published_before": published_before,
                },
                "unique_videos_so_far": len(all_videos),
                "videos": list(all_videos.values()),
            }
            interm_path = auto_save_path.replace(".json", f"_intermediate_{idx}.json")
            with open(interm_path, "w", encoding="utf-8") as f:
                json.dump(intermediate, f, ensure_ascii=False, indent=2)

        # Interactive prompt after each query (except the last)
        if interactive and idx < len(sub_queries):
            choice = prompt_continue(
                idx=idx,
                total=len(sub_queries),
                query=q,
                results_so_far=len(results),
                unique_so_far=len(all_videos),
                auto_save_path=interm_path if auto_save_path else None,
            )

            if choice == "stop_save":
                stop_early = True
                stopped_after_query = idx
                break
            elif choice == "stop_nosave":
                print(f"\n  🛑 Quit without saving. Exiting.")
                sys.exit(0)

        if idx < len(sub_queries) and delay_between_queries > 0 and not stop_early:
            time.sleep(delay_between_queries)

    unique_videos = list(all_videos.values())

    if stop_early:
        print(f"\n{'='*60}")
        print(f"  ⏸ STOPPED after query {stopped_after_query}/{len(sub_queries)}")
        print(f"  📊 {len(unique_videos)} unique videos collected so far")
        print(f"{'='*60}\n")
    else:
        print(f"{'='*60}")
        print(f"📊 SEARCH COMPLETE: {len(unique_videos)} unique videos across {len(sub_queries)} queries")
        print(f"{'='*60}\n")

    if not unique_videos:
        print("No videos found.")
        return [], {
            "sub_queries_run": stopped_after_query if stop_early else len(sub_queries),
            "sub_queries_total": len(sub_queries),
            "stopped_early": stop_early,
            "total_results_across_queries": 0,
            "unique_videos": 0,
            "videos_fetched": 0,
            "videos_skipped": 0,
            "total_comments_fetched": 0,
        }

    # ── Step 2: Fetch comments (also interactive) ──
    print(f"{'='*60}")
    print(f"💬 PHASE 2: Fetching comments for {len(unique_videos)} videos")
    print(f"{'='*60}\n")

    results = []
    skipped = 0
    skipped_disabled = 0
    total_comments = 0
    stop_fetching = False

    for idx, video in enumerate(unique_videos, start=1):
        title_preview = video["title"][:70]
        print(f"[{idx}/{len(unique_videos)}] {title_preview}")
        result = fetch_comments_for_video(api, video, skip_disabled)

        if result is None:
            skipped += 1
        else:
            if result["counts"]["comments_disabled"]:
                skipped_disabled += 1

            fetched = result["counts"]["total_fetched"]
            total_comments += fetched
            status = "🔒 disabled" if result["counts"]["comments_disabled"] else f"{fetched} total"
            print(f"    {result['counts']['top_level_fetched']:,} top-level + "
                  f"{result['counts']['replies_fetched']:,} replies → {status}")
            results.append(result)

        # Prompt every 20 videos (to avoid excessive interruptions)
        if interactive and idx < len(unique_videos) and idx % 20 == 0:
            print(f"\n{'─' * 55}")
            print(f"  Processed {idx}/{len(unique_videos)} videos so far")
            print(f"  Comments collected: {total_comments:,}")
            print(f"  [p] Proceed  [s] Stop & save what we have  [q] Quit without saving")
            print(f"{'─' * 55}")
            while True:
                try:
                    choice = input("  Your choice [p/s/q]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "s"

                if choice in ("p", "proceed"):
                    break
                elif choice in ("s", "stop", "stop_save"):
                    stop_fetching = True
                    break
                elif choice in ("q", "quit"):
                    print(f"\n  🛑 Quit without saving. Exiting.")
                    sys.exit(0)
                else:
                    print(f"  ⚠ Invalid choice '{choice}'. Enter p, s, or q.")

            if stop_fetching:
                print(f"\n  ⏸ Stopped comment fetching at video {idx}/{len(unique_videos)}")
                break

    summary = {
        "sub_queries_run": stopped_after_query if stop_early else len(sub_queries),
        "sub_queries_total": len(sub_queries),
        "stopped_early_search": stop_early,
        "stopped_early_comments": stop_fetching,
        "sub_queries": sub_queries[:stopped_after_query] if stop_early else sub_queries,
        "total_results_across_queries": len(unique_videos),
        "unique_videos": len(unique_videos),
        "videos_fetched": len(results),
        "videos_skipped": skipped,
        "videos_comments_disabled": skipped_disabled,
        "total_comments_fetched": total_comments,
    }

    print(f"\n{'─' * 50}")
    print(f"  Sub-queries planned   : {len(sub_queries)}")
    print(f"  Sub-queries executed  : {summary['sub_queries_run']}")
    if stop_early:
        print(f"  ⚠ Search stopped early")
    if stop_fetching:
        print(f"  ⚠ Comment fetch stopped early")
    print(f"  Unique videos found   : {summary['unique_videos']:,}")
    print(f"  Videos with comments  : {summary['videos_fetched']:,}")
    print(f"  Videos skipped        : {summary['videos_skipped']:,}")
    print(f"  Comments disabled     : {summary['videos_comments_disabled']:,}")
    print(f"  Total comments        : {summary['total_comments_fetched']:,}")
    print(f"{'─' * 50}\n")

    return results, summary


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_to_json(
    base_query: str,
    published_after: Optional[str],
    published_before: Optional[str],
    results: list[dict],
    summary: dict,
    path: str,
) -> None:
    payload = {
        "exported_at": rfc3339(datetime.now(tz=timezone.utc)),
        "query": base_query,
        "search_method": "broad_multi_query",
        "filters": {
            "published_after": published_after,
            "published_before": published_before,
        },
        "summary": summary,
        "videos": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"💾 Saved to: {path}")


def build_default_filename(query: str) -> str:
    slug = query.lower().replace(" ", "_")[:40]
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"yt_broad_{slug}_{ts}.json"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Broad YouTube search + comments (multi-query, de-duplicated).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", help="Base search term")

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        "--days",
        type=int,
        metavar="N",
        help="Videos published in the last N days",
    )
    date_group.add_argument(
        "--after",
        type=parse_date_arg,
        metavar="DATE",
        help="Published on or after YYYY-MM-DD",
    )
    parser.add_argument(
        "--before",
        type=parse_date_arg,
        metavar="DATE",
        help="Published on or before YYYY-MM-DD",
    )
    parser.add_argument(
        "--sub-queries",
        nargs="+",
        default=None,
        metavar="Q",
        help="Custom sub-queries (space-separated). If omitted, auto-generated.",
    )
    parser.add_argument(
        "--no-auto-expand",
        action="store_true",
        help="Do NOT auto-generate sub-queries; use only the base query",
    )
    parser.add_argument(
        "--skip-disabled",
        action="store_true",
        help="Skip videos with disabled comments",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between sub-queries (default: 1.0)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run without prompts — process all queries and comments automatically",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output JSON file path",
    )
    args = parser.parse_args()

    if args.before and args.days:
        parser.error("--before cannot be used together with --days")

    published_after, published_before = resolve_date_range(
        days=args.days,
        after=args.after,
        before=args.before,
    )

    # Determine sub-queries
    if args.no_auto_expand:
        sub_queries = [args.query]
    elif args.sub_queries:
        sub_queries = args.sub_queries
    else:
        sub_queries = generate_sub_queries(args.query)

    print(f"\n🎯 Base query: \"{args.query}\"")
    print(f"📋 Sub-queries ({len(sub_queries)}):")
    for q in sub_queries:
        print(f"    • \"{q}\"")
    print()

    output_path = args.output or build_default_filename(args.query)

    results, summary = broad_search_and_fetch(
        base_query=args.query,
        sub_queries=sub_queries,
        published_after=published_after,
        published_before=published_before,
        skip_disabled=args.skip_disabled,
        delay_between_queries=args.delay,
        interactive=not args.non_interactive,
        auto_save_path=output_path,
    )

    if results:
        save_to_json(args.query, published_after, published_before, results, summary, output_path)


if __name__ == "__main__":
    main()
