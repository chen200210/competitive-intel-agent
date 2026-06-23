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

-- Overview Scanner output (one per day)
CREATE TABLE IF NOT EXISTS daily_overviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    day_type TEXT NOT NULL,           -- 'quiet' | 'normal' | 'volatile'
    volatility REAL,
    industry_news_json TEXT,          -- JSON array
    recommended_focus_json TEXT,      -- JSON array
    skip_json TEXT,                   -- JSON array
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Researcher output, keyed to a change record
CREATE TABLE IF NOT EXISTS research_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id INTEGER REFERENCES changes(id),
    findings_json TEXT,               -- Researcher raw output JSON
    verified_json TEXT,               -- Verifier output JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Daily analysis report (one per day)
CREATE TABLE IF NOT EXISTS analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    research_ids TEXT,                -- JSON array of research_results.id
    report_json TEXT,                 -- Analyst output JSON
    design_analysis_json TEXT,        -- Design Analyst output JSON
    brief_card_json TEXT,             -- Briefer card JSON for Feishu
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Conversation log (Feishu interactive Q&A)
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_message TEXT,
    intent TEXT,
    agent_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Channel effectiveness tracking (Check-2)
CREATE TABLE IF NOT EXISTS channel_effectiveness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT,
    search_query TEXT,
    hit_count INTEGER,
    result_quality_score REAL,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Cross-chart comparison signals (cross_chart module output)
-- One row per (date, bundle_id): a game's multi-chart profile + detected signal
CREATE TABLE IF NOT EXISTS cross_chart_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    game_name TEXT NOT NULL,
    charts_json TEXT NOT NULL,         -- {"免费榜": 5, "畅销榜": 48, "热门榜": 12}
    signal_pattern TEXT,               -- 'leading'|'traffic_leak'|'harvest'|'word_of_mouth'|'divergence'
    signal_description TEXT,           -- human-readable signal description
    threat_level TEXT,                 -- 'high'|'medium'|'low'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, bundle_id)
);

-- In-development competitor tracking (Design Analyst source)
CREATE TABLE IF NOT EXISTS in_development_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    product_name TEXT,
    genre TEXT,
    theme TEXT,
    status TEXT,                      -- '在研' | '测试中' | '即将上线' | '已上线'
    progress_detail TEXT,
    coverage TEXT,                    -- '仅玩法' | '仅题材' | '玩法+题材' | '玩法+题材+商业模式'
    threat_level TEXT,                -- 'high' | 'medium' | 'low'
    evidence_json TEXT,               -- JSON: links, screenshots, etc.
    first_discovered_at TEXT,
    last_updated_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Prompt version history (Check-3 self-optimization)
CREATE TABLE IF NOT EXISTS prompt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT,
    version TEXT,
    prompt_content TEXT,
    performance_score REAL,
    is_active BOOLEAN DEFAULT 0,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, url)
);

-- Diandian on-demand search result cache (triggered by user click)
CREATE TABLE IF NOT EXISTS diandian_search_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_name TEXT NOT NULL,
    bundle_id TEXT,
    search_date TEXT NOT NULL,
    result_json TEXT,            -- full search result JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Unreleased / in-development game tracking
CREATE TABLE IF NOT EXISTS unreleased_games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    game_name TEXT NOT NULL,
    developer TEXT,
    genre TEXT,
    theme TEXT,
    status TEXT,                 -- '已定档' | 'demo' | '在研' | '测试中' | '即将上线'
    release_date TEXT,           -- estimated release date or quarter
    taptap_url TEXT,
    source TEXT,                 -- where this info came from
    track_relevant BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, game_name)
);
CREATE INDEX IF NOT EXISTS idx_taptap_new_date ON taptap_new_games(date);
CREATE INDEX IF NOT EXISTS idx_steam_port_date ON steam_port_games(date);
CREATE INDEX IF NOT EXISTS idx_market_news_date ON market_news(date);
CREATE INDEX IF NOT EXISTS idx_market_news_source ON market_news(source);
CREATE INDEX IF NOT EXISTS idx_diandian_cache_game ON diandian_search_cache(game_name);
CREATE INDEX IF NOT EXISTS idx_unreleased_date ON unreleased_games(date);

