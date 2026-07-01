"""
Core TypedDict definitions for the competitive-intel pipeline.

These TypedDicts define the shape of data as it flows through the pipeline
stages.  They carry **zero runtime overhead** (they are plain dict annotations)
so existing `d["key"]` access patterns continue to work unchanged.

TypedDict inheritance mirrors the data-enrichment chain:
    RankingEntry  →  ChangeRecord
    RawNewsItem   →  EnrichedNewsItem  →  ScoredNewsItem

Composite types like BriefContext aggregate the leaf types to describe
cross-module contract points.

Usage:
    from src.types import RawNewsItem, ScoredNewsItem, BriefContext

    def filter_news(news: list[dict[str, Any]], ...) -> list[RawNewsItem]: ...
    def brief_from_db(date: str, ...) -> BriefContext: ...
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


# ═══════════════════════════════════════════════════════════════════
# Ranking / Change types (pipeline layer)
# ═══════════════════════════════════════════════════════════════════

class RankingEntry(TypedDict):
    """A single row in the `rankings` table (one game on one chart on one day).

    Produced by: Loader (CSV → rankings table)
    Consumed by:  Differ, Briefer (sector_changes)
    """

    date: str
    platform: str           # ios | android
    chart_type: str          # 热门榜 | 免费榜 | 畅销榜
    rank: int
    game_name: str
    bundle_id: str           # may be "0" when unavailable
    developer: str           # may be "0" when unavailable


class ChangeRecord(RankingEntry):
    """A single row in the `changes` table (Differ output).

    Produced by:  Differ (today vs yesterday comparison)
    Consumed by:   StoryPicker, Briefer (sector_changes, ranking table)
    """

    rank_change: int         # positive = climbed, negative = dropped
    prev_rank: int
    attention_score: float   # 0–10
    change_type: str         # up | down | new_entry | dropped_out | same
    day_type: str            # quiet | normal | volatile
    volatility: float        # 0.0–1.0 fraction of games that moved
    is_significant: NotRequired[bool]


# ═══════════════════════════════════════════════════════════════════
# News types (market_pipeline → enrichment → scorer)
# ═══════════════════════════════════════════════════════════════════

class RawNewsItem(TypedDict):
    """News candidate after hard-filter (Phase A).

    Produced by:  market_pipeline.filter_news()
    Consumed by:  market_pipeline.deep_fetch(), scorer (after enrichment)
    """

    url: str
    title: str
    source: str             # NewsSource constant (e.g. NewsSource.THREE_DM.value)
    publish_date: str       # YYYY-MM-DD
    snippet: str            # RSS / list-page blurb


class EnrichedNewsItem(RawNewsItem):
    """News candidate after deep-fetch (Phase B).

    Produced by:  market_pipeline.deep_fetch()
    Consumed by:  scorer.ai_summarize_and_judge()
    """

    body: NotRequired[str]           # extracted article body (first ~500 chars)
    body_length: NotRequired[int]    # character count of body
    og_image: NotRequired[str]       # og:image cover-art URL
    is_bilibili: NotRequired[bool]   # True → body was skipped (AI captions already available)


class ScoredNewsItem(EnrichedNewsItem):
    """News candidate after AI scoring (Phase C) + selection.

    Produced by:  scorer.ai_summarize_and_judge() + _select_top_n()
    Consumed by:  briefer (card assembly), render._build_market_elements()
    """

    ai_score: int                    # 0–100 LLM business-value score
    summary: str                     # 3–5 sentence AI summary
    pos_label: NotRequired[str]      # positive label from Matched Verdict Pool
    neg_label: NotRequired[str]      # negative label from Matched Verdict Pool
    signal_score: float              # 0–100 code-level signal score
    fused_score: float               # β-fusion: 0.3 × signal + 0.7 × AI
    verdict: NotRequired[str]        # AI reasoning trace (one-line rationale)
    track_relevant: NotRequired[bool]


# ═══════════════════════════════════════════════════════════════════
# New game types (scraper → briefer)
# ═══════════════════════════════════════════════════════════════════

class NewGameEntry(TypedDict):
    """A single new-game entry (TapTap new-games calendar or Steam port).

    Produced by:  taptap_new_games / steam_ports scrapers
    Consumed by:  render._build_new_games_md()
    """

    date: str
    game_name: str
    platform: str           # taptap | steam
    url: NotRequired[str]   # TapTap game page link
    tags: NotRequired[str]  # raw tags string (e.g. "塔防,肉鸽,二次元")
    track_relevant: NotRequired[bool]
    genre: NotRequired[str]
    developer: NotRequired[str]
    bundle_id: NotRequired[str]


# ═══════════════════════════════════════════════════════════════════
# Hot topic types (hot_tracker → render)
# ═══════════════════════════════════════════════════════════════════

class HotTopicItem(TypedDict):
    """A single hot-topic entry selected by the Hot Tracker Agent.

    Produced by:  hot_tracker._ai_filter_hot_topics()
    Consumed by:  render.build_hot_topic_elements()
    """

    keyword: str
    title: str
    url: str
    snippet: str
    ai_summary: NotRequired[str]     # Agent-generated summary (may be absent on fallback)
    value_score: NotRequired[int]    # 0–100 Agent business-value score
    source: NotRequired[str]


# ═══════════════════════════════════════════════════════════════════
# Composite / context types (briefer orchestration)
# ═══════════════════════════════════════════════════════════════════

class BriefContext(TypedDict):
    """Full daily-brief context produced by Briefer for card assembly.

    Produced by:  briefer.brief_from_db()
    Consumed by:  runner → feishu.pusher (card push)
    """

    date: str
    new_games: list[NewGameEntry]
    sector_changes: list[ChangeRecord]
    top_news: list[ScoredNewsItem]
    hot_topics: list[HotTopicItem]
    yesterday_new_games: NotRequired[list[str]]   # game names shown yesterday (for 🔴 badge)
    day_type: NotRequired[str]
    volatility: NotRequired[float]


# ═══════════════════════════════════════════════════════════════════
# Feedback / calibration types (bot → calibrator)
# ═══════════════════════════════════════════════════════════════════

class FeedbackRecord(TypedDict):
    """A single row in the `user_feedback` table.

    Produced by:  feishu.bot (feedback button callback)
    Consumed by:  calibrator (feedback analysis)
    """

    date: str
    feedback_type: str      # like | dislike | hot_click
    news_url: NotRequired[str]
    keyword: NotRequired[str]


# ═══════════════════════════════════════════════════════════════════
# Pipeline run stats (runner → audit / monitoring)
# ═══════════════════════════════════════════════════════════════════

class PipelineRunStats(TypedDict):
    """Statistics for a single pipeline run.

    Produced by:  runner.run_pipeline()
    Consumed by:  runner CLI, audit checks
    """

    date: str
    phases: dict[str, bool]              # phase_name → success
    phase_durations: dict[str, float]    # phase_name → seconds
    total_duration: float
    error_messages: list[str]
    scrape_results: NotRequired[dict[str, bool]]  # scraper_name → success


# ═══════════════════════════════════════════════════════════════════
# Bilibili-specific types
# ═══════════════════════════════════════════════════════════════════

class BilibiliVideo(TypedDict):
    """A single Bilibili video entry parsed by the B站 scraper.

    Produced by:  bilibili_creators scraper
    Consumed by:  briefer._bilibili_to_news(), render
    """

    bvid: str
    title: str
    description: str
    uploader: str
    date: str
    ai_caption: NotRequired[str]
    tags: NotRequired[str]
    play_count: NotRequired[int]
    like_count: NotRequired[int]
    comment_count: NotRequired[int]
    duration: NotRequired[int]
    cover_url: NotRequired[str]
