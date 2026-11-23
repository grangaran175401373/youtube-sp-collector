from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.clients.sharepoint_client import DuplicateVideoError, SharePointClient
from src.clients.youtube_client import (
    YouTubeClient,
    extract_video_id,
    parse_channel_input,
    sanitize_text,
)
from src.config_loader import inject_secrets_to_env
from src.models import AppConfig, ChannelConfig, VideoItem
from src.services.collector_service import CollectorService
from src.services.config_store import load_synced_raw_config, save_synced_raw_config
from src.services.video_filter import DEFAULT_CATEGORIES, evaluate_video


def _make_config(channels: list[ChannelConfig]) -> AppConfig:
    return AppConfig(
        youtube_api_key="dummy-yt-key",
        azure_tenant_id="dummy-tenant",
        azure_client_id="dummy-client",
        azure_client_secret="dummy-secret",
        sharepoint_hostname="example.sharepoint.com",
        sharepoint_site_path="/sites/Test",
        sharepoint_list_name="YouTube動画管理",
        sharepoint_site_id="site-1",
        sharepoint_list_id="list-1",
        lookback_days=7,
        max_videos_per_channel=50,
        description_max_length=5000,
        channels=channels,
        categories=DEFAULT_CATEGORIES,
        default_match_target="title_only",
    )


def _make_video(
    video_id: str,
    channel_name: str,
    hours_ago: int = 1,
    title: str | None = None,
    description: str = "sample description",
) -> VideoItem:
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    return VideoItem(
        video_id=video_id,
        title=title or f"Video {video_id}",
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        channel_name=channel_name,
        published_at=now - timedelta(hours=hours_ago),
        fetched_at=now,
        description=description,
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    )


def test_sanitize_text_removes_control_chars_and_truncates() -> None:
    raw = "Hello\x00World\r\nLine2\x07"
    assert sanitize_text(raw, 100) == "HelloWorld\nLine2"
    assert sanitize_text("abcdefghij", 8) == "abcde..."


