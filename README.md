# YouTube新着動画自動収集システム (youtube-sp-collector)

指定したYouTubeチャンネルの新着動画を毎日自動で収集し、SharePoint Online リスト「YouTube動画管理」へ蓄積するPythonバッチシステムです。

## ディレクトリ構成

```text
youtube-sp-collector/
├── .env                     # APIキー・Entra ID認証情報（Git管理外）
├── config.json              # 監視対象チャンネル・カテゴリー絞り込み・収集期間設定
├── requirements.txt         # 依存パッケージ
├── app.py                   # Streamlit 管理・操作UI
├── run_ui.bat               # Streamlit 管理UI起動バッチ（ダブルクリック用）
├── run_batch.bat            # Windowsタスクスケジューラ用実行バッチ
├── scripts/
│   ├── setup_env_check.py   # YouTube API疎通確認＆チャンネルID自動解決
│   ├── setup_sharepoint.py  # SharePointリスト「YouTube動画管理」自動構築＆ID保存
│   └── register_task.ps1    # Windowsタスクスケジューラ登録スクリプト（毎日02:00）
├── src/
│   ├── main.py              # エントリーポイント
│   ├── models.py            # データクラス定義
│   ├── config_loader.py     # 設定読込・バリデーション
│   ├── utils/logger.py      # 標準出力＋日次ローテーションログ
│   ├── clients/
│   │   ├── youtube_client.py
│   │   ├── graph_auth.py
│   │   └── sharepoint_client.py
│   └── services/
│       └── collector_service.py
└── tests/
    └── test_collector.py    # 単体テスト
```

## 初回セットアップ手順

1. `.env` に以下の4項目を記入
   - `YOUTUBE_API_KEY`
   - `AZURE_TENANT_ID`
   - `AZURE_CLIENT_ID`
   - `AZURE_CLIENT_SECRET`
2. 仮想環境の作成とライブラリ導入
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```
3. YouTubeチャンネルIDの自動解決
   ```powershell
   .\.venv\Scripts\python scripts\setup_env_check.py
   ```
4. SharePointリスト「YouTube動画管理」の自動構築
   ```powershell
   .\.venv\Scripts\python scripts\setup_sharepoint.py
   ```
5. テスト・実行
   ```powershell
   .\.venv\Scripts\pytest
   .\.venv\Scripts\python -m src.main --dry-run
   .\.venv\Scripts\python -m src.main
   ```
6. Windows タスクスケジューラへの登録（毎日 02:00 自動実行）
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_task.ps1
   ```
   - 多重起動防止（IgnoreNew）、実行時間上限 1時間、スケジュール超過時の即時補完実行（StartWhenAvailable）を設定します。

## 操作UI（Streamlit 管理画面）の起動

`run_ui.bat` をダブルクリック、または以下を実行するとブラウザで管理画面（`http://localhost:8501`）が開きます。

```powershell
.\.venv\Scripts\streamlit run app.py
```

画面上から以下の操作が可能です（保存した設定は毎日 02:00 の自動収集にも即座に反映されます）：
- 監視チャンネルの追加（チャンネルURL / `@ハンドル` / 動画URL 対応）・削除・ON/OFF
- チャンネル別のカテゴリー絞り込み（例：PIVOT は「AI・生成AI」「テクノロジー・IT」のみ収集し、「政治・国際」「投資・金融」を除外）
- カテゴリー定義キーワードの編集・新規カテゴリー追加
- 単発の動画URL手動登録 ＆ SharePoint 登録済み動画の検索・一括削除
- Dry-Run プレビュー・今すぐ本番収集・ログ閲覧

## チャンネル追加・停止方法

`config.json` の `channels` 配列を編集します。
- **一時停止**: 対象チャンネルの `"enabled": false` に変更
- **新規追加**: `"handle": "@チャンネルハンドル"`, `"channel_id": ""` を追加し、`.\.venv\Scripts\python scripts\setup_env_check.py` を実行すると `channel_id` が自動設定されます。

## 運用上の注意

- **Client Secret 有効期限**: Entra ID で発行したクライアントシークレットの有効期限前に新しいシークレットを発行し、`.env` の `AZURE_CLIENT_SECRET` を差し替えてください。
- **ログ確認**: 実行ログは `logs/collector.log` に日次ローテーション（最大30日保持）で出力されます。
