from __future__ import annotations

import copy
import hmac
import os
from dataclasses import replace
from typing import Any

import pandas as pd
import streamlit as st

from src.clients.graph_auth import GraphAuthClient
from src.clients.sharepoint_client import DuplicateVideoError, SharePointClient
from src.clients.youtube_client import YouTubeClient, extract_video_id
from src.config_loader import (
    DEFAULT_PROJECT_ROOT,
    build_config_from_raw,
    inject_secrets_to_env,
    load_config,
)
from src.models import AppConfig, ChannelConfig
from src.services.collector_service import CollectorService
from src.services.config_store import (
    get_remote_config_path,
    load_synced_raw_config,
    save_synced_raw_config,
)
from src.services.video_filter import DEFAULT_CATEGORIES, detect_categories, evaluate_text
from src.utils.logger import setup_logger

PROJECT_ROOT = DEFAULT_PROJECT_ROOT

MATCH_TARGET_LABELS = {
    "": "全体デフォルトに従う",
    "title_only": "タイトルのみ（推奨・ノイズ少）",
    "title_and_description": "タイトル＋概要欄",
}
DEFAULT_MATCH_LABELS = {
    "title_only": "タイトルのみ（推奨・ノイズ少）",
    "title_and_description": "タイトル＋概要欄",
}

MOBILE_FRIENDLY_CSS = """
<style>
/* iPhone / iPad タッチ操作・視認性最適化 */
.block-container {
    padding-top: 1.2rem;
    padding-bottom: 2.5rem;
    max-width: 1100px;
}
.stButton > button,
.stFormSubmitButton > button,
.stLinkButton > a {
    min-height: 44px;
    border-radius: 10px;
    font-weight: 600;
}
button[data-baseweb="tab"] {
    padding: 0.55rem 0.75rem;
    font-size: 0.95rem;
}
.video-card {
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 0.85rem;
    margin-bottom: 0.75rem;
    background: #FFFFFF;
}
.badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    margin-right: 0.35rem;
}
.badge-ok { background: #DCFCE7; color: #166534; }
.badge-out { background: #FEE2E2; color: #991B1B; }
.badge-cat { background: #DBEAFE; color: #1E40AF; }
@media (max-width: 768px) {
    .block-container {
        padding-left: 0.8rem;
        padding-right: 0.8rem;
        padding-top: 0.8rem;
    }
    h1, h2, h3 {
        font-size: 1.25rem !important;
    }
}
</style>
"""


def _parse_keywords(text: str) -> list[str]:
    normalized = text.replace("、", ",").replace("\n", ",")
    items = [p.strip() for p in normalized.split(",") if p.strip()]
    return list(dict.fromkeys(items))


def _bootstrap_secrets() -> None:
    """Streamlit Cloud の st.secrets を環境変数へ注入する（未設定ローカル環境でも安全）."""
    try:
        inject_secrets_to_env(st.secrets)
    except Exception:
        pass


def _require_login() -> bool:
    """APP_PASSWORD が設定されている場合、ログイン画面を表示して認証する."""
    expected = os.getenv("APP_PASSWORD", "").strip()
    if not expected:
        return True

    if st.session_state.get("authenticated") is True:
        return True

    st.markdown("### 🔐 YouTube 自動収集コントロールパネル")
    st.caption("iPhone / iPad / PC から安全に操作するためのログイン画面です。")

    with st.form("login_form"):
        entered = st.text_input("パスワード", type="password", placeholder="パスワードを入力")
        submitted = st.form_submit_button("ログイン", type="primary", width="stretch")
        if submitted:
            if hmac.compare_digest(entered, expected):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("パスワードが正しくありません。")
    return False


def _build_clients(cfg: AppConfig) -> tuple[YouTubeClient, SharePointClient]:
    logger = setup_logger()
    yt = YouTubeClient(api_key=cfg.youtube_api_key, logger=logger)
    auth = GraphAuthClient(
        tenant_id=cfg.azure_tenant_id,
        client_id=cfg.azure_client_id,
        client_secret=cfg.azure_client_secret,
    )
    sp = SharePointClient(
        auth_client=auth,
        site_id=cfg.sharepoint_site_id,
        list_id=cfg.sharepoint_list_id,
        logger=logger,
    )
    return yt, sp


