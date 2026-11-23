from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.clients.graph_auth import GraphAuthClient
from src.models import VideoItem


GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


class TransientGraphError(RuntimeError):
    """リトライ対象の一時的なGraph APIエラー（429 / 5xx）."""


class SharePointAPIError(RuntimeError):
    """SharePoint / Graph API エラー."""


class DuplicateVideoError(SharePointAPIError):
    """SharePoint の VideoId 一意制約違反エラー."""


def _format_iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_unique_constraint_error(status_code: int, body_text: str) -> bool:
    if status_code not in (400, 409):
        return False
    lower = body_text.lower()
    markers = (
        "unique",
        "duplicate",
        "already exists",
        "-2130575169",
        "一意",
        "重複",
    )
    return any(m in lower for m in markers)


class SharePointClient:
    """Microsoft Graph API を用いた SharePoint Online リストクライアント."""

    def __init__(
        self,
        auth_client: GraphAuthClient,
        site_id: str = "",
        list_id: str = "",
        session: requests.Session | None = None,
        timeout: int = 30,
        logger: logging.Logger | None = None,
    ) -> None:
        self.auth_client = auth_client
        self.site_id = site_id
        self.list_id = list_id
        self.session = session or requests.Session()
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        token = self.auth_client.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        retry=retry_if_exception_type((TransientGraphError, requests.RequestException)),
    )
    def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        resp = self.session.request(
            method=method,
            url=url,
            headers=self._headers(extra_headers),
            params=params,
            json=json_body,
            timeout=self.timeout,
        )
        if resp.status_code in (429, 500, 502, 503, 504):
            raise TransientGraphError(
                f"Graph API 一時エラー ({resp.status_code}) on {method} {url}: {resp.text[:300]}"
            )
        if not resp.ok:
            if method.upper() == "POST" and _is_unique_constraint_error(resp.status_code, resp.text):
                raise DuplicateVideoError(
                    f"SharePoint 一意制約違反 ({resp.status_code}): {resp.text[:300]}"
                )
            raise SharePointAPIError(
                f"Graph API エラー ({resp.status_code}) on {method} {url}: {resp.text[:500]}"
            )
        if not resp.text.strip():
            return {}
        return resp.json()

    def resolve_site(self, hostname: str, site_path: str) -> dict[str, Any]:
        """ホスト名とサイトパスから SharePoint Site 情報を取得する."""
        clean_path = "/" + site_path.lstrip("/")
        url = f"{GRAPH_API_BASE}/sites/{hostname}:{clean_path}"
        return self._request("GET", url)

    def list_site_lists(self, site_id: str) -> list[dict[str, Any]]:
        """指定サイト配下のリスト一覧を取得する."""
        url = f"{GRAPH_API_BASE}/sites/{site_id}/lists"
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        while next_url:
            data = self._request("GET", next_url)
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
        return items

    def get_existing_video_ids(self, since_dt: datetime | None = None) -> set[str]:
        """SharePointリストから既存の VideoId セットをページネーション対応で取得する."""
        if not self.site_id or not self.list_id:
            raise ValueError("site_id および list_id が設定されていません。")

        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/lists/{self.list_id}/items"
        params: dict[str, Any] | None = {
            "$expand": "fields($select=VideoId,PublishedAt)",
            "$top": "500",
        }
        if since_dt is not None:
            params["$filter"] = f"fields/PublishedAt ge '{_format_iso_utc(since_dt)}'"

        video_ids: set[str] = set()
        next_url: str | None = url
        first_page = True

        while next_url:
            data = self._request(
                "GET",
                next_url,
                params=params if first_page else None,
            )
            first_page = False

            for item in data.get("value", []):
                fields = item.get("fields", {})
                vid = fields.get("VideoId")
                if vid:
                    video_ids.add(str(vid).strip())

            next_url = data.get("@odata.nextLink")

        return video_ids

    def create_video_item(self, video: VideoItem) -> dict[str, Any]:
        """SharePointリストに動画アイテムを1件登録する."""
        if not self.site_id or not self.list_id:
            raise ValueError("site_id および list_id が設定されていません。")

        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/lists/{self.list_id}/items"
        fields: dict[str, Any] = {
            "Title": video.title[:255],
            "VideoId": video.video_id,
            "VideoUrl": video.video_url,
            "ChannelName": video.channel_name[:255],
            "PublishedAt": _format_iso_utc(video.published_at),
            "FetchedAt": _format_iso_utc(video.fetched_at),
            "Description": video.description,
            "ThumbnailUrl": video.thumbnail_url[:255],
            "ProcessStatus": video.process_status,
        }
        if video.category:
            fields["Category"] = video.category
        if video.summary:
            fields["Summary"] = video.summary
        if video.importance:
            fields["Importance"] = video.importance
        if video.keywords:
            fields["Keywords"] = video.keywords
        if video.business_insight:
            fields["BusinessInsight"] = video.business_insight

        return self._request("POST", url, json_body={"fields": fields})

    def list_video_items(self, limit: int = 500) -> list[dict[str, Any]]:
        """SharePointリストに登録済みの動画アイテム一覧を取得する（管理UI用）."""
        if not self.site_id or not self.list_id:
            raise ValueError("site_id および list_id が設定されていません。")

        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/lists/{self.list_id}/items"
        select_fields = (
            "Title,VideoId,VideoUrl,ChannelName,PublishedAt,FetchedAt,"
            "Description,ThumbnailUrl,Category,ProcessStatus,Importance,Summary"
        )
        params: dict[str, Any] | None = {
            "$expand": f"fields($select={select_fields})",
            "$top": str(min(500, max(1, limit))),
        }

        records: list[dict[str, Any]] = []
        next_url: str | None = url
        first_page = True

        while next_url and len(records) < limit:
            data = self._request(
                "GET",
                next_url,
                params=params if first_page else None,
            )
            first_page = False

            for item in data.get("value", []):
                fields = item.get("fields", {})
                records.append(
                    {
                        "item_id": str(item.get("id", "")),
                        "title": str(fields.get("Title", "")),
                        "video_id": str(fields.get("VideoId", "")),
                        "video_url": str(fields.get("VideoUrl", "")),
                        "channel_name": str(fields.get("ChannelName", "")),
                        "published_at": str(fields.get("PublishedAt", "")),
                        "fetched_at": str(fields.get("FetchedAt", "")),
                        "description": str(fields.get("Description", "")),
                        "thumbnail_url": str(fields.get("ThumbnailUrl", "")),
                        "category": str(fields.get("Category", "")),
                        "process_status": str(fields.get("ProcessStatus", "")),
                        "importance": str(fields.get("Importance", "")),
                        "summary": str(fields.get("Summary", "")),
                    }
                )
                if len(records) >= limit:
                    break

            next_url = data.get("@odata.nextLink")

        records.sort(key=lambda r: r.get("published_at", ""), reverse=True)
        return records

    def delete_video_item(self, item_id: str) -> None:
        """SharePointリストから指定アイテムIDのレコードを削除する."""
        if not self.site_id or not self.list_id:
            raise ValueError("site_id および list_id が設定されていません。")
        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/lists/{self.list_id}/items/{item_id}"
        self._request("DELETE", url)

    def download_drive_json(self, relative_path: str) -> dict[str, Any] | None:
        """SharePoint既定ドキュメントライブラリからJSONファイルをダウンロードする（未存在時はNone）."""
        if not self.site_id:
            raise ValueError("site_id が設定されていません。")
        clean_path = quote(relative_path.strip("/"), safe="/")
        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/drive/root:/{clean_path}:/content"
        resp = self.session.request(
            method="GET",
            url=url,
            headers={"Authorization": f"Bearer {self.auth_client.get_access_token()}"},
            timeout=self.timeout,
        )
        if resp.status_code == 404:
            return None
        if not resp.ok:
            raise SharePointAPIError(
                f"SharePoint設定ファイル読込エラー ({resp.status_code}): {resp.text[:400]}"
            )
        return json.loads(resp.content.decode("utf-8"))

    def upload_drive_json(self, relative_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """SharePoint既定ドキュメントライブラリへJSONファイルを保存する（フォルダ自動作成）."""
        if not self.site_id:
            raise ValueError("site_id が設定されていません。")
        clean_path = quote(relative_path.strip("/"), safe="/")
        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/drive/root:/{clean_path}:/content"
        body_bytes = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        resp = self.session.request(
            method="PUT",
            url=url,
            headers={
                "Authorization": f"Bearer {self.auth_client.get_access_token()}",
                "Content-Type": "application/json; charset=utf-8",
            },
            data=body_bytes,
            timeout=self.timeout,
        )
        if not resp.ok:
            raise SharePointAPIError(
                f"SharePoint設定ファイル保存エラー ({resp.status_code}): {resp.text[:400]}"
            )
        return resp.json() if resp.text.strip() else {}
