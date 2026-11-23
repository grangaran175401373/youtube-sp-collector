"""Microsoft Entra ID 認証確認 ＆ SharePointリスト「YouTube動画管理」自動構築スクリプト.

実施内容:
1. Entra ID Client Credentials 認証テスト
2. SharePoint Site ID の解決
3. リスト「YouTube動画管理」の存在確認（なければ新規作成）
4. 設計書準拠の14列を「英数字内部名」で作成 → 「日本語表示名」へ変更
   - VideoId（インデックス＋一意制約）、ChannelName/PublishedAt/Category/ProcessStatus（インデックス）
5. SHAREPOINT_SITE_ID と SHAREPOINT_LIST_ID を .env に自動記入

使い方:
    python scripts/setup_sharepoint.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from src.clients.graph_auth import GraphAuthClient
from src.clients.sharepoint_client import GRAPH_API_BASE, SharePointAPIError, SharePointClient


# 設計書（開発計画書 0-3）準拠の14列定義
# Title は SharePoint 標準列のため表示名のみ日本語へ変更する
COLUMN_SPECS: list[dict[str, Any]] = [
    {
        "name": "VideoId",
        "display_name": "動画ID",
        "definition": {
            "name": "VideoId",
            "text": {"maxLength": 255},
            "required": True,
            "indexed": True,
            "enforceUniqueValues": True,
        },
    },
    {
        "name": "VideoUrl",
        "display_name": "動画URL",
        "definition": {
            "name": "VideoUrl",
            "text": {"maxLength": 255},
        },
    },
    {
        "name": "ChannelName",
        "display_name": "チャンネル名",
        "definition": {
            "name": "ChannelName",
            "text": {"maxLength": 255},
            "indexed": True,
        },
    },
    {
        "name": "PublishedAt",
        "display_name": "公開日時",
        "definition": {
            "name": "PublishedAt",
            "dateTime": {"format": "dateTime"},
            "indexed": True,
        },
    },
    {
        "name": "FetchedAt",
        "display_name": "取得日時",
        "definition": {
            "name": "FetchedAt",
            "dateTime": {"format": "dateTime"},
        },
    },
    {
        "name": "Description",
        "display_name": "概要欄",
        "definition": {
            "name": "Description",
            "text": {"allowMultipleLines": True, "textType": "plain"},
        },
    },
    {
        "name": "ThumbnailUrl",
        "display_name": "サムネイルURL",
        "definition": {
            "name": "ThumbnailUrl",
            "text": {"maxLength": 255},
        },
    },
    {
        "name": "Category",
        "display_name": "分野",
        "definition": {
            "name": "Category",
            "text": {"maxLength": 255},
            "indexed": True,
        },
    },
    {
        "name": "Summary",
        "display_name": "AI要約",
        "definition": {
            "name": "Summary",
            "text": {"allowMultipleLines": True, "textType": "plain"},
        },
    },
    {
        "name": "Importance",
        "display_name": "重要度",
        "definition": {
            "name": "Importance",
            "choice": {
                "allowTextEntry": False,
                "choices": ["高", "中", "低"],
                "displayAs": "dropDownMenu",
            },
        },
    },
    {
        "name": "ProcessStatus",
        "display_name": "処理ステータス",
        "definition": {
            "name": "ProcessStatus",
            "choice": {
                "allowTextEntry": False,
                "choices": ["未分析", "分析済み", "分析エラー"],
                "displayAs": "dropDownMenu",
            },
            "defaultValue": {"value": "未分析"},
            "indexed": True,
        },
    },
    {
        "name": "Keywords",
        "display_name": "キーワード",
        "definition": {
            "name": "Keywords",
            "text": {"allowMultipleLines": True, "textType": "plain"},
        },
    },
    {
        "name": "BusinessInsight",
        "display_name": "業務活用ポイント",
        "definition": {
            "name": "BusinessInsight",
            "text": {"allowMultipleLines": True, "textType": "plain"},
        },
    },
]


def _update_env_file(env_path: Path, updates: dict[str, str]) -> None:
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    for key, value in updates.items():
        pattern = re.compile(rf"^{re.escape(key)}=.*$", flags=re.MULTILINE)
        replacement = f"{key}={value}"
        if pattern.search(content):
            content = pattern.sub(replacement, content)
        else:
            if content and not content.endswith("\n"):
                content += "\n"
            content += replacement + "\n"
    env_path.write_text(content, encoding="utf-8")


def _require_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val or val.startswith("ここに"):
        raise ValueError(f".env に {name} が未設定です。")
    return val


def main() -> int:
    env_path = PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=env_path, override=True)

    try:
        tenant_id = _require_env("AZURE_TENANT_ID")
        client_id = _require_env("AZURE_CLIENT_ID")
        client_secret = _require_env("AZURE_CLIENT_SECRET")
        hostname = _require_env("SHAREPOINT_HOSTNAME")
        site_path = _require_env("SHAREPOINT_SITE_PATH")
        list_name = _require_env("SHAREPOINT_LIST_NAME")
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1

    print("1. Microsoft Entra ID 認証テスト中...")
    auth = GraphAuthClient(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    auth.get_access_token()
    print("   [OK] アクセストークン取得成功")

    sp = SharePointClient(auth_client=auth)

    print(f"\n2. SharePoint サイト解決中 ({hostname}{site_path})...")
    site = sp.resolve_site(hostname, site_path)
    site_id = str(site["id"])
    print(f"   [OK] Site Name: {site.get('displayName')} / Site ID: {site_id}")

    print(f"\n3. リスト '{list_name}' の確認・作成中...")
    lists = sp.list_site_lists(site_id)
    target_list = next(
        (lst for lst in lists if lst.get("displayName") == list_name or lst.get("name") == list_name),
        None,
    )
    if target_list:
        list_id = str(target_list["id"])
        print(f"   [EXISTS] 既存リストを検出しました (List ID: {list_id})")
    else:
        created = sp._request(
            "POST",
            f"{GRAPH_API_BASE}/sites/{site_id}/lists",
            json_body={
                "displayName": list_name,
                "list": {"template": "genericList"},
                "description": "YouTube新着動画自動収集システム管理リスト",
            },
        )
        list_id = str(created["id"])
        print(f"   [CREATED] リスト '{list_name}' を新規作成しました (List ID: {list_id})")

    print("\n4. 列（14列）の確認・作成・日本語表示名設定中...")
    cols_resp = sp._request("GET", f"{GRAPH_API_BASE}/sites/{site_id}/lists/{list_id}/columns")
    existing_cols = {c["name"]: c for c in cols_resp.get("value", [])}

    # 標準の Title 列の表示名を「タイトル」に更新
    if "Title" in existing_cols:
        title_col = existing_cols["Title"]
        if title_col.get("displayName") != "タイトル":
            sp._request(
                "PATCH",
                f"{GRAPH_API_BASE}/sites/{site_id}/lists/{list_id}/columns/{title_col['id']}",
                json_body={"displayName": "タイトル"},
            )
            print("   [UPDATED] Title -> 表示名「タイトル」")
        else:
            print("   [OK] Title（表示名「タイトル」設定済み）")

    # カスタム13列の作成＆表示名更新
    for spec in COLUMN_SPECS:
        col_name = spec["name"]
        display_name = spec["display_name"]
        col_def = dict(spec["definition"])

        if col_name in existing_cols:
            col_obj = existing_cols[col_name]
            col_id = col_obj["id"]
            print(f"   [EXISTS] {col_name}（現在の表示名: {col_obj.get('displayName')}）")
        else:
            # 確実に英数字内部名で作成するため、初回POSTは displayName を日本語にしない
            try:
                col_obj = sp._request(
                    "POST",
                    f"{GRAPH_API_BASE}/sites/{site_id}/lists/{list_id}/columns",
                    json_body=col_def,
                )
            except SharePointAPIError:
                # enforceUniqueValues を初回POSTで同時指定できない環境向けフォールバック
                if col_def.get("enforceUniqueValues"):
                    fallback_def = {k: v for k, v in col_def.items() if k != "enforceUniqueValues"}
                    col_obj = sp._request(
                        "POST",
                        f"{GRAPH_API_BASE}/sites/{site_id}/lists/{list_id}/columns",
                        json_body=fallback_def,
                    )
                    sp._request(
                        "PATCH",
                        f"{GRAPH_API_BASE}/sites/{site_id}/lists/{list_id}/columns/{col_obj['id']}",
                        json_body={"enforceUniqueValues": True},
                    )
                else:
                    raise
            col_id = col_obj["id"]
            print(f"   [CREATED] {col_name}")

        # 日本語表示名へPATCH
        if col_obj.get("displayName") != display_name:
            sp._request(
                "PATCH",
                f"{GRAPH_API_BASE}/sites/{site_id}/lists/{list_id}/columns/{col_id}",
                json_body={"displayName": display_name},
            )
            print(f"      -> 表示名を「{display_name}」へ更新しました")

    print("\n5. .env へ SHAREPOINT_SITE_ID / SHAREPOINT_LIST_ID を保存中...")
    _update_env_file(
        env_path,
        {
            "SHAREPOINT_SITE_ID": site_id,
            "SHAREPOINT_LIST_ID": list_id,
        },
    )
    print(f"   [SAVED] {env_path} を更新しました。")
    print("\nすべての SharePoint セットアップが完了しました！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
