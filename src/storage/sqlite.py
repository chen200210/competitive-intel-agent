"""
SQLite database layer — schema creation and CRUD operations.

All structured data flows through this module.
One connection per thread; WAL mode for concurrent reads.

Schema v2: UNIQUE(date, platform, chart_type, bundle_id)
  - 同一天、同一平台、同一榜单类型、同一应用 只存一条
  - 热门榜 / 免费榜 / 畅销榜 / 新品榜 各自独立
"""

import sqlite3
import json
from pathlib import Path
from typing import Any

from src.config import settings

# ── DDL ────────────────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Daily ranking snapshots imported from CSV
-- UNIQUE: (date, platform, chart_type, bundle_id) — 每榜独立
CREATE TABLE IF NOT EXISTS rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    platform TEXT NOT NULL,           -- 'iOS' | 'Android'
    chart_type TEXT NOT NULL,         -- '热门榜' | '免费榜' | '畅销榜' | '新品榜' | '下载榜'
    category TEXT NOT NULL DEFAULT '',-- app category from source (e.g. '游戏', '应用')
    rank INTEGER NOT NULL,
    bundle_id TEXT NOT NULL,
    game_name TEXT NOT NULL,
    developer TEXT,                   -- NULL if "0" in source
    source_file TEXT,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, platform, chart_type, bundle_id)
);

-- Daily change records produced by Differ
-- UNIQUE: (date, platform, chart_type, bundle_id) — 每榜独立
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    platform TEXT NOT NULL,
    chart_type TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    game_name TEXT NOT NULL,
    developer TEXT,
    today_rank INTEGER,
    yesterday_rank INTEGER,
    rank_change INTEGER,             -- positive = up, negative = down, NULL for entry/drop
    change_type TEXT NOT NULL,        -- 'up' | 'down' | 'new_entry' | 'dropped_out'
    attention_score REAL DEFAULT 0.0,
    is_significant BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, platform, chart_type, bundle_id)
);

-- Daily analysis report (one per day)
CREATE TABLE IF NOT EXISTS analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    brief_card_json TEXT,             -- Briefer card JSON for Feishu
    new_games_md TEXT,                -- 新游关注 markdown
    market_md TEXT,                   -- 市场变动 markdown
    ranking_md TEXT,                  -- 排名变动 markdown
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Search query cache (keyed by query_hash = MD5(query + date))
CREATE TABLE IF NOT EXISTS search_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT NOT NULL UNIQUE,       -- MD5(query + date)
    query TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT 'bing',   -- 'bing' | 'ddg'
    results_json TEXT NOT NULL,            -- JSON array [{title, url, snippet}]
    result_count INTEGER DEFAULT 0,
    called_by TEXT,                        -- agent name
    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Web page fetch cache (keyed by url_hash = MD5(url))
CREATE TABLE IF NOT EXISTS fetch_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash TEXT NOT NULL UNIQUE,          -- MD5(url)
    url TEXT NOT NULL,
    title TEXT,
    text TEXT,                              -- extracted body (up to 5000 chars)
    text_length INTEGER,                    -- original full length
    status_code INTEGER,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Agent tool-call audit log (one row per tool invocation)
CREATE TABLE IF NOT EXISTS agent_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,               -- 'overview_scanner' | 'researcher' | ...
    run_id TEXT NOT NULL,                   -- UUID shared by all calls in one agent run
    target_date TEXT,                       -- report date (e.g. '2026-06-16')
    round_num INTEGER,                      -- which tool-call round in the agent loop
    tool_name TEXT NOT NULL,                -- 'web_search' | 'web_fetch' | 'db_query'
    tool_args_json TEXT,                    -- tool call arguments
    tool_result_preview TEXT,              -- first 2000 chars of result
    tool_result_length INTEGER,            -- full result length
    cache_hit BOOLEAN,                      -- did this hit cache? (NULL for non-cache tools)
    latency_ms INTEGER,                     -- tool execution time in ms
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_run_id ON agent_audit_log(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_agent_date ON agent_audit_log(agent_name, target_date);

-- TapTap daily new games (scraped from TapTap 今日新游)
CREATE TABLE IF NOT EXISTS taptap_new_games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    game_name TEXT NOT NULL,
    bundle_id TEXT,
    downloads TEXT,
    rating REAL,
    tags TEXT,                   -- JSON array of tag strings
    genre TEXT,
    description TEXT,
    taptap_url TEXT,
    track_relevant BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, game_name)
);

-- Steam-to-mobile port tracking
CREATE TABLE IF NOT EXISTS steam_port_games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    game_name TEXT NOT NULL,
    steam_url TEXT,
    mobile_bundle_id TEXT,
    gameplay_tags TEXT,          -- JSON array of gameplay tag strings
    genre TEXT,
    has_mobile_version BOOLEAN DEFAULT 0,
    track_relevant BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, game_name)
);

-- Market news headlines (GamerSky / 17173 / etc.)
CREATE TABLE IF NOT EXISTS market_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    headline TEXT NOT NULL,
    source TEXT NOT NULL,        -- '游侠资讯' | '17173' | '其他'
    url TEXT,
    category TEXT,               -- '头条' | '赛道' | '老游戏更新'
    related_game TEXT,
    track_relevant BOOLEAN DEFAULT 0,
    publish_date TEXT,            -- actual article publish date (YYYY-MM-DD), empty if unknown
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, url)
);

-- User feedback on daily report quality (card button clicks)
CREATE TABLE IF NOT EXISTS user_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,                    -- feedback submission date (YYYY-MM-DD)
    target_date TEXT NOT NULL,            -- which report date the feedback is about
    feedback_type TEXT NOT NULL,          -- 'thumbs_up' | 'thumbs_down'
    chat_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Generic dedup tracker — prevents re-reporting across daily runs.
