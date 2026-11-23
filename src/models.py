from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class ChannelConfig:
    """監視対象YouTubeチャンネルの設定."""

    name: str
    channel_id: str
    handle: str = ""
    enabled: bool = True
    include_categories: tuple[str, ...] = ()
    exclude_categories: tuple[str, ...] = ()
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    match_target: str = ""  # "", "title_only", "title_and_description"


@dataclass(frozen=True)
class AppConfig:
    """アプリケーション全体設定（.env + config.json）."""

    youtube_api_key: str
    azure_tenant_id: str
    azure_client_id: str
    azure_client_secret: str
    sharepoint_hostname: str
    sharepoint_site_path: str
    sharepoint_list_name: str
    sharepoint_site_id: str
    sharepoint_list_id: str
    lookback_days: int
    max_videos_per_channel: int
    description_max_length: int
    channels: list[ChannelConfig] = field(default_factory=list)
    categories: dict[str, list[str]] = field(default_factory=dict)
    default_match_target: str = "title_only"


@dataclass(frozen=True)
class VideoItem:
    """YouTubeから取得した動画メタデータ."""

    video_id: str
    title: str
    video_url: str
    channel_name: str
    published_at: datetime
    fetched_at: datetime
    description: str
    thumbnail_url: str
    process_status: str = "未分析"
    category: Optional[str] = None
    summary: Optional[str] = None
    importance: Optional[str] = None
    keywords: Optional[str] = None
    business_insight: Optional[str] = None


@dataclass(frozen=True)
class FilterDecision:
    """動画フィルタリング判定結果."""

    matched: bool
    matched_categories: tuple[str, ...] = ()
    excluded_categories: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class RunSummary:
    """バッチ実行結果サマリー."""

    channels_processed: int = 0
    channels_failed: int = 0
    fetched_count: int = 0
    created_count: int = 0
    skipped_count: int = 0
    filtered_out_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    preview_items: list[dict[str, Any]] = field(default_factory=list)
