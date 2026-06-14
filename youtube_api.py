import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://www.googleapis.com/youtube/v3"


class YouTubeAPI:
    """Singleton client for the YouTube Data API v3."""

    _instance: "YouTubeAPI | None" = None

    def __new__(cls) -> "YouTubeAPI":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        api_key = os.getenv("YOUTUBE-API-KEY")
        if not api_key:
            raise ValueError("YOUTUBE-API-KEY not found in .env file")
        self._base_params = {"key": api_key}

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_video_metadata(self, video_id: str) -> dict:
        """Return snippet + statistics for a single video."""
        data = self._get(
            "videos",
            params={"part": "snippet,statistics", "id": video_id},
        )
        items = data.get("items", [])
        if not items:
            return {}

        snippet = items[0]["snippet"]
        stats = items[0].get("statistics", {})
        return {
            "video_id": video_id,
            "title": snippet["title"],
            "channel": snippet["channelTitle"],
            "published_at": snippet["publishedAt"],
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
        }

    def search_videos(
        self,
        query: str,
        published_after: str | None = None,
        published_before: str | None = None,
        page_token: str | None = None,
        max_results: int = 50,
    ) -> dict:
        """
        Search for videos matching *query* within an optional date range.

        published_after / published_before must be RFC 3339 strings,
        e.g. "2024-01-01T00:00:00Z".
        """
        params: dict = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max_results,
            "order": "date",
        }
        if published_after:
            params["publishedAfter"] = published_after
        if published_before:
            params["publishedBefore"] = published_before
        if page_token:
            params["pageToken"] = page_token
        return self._get("search", params=params)

    def get_videos_metadata(self, video_ids: list[str]) -> list[dict]:
        """
        Return snippet + statistics for up to 50 video IDs in one request.
        The search endpoint does not include statistics, so we batch-fetch them.
        """
        data = self._get(
            "videos",
            params={
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids),
            },
        )
        results = []
        for item in data.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            details = item.get("contentDetails", {})
            results.append({
                "video_id": item["id"],
                "title": snippet["title"],
                "channel": snippet["channelTitle"],
                "channel_id": snippet["channelId"],
                "description": snippet.get("description", ""),
                "published_at": snippet["publishedAt"],
                "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                "duration": details.get("duration", ""),
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
            })
        return results

    def get_comment_page(self, video_id: str, page_token: str | None = None) -> dict:
        """Return one page (up to 100 threads) of comments."""
        params: dict = {
            "part": "snippet,replies",
            "videoId": video_id,
            "maxResults": 100,
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        return self._get("commentThreads", params=params)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: dict) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        response = requests.get(url, params={**self._base_params, **params}, timeout=10)
        response.raise_for_status()
        return response.json()