def _load_cloud_config(force_refresh: bool = False) -> tuple[dict[str, Any], AppConfig, str]:
    """SharePoint 上の config.json を同期読み込みし、(raw, AppConfig, source) を返す."""
    base_cfg = load_config(PROJECT_ROOT, require_channel_ids=False)
    _, sp = _build_clients(base_cfg)

    if force_refresh or "synced_raw_config" not in st.session_state:
        with st.spinner("SharePoint から最新設定を同期中..."):
            raw, source = load_synced_raw_config(sp, project_root=PROJECT_ROOT, logger=setup_logger())
            st.session_state["synced_raw_config"] = raw
            st.session_state["synced_config_source"] = source

    raw_data = copy.deepcopy(st.session_state["synced_raw_config"])
    source = st.session_state.get("synced_config_source", "sharepoint")
    cfg = build_config_from_raw(raw_data, project_root=PROJECT_ROOT, require_channel_ids=False)
    return raw_data, cfg, source


def _save_cloud_config(raw: dict[str, Any], cfg: AppConfig) -> None:
    """設定を SharePoint ドキュメントライブラリへ保存し、セッションキャッシュを更新する."""
    _, sp = _build_clients(cfg)
    with st.spinner("SharePoint へ設定を保存中..."):
        save_synced_raw_config(raw, sp, project_root=PROJECT_ROOT, logger=setup_logger())
    st.session_state["synced_raw_config"] = copy.deepcopy(raw)
    st.session_state["synced_config_source"] = "sharepoint"


def _find_matching_channel(channel_name: str, channels: list[ChannelConfig]) -> ChannelConfig | None:
    lower = (channel_name or "").strip().lower()
    if not lower:
        return None
    for ch in channels:
        ch_lower = ch.name.strip().lower()
        if ch_lower == lower or ch_lower in lower or lower in ch_lower:
            return ch
    return None


def _render_sidebar(raw: dict[str, Any], cfg: AppConfig, source: str) -> None:
    st.sidebar.title("📺 YouTube 自動収集")

    if source in ("sharepoint", "sharepoint_seeded"):
        st.sidebar.success(f"☁️ SharePoint 設定同期中\n(`{get_remote_config_path()}`)")
    else:
        st.sidebar.warning("⚠️ ローカル設定モード（SharePoint未同期）")

    sp_web_url = f"https://{cfg.sharepoint_hostname}{cfg.sharepoint_site_path}/Lists/YouTube"
    st.sidebar.link_button("📂 SharePoint リストを開く", sp_web_url, width="stretch")

    if st.sidebar.button("🔄 SharePoint から設定を再読込", width="stretch"):
        st.session_state.pop("synced_raw_config", None)
        st.session_state.pop("sp_items_cache", None)
        st.rerun()

    st.sidebar.divider()
    st.sidebar.subheader("⚙️ 全体収集設定")

    with st.sidebar.form("global_settings_form"):
        lookback_days = st.number_input(
            "取得対象期間（直近〇日間）",
            min_value=1,
            max_value=60,
            value=int(raw.get("lookback_days", 7)),
            step=1,
        )
        max_videos = st.number_input(
            "1チャンネルあたりの最大取得件数",
            min_value=1,
            max_value=200,
            value=int(raw.get("max_videos_per_channel", 50)),
            step=5,
        )
        default_target = st.selectbox(
            "カテゴリー判定の対象テキスト",
            options=["title_only", "title_and_description"],
            index=0 if raw.get("default_match_target", "title_only") == "title_only" else 1,
            format_func=lambda k: DEFAULT_MATCH_LABELS[k],
            help="概要欄には定型文や他番組URLが入るため、通常は「タイトルのみ」がおすすめです。",
        )
        if st.form_submit_button("💾 全体設定を保存", width="stretch"):
            try:
                raw["lookback_days"] = int(lookback_days)
                raw["max_videos_per_channel"] = int(max_videos)
                raw["default_match_target"] = default_target
                _save_cloud_config(raw, cfg)
                st.sidebar.success("全体設定を SharePoint へ保存しました。")
                st.rerun()
            except Exception as exc:
                st.sidebar.error(f"保存エラー: {exc}")

    st.sidebar.divider()
    enabled_count = sum(1 for c in raw.get("channels", []) if c.get("enabled", True))
    st.sidebar.metric("有効チャンネル数", f"{enabled_count} / {len(raw.get('channels', []))}")
    st.sidebar.caption("※ 保存した設定は iPhone / iPad / PC / 毎日 02:00 のクラウド自動収集にすべて即時反映されます。")

    if os.getenv("APP_PASSWORD", "").strip():
        if st.sidebar.button("🚪 ログアウト", width="stretch"):
            st.session_state["authenticated"] = False
            st.rerun()
    else:
        st.sidebar.info("💡 クラウド公開時は `APP_PASSWORD` を設定するとパスワード保護が有効になります。")


