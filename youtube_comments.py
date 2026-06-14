import json
import argparse
from datetime import datetime
from youtube_api import YouTubeAPI


def get_video_id(url_or_id: str) -> str:
    """Extract video ID from a YouTube URL or return as-is."""
    if "youtube.com/watch?v=" in url_or_id:
        return url_or_id.split("v=")[1].split("&")[0]
    if "youtu.be/" in url_or_id:
        return url_or_id.split("youtu.be/")[1].split("?")[0]
    return url_or_id


def parse_comment_thread(item: dict) -> dict:
    """Convert a raw API comment-thread item into a clean dict."""
    top = item["snippet"]["topLevelComment"]["snippet"]
    comment = {
        "comment_id": item["snippet"]["topLevelComment"]["id"],
        "author": top["authorDisplayName"],
        "author_channel_id": top.get("authorChannelId", {}).get("value", ""),
        "text": top["textDisplay"],
        "like_count": top["likeCount"],
        "published_at": top["publishedAt"],
        "updated_at": top["updatedAt"],
        "reply_count": item["snippet"]["totalReplyCount"],
        "replies": [],
    }

    for reply in item.get("replies", {}).get("comments", []):
        r = reply["snippet"]
        comment["replies"].append(
            {
                "comment_id": reply["id"],
                "author": r["authorDisplayName"],
                "author_channel_id": r.get("authorChannelId", {}).get("value", ""),
                "text": r["textDisplay"],
                "like_count": r["likeCount"],
                "published_at": r["publishedAt"],
                "updated_at": r["updatedAt"],
                "parent_id": r["parentId"],
            }
        )

    return comment


def fetch_all_comments(video_id: str) -> list[dict]:
    """Page through all comment threads and return a flat list."""
    api = YouTubeAPI()
    comments: list[dict] = []
    next_page_token: str | None = None
    page = 0

    print(f"Fetching comments for video: {video_id}")
    while True:
        page += 1
        print(f"  Page {page}...", end="\r")

        data = api.get_comment_page(video_id, next_page_token)
        comments.extend(parse_comment_thread(item) for item in data.get("items", []))

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    total_replies = sum(len(c["replies"]) for c in comments)
    print(f"\n  Top-level comments : {len(comments):,}")
    print(f"  Replies fetched    : {total_replies:,}")
    print(f"  Total fetched      : {len(comments) + total_replies:,}")
    return comments


def save_to_json(
    video_id: str, metadata: dict, comments: list[dict], path: str
) -> None:
    total_replies = sum(len(c["replies"]) for c in comments)
    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "video_metadata": metadata,
        "counts": {
            "reported_by_youtube": metadata.get("comment_count", 0),
            "top_level_fetched": len(comments),
            "replies_fetched": total_replies,
            "total_fetched": len(comments) + total_replies,
            "note": (
                "YouTube's reported count includes all comments and replies and is "
                "approximate — it may include deleted or spam comments no longer "
                "accessible via the API."
            ),
        },
        "comments": comments,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Saved to: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download all YouTube comments to JSON."
    )
    parser.add_argument("video", help="Video ID or URL")
    parser.add_argument(
        "-o", "--output", default=None, help="Output file (default: <id>_comments.json)"
    )
    args = parser.parse_args()

    video_id = get_video_id(args.video)
    output_path = args.output or f"{video_id}_comments.json"

    api = YouTubeAPI()

    print("Fetching video metadata...")
    metadata = api.get_video_metadata(video_id)
    if metadata:
        print(f"  Title   : {metadata['title']}")
        print(f"  Channel : {metadata['channel']}")
        print(f"  Comments: {metadata['comment_count']:,}")

    comments = fetch_all_comments(video_id)
    save_to_json(video_id, metadata, comments, output_path)

    total_replies = sum(len(c["replies"]) for c in comments)
    print(f"\n  YouTube reported   : {metadata.get('comment_count', 0):,}")
    print(f"  Total fetched      : {len(comments) + total_replies:,}")
    print("  (Difference is normal — see counts.note in the JSON for details)")


if __name__ == "__main__":
    main()
