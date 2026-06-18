"""
Database query tool — safe read-only SQL access for Agents.

Agents use this to look up historical ranking data, volatility trends,
and past analysis results. Only SELECT queries are allowed.

Usage:
    from src.tools.db_query import db_query
    result = db_query("SELECT date, rank FROM rankings WHERE bundle_id = 'com.xxx' ORDER BY date DESC LIMIT 30")
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.storage.sqlite import get_db

# ── Safety: query guardrails ────────────────────────────────────

# Maximum rows to return (prevents blowing agent context)
MAX_ROWS = 500

# SQL keywords that are forbidden (write operations)
FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "CREATE", "TRUNCATE", "REPLACE", "ATTACH", "DETACH",
    "PRAGMA", "VACUUM", "REINDEX",
]

# Minimum required keyword — must start with SELECT or WITH
ALLOWED_PREFIXES = ["SELECT", "WITH"]


def _validate_sql(sql: str) -> str | None:
    """Validate that a SQL string is a safe read-only query.

    Returns an error message string if invalid, None if safe.
    """
    stripped = sql.strip()

    # 1. Must start with SELECT or WITH
    upper = stripped.upper()
    if not any(upper.startswith(p) for p in ALLOWED_PREFIXES):
        return (
            f"Only SELECT/WITH queries are allowed. "
            f"Your query starts with: {stripped[:30]}..."
        )

    # 2. No forbidden keywords (case-insensitive word boundary check)
    for kw in FORBIDDEN_KEYWORDS:
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, upper):
            return f"Forbidden SQL keyword detected: {kw}. Only read-only SELECT queries are allowed."

    # 3. Basic sanity — shouldn't be empty
    if len(stripped) < 7:
        return "Query too short — please provide a valid SELECT statement."

    return None


# ── Common query templates (for agent reference) ─────────────────

COMMON_QUERIES = """
Common queries you can adapt:
  Game rank history:
    SELECT date, rank FROM rankings
    WHERE bundle_id = 'com.kurogame.mingchao'
    ORDER BY date DESC LIMIT 30

  Today's rankings (specific chart):
    SELECT rank, game_name, bundle_id, developer FROM rankings
    WHERE date = '2026-06-16' AND chart_type = '热门榜'
    ORDER BY rank

  Available dates:
    SELECT DISTINCT date FROM rankings ORDER BY date DESC LIMIT 10

  Recent changes (significant only):
    SELECT game_name, change_type, today_rank, yesterday_rank, rank_change, attention_score
    FROM changes WHERE date = '2026-06-16' AND is_significant = 1
    ORDER BY attention_score DESC

  Game change history:
    SELECT date, change_type, today_rank, yesterday_rank, rank_change
    FROM changes WHERE bundle_id = 'com.xxx'
    ORDER BY date DESC LIMIT 14

  Daily overview (volatility context):
    SELECT date, day_type, volatility FROM daily_overviews
    WHERE date >= '2026-06-01' ORDER BY date DESC

  Cross-chart signals:
    SELECT * FROM cross_chart_signals WHERE date = '2026-06-16'

  In-development competitors:
    SELECT company, product_name, genre, theme, status, threat_level
    FROM in_development_tracking ORDER BY
      CASE threat_level WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END

  Recent search cache (what was searched before):
    SELECT query, engine, result_count, searched_at FROM search_cache
    ORDER BY searched_at DESC LIMIT 10
"""


# ── Main tool function ──────────────────────────────────────────

def db_query(sql: str, **_meta: Any) -> str:
    """Execute a read-only SQL query against the project database.

    Only SELECT (and WITH ... SELECT) queries are allowed.
    Results are capped at 500 rows.

    Args:
        sql: A SELECT SQL query string.
        _meta: Internal kwargs injected by Agent (_called_by, _run_id, _target_date).

    Returns:
        JSON string with {sql, columns, rows, row_count, truncated}.
    """
    # 1. Validate
    error = _validate_sql(sql)
    if error:
        return json.dumps({
            "error": error,
            "hint": COMMON_QUERIES.strip(),
        }, ensure_ascii=False)

    # 2. Execute read-only
    db = get_db()
    try:
        with db._connect() as conn:
            # Set a short timeout to prevent runaway queries
            conn.execute("PRAGMA query_only = ON")
            cursor = conn.execute(sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as e:
        return json.dumps({
            "error": f"Query execution failed: {e}",
            "sql": sql[:500],
        }, ensure_ascii=False)

    # 3. Format result
    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]

    return json.dumps({
        "sql": sql[:500],
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "hint": "Results truncated at 500 rows." if truncated else "",
    }, ensure_ascii=False, default=str)


# ── Tool descriptor for Agent registration ──────────────────────

TOOL_DESCRIPTOR: dict[str, Any] = {
    "name": "db_query",
    "description": (
        "Execute a read-only SQL query against the project database. "
        "Only SELECT queries are allowed. "
        "Use this to look up historical ranking data, game trends, "
        "volatility context, past analysis results, and cross-chart signals.\n\n"
        "Available tables:\n"
        "  rankings — daily ranking snapshots (date, platform, chart_type, rank, bundle_id, game_name, developer)\n"
        "  changes  — daily change records (date, change_type, today_rank, yesterday_rank, rank_change, attention_score, is_significant)\n"
        "  daily_overviews — Overview Scanner output (date, day_type, volatility, industry_news_json)\n"
        "  research_results — Researcher output (change_id, findings_json, verified_json)\n"
        "  analysis_reports — Analyst + Briefer output (date, report_json, design_analysis_json, brief_card_json)\n"
        "  cross_chart_signals — cross-chart pattern detection (date, bundle_id, signal_pattern, threat_level)\n"
        "  in_development_tracking — competitor pipeline tracking (company, product_name, genre, theme, status, threat_level)\n"
        "\n"
        + COMMON_QUERIES
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A SELECT SQL query. Examples above. "
                    "Always include a LIMIT clause (max 500 rows). "
                    "Use single quotes for string literals."
                ),
            },
        },
        "required": ["sql"],
    },
}


# ── CLI test entry ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2:
        test_sql = " ".join(sys.argv[1:])
    else:
        test_sql = "SELECT DISTINCT date FROM rankings ORDER BY date DESC LIMIT 5"

    print(f"Running: {test_sql}")
    result = db_query(test_sql)
    parsed = json.loads(result)
    if "error" in parsed:
        print(f"Error: {parsed['error']}")
    else:
        print(f"Columns: {parsed['columns']}")
        print(f"Rows: {parsed['row_count']}")
        for row in parsed["rows"][:5]:
            print(f"  {row}")
