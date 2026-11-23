# クラウド公開・iPhone / iPad 運用ガイド（完全無料構成）

このアプリは **Streamlit Community Cloud（無料）＋ GitHub Actions（無料枠）＋ SharePoint Online（Business Basic 標準機能）** の構成で、追加課金 0円 のまま外出先の iPhone / iPad から 24時間操作・毎日 02:00 自動収集できます。

---

## 構成のしくみ

- **設定の正本（クラウド同期）**: 対象 SharePoint サイトのドキュメントライブラリ内 **`YouTubeCollector/config.json`**
- **操作画面（iPhone / iPad / PC）**: Streamlit Community Cloud（パスワード保護付き HTTPS URL）
- **毎日 02:00 の自動収集**: GitHub Actions（`.github/workflows/daily_collect.yml`）が SharePoint 上の最新 `config.json` を読んで自動実行

---

## デプロイ手順（所要時間：約5分）

### ステップ 1：GitHub にプライベートリポジトリを作成して Push

1. [GitHub 新規リポジトリ作成ページ](https://github.com/new) を開く
2. Repository name に `youtube-sp-collector`、公開範囲を **Private** にして作成する（README等は追加しない）
3. 手元の PowerShell で以下を実行して Push する（`.env` 等の秘密ファイルは `.gitignore` で自動除外済み）：

```powershell
cd <プロジェクトフォルダ>
git remote add origin https://github.com/<あなたのGitHubユーザー名>/youtube-sp-collector.git
git branch -M main
git push -u origin main
```

---

### ステップ 2：Streamlit Community Cloud へデプロイ（iPhone / iPad 操作画面）

1. [Streamlit Community Cloud](https://share.streamlit.io/) に GitHub アカウントでログイン
2. **「Create app」** → **「Deploy a public/private app from GitHub」** をクリック
3. 以下を選択：
   - **Repository**: `<あなた>/youtube-sp-collector`
   - **Branch**: `main`
   - **Main file path**: `app.py`
4. **「Advanced settings...」** を開き、**Secrets** 欄へ手元PCで以下コマンドを実行して表示される TOML を貼り付け：
   ```powershell
   cd <プロジェクトフォルダ>
   .\.venv\Scripts\python.exe scripts\export_cloud_secrets.py
   ```
5. **「Deploy」** をクリック → 発行された `https://xxxx.streamlit.app` を iPhone / iPad の Safari で開き、**「ホーム画面に追加」** するとアプリ感覚で使えます！

---

### ステップ 3：GitHub Actions の Secrets 登録（PC電源OFFでも毎日 02:00 自動収集）

1. GitHub リポジトリの **Settings** → **Secrets and variables** → **Actions** → **New repository secret** を開く
2. `.env` 内の以下の9項目を登録する：
   - `YOUTUBE_API_KEY`
   - `AZURE_TENANT_ID`
   - `AZURE_CLIENT_ID`
   - `AZURE_CLIENT_SECRET`
   - `SHAREPOINT_HOSTNAME`
   - `SHAREPOINT_SITE_PATH`
   - `SHAREPOINT_LIST_NAME`
   - `SHAREPOINT_SITE_ID`
   - `SHAREPOINT_LIST_ID`
3. リポジトリの **Actions** タブ → **Daily YouTube to SharePoint Collector** → **Run workflow**（`dry_run: true`）で動作テストできれば完了！

> **補足**: GitHub Actions の稼働確認後、ローカルPC側のタスクスケジューラ（`YouTubeSharePointCollector`）は不要であれば以下で解除できます：
> ```powershell
> Unregister-ScheduledTask -TaskName "YouTubeSharePointCollector" -Confirm:$false
> ```