def _render_tab_channels(raw: dict[str, Any], cfg: AppConfig) -> None:
    st.subheader("📺 監視チャンネル ＆ カテゴリー絞り込み")
    st.caption(
        "例：PIVOT は「AI・生成AI」「テクノロジー・IT」だけ収集し、「政治・国際」「投資・金融」を除外する等、チャンネルごとに自由に設定できます。"
    )

    category_names = list(raw.get("categories", {}).keys())

    # 1. 新規チャンネル追加
    with st.expander("➕ 新しいチャンネルを追加（URL / @ハンドル / 動画URL）", expanded=False):
        with st.form("add_channel_form", clear_on_submit=True):
            url_input = st.text_input(
                "YouTube URL または @ハンドル",
                placeholder="https://www.youtube.com/@pivot00 や 動画URL",
            )
            new_inc_cats = st.multiselect(
                "収集したいカテゴリー（未選択＝全分野）",
                options=category_names,
                key="new_ch_inc_cats",
            )
            new_exc_cats = st.multiselect(
                "除外したいカテゴリー（興味のない分野）",
                options=category_names,
                key="new_ch_exc_cats",
            )
            c1, c2 = st.columns(2)
            with c1:
                new_inc_kws = st.text_input(
                    "追加の必須キーワード（任意・カンマ区切り）",
                    placeholder="例: Copilot, SharePoint",
                )
            with c2:
                new_exc_kws = st.text_input(
                    "追加の除外キーワード（任意・カンマ区切り）",
                    placeholder="例: 雑談, PR",
                )

            if st.form_submit_button("🔍 URLを解析してチャンネル追加", type="primary", width="stretch"):
                if not url_input.strip():
                    st.error("URL またはハンドルを入力してください。")
                else:
                    try:
                        with st.spinner("YouTube API でチャンネル情報を解決中..."):
                            yt, _ = _build_clients(cfg)
                            info = yt.resolve_channel_from_input(url_input.strip())
                        existing_ids = {c.get("channel_id") for c in raw.get("channels", [])}
                        if info["channel_id"] in existing_ids:
                            st.warning(f"チャンネル「{info['title']}」は既に登録済みです。")
                        else:
                            raw.setdefault("channels", []).append(
                                {
                                    "name": info["title"],
                                    "handle": info.get("handle", ""),
                                    "channel_id": info["channel_id"],
                                    "enabled": True,
                                    "include_categories": new_inc_cats,
                                    "exclude_categories": new_exc_cats,
                                    "include_keywords": _parse_keywords(new_inc_kws),
                                    "exclude_keywords": _parse_keywords(new_exc_kws),
                                    "match_target": "",
                                }
                            )
                            _save_cloud_config(raw, cfg)
                            st.success(f"「{info['title']}」を追加し、SharePoint へ同期しました！")
                            st.rerun()
                    except Exception as exc:
                        st.error(f"チャンネル追加に失敗しました: {exc}")

    st.divider()
    channels = raw.get("channels", [])
    if not channels:
        st.warning("監視対象チャンネルがまだ登録されていません。")
        return

    for idx, ch in enumerate(channels):
        status_icon = "✅" if ch.get("enabled", True) else "⏸️"
        inc_cats = [c for c in ch.get("include_categories", []) if c in category_names]
        exc_cats = [c for c in ch.get("exclude_categories", []) if c in category_names]
        inc_summary = ", ".join(inc_cats) if inc_cats else "全分野"
        exc_summary = f" / 除外: {', '.join(exc_cats)}" if exc_cats else ""
        header = f"{status_icon} {ch.get('name', '(名称未設定)')} ｜ {inc_summary}{exc_summary}"

        with st.expander(header, expanded=(idx == 0)):
            ch_id = ch.get("channel_id", "")
            handle = ch.get("handle", "")
            st.caption(f"ID: `{ch_id}`" + (f" / `{handle}`" if handle else ""))

            with st.form(f"edit_channel_{idx}"):
                enabled = st.checkbox("自動収集の対象にする", value=bool(ch.get("enabled", True)))
                selected_inc_cats = st.multiselect(
                    "収集したいカテゴリー（絞りたい分野を選択・空欄なら全件）",
                    options=category_names,
                    default=inc_cats,
                )
                selected_exc_cats = st.multiselect(
                    "除外したいカテゴリー（興味のない分野を除外）",
                    options=category_names,
                    default=exc_cats,
                )
                c1, c2 = st.columns(2)
                with c1:
                    inc_kws_str = st.text_input(
                        "追加の必須キーワード（カンマ区切り）",
                        value=", ".join(ch.get("include_keywords", [])),
                    )
                with c2:
                    exc_kws_str = st.text_input(
                        "追加の除外キーワード（カンマ区切り）",
                        value=", ".join(ch.get("exclude_keywords", [])),
                    )

                current_target = ch.get("match_target", "")
                if current_target not in MATCH_TARGET_LABELS:
                    current_target = ""
                match_target = st.selectbox(
                    "判定対象テキスト",
                    options=list(MATCH_TARGET_LABELS.keys()),
                    index=list(MATCH_TARGET_LABELS.keys()).index(current_target),
                    format_func=lambda k: MATCH_TARGET_LABELS[k],
                )

                btn_c1, btn_c2 = st.columns([3, 1])
                with btn_c1:
                    save_clicked = st.form_submit_button("💾 設定を保存（クラウド同期）", type="primary", width="stretch")
                with btn_c2:
                    delete_clicked = st.form_submit_button("🗑️ 削除", width="stretch")

                if save_clicked:
                    try:
                        raw["channels"][idx]["enabled"] = enabled
                        raw["channels"][idx]["include_categories"] = selected_inc_cats
                        raw["channels"][idx]["exclude_categories"] = selected_exc_cats
                        raw["channels"][idx]["include_keywords"] = _parse_keywords(inc_kws_str)
                        raw["channels"][idx]["exclude_keywords"] = _parse_keywords(exc_kws_str)
                        raw["channels"][idx]["match_target"] = match_target
                        _save_cloud_config(raw, cfg)
                        st.success(f"「{ch.get('name')}」の設定を SharePoint へ保存しました。")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"保存エラー: {exc}")

                if delete_clicked:
                    try:
                        removed_name = raw["channels"].pop(idx).get("name", "")
                        _save_cloud_config(raw, cfg)
                        st.warning(f"チャンネル「{removed_name}」を削除しました。")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"削除エラー: {exc}")