-- Used by bilibili_creators (BVIDs) and briefer (TapTap game names).
CREATE TABLE IF NOT EXISTS reported_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT NOT NULL,         -- BVID or game_name
    item_type TEXT NOT NULL,        -- 'bilibili' or 'taptap'
    reported_date TEXT NOT NULL,    -- YYYY-MM-DD when first reported
    meta TEXT DEFAULT '',           -- optional JSON: creator, title, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(item_key, item_type)
);

-- Bilibili creator video monitor output
CREATE TABLE IF NOT EXISTS bilibili_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,             -- scrape date (YYYY-MM-DD)
    creator_uid TEXT NOT NULL,
    creator_label TEXT NOT NULL,
    bvid TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    url TEXT,
    cover TEXT,
    play_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    video_review INTEGER DEFAULT 0, -- danmu count
    like_count INTEGER DEFAULT 0,
    favorite_count INTEGER DEFAULT 0,
    coin_count INTEGER DEFAULT 0,
    share_count INTEGER DEFAULT 0,
    duration TEXT,
    category TEXT,
    tags TEXT,
    ai_subtitle TEXT,
    created_at_ts INTEGER DEFAULT 0, -- video publish timestamp
    created_at TEXT,                 -- video publish time (YYYY-MM-DD HH:MM)
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(bvid, date)               -- same video can appear once per scrape date
);
CREATE INDEX IF NOT EXISTS idx_taptap_new_date ON taptap_new_games(date);
CREATE INDEX IF NOT EXISTS idx_steam_port_date ON steam_port_games(date);
CREATE INDEX IF NOT EXISTS idx_market_news_date ON market_news(date);
CREATE INDEX IF NOT EXISTS idx_market_news_source ON market_news(source);
CREATE INDEX IF NOT EXISTS idx_market_news_url ON market_news(url);

-- run_id column for traceability (added via migration if missing)
-- analysis_reports gets run_id

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_rankings_date ON rankings(date);
CREATE INDEX IF NOT EXISTS idx_rankings_bundle_id ON rankings(bundle_id);
CREATE INDEX IF NOT EXISTS idx_rankings_date_platform ON rankings(date, platform);
CREATE INDEX IF NOT EXISTS idx_rankings_date_platform_chart ON rankings(date, platform, chart_type);
CREATE INDEX IF NOT EXISTS idx_changes_date ON changes(date);
CREATE INDEX IF NOT EXISTS idx_changes_type ON changes(change_type);
CREATE INDEX IF NOT EXISTS idx_changes_attention ON changes(attention_score DESC);
CREATE INDEX IF NOT EXISTS idx_changes_date_platform_chart ON changes(date, platform, chart_type);

-- Pipeline run tracking (one row per invocation)
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    phases_json TEXT DEFAULT '[]',
    exit_code INTEGER DEFAULT 0,
    error_summary TEXT DEFAULT '',
    total_ms INTEGER DEFAULT 0
);

-- Daily hot keywords (Phase 0.5: collected from baidu/zhihu/curated)
CREATE TABLE IF NOT EXISTS hot_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    keyword TEXT NOT NULL,
    source TEXT NOT NULL,          -- 'baidu' | 'zhihu' | 'curated'
    rank INTEGER,                  -- position in source (smaller = hotter)
    weight REAL DEFAULT 1.0,       -- dynamic weight (feedback loop adjusts)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, keyword)
);

-- Hot topic news search results (Phase 1.5: DDG-first search per keyword)
CREATE TABLE IF NOT EXISTS hot_topic_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    keyword TEXT NOT NULL,
    headline TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',   -- domain name, e.g. '36kr.com'
    snippet TEXT DEFAULT '',
    search_engine TEXT DEFAULT '',     -- 'ddg' | '360' | 'sogou' | 'bing'
    selected BOOLEAN DEFAULT 0,       -- whether selected for daily card
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, url)
);
CREATE INDEX IF NOT EXISTS idx_hot_keywords_date ON hot_keywords(date);
CREATE INDEX IF NOT EXISTS idx_hot_topic_news_date ON hot_topic_news(date);
CREATE INDEX IF NOT EXISTS idx_hot_topic_news_selected ON hot_topic_news(date, selected);

-- Feedback queries always filter on date or target_date (daily + weekly paths)
CREATE INDEX IF NOT EXISTS idx_user_feedback_date ON user_feedback(date);
CREATE INDEX IF NOT EXISTS idx_user_feedback_target_date ON user_feedback(target_date);

