from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.models import VideoItem


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def extract_video_id(url_or_id: str) -> str | None:
    """YouTube 動画URLまたは11文字の動画IDから videoId を抽出する."""
    raw = (url_or_id or "").strip()
    if not raw:
        return None
    if _VIDEO_ID_RE.match(raw):
        return raw

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if host in ("youtu.be", "www.youtu.be"):
        candidate = path.strip("/").split("/")[0]
        return candidate if _VIDEO_ID_RE.match(candidate) else None

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            candidate = qs["v"][0].strip()
            if _VIDEO_ID_RE.match(candidate):
                return candidate
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
            candidate = parts[1].strip()
            if _VIDEO_ID_RE.match(candidate):
                return candidate

    return None


def parse_channel_input(url_or_text: str) -> tuple[str, str]:
    """チャンネルURL / @ハンドル / UC...ID / 動画URL から種別と識別子を抽出する.

    Returns:
        ("channel_id", "UC...") | ("handle", "@...") | ("video_id", "...")
    """
    raw = (url_or_text or "").strip()
    if not raw:
        raise ValueError("URL またはチャンネルID / ハンドルを入力してください。")

    if _CHANNEL_ID_RE.match(raw):
        return ("channel_id", raw)
    if raw.startswith("@") and "/" not in raw:
        return ("handle", raw)

    vid = extract_video_id(raw)
    if vid:
        return ("video_id", vid)

    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and "youtube.com" in (parsed.netloc or "").lower():
        parts = [unquote(p) for p in parsed.path.split("/") if p]
        if parts:
            if parts[0].startswith("@"):
                return ("handle", parts[0])
            if parts[0] == "channel" and len(parts) >= 2 and _CHANNEL_ID_RE.match(parts[1]):
                return ("channel_id", parts[1])
            if parts[0] in ("c", "user") and len(parts) >= 2:
                return ("handle", f"@{parts[1]}")

    raise ValueError(
        "対応していないURL/形式です。YouTubeのチャンネルURL（/@handle や /channel/UC...）、@ハンドル、または動画URLを入力してください。"
    )


class TransientAPIError(RuntimeError):
    """リトライ対象の一時的なHTTPエラー（429 / 5xx）."""


class YouTubeAPIError(RuntimeError):
    """YouTube Data API v3 呼び出しエラー."""


def sanitize_text(text: str, max_length: int) -> str:
    """制御文字を除去し、改行を統一したうえで指定長に切り詰める."""
    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _CONTROL_CHARS_RE.sub("", cleaned).strip()
    if len(cleaned) > max_length:
        return cleaned[: max(0, max_length - 3)] + "..."
    return cleaned


def _parse_iso_datetime(value: str) -> datetime:
    """YouTubeのISO8601日時文字列（末尾Z含む）をUTC aware datetimeに変換する."""
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _select_best_thumbnail(thumbnails: dict[str, Any]) -> str:
    """利用可能な最も高解像度のサムネイルURLを返す."""
    for key in ("maxres", "standard", "high", "medium", "default"):
        info = thumbnails.get(key)
        if isinstance(info, dict) and info.get("url"):
            return str(info["url"])
    return ""


