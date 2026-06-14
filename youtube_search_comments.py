"""
youtube_search_comments.py
--------------------------
Search YouTube for a query within a time period, then fetch ALL comments
from every result video and save everything into a single JSON file.

Date range options (mutually exclusive with --days):
  --after  2024-01-01   videos published on or after this date
  --before 2024-06-01   videos published on or before this date

Rolling window option:
  --days 30             videos published in the last N days (from today)

Flags:
  --skip-disabled       skip videos with comments disabled (default: warn and continue)

Examples
--------
  python youtube_search_comments.py "nike football" --days 90
  python youtube_search_comments.py "climate change" --after 2024-01-01 --before 2024-06-01
  python youtube_search_comments.py "AI news" --days 7 -o ai_news_comments.json
"""

import json
import argparse
from datetime import datetime, timedelta, timezone
from requests.exceptions import HTTPError

from youtube_api import YouTubeAPI
from youtube_search import fetch_all_search_results, resolve_date_range, parse_date_arg, rfc3339
from youtube_comments import parse_comment_thread


# ---------------------------------------------------------------------------
# Comment fetcher (per video, errors handled gracefully)
# ---------------------------------------------------------------------------

def fetch_comments_for_video(api: YouTubeAPI, video: dict, skip_disabled: bool) -> dict | None:
    """
    Fetch all comments for a single video.
    Returns a result dict, or None if the video should be skipped entirely.
    """
    video_id = video["video_id"]
    comments: list[dict] = []
    next_page_token: str | None = None
    page = 0
    disabled = False

    while True:
        page += 1
        try:
            data = api.get_comment_page(video_id, next_page_token)
        except HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 403:
                # Comments are disabled or restricted on this video
                disabled = True
                break
            elif status == 404:
                print(f"    [404] Video not found, skipping.")
                return None
            else:
                print(f"    [HTTP {status}] Unexpected error, skipping.")
                return None

        comments.extend(parse_comment_thread(item) for item in data.get("items", []))
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    if disabled:
        if skip_disabled:
            print(f"    [skipped] Comments disabled.")
            return None
        else:
            print(f"    [warning] Comments disabled — included with empty comment list.")

    total_replies = sum(len(c["replies"]) for c in comments)
    return {
        "video_id": video_id,
        "title": video["title"],
        "channel": video["channel"],
        "published_at": video["published_at"],
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

def fetch_all_search_comments(
    query: str,
    published_after: str | None,
    published_before: str | None,
    skip_disabled: bool,
) -> tuple[list[dict], dict]:
    """
    Search for videos, then fetch comments for each one.
    Returns (results, summary_stats).
    """
    api = YouTubeAPI()

    # Step 1 — get all videos matching the search
    videos = fetch_all_search_results(query, published_after, published_before)
    total_videos = len(videos)

    if not videos:
        print("No videos found for the given query and date range.")
        return [], {}

    # Step 2 — fetch comments video by video
    print(f"\nFetching comments for {total_videos} videos...\n")
    results: list[dict] = []
    skipped = 0
    total_comments = 0

    for idx, video in enumerate(videos, start=1):
        print(f"[{idx}/{total_videos}] {video['title'][:70]}")
        result = fetch_comments_for_video(api, video, skip_disabled)

        if result is None:
            skipped += 1
            continue

        fetched = result["counts"]["total_fetched"]
        total_comments += fetched
        print(f"    {result['counts']['top_level_fetched']:,} top-level + "
              f"{result['counts']['replies_fetched']:,} replies = {fetched:,} total")
        results.append(result)

    summary = {
        "videos_found": total_videos,
        "videos_fetched": len(results),
        "videos_skipped": skipped,
        "total_comments_fetched": total_comments,
    }

    print(f"\n{'─' * 50}")
    print(f"  Videos found    : {summary['videos_found']:,}")
    print(f"  Videos fetched  : {summary['videos_fetched']:,}")
    print(f"  Videos skipped  : {summary['videos_skipped']:,}")
    print(f"  Total comments  : {summary['total_comments_fetched']:,}")
    print(f"{'─' * 50}\n")

    return results, summary


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_to_json(
    query: str,
    published_after: str | None,
    published_before: str | None,
    results: list[dict],
    summary: dict,
    path: str,
) -> None:
    payload = {
        "exported_at": rfc3339(datetime.now(tz=timezone.utc)),
        "query": query,
        "filters": {
            "published_after": published_after,
            "published_before": published_before,
        },
        "summary": summary,
        "videos": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Saved to: {path}")


def build_default_filename(query: str) -> str:
    slug = query.lower().replace(" ", "_")[:40]
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"yt_search_comments_{slug}_{ts}.json"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search YouTube and fetch all comments from every result video.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", help="Search string")

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
        help="Published on or before YYYY-MM-DD (used with --after)",
    )
    parser.add_argument(
        "--skip-disabled",
        action="store_true",
        help="Silently skip videos with comments disabled (default: include with empty list)",
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

    output_path = args.output or build_default_filename(args.query)

    results, summary = fetch_all_search_comments(
        query=args.query,
        published_after=published_after,
        published_before=published_before,
        skip_disabled=args.skip_disabled,
    )

    if results:
        save_to_json(args.query, published_after, published_before, results, summary, output_path)


if __name__ == "__main__":
    main()
