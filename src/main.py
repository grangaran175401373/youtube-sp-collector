from __future__ import annotations

import argparse
import sys

from src.clients.graph_auth import GraphAuthClient
from src.clients.sharepoint_client import SharePointClient
from src.clients.youtube_client import YouTubeClient
from src.config_loader import build_config_from_raw, load_config
from src.services.collector_service import CollectorService
from src.services.config_store import load_synced_raw_config
from src.utils.logger import setup_logger


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YouTube新着動画を収集し、SharePoint Onlineリストへ登録します。"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="SharePointへの書き込みを行わず、登録予定の動画のみログ出力します。",
    )
    parser.add_argument(
        "--local-config-only",
        action="store_true",
        help="SharePoint上の設定ファイルを参照せず、ローカルの config.json のみを使用します。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logger = setup_logger()
    logger.info("========== YouTube新着動画自動収集バッチ開始 ==========")

    try:
        base_config = load_config(require_channel_ids=False)
    except Exception as exc:
        logger.error("設定読み込みエラー: %s", exc)
        return 1

    auth_client = GraphAuthClient(
        tenant_id=base_config.azure_tenant_id,
        client_id=base_config.azure_client_id,
        client_secret=base_config.azure_client_secret,
    )
    sharepoint_client = SharePointClient(
        auth_client=auth_client,
        site_id=base_config.sharepoint_site_id,
        list_id=base_config.sharepoint_list_id,
        logger=logger,
    )

    if args.local_config_only:
        config = base_config
        logger.info("設定ソース: ローカル config.json（--local-config-only）")
    else:
        synced_raw, source = load_synced_raw_config(sharepoint_client, logger=logger)
        try:
            config = build_config_from_raw(synced_raw)
        except Exception as exc:
            logger.error("同期設定の読み込みエラー: %s", exc)
            return 1
        logger.info("設定ソース: %s（監視チャンネル数=%d）", source, len(config.channels))

    youtube_client = YouTubeClient(api_key=config.youtube_api_key, logger=logger)

    service = CollectorService(
        config=config,
        youtube_client=youtube_client,
        sharepoint_client=sharepoint_client,
        logger=logger,
    )

    try:
        summary = service.run(dry_run=args.dry_run)
    except Exception as exc:
        logger.exception("致命的なエラーによりバッチが中断されました: %s", exc)
        return 1

    logger.info(
        "========== 実行サマリー: 処理チャンネル=%d, 失敗チャンネル=%d, 取得=%d, 新規登録=%d, フィルタ除外=%d, スキップ=%d, エラー=%d ==========",
        summary.channels_processed,
        summary.channels_failed,
        summary.fetched_count,
        summary.created_count,
        summary.filtered_out_count,
        summary.skipped_count,
        summary.error_count,
    )

    if summary.error_count > 0:
        logger.warning("一部エラーが発生しました。詳細は上記ログを確認してください。")
        return 2

    logger.info("========== YouTube新着動画自動収集バッチ正常終了 ==========")
    return 0


if __name__ == "__main__":
    sys.exit(main())