def _render_tab_categories(raw: dict[str, Any], cfg: AppConfig) -> None:
    st.subheader("🏷️ カテゴリーと判定キーワードの定義")
    st.caption("キーワードを追加・編集すると、チャンネル絞り込みと SharePoint「分野」列の自動分類に使われます。")

    categories: dict[str, list[str]] = raw.get("categories", {})

    with st.expander("➕ 新しいカテゴリーを追加", expanded=False):
        with st.form("add_category_form", clear_on_submit=True):
            new_cat_name = st.text_input("カテゴリー名", placeholder="例: 美容・サロン経営")
            new_cat_kws = st.text_area(
                "キーワード（カンマ区切りまたは改行区切り）",
                placeholder="美容室, サロン, 集客, リピート, 接客",
            )
            if st.form_submit_button("カテゴリーを追加して保存", type="primary", width="stretch"):
                name = new_cat_name.strip()
                kws = _parse_keywords(new_cat_kws)
                if not name or not kws:
                    st.error("カテゴリー名と1つ以上のキーワードを入力してください。")
                elif name in categories:
                    st.warning(f"カテゴリー「{name}」は既に存在します。")
                else:
                    categories[name] = kws
                    raw["categories"] = categories
                    _save_cloud_config(raw, cfg)
                    st.success(f"カテゴリー「{name}」を保存しました。")
                    st.rerun()

    for cat_name, kws in list(categories.items()):
        with st.expander(f"🏷️ {cat_name}（{len(kws)} 語）", expanded=False):
            with st.form(f"edit_cat_{cat_name}"):
                kw_text = st.text_area(
                    "キーワード（カンマまたは改行区切り）",
                    value=", ".join(kws),
                    height=110,
                )
                c1, c2 = st.columns([3, 1])
                with c1:
                    save_cat = st.form_submit_button("💾 保存", type="primary", width="stretch")
                with c2:
                    del_cat = st.form_submit_button("🗑️ 削除", width="stretch")

                if save_cat:
                    parsed = _parse_keywords(kw_text)
                    if not parsed:
                        st.error("キーワードを1つ以上指定してください。")
                    else:
                        categories[cat_name] = parsed
                        raw["categories"] = categories
                        _save_cloud_config(raw, cfg)
                        st.success(f"カテゴリー「{cat_name}」を更新しました。")
                        st.rerun()

                if del_cat:
                    categories.pop(cat_name, None)
                    for ch in raw.get("channels", []):
                        ch["include_categories"] = [c for c in ch.get("include_categories", []) if c != cat_name]
                        ch["exclude_categories"] = [c for c in ch.get("exclude_categories", []) if c != cat_name]
                    raw["categories"] = categories
                    _save_cloud_config(raw, cfg)
                    st.warning(f"カテゴリー「{cat_name}」を削除しました。")
                    st.rerun()

    st.divider()
    if st.button("🔄 初期プリセットカテゴリーを復元", width="stretch"):
        for k, v in DEFAULT_CATEGORIES.items():
            if k not in categories:
                categories[k] = copy.deepcopy(v)
        raw["categories"] = categories
        _save_cloud_config(raw, cfg)
        st.success("初期プリセットを復元しました。")
        st.rerun()


