from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from src.clients.sharepoint_client import DuplicateVideoError, SharePointClient
from src.clients.youtube_client import YouTubeClient
from src.models import AppConfig, RunSummary, VideoItem
from src.services.video_filter import evaluate_video


class CollectorService:
    """YouTube新着動画の収集・フィルタリング・重複除外・SharePoint登録を統括するサービス."""

    def __init__(
        self,
        config: AppConfig,
        youtube_client: YouTubeClient,
        sharepoint_client: SharePointClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.youtube_client = youtube_client
        self.sharepoint_client = sharepoint_client
        self.logger = logger or logging.getLogger(__name__)

    def run(self, now: datetime | None = None, dry_run: bool = False) -> RunSummary:
        """全有効チャンネルの新着動画を収集し、フィルタ適用後にSharePointへ登録する."""
        run_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        summary = RunSummary()

        enabled_channels = [ch for ch in self.config.channels if ch.enabled]
        if not enabled_channels:
            self.logger.warning("有効な監視対象チャンネルが0件です。config.json を確認してください。")
            return summary

        # 既存VideoIdをSharePointから事前取得（lookback_days + 30日の余裕を持たせたインデックス範囲）
        since_dt = run_now - timedelta(days=self.config.lookback_days + 30)
        self.logger.info(
            "SharePointから既存VideoId一覧を取得中（PublishedAt >= %s）...",
            since_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        known_video_ids = self.sharepoint_client.get_existing_video_ids(since_dt=since_dt)
        self.logger.info("既存VideoId取得完了: %d 件", len(known_video_ids))

        # チャンネル単位で収集（1チャンネル失敗しても他チャンネルは継続）
        collected_videos: list[tuple[VideoItem, str]] = []
        seen_in_batch: set[str] = set()

        for channel in enabled_channels:
            try:
                self.logger.info(
                    "チャンネル取得開始: %s (%s)", channel.name, channel.channel_id
                )
                videos = self.youtube_client.fetch_recent_videos(
                    channel_id=channel.channel_id,
                    channel_name=channel.name,
                    lookback_days=self.config.lookback_days,
                    max_videos=self.config.max_videos_per_channel,
                    description_max_length=self.config.description_max_length,
                    now=run_now,
                )
                summary.channels_processed += 1
                summary.fetched_count += len(videos)
                self.logger.info(
                    "チャンネル取得完了: %s -> %d 件", channel.name, len(videos)
                )

                for video in videos:
                    if video.video_id in seen_in_batch:
                        summary.skipped_count += 1
                        continue
                    seen_in_batch.add(video.video_id)

                    decision = evaluate_video(
                        video=video,
                        channel=channel,
                        categories=self.config.categories,
                        default_match_target=self.config.default_match_target,
                    )
                    if not decision.matched:
                        summary.filtered_out_count += 1
                        summary.preview_items.append(
                            {
                                "status": "フィルタ除外",
                                "video_id": video.video_id,
                                "channel_name": video.channel_name,
                                "title": video.title,
                                "video_url": video.video_url,
                                "published_at": video.published_at.strftime("%Y-%m-%d %H:%M"),
                                "category": ", ".join(decision.matched_categories),
                                "reason": decision.reason,
                            }
                        )
                        self.logger.debug(
                            "フィルタ除外: %s | %s | %s",
                            video.video_id,
                            video.title,
                            decision.reason,
                        )
                        continue

                    auto_category = (
                        ", ".join(decision.matched_categories)
                        if decision.matched_categories
                        else video.category
                    )
                    enriched_video = replace(video, category=auto_category)
                    collected_videos.append((enriched_video, decision.reason))

            except Exception as exc:
                summary.channels_failed += 1
                summary.error_count += 1
                msg = f"チャンネル '{channel.name}' ({channel.channel_id}) の取得に失敗: {exc}"
                summary.errors.append(msg)
                self.logger.exception(msg)

        # 投稿日時の昇順（古い→新しい）で登録するとSharePoint側で時系列が見やすい
        collected_videos.sort(key=lambda pair: pair[0].published_at)

        for video, match_reason in collected_videos:
            if video.video_id in known_video_ids:
                summary.skipped_count += 1
                summary.preview_items.append(
                    {
                        "status": "登録済み(スキップ)",
                        "video_id": video.video_id,
                        "channel_name": video.channel_name,
                        "title": video.title,
                        "video_url": video.video_url,
                        "published_at": video.published_at.strftime("%Y-%m-%d %H:%M"),
                        "category": video.category or "",
                        "reason": f"既存登録済み ({match_reason})",
                    }
                )
                self.logger.debug("重複スキップ（事前チェック）: %s | %s", video.video_id, video.title)
                continue

            if dry_run:
                self.logger.info(
                    "[DRY-RUN] 登録予定: %s | %s | [%s] %s",
                    video.video_id,
                    video.channel_name,
                    video.category or "未分類",
                    video.title,
                )
                summary.created_count += 1
                known_video_ids.add(video.video_id)
                summary.preview_items.append(
                    {
                        "status": "登録予定",
                        "video_id": video.video_id,
                        "channel_name": video.channel_name,
                        "title": video.title,
                        "video_url": video.video_url,
                        "published_at": video.published_at.strftime("%Y-%m-%d %H:%M"),
                        "category": video.category or "",
                        "reason": match_reason,
                    }
                )
                continue

            try:
                self.sharepoint_client.create_video_item(video)
                known_video_ids.add(video.video_id)
                summary.created_count += 1
                summary.preview_items.append(
                    {
                        "status": "新規登録",
                        "video_id": video.video_id,
                        "channel_name": video.channel_name,
                        "title": video.title,
                        "video_url": video.video_url,
                        "published_at": video.published_at.strftime("%Y-%m-%d %H:%M"),
                        "category": video.category or "",
                        "reason": match_reason,
                    }
                )
                self.logger.info(
                    "SharePoint登録成功: %s | %s | [%s] %s",
                    video.video_id,
                    video.channel_name,
                    video.category or "未分類",
                    video.title,
                )
            except DuplicateVideoError:
                known_video_ids.add(video.video_id)
                summary.skipped_count += 1
                self.logger.info(
                    "重複スキップ（SharePoint一意制約）: %s | %s",
                    video.video_id,
                    video.title,
                )
            except Exception as exc:
                summary.error_count += 1
                msg = f"SharePoint登録失敗 ({video.video_id} / {video.title}): {exc}"
                summary.errors.append(msg)
                self.logger.exception(msg)

        return summary
