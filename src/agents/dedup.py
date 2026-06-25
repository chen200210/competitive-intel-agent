"""
Dedup I/O — read/write reported_items table for cross-run deduplication.

Centralises all dedup logic that was previously scattered across briefer.py.
Import this from briefer (orchestration), market_pipeline (news dedup), and
any future module that needs to check/save reported items.

Item types and their TTLs:
  news       — pushed top-7 headlines (30-day TTL)
  news_seen  — all 15 candidates seen (7-day TTL)
  news_h     — headline dedup tokens for cross-source comparison (30-day TTL)
  steam      — Steam port game names (30-day TTL)
  taptap     — TapTap new game names (30-day TTL)
"""

from __future__ import annotations

import re
import sys
from typing import Any


# ═════════════════════════════════════════════════════════════
# News dedup
# ═════════════════════════════════════════════════════════════

def load_reported_news() -> set[str]:
    """Load normalized URLs of news already pushed in previous reports.

    Checks both long-term ('news') and short-term ('news_seen') dedup records.
    """
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        urls = db.get_reported_keys("news")
        urls |= db.get_reported_keys("news_seen")
        return urls
    except Exception:
        return set()


def load_reported_news_headlines() -> set[str]:
    """Load headline dedup keys from previous reports (cross-source dedup)."""
    try:
        from src.storage.sqlite import get_db
        return get_db().get_reported_keys("news_h")
    except Exception:
        return set()


def headline_dedup_tokens(headline: str) -> set[str]:
    """Extract dedup tokens from a headline for cross-source comparison.

    Returns a set of normalized key phrases. If ANY token from a new headline
    matches ANY token from a previously reported headline, it's a duplicate.
    """
    from src.pipeline.token_utils import headline_dedup_tokens as _fn
    return _fn(headline)


def save_reported_news(urls: set[str], date: str,
                       headline_tokens: set[str] | None = None) -> None:
    """Save pushed news URLs and headline dedup keys to dedup table."""
    # Save normalized URLs
    if urls:
        try:
            normalized = {re.sub(r'[?#].*$', '', u) for u in urls if u}
            from src.storage.sqlite import get_db
            db = get_db()
            n = db.mark_reported(normalized, "news", date)
            db.prune_reported("news", max_age_days=30)
            if n:
                print(f"   DB: marked {n} news URLs as reported")
        except Exception as e:
            print(f"   [warn] DB news reported save failed: {e}")

    # Save headline dedup tokens for cross-source dedup
    if headline_tokens:
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            n = db.mark_reported(headline_tokens, "news_h", date)
            db.prune_reported("news_h", max_age_days=30)
            if n:
                print(f"   DB: marked {n} headline tokens as reported")
        except Exception as e:
            print(f"   [warn] DB headline tokens save failed: {e}")


def save_seen_candidates(candidates: list[dict[str, Any]], date: str) -> None:
    """Save ALL candidate URLs to dedup table with short TTL (7 days).

    This prevents the same low-scoring articles from being reconsidered
    day after day when they persistently appear in scraper output.
    Items that make it to the final top-7 are separately saved as 'news'
    with a 30-day TTL via save_reported_news().
    """
    urls = {re.sub(r'[?#].*$', '', c.get("url", "")) for c in candidates if c.get("url")}
    if not urls:
        return
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        n = db.mark_reported(urls, "news_seen", date)
        db.prune_reported("news_seen", max_age_days=7)
        if n:
            print(f"   DB: marked {n} candidate URLs as seen (7-day TTL)")
    except Exception as e:
        print(f"   [warn] DB candidate save failed: {e}")


# ═════════════════════════════════════════════════════════════
# Steam port dedup
# ═════════════════════════════════════════════════════════════

def load_reported_steam(target_date: str = "") -> set[str]:
    """Load Steam port game names from PREVIOUS dates only.
    Excludes today so re-running same day is deterministic."""
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        reported: set[str] = set()
        if target_date:
            for row in db._connect().execute(
                "SELECT item_key FROM reported_items WHERE item_type IN ('steam','taptap') AND reported_date < ?",
                (target_date,)
            ).fetchall():
                reported.add(row["item_key"])
            for row in db._connect().execute(
                "SELECT DISTINCT game_name FROM steam_port_games WHERE date < ?", (target_date,)
            ).fetchall():
                reported.add(row["game_name"])
        return reported
    except Exception:
        return set()


def save_reported_steam(names: set[str], date: str) -> None:
    """Save newly reported Steam port game names to DB."""
    if not names:
        return
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        n = db.mark_reported(names, "steam", date)
        db.prune_reported("steam", max_age_days=30)
        if n:
            print(f"   DB: marked {n} steam games as reported")
    except Exception as e:
        print(f"   [warn] DB steam reported save failed: {e}")


# ═════════════════════════════════════════════════════════════
# TapTap new game dedup
# ═════════════════════════════════════════════════════════════

def load_reported_taptap(target_date: str = "") -> set[str]:
    """Load TapTap game names from PREVIOUS dates only.
    Excludes today so re-running same day is deterministic."""
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        reported: set[str] = set()
        if target_date:
            for row in db._connect().execute(
                "SELECT item_key FROM reported_items WHERE item_type = 'taptap' AND reported_date < ?",
                (target_date,)
            ).fetchall():
                reported.add(row["item_key"])
            for row in db._connect().execute(
                "SELECT DISTINCT game_name FROM taptap_new_games WHERE date < ? AND date >= date(?, '-7 days')",
                (target_date, target_date)
            ).fetchall():
                reported.add(row["game_name"])
        return reported
    except Exception:
        return set()


def save_reported_taptap(names: set[str], date: str) -> None:
    """Save newly reported TapTap game names to DB."""
    if not names:
        return
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        n = db.mark_reported(names, "taptap", date)
        db.prune_reported("taptap", max_age_days=30)
        if n:
            print(f"   DB: marked {n} taptap games as reported")
    except Exception as e:
        print(f"   [warn] DB reported save failed: {e}")
