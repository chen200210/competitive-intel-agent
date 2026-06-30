"""
Dedup I/O — read/write reported_items table for cross-run deduplication.

Centralises all dedup logic that was previously scattered across briefer.py.
Import this from briefer (orchestration), market_pipeline (news dedup), and
any future module that needs to check/save reported items.

Item types and their TTLs:
  news       — pushed top-7 headlines (30-day TTL)
  news_h     — headline dedup tokens for cross-source comparison (30-day TTL)
  steam      — Steam port game names (30-day TTL)
  taptap     — TapTap new game names (30-day TTL)

Design note (2026-06-26): news_seen type was removed. It used to save ALL
candidate URLs (including non-selected ones) with a 7-day TTL, but this
starved the pipeline — 30+ non-published candidates from yesterday were
blocked from reconsideration today. Only published items (news type) are
now saved for cross-day dedup. apply_fatigue() handles topic repetition.
"""

from __future__ import annotations

import re
import sys
from typing import Any

from src.pipeline.token_utils import headline_dedup_tokens as _fn


# ═════════════════════════════════════════════════════════════
# News dedup
# ═════════════════════════════════════════════════════════════

def load_reported_news() -> set[str]:
    """Load normalized URLs of news already pushed in previous reports.

    Only checks the 'news' type (30-day TTL) — items that were actually
    published in a daily report. Non-selected candidates are NOT dedup'd
    across days; they get a fresh chance at scoring tomorrow.
    """
    try:
        from src.storage.sqlite import get_db
        return get_db().get_reported_keys("news")
    except Exception as e:
        print(f"  [WARN] load_reported_news failed: {e}", file=sys.stderr)
        return set()


def load_reported_news_headlines() -> set[str]:
    """Load headline dedup keys from previous reports (cross-source dedup)."""
    try:
        from src.storage.sqlite import get_db
        return get_db().get_reported_keys("news_h")
    except Exception as e:
        print(f"  [WARN] load_reported_news_headlines failed: {e}", file=sys.stderr)
        return set()


def headline_dedup_tokens(headline: str) -> set[str]:
    """Extract dedup tokens from a headline for cross-source comparison.

    Returns a set of normalized key phrases. If ANY token from a new headline
    matches ANY token from a previously reported headline, it's a duplicate.
    """
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
            print(f"   [WARN] DB news reported save failed: {e}", file=sys.stderr)

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
            print(f"   [WARN] DB headline tokens save failed: {e}", file=sys.stderr)


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
    except Exception as e:
        print(f"  [WARN] load_reported_steam failed: {e}", file=sys.stderr)
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
        print(f"   [WARN] DB steam reported save failed: {e}", file=sys.stderr)


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
    except Exception as e:
        print(f"  [WARN] load_reported_taptap failed: {e}", file=sys.stderr)
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
        print(f"   [WARN] DB taptap reported save failed: {e}", file=sys.stderr)
