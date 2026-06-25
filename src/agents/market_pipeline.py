"""
Market news pipeline — hard filters + topic fatigue + article body fetch.

Three phases before AI scoring:
  Phase A:  hard filter — block keywords + dedup + freshness + track ignored
            Returns ALL survivors (no scoring, no selection).
            AI scoring handles quality judgment in scorer.py.
  Phase A2: topic fatigue check — downgrade/block repeated topics
  Phase B:  deep fetch article bodies for richer AI summaries

Phase C (AI summarize + score) is in scorer.py.
Phase D (format + push) is in briefer.py.
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timedelta
from typing import Any

from src.pipeline.source_constants import is_bilibili
from src.pipeline.token_utils import _RE_GAME_NAMES, _RE_TOPIC_WORDS


# ── 二次元过滤器辅助 ──

# 标题中出现这些词 → 大概率是产品动态新闻而非泛行业分析，
# 即使没有 《》 括注也应放行让 AI 打分决定。
_PRODUCT_SIGNAL_WORDS: list[str] = [
    "联动", "上线", "更新", "公测", "内测", "发售", "开服",
    "新角色", "新版本", "新活动", "限定", "复刻", "周年庆",
    "新赛季", "新地图", "新玩法",
    "改版", "重制", "移植", "下架", "停服", "关服",
    "发布", "推出", "曝光", "预告", "首曝",
    "下载", "预约", "预购", "Steam", "steam", "手游",
    "iOS", "Android", "安卓", "苹果",
]

# 常见的游戏品牌名，可能在标题中不括 《》 也出现。
# 只放真正的大品牌，避免"二游"本身被误判。
_KNOWN_GAME_NAMES: list[str] = [
    "原神", "崩坏", "星穹铁道", "绝区零", "明日方舟",
    "碧蓝航线", "少女前线", "FGO", "Fate/Grand Order",
    "阴阳师", "白夜极光", "鸣潮", "蔚蓝档案", "赛马娘",
    "公主连结", "崩坏3", "未定事件簿", "世界计划",
    "学园偶像祭", "BanG Dream", "Love Live",
    "王者荣耀", "和平精英", "蛋仔派对",
    "无限暖暖", "恋与深空", "重返未来",
    "光遇", "第五人格", "梦幻西游",
    "英雄联盟", "LOL", "DNF", "CF", "逆水寒",
    "永劫无间", "幻塔", "尘白禁区",
]


def _has_product_signal(headline: str) -> bool:
    """Check if a headline contains product-news signal words."""
    return any(kw in headline for kw in _PRODUCT_SIGNAL_WORDS)


def _has_known_game_name(headline: str) -> bool:
    """Check if a headline mentions a known game brand without 《》 brackets."""
    return any(name in headline for name in _KNOWN_GAME_NAMES)


# ═════════════════════════════════════════════════════════════
# Phase A: hard filter — block keywords + dedup + freshness + track ignored
# ═════════════════════════════════════════════════════════════

def filter_news(news: list[dict[str, Any]], target_date: str = "",
                ) -> list[dict[str, Any]]:
    """Hard-filter news: block keywords, dedup, freshness gate, track exclusions.

    Returns ALL survivors. No scoring, no diversity selection — AI handles
    quality judgment in scorer.ai_summarize_and_judge().

    Pipeline:
      1. Block keywords (tech spam, sports, entertainment, discounts)
      2. URL dedup (cross-day, via reported_items)
      3. Cross-source headline dedup (token-level)
      4. Track-filter ignored check (exclude excluded genres)
      5. Freshness: non-bilibili items must be within 7 days
    """
    from src.agents.dedup import headline_dedup_tokens, load_reported_news, load_reported_news_headlines

    # Full filter for news articles (tech spam, sports, entertainment, PC deals)
    news_block_keywords = [
        "AirPods", "iPhone", "iPad", "MacBook", "Apple Watch",
        "电动滑板车", "电视", "耳机", "音箱", "手表",
        "Prime Day", "特惠精选",
        "世界杯", "足球", "NBA", "英超", "西甲", "欧冠",
        "演唱会", "张靓颖", "明星", "八卦", "走光", "抄袭",
        "芝麻街", "Netflix", "电影", "预告", "剧透",
        "礼包", "广告", "赛马大会", "抢号",
        "Alienware", "ROG新品", "游戏电脑", "大促", "立省",
        # Consumer discount content
        "史低", "新史低", "平史低", "白菜价", "白嫖", "喜加",
        "夏促", "冬促", "春促", "秋促", "打折", "促销",
        "免费领", "限免", "免费玩",
        "捆绑包", "折扣推荐", "史低推荐",
        "音游", "节奏游戏",
        "Steam新品节", "游戏节",
    ]

    # Bilibili-only: filter consumer discount + esports content
    bilibili_block_keywords = [
        # Consumer discount
        "史低", "新史低", "平史低", "白菜价", "白嫖", "喜加",
        "夏促", "冬促", "春促", "秋促", "打折", "促销",
        "免费领", "限免", "免费玩",
        "捆绑包", "折扣推荐", "史低推荐",
        # Esports
        "电竞", "夺冠", "决赛", "赛事", "Major", "major",
        "CS2", "CS:GO", "CSGO", "NiKo", "Falcons", "猎鹰",
        # Music games (not track)
        "音游", "节奏游戏",
    ]

    # ── News URL dedup ──
    reported_urls = load_reported_news()
    reported_headlines = load_reported_news_headlines()

    filtered = []
    for n in news:
        source = (n.get("source", "") or "").lower()
        url = (n.get("url", "") or "").lower()

        headline = n.get("headline", "")
        is_bili = is_bilibili(source) or "bilibili" in url

        # URL dedup: normalize (strip query params + fragment), then check
        normalized = re.sub(r'[?#].*$', '', url)
        if normalized and normalized in reported_urls:
            continue

        # Cross-source dedup: check token-level overlap with reported headlines
        dedup_tokens = headline_dedup_tokens(headline)
        if dedup_tokens and any(t in reported_headlines for t in dedup_tokens):
            continue

        if is_bili:
            if any(kw in headline for kw in bilibili_block_keywords):
                continue
        else:
            if any(kw.lower() in headline.lower() for kw in news_block_keywords):
                continue

        # ── Track-filter ignored check: same logic as game classification ──
        # Skip news about games/categories that are explicitly excluded
        # (女性向/二次元/乙女), unless the headline also triggers a track keyword.
        try:
            from src.pipeline.track_filter import classify_game
            rel_game = n.get("related_game", "") or ""
            classification = classify_game(
                game_name=rel_game or headline,
                description=headline,
            )
            if classification == "ignored":
                continue
            # Scraper may have misclassified (e.g. PG.biz hardcodes
            # track_relevant=False). Correct it here when the classifier
            # detects track relevance.
            if classification == "track" and not n.get("track_relevant"):
                n["track_relevant"] = True
        except Exception:
            pass  # best-effort, don't block news on classifier error

        # ── 二次元行业分析过滤 ──
        # 头条里常有"二游市场缩水"、"二游玩家疲劳"这类泛行业趋势分析，
        # 对关注塔防/肉鸽/割草的读者没有参考价值。但如果标题里提到了
        # 具体游戏（《蔚蓝档案》、《原神》等），则是产品级新闻，
        # 放行让 AI 打分决定去留。
        _er_yuan_kw = ["二游", "二次元", "乙女"]
        if any(kw in headline for kw in _er_yuan_kw):
            games_in_headline = _RE_GAME_NAMES.findall(headline)
            if games_in_headline:
                # 有《》括注的具体游戏名 → 产品新闻，放行
                pass
            elif _has_product_signal(headline):
                # 含"联动""上线""更新"等产品动态信号词 → 非行业分析，放行
                pass
            elif _has_known_game_name(headline):
                # 含已知游戏品牌名（如"原神"）但未加《》→ 大概率产品新闻，放行
                pass
            else:
                # 无具体游戏标识 → 泛行业分析 → 过滤
                continue

        # ── Freshness check: non-bilibili items must have publish_date within 7 days ──
        if not is_bili and target_date:
            pub_date = n.get("publish_date", "")
            if not pub_date:
                # Fallback 1: try to extract date from URL (e.g. GameLook /2025/03/566840/)
                url = n.get("url", "")
                pub_date = _extract_date_from_url(url)
            if not pub_date:
                # Fallback 2: treat as today's article (scraper found it on the list page)
                pub_date = target_date
            if not _is_within_days(pub_date, target_date, days=7):
                continue

        filtered.append(n)

    return filtered



# ═════════════════════════════════════════════════════════════
# Freshness helpers
# ═════════════════════════════════════════════════════════════

def _is_within_days(publish_date: str, target_date: str, days: int = 7) -> bool:
    """Check if publish_date is within N days of target_date.

    Handles partial dates: '2026-06' (month-only) is treated as
    2026-06-01 for comparison.
    """
    try:
        target = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return True  # can't compare, don't filter

    # Parse publish_date — may be YYYY-MM-DD or YYYY-MM
    pub_str = publish_date.strip()
    try:
        if len(pub_str) == 7:  # YYYY-MM → accept if same month as target
            pub = datetime.strptime(pub_str, "%Y-%m")
            # Same month and same year: accept (conservative — we don't know the day)
            if pub.year == target.year and pub.month == target.month:
                return True
            # Different month: check if within 7 days (edge of month)
            # Use the 1st of the month; if that's within 7 days, accept
            delta = target - pub
            return timedelta(0) <= delta <= timedelta(days=days)
        else:
            pub = datetime.strptime(pub_str[:10], "%Y-%m-%d")
    except ValueError:
        return False  # unparseable date → filter out

    delta = target - pub
    return timedelta(0) <= delta <= timedelta(days=days)


def _extract_date_from_url(url: str) -> str:
    """Extract a date from a news article URL as last-resort fallback.

    Supports: /YYYY/MM/article_id (GameLook), /news/YYYYMM/ (3DM, 17173),
    /YYYY/MMDD/ (full date), /YYYYMMDD/ (compact date).
    """
    # /news/202606/... → 2026-06
    m = re.search(r'/(20[2-9]\d)(\d{2})/', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # /2026/0623/... → 2026-06-23
    m = re.search(r'/(20[2-9]\d)/(\d{2})(\d{2})/', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # /2026/02/586962/ or /2026/02/586962.html → 2026-02 (GameLook pattern)
    m = re.search(r'/(20[2-9]\d)/(\d{2})/\d{4,}(?:\.html?)?', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # /20260623... → 2026-06-23
    m = re.search(r'/(20[2-9]\d)(\d{2})(\d{2})\D', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


# ═════════════════════════════════════════════════════════════
# Phase A2: topic fatigue
# ═════════════════════════════════════════════════════════════

def apply_fatigue(
    candidates: list[dict[str, Any]], date: str, window_days: int = 3,
) -> list[dict[str, Any]]:
    """Downgrade or remove candidates whose topics appeared in recent reports.

    Rules:
      - Topic seen 1 day ago (yesterday) → candidate stays, score marked down
      - Topic seen 2 consecutive days → candidate removed

    Topic matching: extract game names from 《》brackets, fall back to
    headline keyword overlap if no bracketed name found.
    """
    if not candidates:
        return candidates

    # ── Collect past headlines from analysis_reports ──
    try:
        target = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return candidates

    past_dates = [
        (target - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(1, window_days + 1)
    ]

    past_headlines: dict[str, set[str]] = {}  # date → set of topics
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        for d in past_dates:
            report = db.get_analysis_report(d)
            if not report:
                continue
            # Read directly from the market_md column (no JSON parsing needed)
            market_content = report.get("market_md", "")
            if not market_content:
                continue
            # Extract bold text (headlines) from blockquotes
            headlines = re.findall(r'>\s*\*\*(.+?)\*\*', market_content)
            past_headlines[d] = set(headlines)
    except Exception as e:
        print(f"  [WARN] Failed to read past headlines for fatigue check: {e}", file=sys.stderr)

    if not past_headlines:
        return candidates

    # ── Helper: extract topic key from a headline ──
    def _topic_key(headline: str) -> str:
        """Extract the most distinctive topic identifier from a headline."""
        # Try 《game_name》 first — most reliable
        m = _RE_GAME_NAMES.findall(headline)
        if m:
            return m[0].strip()
        # Fallback: first 15 chars after stripping source prefix
        clean = re.sub(r'\[B站[^\]]+\]', '', headline).strip()
        return clean[:20]

    def _headlines_overlap(h1: str, h2: str) -> bool:
        """Check if two headlines refer to the same topic."""
        k1 = _topic_key(h1)
        k2 = _topic_key(h2)
        if len(k1) >= 3 and len(k2) >= 3 and (k1 in k2 or k2 in k1):
            return True
        # Check significant word overlap (ignore common words)
        words1 = set(_RE_TOPIC_WORDS.findall(h1))
        words2 = set(_RE_TOPIC_WORDS.findall(h2))
        common = words1 & words2
        # Remove noise words
        noise = {"游戏", "资讯", "新闻", "今日", "最新", "推荐"}
        common -= noise
        return len(common) >= 4  # at least 4 meaningful words in common

    # ── Build "days seen" map for each past topic ──
    topic_days: dict[str, int] = {}
    for d, headlines in sorted(past_headlines.items()):
        for h in headlines:
            key = _topic_key(h)
            topic_days[key] = topic_days.get(key, 0) + 1

    # ── Apply fatigue to candidates ──
    result: list[dict[str, Any]] = []
    for c in candidates:
        headline = c.get("headline", "")
        key = _topic_key(headline)

        # Check if this topic appears in past headlines
        seen_days = 0
        for past_h in set().union(*past_headlines.values()):
            if _headlines_overlap(headline, past_h):
                seen_days = topic_days.get(_topic_key(past_h), 0)
                break

        if seen_days >= 2:
            continue  # blocked — seen 2+ days in window
        elif seen_days == 1:
            c = dict(c)
            c["fatigue"] = "downgraded"

        result.append(c)

    return result


# ═════════════════════════════════════════════════════════════
# Phase B: deep fetch article bodies
# ═════════════════════════════════════════════════════════════

def deep_fetch(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich candidate news items with article body text.

    Runs HTTP fetches sequentially to avoid hammering servers.
    B站 items are skipped (already have AI subtitle content).
    """
    from src.agents.enrichment import fetch_article_body

    enriched: list[dict[str, Any]] = []
    for item in candidates:
        # B站 items already have body from ai_subtitle; skip HTTP fetch
        if item.get("body"):
            enriched.append(dict(item))
            continue

        url = item.get("url", "")
        body = ""
        if url:
            body = fetch_article_body(url)
            if body:
                time.sleep(0.5)

        enriched.append({**item, "body": body})
    return enriched