-- run_id columns for traceability (added via migration if missing)
-- daily_overviews, research_results, analysis_reports get run_id

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_rankings_date ON rankings(date);
CREATE INDEX IF NOT EXISTS idx_rankings_bundle_id ON rankings(bundle_id);
CREATE INDEX IF NOT EXISTS idx_rankings_date_platform ON rankings(date, platform);
CREATE INDEX IF NOT EXISTS idx_rankings_date_platform_chart ON rankings(date, platform, chart_type);
CREATE INDEX IF NOT EXISTS idx_changes_date ON changes(date);
CREATE INDEX IF NOT EXISTS idx_changes_type ON changes(change_type);
CREATE INDEX IF NOT EXISTS idx_changes_attention ON changes(attention_score DESC);
CREATE INDEX IF NOT EXISTS idx_changes_date_platform_chart ON changes(date, platform, chart_type);
CREATE INDEX IF NOT EXISTS idx_cross_chart_date ON cross_chart_signals(date);
CREATE INDEX IF NOT EXISTS idx_cross_chart_threat ON cross_chart_signals(threat_level);
"""


class Database:
    """SQLite database manager with connection pooling per thread."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.sqlite_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate_v2()
        self._migrate_v3()

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
        """Add run_id columns to product tables + cache/audit tables (created via SCHEMA_SQL already)."""
        with self._connect() as conn:
            # Add run_id to daily_overviews if missing
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(daily_overviews)").fetchall()]
            if "run_id" not in cols:
                conn.execute("ALTER TABLE daily_overviews ADD COLUMN run_id TEXT")
                print("[migrate] Added run_id to daily_overviews")

            # Add run_id to research_results if missing
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(research_results)").fetchall()]
            if "run_id" not in cols:
                conn.execute("ALTER TABLE research_results ADD COLUMN run_id TEXT")
                print("[migrate] Added run_id to research_results")

            # Add run_id to analysis_reports if missing
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(analysis_reports)").fetchall()]
            if "run_id" not in cols:
                conn.execute("ALTER TABLE analysis_reports ADD COLUMN run_id TEXT")
                print("[migrate] Added run_id to analysis_reports")

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

    # ── Overview CRUD ───────────────────────────────────────

    def upsert_daily_overview(self, date: str, day_type: str, volatility: float,
                               industry_news_json: str, recommended_focus_json: str,
                               skip_json: str, run_id: str = "") -> None:
        sql = """
            INSERT OR REPLACE INTO daily_overviews
                (date, day_type, volatility, industry_news_json, recommended_focus_json, skip_json, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (date, day_type, volatility, industry_news_json,
                              recommended_focus_json, skip_json, run_id))

    def get_daily_overview(self, date: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM daily_overviews WHERE date = ?", (date,)
            ).fetchone()
        return dict(row) if row else None

    # ── Research CRUD ───────────────────────────────────────

    def insert_research_result(self, change_id: int, findings_json: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO research_results (change_id, findings_json) VALUES (?, ?)",
                (change_id, findings_json),
            )
            return cur.lastrowid

    def update_research_verification(self, research_id: int, verified_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE research_results SET verified_json = ? WHERE id = ?",
                (verified_json, research_id),
            )

    # ── Analysis CRUD ───────────────────────────────────────

    def upsert_analysis_report(self, date: str, research_ids: str, report_json: str,
                                design_analysis_json: str = "", brief_card_json: str = "") -> None:
        sql = """
            INSERT OR REPLACE INTO analysis_reports
                (date, research_ids, report_json, design_analysis_json, brief_card_json)
            VALUES (?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (date, research_ids, report_json,
                              design_analysis_json, brief_card_json))

    def get_analysis_report(self, date: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_reports WHERE date = ?", (date,)
            ).fetchone()
        return dict(row) if row else None

    # ── Conversation CRUD ───────────────────────────────────

    def log_conversation(self, user_id: str, user_message: str,
                         intent: str, agent_response: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (user_id, user_message, intent, agent_response) VALUES (?, ?, ?, ?)",
                (user_id, user_message, intent, agent_response),
            )
            return cur.lastrowid

    # ── In-Development Tracking CRUD ────────────────────────

    def upsert_in_dev_company(self, company: str, product_name: str, genre: str,
                               theme: str, status: str, progress_detail: str,
                               coverage: str, threat_level: str,
                               evidence_json: str, last_updated_at: str) -> None:
        sql = """
            INSERT INTO in_development_tracking
                (company, product_name, genre, theme, status, progress_detail,
                 coverage, threat_level, evidence_json, first_discovered_at, last_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, DATE('now'), ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status, progress_detail=excluded.progress_detail,
                threat_level=excluded.threat_level, evidence_json=excluded.evidence_json,
                last_updated_at=excluded.last_updated_at
        """
        with self._connect() as conn:
            conn.execute(sql, (company, product_name, genre, theme, status,
                              progress_detail, coverage, threat_level, evidence_json,
                              last_updated_at))

    def get_in_dev_companies(self, threat_level: str | None = None) -> list[dict[str, Any]]:
        if threat_level:
            sql = "SELECT * FROM in_development_tracking WHERE threat_level = ? ORDER BY company"
            params = (threat_level,)
        else:
            sql = "SELECT * FROM in_development_tracking ORDER BY company"
            params = ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Cross-Chart Signals CRUD ───────────────────────────

    def insert_cross_chart_signals(self, records: list[dict[str, Any]]) -> int:
        """Bulk insert cross-chart signal records. Uses INSERT OR REPLACE for idempotency."""
        sql = """
            INSERT OR REPLACE INTO cross_chart_signals
                (date, bundle_id, game_name, charts_json, signal_pattern,
                 signal_description, threat_level)
            VALUES (:date, :bundle_id, :game_name, :charts_json, :signal_pattern,
                    :signal_description, :threat_level)
        """
        with self._connect() as conn:
            conn.executemany(sql, records)
        return len(records)

    def get_cross_chart_signals(
        self, date: str | None = None,
        threat_level: str | None = None,
        signal_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return cross-chart signals, optionally filtered by date / threat / pattern."""
        conditions: list[str] = []
        params: list[Any] = []
        if date:
            conditions.append("date = ?")
            params.append(date)
        if threat_level:
            conditions.append("threat_level = ?")
            params.append(threat_level)
        if signal_pattern:
            conditions.append("signal_pattern = ?")
            params.append(signal_pattern)
        sql = "SELECT * FROM cross_chart_signals"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY date DESC, bundle_id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

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
                (date, headline, source, url, category, related_game, track_relevant)
            VALUES (:date, :headline, :source, :url, :category, :related_game, :track_relevant)
        """
        with self._connect() as conn:
            conn.executemany(sql, records)
        return len(records)

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

    # ── Diandian Search Cache CRUD ────────────────────────────

    def cache_diandian_search(self, game_name: str, search_date: str,
                              result_json: str, bundle_id: str = "") -> int:
        """Cache a Diandian search result."""
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO diandian_search_cache
                   (game_name, bundle_id, search_date, result_json)
                   VALUES (?, ?, ?, ?)""",
                (game_name, bundle_id, search_date, result_json),
            )
            return cur.lastrowid

    def get_diandian_search_cache(
        self, game_name: str, max_age_days: int = 7,
    ) -> dict[str, Any] | None:
        """Return cached Diandian search result if fresh enough."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM diandian_search_cache
                   WHERE game_name = ?
                     AND datetime(created_at) > datetime('now', ? || ' days')
                   ORDER BY created_at DESC LIMIT 1""",
                (game_name, f"-{max_age_days}"),
            ).fetchone()
        return dict(row) if row else None

    # ── Unreleased Games CRUD ─────────────────────────────────

    def upsert_unreleased_games(self, records: list[dict[str, Any]]) -> int:
        """Bulk upsert unreleased game records."""
        sql = """
            INSERT OR REPLACE INTO unreleased_games
                (date, game_name, developer, genre, theme, status, release_date, taptap_url, source, track_relevant)
            VALUES (:date, :game_name, :developer, :genre, :theme, :status, :release_date, :taptap_url, :source, :track_relevant)
        """
        with self._connect() as conn:
            conn.executemany(sql, records)
        return len(records)

    def get_unreleased_games_by_date(
        self, date: str | None = None, track_relevant: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Return unreleased games, optionally filtered."""
        conditions: list[str] = []
        params: list[Any] = []
        if date:
            conditions.append("date = ?")
            params.append(date)
        if track_relevant is not None:
            conditions.append("track_relevant = ?")
            params.append(1 if track_relevant else 0)
        sql = "SELECT * FROM unreleased_games"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY date DESC, id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Utility ─────────────────────────────────────────────

    def table_has_data(self, table: str) -> bool:
        """Check if a table has any rows."""
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        return row["cnt"] > 0


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