def test_collector_skips_existing_and_in_batch_duplicates() -> None:
    cfg = _make_config(
        [
            ChannelConfig(name="CH1", channel_id="UC1", enabled=True),
            ChannelConfig(name="CH2", channel_id="UC2", enabled=True),
        ]
    )
    yt = MagicMock(spec=YouTubeClient)
    sp = MagicMock(spec=SharePointClient)

    sp.get_existing_video_ids.return_value = {"vid_existing"}
    yt.fetch_recent_videos.side_effect = [
        [_make_video("vid_existing", "CH1", 5), _make_video("vid_new_1", "CH1", 3)],
        [_make_video("vid_new_1", "CH2", 2), _make_video("vid_new_2", "CH2", 1)],
    ]

    service = CollectorService(config=cfg, youtube_client=yt, sharepoint_client=sp)
    summary = service.run(now=datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    assert summary.channels_processed == 2
    assert summary.channels_failed == 0
    assert summary.fetched_count == 4
    assert summary.created_count == 2
    assert summary.skipped_count == 2
    assert summary.error_count == 0
    assert sp.create_video_item.call_count == 2
    created_ids = [call.args[0].video_id for call in sp.create_video_item.call_args_list]
    assert created_ids == ["vid_new_1", "vid_new_2"]


def test_collector_continues_when_one_channel_fails() -> None:
    cfg = _make_config(
        [
            ChannelConfig(name="BAD_CH", channel_id="UC_BAD", enabled=True),
            ChannelConfig(name="GOOD_CH", channel_id="UC_GOOD", enabled=True),
        ]
    )
    yt = MagicMock(spec=YouTubeClient)
    sp = MagicMock(spec=SharePointClient)

    sp.get_existing_video_ids.return_value = set()
    yt.fetch_recent_videos.side_effect = [
        RuntimeError("YouTube 404"),
        [_make_video("vid_ok", "GOOD_CH", 1)],
    ]

    service = CollectorService(config=cfg, youtube_client=yt, sharepoint_client=sp)
    summary = service.run(now=datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    assert summary.channels_processed == 1
    assert summary.channels_failed == 1
    assert summary.created_count == 1
    assert summary.error_count == 1
    assert len(summary.errors) == 1


def test_collector_handles_sharepoint_unique_constraint_as_skip() -> None:
    cfg = _make_config([ChannelConfig(name="CH1", channel_id="UC1", enabled=True)])
    yt = MagicMock(spec=YouTubeClient)
    sp = MagicMock(spec=SharePointClient)

    sp.get_existing_video_ids.return_value = set()
    yt.fetch_recent_videos.return_value = [_make_video("vid_race", "CH1", 1)]
    sp.create_video_item.side_effect = DuplicateVideoError("duplicate")

    service = CollectorService(config=cfg, youtube_client=yt, sharepoint_client=sp)
    summary = service.run(now=datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    assert summary.created_count == 0
    assert summary.skipped_count == 1
    assert summary.error_count == 0


def test_sharepoint_existing_ids_pagination() -> None:
    auth = MagicMock()
    auth.get_access_token.return_value = "fake-token"
    session = MagicMock()

    resp1 = MagicMock(ok=True, status_code=200, text='{"value": []}')
    resp1.json.return_value = {
        "value": [{"fields": {"VideoId": "v1"}}, {"fields": {"VideoId": "v2"}}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-page",
    }
    resp2 = MagicMock(ok=True, status_code=200, text='{"value": []}')
    resp2.json.return_value = {
        "value": [{"fields": {"VideoId": "v3"}}],
    }
    session.request.side_effect = [resp1, resp2]

    sp = SharePointClient(
        auth_client=auth,
        site_id="site-1",
        list_id="list-1",
        session=session,
    )
    ids = sp.get_existing_video_ids(since_dt=datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert ids == {"v1", "v2", "v3"}
    assert session.request.call_count == 2


def test_video_filter_includes_ai_tech_and_excludes_politics_finance() -> None:
    pivot_ch = ChannelConfig(
        name="PIVOT 公式チャンネル",
        channel_id="UC_PIVOT",
        enabled=True,
        include_categories=("AI・生成AI", "テクノロジー・IT"),
        exclude_categories=("政治・国際", "投資・金融"),
    )

    ai_video = _make_video("v_ai", "PIVOT", title="【AIボイスレコーダー活用術】生成AIで議事録自動化")
    invest_ai_video = _make_video("v_inv", "PIVOT", title="【アフターAIの投資戦略】日経平均と新NISAの勝ち方")
    politics_video = _make_video("v_pol", "PIVOT", title="【トランプ政権の外交戦略】日本政治への影響")
    daily_word_video = _make_video("v_daily", "PIVOT", title="Daily routine and email habits for life")

    d_ai = evaluate_video(ai_video, pivot_ch, DEFAULT_CATEGORIES)
    assert d_ai.matched is True
    assert "AI・生成AI" in d_ai.matched_categories

    # AIを含んでいても除外カテゴリー「投資・金融」に該当するため除外される
    d_inv = evaluate_video(invest_ai_video, pivot_ch, DEFAULT_CATEGORIES)
    assert d_inv.matched is False
    assert "投資・金融" in d_inv.excluded_categories

    d_pol = evaluate_video(politics_video, pivot_ch, DEFAULT_CATEGORIES)
    assert d_pol.matched is False

    # 'daily' や 'email' 内の 'ai' に誤爆しないこと
    d_daily = evaluate_video(daily_word_video, pivot_ch, DEFAULT_CATEGORIES)
    assert d_daily.matched is False


def test_collector_applies_channel_category_filter_and_sets_category() -> None:
    cfg = _make_config(
        [
            ChannelConfig(
                name="PIVOT",
                channel_id="UC_PIVOT",
                enabled=True,
                include_categories=("AI・生成AI", "テクノロジー・IT"),
                exclude_categories=("投資・金融",),
            )
        ]
    )
    yt = MagicMock(spec=YouTubeClient)
    sp = MagicMock(spec=SharePointClient)
    sp.get_existing_video_ids.return_value = set()
    yt.fetch_recent_videos.return_value = [
        _make_video("v1", "PIVOT", 2, title="生成AIエージェントの最前線"),
        _make_video("v2", "PIVOT", 1, title="新NISAと株式投資の鉄則"),
    ]

    service = CollectorService(config=cfg, youtube_client=yt, sharepoint_client=sp)
    summary = service.run(now=datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc))

    assert summary.fetched_count == 2
    assert summary.created_count == 1
    assert summary.filtered_out_count == 1
    created_video = sp.create_video_item.call_args.args[0]
    assert created_video.video_id == "v1"
    assert created_video.category == "AI・生成AI"


def test_url_parsers() -> None:
    assert extract_video_id("https://www.youtube.com/watch?v=yEOXJGMnbSY&t=10s") == "yEOXJGMnbSY"
    assert extract_video_id("https://youtu.be/yEOXJGMnbSY") == "yEOXJGMnbSY"
    assert extract_video_id("https://www.youtube.com/shorts/yEOXJGMnbSY") == "yEOXJGMnbSY"
    assert parse_channel_input("https://www.youtube.com/@pivot00") == ("handle", "@pivot00")
    assert parse_channel_input("https://www.youtube.com/channel/UC8yHePe_RgUBE-waRWy6olw") == (
        "channel_id",
        "UC8yHePe_RgUBE-waRWy6olw",
    )
    assert parse_channel_input("https://youtu.be/yEOXJGMnbSY") == ("video_id", "yEOXJGMnbSY")


def test_config_store_seeds_and_loads_from_sharepoint(tmp_path) -> None:
    local_cfg = tmp_path / "config.json"
    local_cfg.write_text('{"lookback_days": 5, "channels": []}', encoding="utf-8")

    sp = MagicMock(spec=SharePointClient)
    # 1回目: SharePoint未存在 -> ローカルを初期アップロードしてシード
    sp.download_drive_json.return_value = None
    raw, source = load_synced_raw_config(sp, project_root=tmp_path)
    assert source == "sharepoint_seeded"
    assert raw["lookback_days"] == 5
    assert sp.upload_drive_json.call_count == 1

    # 2回目: SharePoint上に最新版がある場合はリモートを優先
    sp.download_drive_json.return_value = {"lookback_days": 14, "channels": []}
    raw2, source2 = load_synced_raw_config(sp, project_root=tmp_path)
    assert source2 == "sharepoint"
    assert raw2["lookback_days"] == 14

    # 保存: SharePointアップロード＆ローカルキャッシュ保存
    raw2["lookback_days"] = 10
    save_synced_raw_config(raw2, sp, project_root=tmp_path)
    assert sp.upload_drive_json.call_count == 2
    assert '"lookback_days": 10' in local_cfg.read_text(encoding="utf-8")


def test_inject_secrets_to_env(monkeypatch) -> None:
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    inject_secrets_to_env({"APP_PASSWORD": "secret-pass", "UNKNOWN_KEY": "ignored"})
    import os

    assert os.getenv("APP_PASSWORD") == "secret-pass"
    assert os.getenv("UNKNOWN_KEY") is None