class YouTubeClient:
    """YouTube Data API v3 クライアント（省クォータ方式）."""

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        timeout: int = 30,
        logger: logging.Logger | None = None,
    ) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        retry=retry_if_exception_type((TransientAPIError, requests.RequestException)),
    )
    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{YOUTUBE_API_BASE}/{endpoint}"
        query = {**params, "key": self.api_key}
        resp = self.session.get(url, params=query, timeout=self.timeout)
        if resp.status_code in (429, 500, 502, 503, 504):
            raise TransientAPIError(
                f"YouTube API 一時エラー ({resp.status_code}) on {endpoint}: {resp.text[:300]}"
            )
        if not resp.ok:
            raise YouTubeAPIError(
                f"YouTube API エラー ({resp.status_code}) on {endpoint}: {resp.text[:500]}"
            )
        return resp.json()

    def resolve_channel_by_handle(self, handle: str) -> dict[str, str]:
        """@handle からチャンネルIDと公式タイトルを取得する（1ユニット）."""
        normalized = handle if handle.startswith("@") else f"@{handle}"
        data = self._get(
            "channels",
            {
                "part": "id,snippet,contentDetails",
                "forHandle": normalized,
            },
        )
        items = data.get("items", [])
        if not items:
            raise YouTubeAPIError(f"ハンドル '{normalized}' に対応するチャンネルが見つかりません。")
        ch = items[0]
        return {
            "channel_id": ch["id"],
            "title": ch["snippet"]["title"],
            "handle": ch["snippet"].get("customUrl", ""),
            "uploads_playlist_id": ch["contentDetails"]["relatedPlaylists"]["uploads"],
        }

    def get_channel_info(self, channel_id: str) -> dict[str, str]:
        """channel_id からアップロード再生リストIDとチャンネル名を取得する（1ユニット）."""
        data = self._get(
            "channels",
            {
                "part": "snippet,contentDetails",
                "id": channel_id,
            },
        )
        items = data.get("items", [])
        if not items:
            raise YouTubeAPIError(f"channel_id '{channel_id}' が見つかりません。")
        ch = items[0]
        return {
            "channel_id": ch["id"],
            "title": ch["snippet"]["title"],
            "handle": ch["snippet"].get("customUrl", ""),
            "uploads_playlist_id": ch["contentDetails"]["relatedPlaylists"]["uploads"],
        }

    def resolve_channel_from_input(self, url_or_text: str) -> dict[str, str]:
        """チャンネルURL / @ハンドル / channel_id / 動画URL からチャンネル情報を解決する."""
        kind, value = parse_channel_input(url_or_text)
        if kind == "handle":
            info = self.resolve_channel_by_handle(value)
            info.setdefault("handle", value)
            return info
        if kind == "channel_id":
            return self.get_channel_info(value)
        if kind == "video_id":
            data = self._get("videos", {"part": "snippet", "id": value})
            items = data.get("items", [])
            if not items:
                raise YouTubeAPIError(f"動画ID '{value}' が見つかりません。")
            ch_id = items[0]["snippet"]["channelId"]
            return self.get_channel_info(ch_id)
        raise YouTubeAPIError(f"チャンネル解決に失敗しました: {url_or_text}")

    def fetch_videos_by_ids(
        self,
        video_ids: list[str],
        description_max_length: int = 5000,
        now: datetime | None = None,
    ) -> list[VideoItem]:
        """指定した video_id リストから動画詳細を直接取得する（単発URL登録用）."""
        fetched_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        unique_ids = list(dict.fromkeys(vid.strip() for vid in video_ids if vid.strip()))
        if not unique_ids:
            return []
        return self._get_videos_detail(
            video_ids=unique_ids,
            fallback_channel_name="",
            fetched_at=fetched_at,
            description_max_length=description_max_length,
        )

    def _list_recent_video_ids(
        self,
        uploads_playlist_id: str,
        cutoff_dt: datetime,
        max_videos: int,
    ) -> list[str]:
        """uploads プレイリストから直近動画の videoId 一覧を取得する（1ページ=1ユニット）."""
        video_ids: list[str] = []
        seen: set[str] = set()
        page_token: str | None = None

        while len(video_ids) < max_videos:
            params: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": min(50, max_videos - len(video_ids)),
            }
            if page_token:
                params["pageToken"] = page_token

            data = self._get("playlistItems", params)
            items = data.get("items", [])
            if not items:
                break

            page_has_recent = False
            for item in items:
                content_details = item.get("contentDetails", {})
                snippet = item.get("snippet", {})
                vid = content_details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
                pub_str = content_details.get("videoPublishedAt") or snippet.get("publishedAt")
                if not vid or not pub_str:
                    continue

                pub_dt = _parse_iso_datetime(pub_str)
                if pub_dt >= cutoff_dt:
                    page_has_recent = True
                    if vid not in seen:
                        seen.add(vid)
                        video_ids.append(vid)

            page_token = data.get("nextPageToken")
            if not page_token or not page_has_recent:
                break

        return video_ids

    def _get_videos_detail(
        self,
        video_ids: list[str],
        fallback_channel_name: str,
        fetched_at: datetime,
        description_max_length: int,
    ) -> list[VideoItem]:
        """videos.list で詳細情報を50件ずつ一括取得する（50件=1ユニット）."""
        results: list[VideoItem] = []

        for idx in range(0, len(video_ids), 50):
            batch = video_ids[idx : idx + 50]
            data = self._get(
                "videos",
                {
                    "part": "snippet",
                    "id": ",".join(batch),
                },
            )
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                # 未公開のライブ配信予約（プレミア公開前）は除外
                if snippet.get("liveBroadcastContent") == "upcoming":
                    continue

                vid = str(item["id"])
                title = sanitize_text(str(snippet.get("title", "")), max_length=255)
                channel_name = str(snippet.get("channelTitle") or fallback_channel_name).strip()
                published_at = _parse_iso_datetime(str(snippet["publishedAt"]))
                description = sanitize_text(
                    str(snippet.get("description", "")),
                    max_length=description_max_length,
                )
                thumbnail_url = _select_best_thumbnail(snippet.get("thumbnails", {}))

                results.append(
                    VideoItem(
                        video_id=vid,
                        title=title,
                        video_url=f"https://www.youtube.com/watch?v={vid}",
                        channel_name=channel_name,
                        published_at=published_at,
                        fetched_at=fetched_at,
                        description=description,
                        thumbnail_url=thumbnail_url,
                        process_status="未分析",
                    )
                )

        return results

    def fetch_recent_videos(
        self,
        channel_id: str,
        channel_name: str,
        lookback_days: int = 7,
        max_videos: int = 50,
        description_max_length: int = 5000,
        now: datetime | None = None,
    ) -> list[VideoItem]:
        """指定チャンネルの直近 lookback_days 日間の動画詳細リストを返す."""
        fetched_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff_dt = fetched_at - timedelta(days=lookback_days)

        info = self.get_channel_info(channel_id)
        uploads_playlist_id = info["uploads_playlist_id"]
        resolved_name = channel_name or info["title"]

        video_ids = self._list_recent_video_ids(
            uploads_playlist_id=uploads_playlist_id,
            cutoff_dt=cutoff_dt,
            max_videos=max_videos,
        )
        if not video_ids:
            return []

        videos = self._get_videos_detail(
            video_ids=video_ids,
            fallback_channel_name=resolved_name,
            fetched_at=fetched_at,
            description_max_length=description_max_length,
        )
        # videos.list の正確な publishedAt でも最終フィルタリング
        return [v for v in videos if v.published_at >= cutoff_dt]
