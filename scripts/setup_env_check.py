"""YouTube APIキー疎通確認 ＆ 対象チャンネルID自動解決スクリプト.

使い方:
    python scripts/setup_env_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import os

from src.clients.youtube_client import YouTubeClient


def main() -> int:
    env_path = PROJECT_ROOT / ".env"
    config_path = PROJECT_ROOT / "config.json"

    load_dotenv(dotenv_path=env_path, override=True)
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key or api_key.startswith("ここに"):
        print("[ERROR] .env に YOUTUBE_API_KEY が設定されていません。")
        return 1

    with config_path.open("r", encoding="utf-8") as f:
        config_data = json.load(f)

    client = YouTubeClient(api_key=api_key)
    updated = False

    print("YouTube Data API v3 疎通確認 ＆ チャンネルID解決を開始します...\n")
    for ch in config_data.get("channels", []):
        name = ch.get("name", "")
        handle = ch.get("handle", "").strip()
        channel_id = ch.get("channel_id", "").strip()

        if channel_id:
            info = client.get_channel_info(channel_id)
            print(f"[OK] {name}")
            print(f"     公式名     : {info['title']}")
            print(f"     channel_id : {info['channel_id']}")
        elif handle:
            info = client.resolve_channel_by_handle(handle)
            ch["channel_id"] = info["channel_id"]
            if not name:
                ch["name"] = info["title"]
            updated = True
            print(f"[RESOLVED] {handle}")
            print(f"     公式名     : {info['title']}")
            print(f"     channel_id : {info['channel_id']}")
        else:
            print(f"[WARN] '{name}' は handle も channel_id も未設定のためスキップします。")

    if updated:
        with config_path.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\n[SAVED] 解決した channel_id を {config_path} に自動保存しました。")
    else:
        print("\n[INFO] config.json の更新は不要でした（すべて設定済み）。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
