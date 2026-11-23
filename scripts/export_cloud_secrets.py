"""Streamlit Community Cloud / GitHub Actions 用の Secrets 設定テキストをローカル .env から出力するヘルパー.

※ このスクリプトは手元のPCでのみ実行し、出力された秘密情報は絶対にGitへコミットしないでください。

使い方:
    .\.venv\Scripts\python.exe scripts\export_cloud_secrets.py
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_KEYS = [
    "YOUTUBE_API_KEY",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "SHAREPOINT_HOSTNAME",
    "SHAREPOINT_SITE_PATH",
    "SHAREPOINT_LIST_NAME",
    "SHAREPOINT_SITE_ID",
    "SHAREPOINT_LIST_ID",
]


def main() -> None:
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)
    app_pw = os.getenv("APP_PASSWORD", "").strip() or f"yt-{secrets.token_urlsafe(10)}"

    print("=" * 72)
    print("1) Streamlit Community Cloud -> App settings -> Secrets にそのまま貼り付け:")
    print("=" * 72)
    for key in REQUIRED_KEYS:
        val = os.getenv(key, "").strip().replace('"', '\\"')
        print(f'{key} = "{val}"')
    print(f'APP_PASSWORD = "{app_pw}"')

    print("\n" + "=" * 72)
    print("2) GitHub リポジトリ -> Settings -> Secrets and variables -> Actions に登録する9項目:")
    print("=" * 72)
    for key in REQUIRED_KEYS:
        val = os.getenv(key, "").strip()
        status = "設定済み" if val else "未設定"
        print(f"  - {key} ({status})")
    print("\n※ APP_PASSWORD は Streamlit 画面ログイン用のパスワードです（お好きな文字列に変更可）。")


if __name__ == "__main__":
    main()
