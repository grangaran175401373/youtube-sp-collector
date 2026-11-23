from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from src.models import AppConfig, ChannelConfig
from src.services.video_filter import DEFAULT_CATEGORIES


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent.parent

KNOWN_SECRET_KEYS: tuple[str, ...] = (
    "YOUTUBE_API_KEY",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "SHAREPOINT_HOSTNAME",
    "SHAREPOINT_SITE_PATH",
    "SHAREPOINT_LIST_NAME",
    "SHAREPOINT_SITE_ID",
    "SHAREPOINT_LIST_ID",
    "SHAREPOINT_CONFIG_PATH",
    "APP_PASSWORD",
)


def inject_secrets_to_env(secrets: Mapping[str, Any] | None) -> None:
    """Streamlit Cloud の st.secrets 等を os.environ へ安全に反映する."""
    if not secrets:
        return
    for key in KNOWN_SECRET_KEYS:
        try:
            if key in secrets:
                val = secrets[key]
                if val is not None and str(val).strip():
                    os.environ[key] = str(val).strip()
        except Exception:
            continue


def _require_env(name: str, allow_empty: bool = False) -> str:
    value = os.getenv(name, "").strip()
    if not allow_empty and (not value or value.startswith("ここに")):
        raise ValueError(
            f"環境変数 {name} が未設定です。.env または Streamlit Secrets / GitHub Secrets を確認してください。"
        )
    return value


def _to_str_tuple(raw_val: Any) -> tuple[str, ...]:
    if not raw_val:
        return ()
    if isinstance(raw_val, str):
        parts = [p.strip() for p in raw_val.replace("、", ",").split(",")]
        return tuple(p for p in parts if p)
    return tuple(str(v).strip() for v in raw_val if str(v).strip())


def normalize_raw_config(raw: dict[str, Any]) -> dict[str, Any]:
    """設定辞書に不足しているデフォルト値を補完した新しい辞書を返す."""
    data = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    data.setdefault("lookback_days", 7)
    data.setdefault("max_videos_per_channel", 50)
    data.setdefault("description_max_length", 5000)

    if not isinstance(data.get("categories"), dict) or not data["categories"]:
        data["categories"] = copy.deepcopy(DEFAULT_CATEGORIES)
    if not data.get("default_match_target"):
        data["default_match_target"] = "title_only"

    channels = data.get("channels")
    if not isinstance(channels, list):
        channels = []
        data["channels"] = channels

    for ch in channels:
        ch.setdefault("enabled", True)
        ch.setdefault("include_categories", [])
        ch.setdefault("exclude_categories", [])
        ch.setdefault("include_keywords", [])
        ch.setdefault("exclude_keywords", [])
        ch.setdefault("match_target", "")

    return data


def load_raw_config(project_root: Path | None = None) -> dict[str, Any]:
    """ローカル config.json を辞書として読み込み、不足項目にデフォルト値を補完する."""
    root = project_root or DEFAULT_PROJECT_ROOT
    config_path = root / "config.json"
    if not config_path.exists():
        return normalize_raw_config({})

    with config_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    return normalize_raw_config(raw)


def save_raw_config(raw: dict[str, Any], project_root: Path | None = None) -> Path:
    """ローカル config.json をUTF-8整形JSONで安全に保存する."""
    root = project_root or DEFAULT_PROJECT_ROOT
    config_path = root / "config.json"
    tmp_path = root / "config.json.tmp"
    content = json.dumps(normalize_raw_config(raw), ensure_ascii=False, indent=2) + "\n"
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(config_path)
    return config_path


def load_env_file(project_root: Path | None = None) -> None:
    """ローカル .env が存在すれば環境変数に読み込む（既存環境変数は上書きしない）."""
    root = project_root or DEFAULT_PROJECT_ROOT
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


def build_config_from_raw(
    raw: dict[str, Any],
    project_root: Path | None = None,
    require_sharepoint_ids: bool = True,
    require_channel_ids: bool = True,
) -> AppConfig:
    """環境変数（.env / Secrets）と設定辞書から AppConfig を構築する."""
    load_env_file(project_root)
    normalized = normalize_raw_config(raw)

    channels: list[ChannelConfig] = []
    for item in normalized.get("channels", []):
        ch = ChannelConfig(
            name=str(item.get("name", "")).strip(),
            channel_id=str(item.get("channel_id", "")).strip(),
            handle=str(item.get("handle", "")).strip(),
            enabled=bool(item.get("enabled", True)),
            include_categories=_to_str_tuple(item.get("include_categories")),
            exclude_categories=_to_str_tuple(item.get("exclude_categories")),
            include_keywords=_to_str_tuple(item.get("include_keywords")),
            exclude_keywords=_to_str_tuple(item.get("exclude_keywords")),
            match_target=str(item.get("match_target", "")).strip(),
        )
        if ch.enabled and require_channel_ids and not ch.channel_id:
            raise ValueError(
                f"チャンネル '{ch.name}' の channel_id が未設定です。"
                "先に scripts/setup_env_check.py を実行してください。"
            )
        channels.append(ch)

    lookback_days = int(normalized.get("lookback_days", 7))
    if lookback_days < 1:
        raise ValueError("lookback_days は 1 以上の整数を指定してください。")

    max_videos = int(normalized.get("max_videos_per_channel", 50))
    if max_videos < 1:
        raise ValueError("max_videos_per_channel は 1 以上の整数を指定してください。")

    desc_max_len = int(normalized.get("description_max_length", 5000))
    categories = {
        str(k).strip(): [str(kw).strip() for kw in v if str(kw).strip()]
        for k, v in normalized.get("categories", {}).items()
        if str(k).strip()
    }
    default_match_target = str(normalized.get("default_match_target", "title_only")).strip() or "title_only"

    return AppConfig(
        youtube_api_key=_require_env("YOUTUBE_API_KEY"),
        azure_tenant_id=_require_env("AZURE_TENANT_ID"),
        azure_client_id=_require_env("AZURE_CLIENT_ID"),
        azure_client_secret=_require_env("AZURE_CLIENT_SECRET"),
        sharepoint_hostname=_require_env("SHAREPOINT_HOSTNAME"),
        sharepoint_site_path=_require_env("SHAREPOINT_SITE_PATH"),
        sharepoint_list_name=_require_env("SHAREPOINT_LIST_NAME"),
        sharepoint_site_id=_require_env("SHAREPOINT_SITE_ID", allow_empty=not require_sharepoint_ids),
        sharepoint_list_id=_require_env("SHAREPOINT_LIST_ID", allow_empty=not require_sharepoint_ids),
        lookback_days=lookback_days,
        max_videos_per_channel=max_videos,
        description_max_length=desc_max_len,
        channels=channels,
        categories=categories,
        default_match_target=default_match_target,
    )


def load_config(
    project_root: Path | None = None,
    require_sharepoint_ids: bool = True,
    require_channel_ids: bool = True,
) -> AppConfig:
    """`.env` とローカル `config.json` を読み込み、バリデーション済み AppConfig を返す."""
    raw = load_raw_config(project_root)
    return build_config_from_raw(
        raw=raw,
        project_root=project_root,
        require_sharepoint_ids=require_sharepoint_ids,
        require_channel_ids=require_channel_ids,
    )
