"""
youtube_search.py
-----------------
Dump all YouTube search results for a query into a JSON file.

Date range options (mutually exclusive with --days):
  --after  2024-01-01   published on or after this date
  --before 2024-06-01   published on or before this date

Rolling window option:
  --days 30             videos published in the last N days (from today)

Examples
--------
  python youtube_search.py "python tutorials" --days 30
  python youtube_search.py "climate change" --after 2024-01-01 --before 2024-06-01
  python youtube_search.py "AI news" --days 7 -o ai_news.json
"""

import json
import argparse
from datetime import datetime, timedelta, timezone
from youtube_api import YouTubeAPI


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def rfc3339(dt: datetime) -> str:
    """Convert a datetime to the RFC 3339 format YouTube expects."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date_arg(value: str) -> datetime:
    """Accept YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Invalid date '{value}'. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ"
    )


def resolve_date_range(
    days: int | None,
    after: datetime | None,
    before: datetime | None,
) -> tuple[str | None, str | None]:
    """Return (published_after, published_before) as RFC 3339 strings or None."""
    if days is not None:
        now = datetime.now(tz=timezone.utc)
        return rfc3339(now - timedelta(days=days)), rfc3339(now)

    return (
        rfc3339(after) if after else None,
        rfc3339(before) if before else None,
    )


# ---------------------------------------------------------------------------
# Core fetcher
# ---------------------------------------------------------------------------

def fetch_all_search_results(
    query: str,
    published_after: str | None,
    published_before: str | None,
) -> list[dict]:
    """
    Page through the search endpoint and enrich each result with full
    statistics by batch-fetching video metadata (50 IDs per request).
    """
    api = YouTubeAPI()
    search_items: list[dict] = []
    next_page_token: str | None = None
    page = 0

    print(f'Searching: "{query}"')
    if published_after:
        print(f"  From : {published_after}")
    if published_before:
        print(f"  To   : {published_before}")

    # Step 1 — collect all search result stubs (id + basic snippet)
    while True:
        page += 1
        print(f"  Fetching search page {page}...", end="\r")

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

    print(f"\n  Found {len(search_items)} results. Enriching with statistics...")

    # Step 2 — batch-fetch full metadata (views, likes, duration, …)
    video_ids = [
        item["id"]["videoId"]
        for item in search_items
        if item.get("id", {}).get("kind") == "youtube#video"
    ]

    enriched: list[dict] = []
    batch_size = 50  # API limit per videos.list call
    for i in range(0, len(video_ids), batch_size):
        batch = video_ids[i : i + batch_size]
        enriched.extend(api.get_videos_metadata(batch))
        print(f"  Enriched {min(i + batch_size, len(video_ids))}/{len(video_ids)}...", end="\r")

    print(f"\n  Done. {len(enriched)} videos enriched.")
    return enriched


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_to_json(
    query: str,
    published_after: str | None,
    published_before: str | None,
    videos: list[dict],
    path: str,
) -> None:
    payload = {
        "exported_at": rfc3339(datetime.now(tz=timezone.utc)),
        "query": query,
        "filters": {
            "published_after": published_after,
            "published_before": published_before,
        },
        "total_results": len(videos),
        "videos": videos,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Saved to: {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_default_filename(query: str) -> str:
    slug = query.lower().replace(" ", "_")[:40]
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"yt_search_{slug}_{ts}.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump YouTube search results to JSON.",
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

    videos = fetch_all_search_results(args.query, published_after, published_before)
    save_to_json(args.query, published_after, published_before, videos, output_path)


if __name__ == "__main__":
    main()