def _load_sp_items(cfg: AppConfig, force_refresh: bool = False) -> list[dict[str, Any]]:
    if force_refresh or "sp_items_cache" not in st.session_state:
        with st.spinner("SharePoint から動画一覧を取得中..."):
            _, sp = _build_clients(cfg)
            st.session_state["sp_items_cache"] = sp.list_video_items(limit=500)
    return st.session_state["sp_items_cache"]


def _render_tab_videos(raw: dict[str, Any], cfg: AppConfig) -> None:
    st.subheader("🎬 動画URLの手動追加 ＆ 登録済み動画の削除")

    # 1. 単発動画URLの手動追加
    with st.expander("➕ 個別の YouTube 動画URLを SharePoint へ追加", expanded=False):
        with st.form("add_single_video_form", clear_on_submit=True):
            urls_text = st.text_area(
                "YouTube 動画URL（複数行で一括追加OK）",
                placeholder="https://www.youtube.com/watch?v=xxxxxxxxxxx\nhttps://youtu.be/yyyyyyyyyyy",
                height=90,
            )
            cat_options = ["（タイトルから自動判定）"] + list(raw.get("categories", {}).keys())
            manual_cat = st.selectbox("分野（Category列）の指定", options=cat_options)
            if st.form_submit_button("📥 動画情報を取得して SharePoint へ登録", type="primary", width="stretch"):
                lines = [ln.strip() for ln in urls_text.splitlines() if ln.strip()]
                video_ids: list[str] = []
                invalid_lines: list[str] = []
                for ln in lines:
                    vid = extract_video_id(ln)
                    if vid:
                        video_ids.append(vid)
                    else:
                        invalid_lines.append(ln)

                if invalid_lines:
                    st.warning("URLを解析できなかった行: " + ", ".join(invalid_lines))

                if not video_ids:
                    st.error("有効な YouTube 動画URL が見つかりませんでした。")
                else:
                    try:
                        with st.spinner(f"{len(video_ids)} 件を取得・登録中..."):
                            yt, sp = _build_clients(cfg)
                            videos = yt.fetch_videos_by_ids(
                                video_ids=video_ids,
                                description_max_length=cfg.description_max_length,
                            )
                            created, skipped, failed = 0, 0, 0
                            for video in videos:
                                if manual_cat == "（タイトルから自動判定）":
                                    detected = detect_categories(video.title, cfg.categories)
                                    cat_val = ", ".join(detected) if detected else None
                                else:
                                    cat_val = manual_cat
                                enriched = replace(video, category=cat_val)
                                try:
                                    sp.create_video_item(enriched)
                                    created += 1
                                except DuplicateVideoError:
                                    skipped += 1
                                except Exception as exc:
                                    failed += 1
                                    st.error(f"登録エラー ({video.title}): {exc}")

                        st.success(f"完了: 新規登録 {created} 件 / 重複スキップ {skipped} 件 / エラー {failed} 件")
                        st.session_state.pop("sp_items_cache", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"登録処理エラー: {exc}")

    st.divider()

    top_c1, top_c2 = st.columns([3, 1])
    with top_c2:
        if st.button("🔄 一覧を更新", width="stretch"):
            _load_sp_items(cfg, force_refresh=True)

    try:
        items = _load_sp_items(cfg, force_refresh=False)
    except Exception as exc:
        st.error(f"SharePoint からの一覧取得に失敗しました: {exc}")
        return

    with top_c1:
        st.markdown(f"#### 📋 登録済み動画一覧（全 {len(items)} 件）")

    if not items:
        st.info("登録済みの動画はありません。")
        return

    enriched_rows: list[dict[str, Any]] = []
    for item in items:
        matched_ch = _find_matching_channel(item["channel_name"], cfg.channels)
        if matched_ch:
            decision = evaluate_text(
                title=item["title"],
                description=item["description"],
                channel=matched_ch,
                categories=cfg.categories,
                default_match_target=cfg.default_match_target,
            )
            filter_state = "条件適合" if decision.matched else "条件外"
            filter_reason = decision.reason
            detected_cat = item["category"] or ", ".join(decision.matched_categories)
        else:
            filter_state = "監視外チャンネル"
            filter_reason = "単発追加または監視外"
            detected_cat = item["category"] or ", ".join(detect_categories(item["title"], cfg.categories))

        enriched_rows.append(
            {
                "削除": False,
                "item_id": item["item_id"],
                "公開日時": item["published_at"][:16].replace("T", " "),
                "チャンネル": item["channel_name"],
                "タイトル": item["title"],
                "分野": detected_cat,
                "現在フィルタ判定": filter_state,
                "判定理由": filter_reason,
                "動画URL": item["video_url"],
                "サムネイルURL": item["thumbnail_url"],
                "動画ID": item["video_id"],
            }
        )

    # フィルタUI
    f1, f2 = st.columns(2)
    with f1:
        all_channels = sorted({r["チャンネル"] for r in enriched_rows if r["チャンネル"]})
        ch_filter = st.multiselect("チャンネル絞り込み", options=all_channels)
    with f2:
        state_filter = st.multiselect(
            "現在フィルタ判定で絞り込み",
            options=["条件適合", "条件外", "監視外チャンネル"],
            help="「条件外」を選ぶと、設定から外れた既存動画（政治・投資など）をまとめて削除できます。",
        )

    f3, f4 = st.columns([2, 1])
    with f3:
        keyword_search = st.text_input("タイトル・分野検索", placeholder="キーワードで絞り込み")
    with f4:
        view_mode = st.radio(
            "表示形式",
            options=["📱 カード（スマホ向け）", "💻 テーブル（一括削除向け）"],
            horizontal=True,
        )

    filtered_rows = enriched_rows
    if ch_filter:
        filtered_rows = [r for r in filtered_rows if r["チャンネル"] in ch_filter]
    if state_filter:
        filtered_rows = [r for r in filtered_rows if r["現在フィルタ判定"] in state_filter]
    if keyword_search.strip():
        q = keyword_search.strip().lower()
        filtered_rows = [
            r for r in filtered_rows if q in r["タイトル"].lower() or q in r["分野"].lower()
        ]

    st.caption(f"表示中: **{len(filtered_rows)} 件** / 全 {len(enriched_rows)} 件")
    if not filtered_rows:
        st.info("条件に一致する動画はありません。")
        return

    if view_mode.startswith("📱"):
        # iPhone / iPad で操作しやすいカード一覧（ページネーション付き）
        page_size = 20
        total_pages = max(1, (len(filtered_rows) + page_size - 1) // page_size)
        page = st.number_input("ページ", min_value=1, max_value=total_pages, value=1, step=1) if total_pages > 1 else 1
        start = (page - 1) * page_size
        page_rows = filtered_rows[start : start + page_size]

        for row in page_rows:
            with st.container(border=True):
                c_img, c_body = st.columns([1, 2])
                with c_img:
                    if row["サムネイルURL"]:
                        st.image(row["サムネイルURL"], width="stretch")
                with c_body:
                    badge_cls = "badge-ok" if row["現在フィルタ判定"] == "条件適合" else "badge-out"
                    cat_badge = f"<span class='badge badge-cat'>{row['分野']}</span>" if row["分野"] else ""
                    st.markdown(
                        f"<span class='badge {badge_cls}'>{row['現在フィルタ判定']}</span>{cat_badge}",
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"**{row['タイトル']}**")
                    st.caption(f"{row['チャンネル']} ｜ {row['公開日時']}")
                    act1, act2 = st.columns(2)
                    with act1:
                        st.link_button("▶️ YouTubeで見る", row["動画URL"], width="stretch")
                    with act2:
                        if st.button("🗑️ SharePointから削除", key=f"del_card_{row['item_id']}", width="stretch"):
                            try:
                                _, sp = _build_clients(cfg)
                                sp.delete_video_item(str(row["item_id"]))
                                st.success("削除しました。")
                                st.session_state.pop("sp_items_cache", None)
                                st.rerun()
                            except Exception as exc:
                                st.error(f"削除失敗: {exc}")
    else:
        select_all_shown = st.checkbox("表示中の動画をすべて削除チェック")
        if select_all_shown:
            for r in filtered_rows:
                r["削除"] = True

        df = pd.DataFrame(filtered_rows)
        edited_df = st.data_editor(
            df,
            width="stretch",
            hide_index=True,
            column_order=["削除", "公開日時", "チャンネル", "タイトル", "分野", "現在フィルタ判定", "動画URL", "判定理由"],
            disabled=["公開日時", "チャンネル", "タイトル", "分野", "現在フィルタ判定", "動画URL", "判定理由"],
            column_config={
                "削除": st.column_config.CheckboxColumn("削除", width="small"),
                "動画URL": st.column_config.LinkColumn("動画URL", display_text="YouTubeを開く"),
                "タイトル": st.column_config.TextColumn("タイトル", width="large"),
            },
            key="sp_video_editor",
        )

        selected_df = edited_df[edited_df["削除"] == True]  # noqa: E712
        selected_count = len(selected_df)

        confirm_delete = st.checkbox(f"選択中の {selected_count} 件を SharePoint から完全削除することに同意する")
        if st.button(
            f"🗑️ 選択した {selected_count} 件を SharePoint から一括削除",
            type="primary",
            disabled=(selected_count == 0 or not confirm_delete),
            width="stretch",
        ):
            _, sp = _build_clients(cfg)
            deleted, failed = 0, 0
            progress = st.progress(0.0)
            target_rows = selected_df.to_dict(orient="records")
            for i, row in enumerate(target_rows, start=1):
                try:
                    sp.delete_video_item(str(row["item_id"]))
                    deleted += 1
                except Exception as exc:
                    failed += 1
                    st.error(f"削除失敗 ({row['タイトル']}): {exc}")
                progress.progress(i / len(target_rows))

            st.success(f"SharePoint から {deleted} 件を削除しました（失敗: {failed} 件）。")
            st.session_state.pop("sp_items_cache", None)
            st.rerun()


def _render_tab_runner(cfg: AppConfig) -> None:
    st.subheader("🚀 プレビュー（Dry-Run）＆ 収集実行")
    st.caption(
        "現在の絞り込み設定でどの動画が対象・除外になるかをプレビュー確認したり、iPhone/iPad から今すぐ本番収集を実行できます。"
    )

    c1, c2 = st.columns(2)
    with c1:
        run_dry = st.button("🔍 プレビュー確認（Dry-Run）", width="stretch")
    with c2:
        run_prod = st.button("▶️ 今すぐ本番収集を実行", type="primary", width="stretch")

    if run_dry or run_prod:
        is_dry = run_dry and not run_prod
        label = "プレビュー（Dry-Run）" if is_dry else "本番収集"
        try:
            with st.spinner(f"{label} を実行中..."):
                yt, sp = _build_clients(cfg)
                logger = setup_logger()
                service = CollectorService(
                    config=cfg,
                    youtube_client=yt,
                    sharepoint_client=sp,
                    logger=logger,
                )
                summary = service.run(dry_run=is_dry)
                st.session_state["last_run_result"] = {
                    "mode": label,
                    "summary": summary,
                }
                if not is_dry:
                    st.session_state.pop("sp_items_cache", None)
        except Exception as exc:
            st.error(f"{label} でエラーが発生しました: {exc}")

    last = st.session_state.get("last_run_result")
    if last:
        summary = last["summary"]
        st.markdown(f"#### 📊 実行結果（{last['mode']}）")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("取得総数", summary.fetched_count)
        m2.metric("対象 / 新規登録", summary.created_count)
        m3.metric("フィルタ除外", summary.filtered_out_count)
        m4.metric("登録済スキップ", summary.skipped_count)

        if summary.errors:
            for err in summary.errors:
                st.error(err)

        if summary.preview_items:
            p_df = pd.DataFrame(summary.preview_items)
            status_opts = list(p_df["status"].unique())
            selected_status = st.multiselect(
                "表示ステータス絞り込み",
                options=status_opts,
                default=status_opts,
            )
            show_df = p_df[p_df["status"].isin(selected_status)]
            st.dataframe(
                show_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "video_url": st.column_config.LinkColumn("動画URL", display_text="開く"),
                },
            )

    log_path = PROJECT_ROOT / "logs" / "collector.log"
    if log_path.exists():
        with st.expander("📜 ローカル実行ログ（logs/collector.log）", expanded=False):
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            st.code("\n".join(lines[-80:]), language="text")


def main() -> None:
    st.set_page_config(
        page_title="YouTube 自動収集",
        page_icon="📺",
        layout="wide",
        initial_sidebar_state="auto",
    )
    st.markdown(MOBILE_FRIENDLY_CSS, unsafe_allow_html=True)
    _bootstrap_secrets()

    if not _require_login():
        return

    try:
        raw, cfg, source = _load_cloud_config(force_refresh=False)
    except Exception as exc:
        st.error(f"設定または認証情報の読み込みに失敗しました: {exc}")
        return

    _render_sidebar(raw, cfg, source)

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "📺 チャンネル",
            "🏷️ 分野",
            "🎬 動画",
            "🚀 実行",
        ]
    )

    with tab1:
        _render_tab_channels(raw, cfg)
    with tab2:
        _render_tab_categories(raw, cfg)
    with tab3:
        _render_tab_videos(raw, cfg)
    with tab4:
        _render_tab_runner(cfg)


if __name__ == "__main__":
    main()
