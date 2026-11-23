from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from src.models import ChannelConfig, FilterDecision, VideoItem


DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "AI・生成AI": [
        "AI",
        "生成AI",
        "ChatGPT",
        "Gemini",
        "Claude",
        "OpenAI",
        "Anthropic",
        "LLM",
        "Copilot",
        "人工知能",
        "機械学習",
        "ディープラーニング",
        "AIエージェント",
        "エージェント",
        "プロンプト",
        "NVIDIA",
        "エヌビディア",
    ],
    "テクノロジー・IT": [
        "テクノロジー",
        "テック",
        "IT",
        "DX",
        "クラウド",
        "SaaS",
        "半導体",
        "データセンター",
        "ロボット",
        "ロボティクス",
        "自動化",
        "プログラミング",
        "Python",
        "ソフトウェア",
        "アプリ",
        "サイバー",
        "セキュリティ",
        "量子",
        "自動運転",
        "スタートアップ",
        "プロダクト開発",
    ],
    "ビジネス・業務効率化": [
        "業務効率化",
        "生産性",
        "マーケティング",
        "経営",
        "マネジメント",
        "組織",
        "働き方",
        "仕事術",
        "インプット術",
        "議事録",
        "Microsoft",
        "SharePoint",
        "Power Platform",
        "ノーコード",
        "起業",
    ],
    "投資・金融": [
        "投資",
        "株式投資",
        "株",
        "NISA",
        "新NISA",
        "日経平均",
        "金利",
        "為替",
        "資産運用",
        "ポートフォリオ",
        "ビットコイン",
        "仮想通貨",
        "マネー",
        "FIRE",
        "億り人",
        "FRB",
        "日銀",
        "相場",
        "上場",
        "MBO",
    ],
    "政治・国際": [
        "政治",
        "選挙",
        "トランプ",
        "自民党",
        "国会",
        "外交",
        "軍事",
        "戦争",
        "北朝鮮",
        "政権",
        "独裁",
        "大統領",
        "首相",
        "財政",
        "税優遇",
    ],
    "エンタメ・ライフ": [
        "占い",
        "六星占術",
        "夫婦",
        "子育て",
        "恋愛",
        "グラビア",
        "美容",
        "ダイエット",
        "料理",
        "グルメ",
        "編み物",
        "スクイーズ",
        "小説",
        "読書",
    ],
}

_SHORT_ASCII_RE = re.compile(r"^[a-z0-9]{1,3}$")


def normalize_text(text: str) -> str:
    """全角半角正規化＋小文字化を行う."""
    return unicodedata.normalize("NFKC", text or "").lower()


def keyword_matches(text_normalized: str, keyword: str) -> bool:
    """短いASCIIキーワードの誤爆を防ぎつつキーワード一致を判定する."""
    kw = normalize_text(keyword).strip()
    if not kw:
        return False
    if _SHORT_ASCII_RE.match(kw):
        # 例: 'ai' が 'daily' や 'email' に誤爆しないように英数字境界を要求
        pattern = rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])"
        return re.search(pattern, text_normalized) is not None
    return kw in text_normalized


def detect_categories(
    text: str,
    categories: dict[str, list[str]],
) -> list[str]:
    """テキストに一致したカテゴリー名のリストを返す."""
    normalized = normalize_text(text)
    matched: list[str] = []
    for cat_name, keywords in categories.items():
        if any(keyword_matches(normalized, kw) for kw in keywords):
            matched.append(cat_name)
    return matched


def _build_target_text(title: str, description: str, match_target: str) -> str:
    if match_target == "title_and_description":
        return f"{title}\n{description}"
    return title


def _matched_keywords(text_normalized: str, keywords: Iterable[str]) -> list[str]:
    return [kw for kw in keywords if keyword_matches(text_normalized, kw)]


def evaluate_text(
    title: str,
    description: str,
    channel: ChannelConfig,
    categories: dict[str, list[str]],
    default_match_target: str = "title_only",
) -> FilterDecision:
    """タイトル・概要欄とチャンネルフィルタ設定から採用/除外を判定する."""
    effective_target = channel.match_target or default_match_target or "title_only"
    target_text = _build_target_text(title, description, effective_target)
    normalized = normalize_text(target_text)

    all_matched_cats = detect_categories(target_text, categories)

    # 1. 除外キーワードチェック
    hit_exclude_kws = _matched_keywords(normalized, channel.exclude_keywords)
    if hit_exclude_kws:
        return FilterDecision(
            matched=False,
            matched_categories=tuple(all_matched_cats),
            reason=f"除外キーワード一致: {', '.join(hit_exclude_kws)}",
        )

    # 2. 除外カテゴリーチェック
    exclude_cat_set = set(channel.exclude_categories)
    hit_exclude_cats = [c for c in all_matched_cats if c in exclude_cat_set]
    if hit_exclude_cats:
        return FilterDecision(
            matched=False,
            matched_categories=tuple(all_matched_cats),
            excluded_categories=tuple(hit_exclude_cats),
            reason=f"除外カテゴリー一致: {', '.join(hit_exclude_cats)}",
        )

    # 3. 採用条件チェック（include_categories / include_keywords が指定されている場合のみ絞り込み）
    has_include_filter = bool(channel.include_categories or channel.include_keywords)
    if has_include_filter:
        include_cat_set = set(channel.include_categories)
        hit_include_cats = [c for c in all_matched_cats if c in include_cat_set]
        hit_include_kws = _matched_keywords(normalized, channel.include_keywords)

        if not hit_include_cats and not hit_include_kws:
            wanted = list(channel.include_categories) + list(channel.include_keywords)
            return FilterDecision(
                matched=False,
                matched_categories=tuple(all_matched_cats),
                reason=f"対象カテゴリー/キーワード不一致（対象: {', '.join(wanted)}）",
            )

        reasons: list[str] = []
        if hit_include_cats:
            reasons.append(f"カテゴリー一致: {', '.join(hit_include_cats)}")
        if hit_include_kws:
            reasons.append(f"キーワード一致: {', '.join(hit_include_kws)}")
        return FilterDecision(
            matched=True,
            matched_categories=tuple(hit_include_cats or all_matched_cats),
            reason=" / ".join(reasons),
        )

    return FilterDecision(
        matched=True,
        matched_categories=tuple(all_matched_cats),
        reason="絞り込みなし（全件対象）",
    )


def evaluate_video(
    video: VideoItem,
    channel: ChannelConfig,
    categories: dict[str, list[str]],
    default_match_target: str = "title_only",
) -> FilterDecision:
    """VideoItem に対するフィルタリング判定を行う."""
    return evaluate_text(
        title=video.title,
        description=video.description,
        channel=channel,
        categories=categories,
        default_match_target=default_match_target,
    )