-- Calibration parameters (versioned, produced by Calibrator agent)
CREATE TABLE IF NOT EXISTS calibration_params (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL UNIQUE,
    topic_boosts_json TEXT DEFAULT '{}',   -- {"独立游戏": 5, "二次元": -10}
    dim_weights_json TEXT DEFAULT '{}',    -- {"track": 40, "density": 40, "insight": 20}
    findings_json TEXT DEFAULT '[]',       -- [{"pattern": "...", "evidence_count": 5, ...}]
    summary TEXT DEFAULT '',
    feedback_start_date TEXT NOT NULL,     -- e.g. '2026-06-11'
    feedback_end_date TEXT NOT NULL,       -- e.g. '2026-06-25'
    total_feedback_count INTEGER DEFAULT 0,
    applied BOOLEAN DEFAULT 0,            -- whether scorer has picked up this version yet
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    """SQLite database manager with connection pooling per thread."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.sqlite_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate_v2()
        self._migrate_v3()
        self._migrate_v4()
        self._migrate_v5()
        self._migrate_v6()
        self._migrate_v7()
        self._migrate_v8()
        self._migrate_v9()
        self._migrate_v10()
        self._migrate_v11()

    # ── Connection ──────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── Schema ──────────────────────────────────────────────

    def _init_schema(self) -> None:
        """Create all tables if they don't exist."""
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def _migrate_v2(self) -> None:
        """
        Migrate from v1 schema (UNIQUE: date+platform+bundle_id, no chart_type)
        to v2 schema (UNIQUE: date+platform+chart_type+bundle_id, with chart_type).

        Safe to run on new databases — detects and skips if already migrated.
        """
        with self._connect() as conn:
            # Check if chart_type column exists in rankings
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(rankings)").fetchall()]

            if "chart_type" in cols:
                return  # Already migrated

            print("[migrate] Upgrading schema from v1 → v2 (adding chart_type)...")

            # ── Migrate rankings ──
            # 1. Create new table with correct schema
            conn.execute("""
                CREATE TABLE rankings_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    chart_type TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    rank INTEGER NOT NULL,
                    bundle_id TEXT NOT NULL,
                    game_name TEXT NOT NULL,
                    developer TEXT,
                    source_file TEXT,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, platform, chart_type, bundle_id)
                )
            """)

            # 2. Copy old data → new table (old 'category' values become 'chart_type')
            conn.execute("""
                INSERT INTO rankings_v2
                    (id, date, platform, chart_type, category, rank, bundle_id,
                     game_name, developer, source_file, imported_at)
                SELECT id, date, platform,
                       COALESCE(NULLIF(category, ''), '热门榜'),
                       '', rank, bundle_id, game_name, developer, source_file, imported_at
                FROM rankings
            """)

            # 3. Swap tables
            conn.execute("DROP TABLE rankings")
            conn.execute("ALTER TABLE rankings_v2 RENAME TO rankings")

            # ── Migrate changes ──
            # Check if changes table has chart_type
            changes_cols = [r["name"] for r in conn.execute("PRAGMA table_info(changes)").fetchall()]
            if "chart_type" not in changes_cols:
                conn.execute("""
                    CREATE TABLE changes_v2 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT NOT NULL,
                        platform TEXT NOT NULL,
                        chart_type TEXT NOT NULL,
                        bundle_id TEXT NOT NULL,
                        game_name TEXT NOT NULL,
                        developer TEXT,
                        today_rank INTEGER,
                        yesterday_rank INTEGER,
                        rank_change INTEGER,
                        change_type TEXT NOT NULL,
                        attention_score REAL DEFAULT 0.0,
                        is_significant BOOLEAN DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(date, platform, chart_type, bundle_id)
                    )
                """)

                conn.execute("""
                    INSERT INTO changes_v2
                        (id, date, platform, chart_type, bundle_id, game_name, developer,
                         today_rank, yesterday_rank, rank_change, change_type,
                         attention_score, is_significant, created_at)
                    SELECT id, date, platform, '热门榜', bundle_id, game_name, developer,
                           today_rank, yesterday_rank, rank_change, change_type,
                           attention_score, is_significant, created_at
                    FROM changes
                """)

                conn.execute("DROP TABLE changes")
                conn.execute("ALTER TABLE changes_v2 RENAME TO changes")

            # ── Rebuild indexes ──
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_date ON rankings(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_bundle_id ON rankings(bundle_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_date_platform ON rankings(date, platform)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rankings_date_platform_chart ON rankings(date, platform, chart_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_date ON changes(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_type ON changes(change_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_attention ON changes(attention_score DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_date_platform_chart ON changes(date, platform, chart_type)")

            print("[migrate] Schema v2 migration complete.")

    def _migrate_v3(self) -> None:
        """Add run_id column to analysis_reports (legacy migration)."""
        with self._connect() as conn:
            cols = [r["name"] for r in conn.execute(
                "PRAGMA table_info(analysis_reports)").fetchall()]
            if "run_id" not in cols:
                conn.execute("ALTER TABLE analysis_reports ADD COLUMN run_id TEXT")
                print("[migrate] Added run_id to analysis_reports")

    def _migrate_v4(self) -> None:
        """Add publish_date column to market_news for 7-day freshness filter."""
        with self._connect() as conn:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(market_news)").fetchall()]
            if "publish_date" not in cols:
                conn.execute("ALTER TABLE market_news ADD COLUMN publish_date TEXT")
                print("[migrate] Added publish_date to market_news")

    def _migrate_v5(self) -> None:
        """Add per-section markdown columns to analysis_reports."""
        with self._connect() as conn:
            for col in ["new_games_md", "market_md", "ranking_md"]:
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(analysis_reports)").fetchall()]
                if col not in cols:
                    conn.execute(f"ALTER TABLE analysis_reports ADD COLUMN {col} TEXT")
                    print(f"[migrate] Added {col} to analysis_reports")

    def _migrate_v6(self) -> None:
        """Add feedback counter columns to market_news + news_url to user_feedback."""
        with self._connect() as conn:
            # market_news: per-news feedback counters
            news_cols = [r["name"] for r in conn.execute("PRAGMA table_info(market_news)").fetchall()]
            for col in ["useful_count", "useless_count"]:
                if col not in news_cols:
                    conn.execute(f"ALTER TABLE market_news ADD COLUMN {col} INTEGER DEFAULT 0")
                    print(f"[migrate] Added {col} to market_news")

            # user_feedback: optional per-news URL for news-level feedback
            fb_cols = [r["name"] for r in conn.execute("PRAGMA table_info(user_feedback)").fetchall()]
            if "news_url" not in fb_cols:
                conn.execute("ALTER TABLE user_feedback ADD COLUMN news_url TEXT DEFAULT ''")
                print("[migrate] Added news_url to user_feedback")

    def _migrate_v7(self) -> None:
        """Add open_id to user_feedback for per-user dedup + unique constraint."""
        with self._connect() as conn:
            fb_cols = [r["name"] for r in conn.execute("PRAGMA table_info(user_feedback)").fetchall()]
            if "open_id" not in fb_cols:
                conn.execute("ALTER TABLE user_feedback ADD COLUMN open_id TEXT DEFAULT ''")
                print("[migrate] Added open_id to user_feedback")
            # Unique constraint: one feedback per user per news URL
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_user_feedback_dedup
                ON user_feedback(news_url, open_id)
                WHERE news_url != '' AND open_id != ''
            """)

    def _migrate_v8(self) -> None:
        """Add hot_topics_json to analysis_reports + keyword to user_feedback."""
        with self._connect() as conn:
            # analysis_reports: store hot topics section data for audit
            ar_cols = [r["name"] for r in conn.execute("PRAGMA table_info(analysis_reports)").fetchall()]
            if "hot_topics_json" not in ar_cols:
                conn.execute("ALTER TABLE analysis_reports ADD COLUMN hot_topics_json TEXT DEFAULT ''")
                print("[migrate] Added hot_topics_json to analysis_reports")

            # user_feedback: keyword column for hot_click feedback loop
            fb_cols = [r["name"] for r in conn.execute("PRAGMA table_info(user_feedback)").fetchall()]
            if "keyword" not in fb_cols:
                conn.execute("ALTER TABLE user_feedback ADD COLUMN keyword TEXT DEFAULT ''")
                print("[migrate] Added keyword to user_feedback")

    def _migrate_v9(self) -> None:
        """Rebuild user_feedback dedup index to include feedback_type.

        The old index on (news_url, open_id) prevented a user from having
        both a thumbs_up AND a hot_click on the same URL. Adding feedback_type
        to the index fixes this — each (news_url, open_id, feedback_type) tuple
        is now independently unique.
        """
        with self._connect() as conn:
            # Check if the old 2-column index exists
            rows = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_user_feedback_dedup'"
            ).fetchall()
            if rows:
                old_sql = rows[0]["sql"] or ""
                # Only rebuild if feedback_type is not already in the index
                if "feedback_type" not in old_sql:
                    conn.execute("DROP INDEX IF EXISTS idx_user_feedback_dedup")
                    conn.execute("""
                        CREATE UNIQUE INDEX idx_user_feedback_dedup
                        ON user_feedback(news_url, open_id, feedback_type)
                        WHERE news_url != '' AND open_id != ''
                    """)
                    print("[migrate] Rebuilt idx_user_feedback_dedup with feedback_type")

    def _migrate_v10(self) -> None:
        """Add calibration_params table for Calibrator agent output.

        Versioned parameter sets allow rollback if miscalibration is detected.
        The table DDL is in SCHEMA_SQL — this migration is a no-op that
        exists for the init call chain.  The IF NOT EXISTS in SCHEMA_SQL
        handles the actual creation.
        """
        pass

    def _migrate_v11(self) -> None:
        """Add pos_label / neg_label columns to market_news for P2 Matched Verdict Pool.

        These are populated by scorer.py after AI summarization and consumed
        by the Calibrator agent for pos_label × feedback statistical analysis.
        """
        with self._connect() as conn:
            existing = {row[1] for row in conn.execute("PRAGMA table_info('market_news')").fetchall()}
            if "pos_label" not in existing:
                conn.execute("ALTER TABLE market_news ADD COLUMN pos_label TEXT DEFAULT ''")
                print("[migrate] Added pos_label column to market_news")
            if "neg_label" not in existing:
                conn.execute("ALTER TABLE market_news ADD COLUMN neg_label TEXT DEFAULT ''")
                print("[migrate] Added neg_label column to market_news")

    # ── Calibration CRUD ─────────────────────────────────────

    def get_next_calibration_version(self) -> int:
        """Return the next calibration version number."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM calibration_params"
            ).fetchone()
            return row[0]

    def insert_calibration_params(
        self,
        topic_boosts: dict[str, int],
        dim_weights: dict[str, int],
        findings: list[dict[str, Any]],
        summary: str,
        feedback_start_date: str,
        feedback_end_date: str,
        total_feedback_count: int = 0,
    ) -> int:
        """Insert a new calibration parameter version. Returns version number.

        The SELECT MAX(version)+1 and INSERT run in the same transaction
        so concurrent calibrator invocations cannot produce duplicate versions.
        """
        import json as _json
        sql = """
            INSERT INTO calibration_params
                (version, topic_boosts_json, dim_weights_json, findings_json,
                 summary, feedback_start_date, feedback_end_date,
                 total_feedback_count, applied)
            VALUES (
                (SELECT COALESCE(MAX(version), 0) + 1 FROM calibration_params),
                ?, ?, ?, ?, ?, ?, ?, 0
            )
        """
        with self._connect() as conn:
            conn.execute(sql, (
                _json.dumps(topic_boosts, ensure_ascii=False),
                _json.dumps(dim_weights, ensure_ascii=False),
                _json.dumps(findings, ensure_ascii=False),
                summary,
                feedback_start_date,
                feedback_end_date,
                total_feedback_count,
            ))
            # Read back the version that was just assigned
            row = conn.execute(
                "SELECT version FROM calibration_params WHERE id = last_insert_rowid()"
            ).fetchone()
            return row["version"] if row else 0

    def get_latest_calibration_params(self) -> dict[str, Any] | None:
        """Return the most recent calibration params, or None if none exist."""
        import json as _json
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM calibration_params ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return {
                "version": row["version"],
                "topic_boosts": _json.loads(row["topic_boosts_json"] or "{}"),
                "dim_weights": _json.loads(row["dim_weights_json"] or "{}"),
                "findings": _json.loads(row["findings_json"] or "[]"),
                "summary": row["summary"],
                "feedback_start_date": row["feedback_start_date"],
                "feedback_end_date": row["feedback_end_date"],
                "total_feedback_count": row["total_feedback_count"],
                "applied": bool(row["applied"]),
            }

    def mark_calibration_applied(self, version: int) -> None:
        """Mark a calibration version as picked up by the scorer."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE calibration_params SET applied = 1 WHERE version = ?",
                (version,),
            )

    # ── Rankings CRUD ───────────────────────────────────────

    def insert_rankings(self, records: list[dict[str, Any]]) -> int:
        """Bulk insert ranking records. Uses INSERT OR REPLACE for idempotency."""
        sql = """
            INSERT OR REPLACE INTO rankings
                (date, platform, chart_type, category, rank, bundle_id, game_name, developer, source_file)
            VALUES (:date, :platform, :chart_type, :category, :rank, :bundle_id, :game_name, :developer, :source_file)
        """
        with self._connect() as conn:
            conn.executemany(sql, records)
        return len(records)

    def get_rankings_by_date(
        self, date: str, platform: str | None = None, chart_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all rankings for a given date, optionally filtered by platform / chart_type."""
        conditions = ["date = ?"]
        params: list[Any] = [date]
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if chart_type:
            conditions.append("chart_type = ?")
            params.append(chart_type)
        sql = f"SELECT * FROM rankings WHERE {' AND '.join(conditions)} ORDER BY chart_type, rank"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_available_dates(self) -> list[str]:
        """Return all dates that have ranking data, sorted descending."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM rankings ORDER BY date DESC"
            ).fetchall()
        return [r["date"] for r in rows]

    def get_available_chart_types(self, date: str, platform: str | None = None) -> list[str]:
        """Return all chart_types present for a given date."""
        if platform:
            sql = "SELECT DISTINCT chart_type FROM rankings WHERE date = ? AND platform = ? ORDER BY chart_type"
            params = (date, platform)
        else:
            sql = "SELECT DISTINCT chart_type FROM rankings WHERE date = ? ORDER BY chart_type"
            params = (date,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [r["chart_type"] for r in rows]

    def get_previous_date(self, date: str) -> str | None:
        """Return the most recent date before `date` that has data."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM rankings WHERE date < ?", (date,)
            ).fetchone()
        return row[0] if row and row[0] else None

    # ── Changes CRUD ────────────────────────────────────────

    def insert_changes(self, records: list[dict[str, Any]]) -> int:
        """Bulk insert change records."""
        sql = """
            INSERT OR REPLACE INTO changes
                (date, platform, chart_type, bundle_id, game_name, developer,
                 today_rank, yesterday_rank, rank_change, change_type,
                 attention_score, is_significant)
            VALUES (:date, :platform, :chart_type, :bundle_id, :game_name, :developer,
                    :today_rank, :yesterday_rank, :rank_change, :change_type,
                    :attention_score, :is_significant)
        """
        with self._connect() as conn:
            conn.executemany(sql, records)
        return len(records)

    def get_changes_by_date(
        self, date: str, significant_only: bool = False,
        chart_type: str | None = None, platform: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return all changes for a given date, with optional filters."""
        conditions = ["date = ?"]
        params: list[Any] = [date]
        if significant_only:
            conditions.append("is_significant = 1")
        if chart_type:
            conditions.append("chart_type = ?")
            params.append(chart_type)
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        sql = f"SELECT * FROM changes WHERE {' AND '.join(conditions)} ORDER BY attention_score DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_game_history(
        self, bundle_id: str, platform: str | None = None,
        chart_type: str | None = None, days: int = 30,
    ) -> list[dict[str, Any]]:
        """Return recent ranking history for a specific game."""
        conditions = ["bundle_id = ?"]
        params: list[Any] = [bundle_id]
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if chart_type:
            conditions.append("chart_type = ?")
            params.append(chart_type)
        sql = f"SELECT date, rank FROM rankings WHERE {' AND '.join(conditions)} ORDER BY date DESC LIMIT ?"
        params.append(days)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows][::-1]  # chronological order

    # ── Analysis CRUD ───────────────────────────────────────

    def upsert_analysis_report(self, date: str, brief_card_json: str = "",
                                new_games_md: str = "", market_md: str = "",
                                ranking_md: str = "", hot_topics_json: str = "") -> None:
        sql = """
            INSERT OR REPLACE INTO analysis_reports
                (date, brief_card_json, new_games_md, market_md, ranking_md, hot_topics_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (date, brief_card_json,
                              new_games_md, market_md, ranking_md, hot_topics_json))

    def get_analysis_report(self, date: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_reports WHERE date = ?", (date,)
            ).fetchone()
        return dict(row) if row else None

    # ── Search Cache CRUD ─────────────────────────────────────

    def get_cached_search(self, query_hash: str, max_age_hours: int = 24) -> list[dict[str, Any]] | None:
        """Return cached search results if fresh enough, else None."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM search_cache
                   WHERE query_hash = ?
                     AND datetime(searched_at) > datetime('now', ? || ' hours')
                   ORDER BY searched_at DESC LIMIT 1""",
                (query_hash, f"-{max_age_hours}"),
            ).fetchone()
        if row is None:
            return None
        import json
        return json.loads(row["results_json"])

    def cache_search(self, query_hash: str, query: str, engine: str,
                     results_json: str, result_count: int, called_by: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO search_cache
                   (query_hash, query, engine, results_json, result_count, called_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (query_hash, query, engine, results_json, result_count, called_by),
            )

    # ── Fetch Cache CRUD ─────────────────────────────────────

    def get_cached_fetch(self, url_hash: str, max_age_days: int = 7) -> dict[str, Any] | None:
        """Return cached page fetch if fresh enough, else None."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM fetch_cache
                   WHERE url_hash = ?
                     AND datetime(fetched_at) > datetime('now', ? || ' days')
                   ORDER BY fetched_at DESC LIMIT 1""",
                (url_hash, f"-{max_age_days}"),
            ).fetchone()
        return dict(row) if row else None

    def cache_fetch(self, url_hash: str, url: str, title: str, text: str,
                    text_length: int, status_code: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO fetch_cache
                   (url_hash, url, title, text, text_length, status_code)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (url_hash, url, title, text, text_length, status_code),
            )

    # ── Audit Log CRUD ───────────────────────────────────────

    def insert_audit_log(self, agent_name: str, run_id: str, target_date: str,
                         round_num: int, tool_name: str, tool_args_json: str,
                         tool_result_preview: str, tool_result_length: int,
                         cache_hit: bool | None, latency_ms: int) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO agent_audit_log
                   (agent_name, run_id, target_date, round_num, tool_name,
                    tool_args_json, tool_result_preview, tool_result_length,
                    cache_hit, latency_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (agent_name, run_id, target_date, round_num, tool_name,
                 tool_args_json, tool_result_preview, tool_result_length,
                 cache_hit, latency_ms),
            )
            return cur.lastrowid

    def get_audit_logs(self, run_id: str) -> list[dict[str, Any]]:
        """Return all audit logs for a given run_id, ordered by id."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_audit_log WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── TapTap New Games CRUD ─────────────────────────────────

    def insert_taptap_games(self, records: list[dict[str, Any]]) -> int:
        """Bulk insert TapTap new game records."""
        sql = """
            INSERT OR REPLACE INTO taptap_new_games
                (date, game_name, bundle_id, downloads, rating, tags, genre, description, taptap_url, track_relevant)
            VALUES (:date, :game_name, :bundle_id, :downloads, :rating, :tags, :genre, :description, :taptap_url, :track_relevant)
        """
        with self._connect() as conn:
            conn.executemany(sql, records)
        return len(records)

    def get_taptap_games_by_date(self, date: str) -> list[dict[str, Any]]:
        """Return TapTap new games for a given date."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM taptap_new_games WHERE date = ? ORDER BY id",
                (date,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Steam Port Games CRUD ─────────────────────────────────

    def insert_steam_ports(self, records: list[dict[str, Any]]) -> int:
        """Bulk insert Steam port game records."""
        sql = """
            INSERT OR REPLACE INTO steam_port_games
                (date, game_name, steam_url, mobile_bundle_id, gameplay_tags, genre, has_mobile_version, track_relevant)
            VALUES (:date, :game_name, :steam_url, :mobile_bundle_id, :gameplay_tags, :genre, :has_mobile_version, :track_relevant)
        """
        with self._connect() as conn:
            conn.executemany(sql, records)
        return len(records)

    def get_steam_ports_by_date(self, date: str) -> list[dict[str, Any]]:
        """Return Steam port games for a given date."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM steam_port_games WHERE date = ? ORDER BY id",
                (date,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Market News CRUD ──────────────────────────────────────

    def insert_market_news(self, records: list[dict[str, Any]]) -> int:
        """Bulk insert market news records."""
        sql = """
            INSERT OR REPLACE INTO market_news
                (date, headline, source, url, category, related_game, track_relevant, publish_date)
            VALUES (:date, :headline, :source, :url, :category, :related_game, :track_relevant, :publish_date)
        """
        with self._connect() as conn:
            conn.executemany(sql, records)
        return len(records)

    def insert_market_news_deduped(
        self, records: list[dict[str, Any]], date: str,
    ) -> int:
        """Insert market_news records, skipping URLs already seen on the same date.

        Same-day dedup only — cross-day dedup is handled by reported_items (TTL-based).
        Callers are responsible for mapping their data into standard record dicts:
          {date, headline, source, url, category, related_game, track_relevant, publish_date}

        Returns the number of records actually inserted.
        """
        if not records:
            return 0

        # ── Load existing URLs for this date only ──
        # Cross-day dedup is handled by reported_items (TTL-based), not here.
        existing_urls: set[str] = set()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT url FROM market_news WHERE date = ? AND url IS NOT NULL",
                    (date,),
                ).fetchall()
            existing_urls = {r["url"] for r in rows if r["url"]}
        except Exception as e:
            import sys
            print(f"  [WARN] Failed to load existing URLs for dedup: {e}", file=sys.stderr)

        # ── Filter out duplicates ──
        new_records: list[dict[str, Any]] = []
        skipped = 0
        for rec in records:
            url = rec.get("url", "")
            if not url or url in existing_urls:
                skipped += 1
                continue
            # Ensure date is set
            if "date" not in rec or not rec["date"]:
                rec["date"] = date
            new_records.append(rec)

        # ── Insert ──
        if new_records:
            self.insert_market_news(new_records)
            print(f"  [news] Synced {len(new_records)} new items to market_news"
                  f" ({skipped} duplicates skipped)")
        elif skipped:
            print(f"  [news] All {skipped} items already in market_news — nothing new")

        return len(new_records)

    def get_market_news_by_date(
        self, date: str, source: str | None = None, track_relevant: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Return market news for a given date, optionally filtered."""
        conditions = ["date = ?"]
        params: list[Any] = [date]
        if source:
            conditions.append("source = ?")
            params.append(source)
        if track_relevant is not None:
            conditions.append("track_relevant = ?")
            params.append(1 if track_relevant else 0)
        sql = f"SELECT * FROM market_news WHERE {' AND '.join(conditions)} ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update_market_news_labels(
        self, date: str, url_to_labels: dict[str, tuple[str, str]]
    ) -> int:
        """Persist AI-annotated pos_label / neg_label to market_news rows.

        Args:
            date: Report date (YYYY-MM-DD).
            url_to_labels: Dict mapping URL → (pos_label, neg_label).
        Returns:
            Number of rows updated.
        """
        if not url_to_labels:
            return 0
        sql = """
            UPDATE market_news SET pos_label = ?, neg_label = ?
            WHERE date = ? AND url = ?
        """
        count = 0
        with self._connect() as conn:
            for url, (pos, neg) in url_to_labels.items():
                cur = conn.execute(sql, (pos or "", neg or "", date, url))
                count += cur.rowcount
        return count

    # ── Bilibili videos ──────────────────────────────────────

    def insert_bilibili_videos(self, records: list[dict[str, Any]]) -> int:
        """Insert or replace bilibili videos for a scrape date.

        Each record should have: date, creator_uid, creator_label, bvid,
        title, description, url, cover, play_count, comment_count,
        video_review, like_count, favorite_count, coin_count, share_count,
        duration, category, tags, ai_subtitle, created_at_ts, created_at.
        """
        sql = """INSERT OR REPLACE INTO bilibili_videos
                 (date, creator_uid, creator_label, bvid, title, description,
                  url, cover, play_count, comment_count, video_review,
                  like_count, favorite_count, coin_count, share_count,
                  duration, category, tags, ai_subtitle,
                  created_at_ts, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        count = 0
        with self._connect() as conn:
            for r in records:
                conn.execute(sql, (
                    r.get("date", ""),
                    r.get("creator_uid", ""),
                    r.get("creator_label", ""),
                    r.get("bvid", ""),
                    r.get("title", ""),
                    r.get("description", ""),
                    r.get("url", ""),
                    r.get("cover", ""),
                    int(r.get("play_count", 0)),
                    int(r.get("comment_count", 0)),
                    int(r.get("video_review", 0)),
                    int(r.get("like_count", 0)),
                    int(r.get("favorite_count", 0)),
                    int(r.get("coin_count", 0)),
                    int(r.get("share_count", 0)),
                    r.get("duration", ""),
                    r.get("category", ""),
                    r.get("tags", ""),
                    r.get("ai_subtitle", ""),
                    int(r.get("created_ts", 0)),
                    r.get("created_at", ""),
                ))
                count += 1
        return count

    def get_bilibili_videos_by_date(
        self, date: str | None = None, creator_label: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return bilibili videos, optionally filtered by date and/or creator."""
        conditions: list[str] = []
        params: list[Any] = []
        if date:
            conditions.append("date = ?")
            params.append(date)
        if creator_label:
            conditions.append("creator_label = ?")
            params.append(creator_label)
        sql = "SELECT * FROM bilibili_videos"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at_ts DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Reported items (dedup) ───────────────────────────────

    def get_reported_keys(self, item_type: str) -> set[str]:
        """Return all reported item_keys of a given type (e.g. 'bilibili', 'taptap')."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT item_key FROM reported_items WHERE item_type = ?",
                (item_type,)
            ).fetchall()
        return {r["item_key"] for r in rows}

    def mark_reported(
        self, keys: set[str], item_type: str, date: str, meta: str = ""
    ) -> int:
        """Mark items as reported. Uses REPLACE to update reported_date on re-runs."""
        count = 0
        with self._connect() as conn:
            for key in keys:
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO reported_items
                           (item_key, item_type, reported_date, meta)
                           VALUES (?, ?, ?, ?)""",
                        (key, item_type, date, meta)
                    )
                    count += 1
                except Exception as e:
                    print(f"  [WARN] mark_reported failed for {item_type}:{key}: {e}", file=sys.stderr)
        return count

    def prune_reported(self, item_type: str, max_age_days: int = 30) -> int:
        """Delete reported items older than max_age_days. Returns count removed."""
        with self._connect() as conn:
            cur = conn.execute(
                """DELETE FROM reported_items
                   WHERE item_type = ?
                     AND datetime(reported_date) < datetime('now', ? || ' days')""",
                (item_type, f"-{max_age_days}")
            )
            return cur.rowcount

    # ── User Feedback ───────────────────────────────────────

    def get_feedback_stats(
        self, target_date: str | None = None, days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return feedback counts grouped by target_date and type."""
        if target_date:
            sql = """SELECT target_date, feedback_type, COUNT(*) as cnt
                     FROM user_feedback WHERE target_date = ?
                     GROUP BY target_date, feedback_type ORDER BY target_date DESC"""
            params = (target_date,)
        else:
            sql = """SELECT target_date, feedback_type, COUNT(*) as cnt
                     FROM user_feedback
                     WHERE target_date >= date('now', ? || ' days')
                     GROUP BY target_date, feedback_type ORDER BY target_date DESC"""
            params = (f"-{days}",)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def increment_news_feedback(
        self, url: str, date: str, feedback_type: str,
        chat_id: str = "", open_id: str = "",
    ) -> str:
        """Record user feedback on a news item.

        Writes to user_feedback (source of truth) with atomic dedup via
        INSERT OR IGNORE + UNIQUE constraint on (news_url, open_id).
        Updates market_news counters as a best-effort optimization
        (may fail if market_news was cleaned for a re-run).

        Returns: 'inserted' | 'duplicate' | 'error'
        """
        col = "useful_count" if feedback_type == "thumbs_up" else "useless_count"

        with self._connect() as conn:
            # ── Best-effort: update market_news counter ──
            try:
                conn.execute(
                    f"UPDATE market_news SET {col} = {col} + 1 WHERE url = ? AND date = ?",
                    (url, date),
                )
            except Exception:
                pass  # market_news row might not exist (post-cleanup re-run)

            # ── Atomic insert with dedup (UNIQUE constraint on news_url+open_id) ──
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO user_feedback
                       (date, target_date, feedback_type, chat_id, news_url, open_id)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (date, date, feedback_type, chat_id, url, open_id),
                )
                if cur.rowcount == 0:
                    return "duplicate"
                return "inserted"
            except Exception:
                return "error"

    # ── Utility ─────────────────────────────────────────────

    def table_has_data(self, table: str) -> bool:
        """Check if a table has any rows."""
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        return row["cnt"] > 0

    # ── Hot Keywords CRUD ────────────────────────────────────

    def insert_hot_keywords(self, records: list[dict[str, Any]]) -> int:
        """Insert hot keywords, replacing existing rows on (date, keyword) conflict.

        Uses INSERT OR REPLACE so that re-runs with updated weights
        (from fresh feedback-loop data) overwrite stale values instead
        of being silently ignored.
        """
        sql = """
            INSERT OR REPLACE INTO hot_keywords
                (date, keyword, source, rank, weight)
            VALUES (:date, :keyword, :source, :rank, :weight)
        """
        with self._connect() as conn:
            conn.executemany(sql, records)
        return len(records)

    def get_hot_keywords_by_date(self, date: str) -> list[dict[str, Any]]:
        """Return hot keywords for a date, ordered by weight desc."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM hot_keywords WHERE date = ? ORDER BY weight DESC",
                (date,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Hot Topic News CRUD ──────────────────────────────────

    def insert_hot_topic_news_deduped(
        self, records: list[dict[str, Any]], date: str
    ) -> int:
        """Insert hot topic search results with (date, url) dedup."""
        sql = """
            INSERT OR IGNORE INTO hot_topic_news
                (date, keyword, headline, url, source, snippet, search_engine, selected)
            VALUES (:date, :keyword, :headline, :url, :source, :snippet, :search_engine, :selected)
        """
        inserted = 0
        with self._connect() as conn:
            for r in records:
                r["date"] = date
                r.setdefault("source", "")
                r.setdefault("snippet", "")
                r.setdefault("search_engine", "")
                r.setdefault("selected", 0)
                cur = conn.execute(sql, r)
                if cur.rowcount > 0:
                    inserted += 1
        return inserted

    def get_hot_topic_news_by_date(
        self, date: str, selected: bool | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return hot topic news for a date, optionally filtered by selected flag."""
        conditions = ["date = ?"]
        params: list[Any] = [date]
        if selected is not None:
            conditions.append("selected = ?")
            params.append(1 if selected else 0)
        sql = f"SELECT * FROM hot_topic_news WHERE {' AND '.join(conditions)} ORDER BY id"
        if limit:
            sql += f" LIMIT {limit}"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def mark_hot_topic_selected(self, urls: list[str], date: str) -> None:
        """Mark hot topic news items as selected for the daily card.

        Clears all previously selected items for this date first, then marks
        the given URLs. This ensures --force re-runs don't accumulate stale
        selections from prior runs.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE hot_topic_news SET selected = 0 WHERE date = ?",
                (date,),
            )
            conn.executemany(
                "UPDATE hot_topic_news SET selected = 1 WHERE url = ? AND date = ?",
                [(url, date) for url in urls],
            )

    # ── Hot Topic Click Feedback ─────────────────────────────

    def record_hot_topic_click(
        self, date: str, target_date: str, news_url: str, keyword: str,
        chat_id: str = "", open_id: str = "",
    ) -> str:
        """Record a hot topic click in user_feedback. Returns 'inserted' or 'duplicate'."""
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO user_feedback
                   (date, target_date, feedback_type, chat_id, news_url, keyword, open_id)
                   VALUES (?, ?, 'hot_click', ?, ?, ?, ?)""",
                (date, target_date, chat_id, news_url, keyword, open_id),
            )
            return "inserted" if cur.rowcount > 0 else "duplicate"

    def get_hot_keyword_click_stats(self, days: int = 14) -> dict[str, int]:
        """Get click counts per keyword from hot_click feedback (last N days)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT keyword, COUNT(*) as clicks
                   FROM user_feedback
                   WHERE feedback_type = 'hot_click'
                     AND date >= date('now', ? || ' days')
                     AND keyword != ''
                   GROUP BY keyword""",
                (f"-{days}",),
            ).fetchall()
        return {r["keyword"]: r["clicks"] for r in rows}

    # ── Pipeline Run Tracking ────────────────────────────────

    def insert_pipeline_run(
        self,
        date: str,
        phases_json: str = "[]",
        exit_code: int = 0,
        error_summary: str = "",
        total_ms: int = 0,
    ) -> None:
        """Record a pipeline run for monitoring / debugging."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO pipeline_runs (date, phases_json, exit_code, error_summary, total_ms)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (date, phases_json, exit_code, error_summary, total_ms),
                )
                conn.commit()
        except Exception:
            pass  # best-effort — never let monitoring break the pipeline


# ── Module-level convenience ─────────────────────────────────

_db: Database | None = None


def get_db() -> Database:
    """Return the singleton Database instance."""
    global _db
    if _db is None:
        _db = Database()
    return _db


# ── CLI test entry ───────────────────────────────────────────

if __name__ == "__main__":
    db = get_db()
    print(f"Database initialized at: {db.db_path}")
    print(f"Available dates: {db.get_available_dates()}")
    for d in db.get_available_dates():
        print(f"  {d}: charts={db.get_available_chart_types(d)}")
    print("Tables created successfully. [OK]")
