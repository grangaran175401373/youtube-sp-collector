from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from src.clients.sharepoint_client import SharePointClient
from src.config_loader import (
    DEFAULT_PROJECT_ROOT,
    load_raw_config,
    normalize_raw_config,
    save_raw_config,
)

DEFAULT_REMOTE_CONFIG_PATH = "YouTubeCollector/config.json"


def get_remote_config_path() -> str:
    return os.getenv("SHAREPOINT_CONFIG_PATH", DEFAULT_REMOTE_CONFIG_PATH).strip() or DEFAULT_REMOTE_CONFIG_PATH


def load_synced_raw_config(
    sp_client: SharePointClient,
    project_root: Path | None = None,
    logger: logging.Logger | None = None,
) -> tuple[dict[str, Any], str]:
    """SharePoint上の config.json を最優先で読み込む（初回はローカル設定をSharePointへ初期配置）.

    Returns:
        (正規化済み設定辞書, ソース種別: "sharepoint" | "sharepoint_seeded" | "local_fallback")
    """
    log = logger or logging.getLogger(__name__)
    root = project_root or DEFAULT_PROJECT_ROOT
    remote_path = get_remote_config_path()

    try:
        remote_raw = sp_client.download_drive_json(remote_path)
        if remote_raw is not None:
            normalized = normalize_raw_config(remote_raw)
            return normalized, "sharepoint"

        # SharePoint にまだ設定ファイルが無い初回はローカル config.json を初期アップロード
        local_raw = load_raw_config(root)
        sp_client.upload_drive_json(remote_path, local_raw)
        log.info("SharePoint (%s) へ初期 config.json をアップロードしました。", remote_path)
        return local_raw, "sharepoint_seeded"
    except Exception as exc:
        log.warning(
            "SharePoint からの設定読込に失敗したためローカル config.json を使用します: %s",
            exc,
        )
        return load_raw_config(root), "local_fallback"


def save_synced_raw_config(
    raw: dict[str, Any],
    sp_client: SharePointClient,
    project_root: Path | None = None,
    logger: logging.Logger | None = None,
) -> str:
    """設定を SharePoint ドキュメントライブラリへ保存し、可能ならローカルにも同期保存する."""
    log = logger or logging.getLogger(__name__)
    root = project_root or DEFAULT_PROJECT_ROOT
    remote_path = get_remote_config_path()
    normalized = normalize_raw_config(raw)

    # クラウド（Streamlit Cloud / GitHub Actions）共通の正本として SharePoint へ保存
    sp_client.upload_drive_json(remote_path, normalized)

    # ローカルPCでもキャッシュとして保存（クラウドの読み取り専用FSでも失敗させない）
    try:
        save_raw_config(normalized, root)
    except OSError as exc:
        log.debug("ローカル config.json へのキャッシュ保存をスキップ: %s", exc)

    return remote_path
