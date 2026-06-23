"""
TapTap App ID resolver — finds TapTap app page URLs by searching game names.

Uses Playwright to search, click the first result, and capture the navigated URL.
Results cached in DB for reuse (each game only needs one Playwright run).

Usage:
    python -m src.tools.taptap_resolver --game "保卫萝卜4"
    python -m src.tools.taptap_resolver --batch  # resolve all missing
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

CHROME_PROFILE = _PROJECT_ROOT / "data" / ".diandian_chrome_profile"


def resolve_taptap_url(game_name: str, force: bool = False) -> str | None:
    """Find a game's TapTap app page URL via search + click.

    Returns: "https://www.taptap.cn/app/{id}" or None if not found.
    """
    # ── Check DB cache first ──
    if not force:
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            rows = db._connect().execute(
                "SELECT taptap_url FROM taptap_new_games WHERE game_name = ? AND taptap_url != ''",
                (game_name,)
            ).fetchall()
            if rows:
                return rows[0][0]
            rows = db._connect().execute(
                "SELECT value FROM kv_cache WHERE key = ?",
                (f"taptap_url:{game_name}",)
            ).fetchall()
            if rows and rows[0][0] and "/app/" in rows[0][0]:
                return rows[0][0]
        except Exception:
            pass

    # ── Live search via Playwright ──
    if not CHROME_PROFILE.exists():
        return None

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE),
                headless=True,
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.new_page()

            page.goto(
                f"https://www.taptap.cn/search?q={game_name}",
                wait_until="networkidle", timeout=20_000,
            )
            page.wait_for_timeout(2_000)

            # TapTap renders results in virtual DOM — click first game icon
            # to trigger navigation, then read the URL for the app ID.
            app_id: str | None = None
            try:
                page.locator("img[alt]").first.click(timeout=5_000)
                page.wait_for_timeout(2_000)
                match = re.search(r'/app/(\d+)', page.url)
                if match:
                    app_id = match.group(1)
            except Exception:
                pass

            context.close()

            if app_id:
                app_url = f"https://www.taptap.cn/app/{app_id}"
                # ── Cache to DB ──
                try:
                    from src.storage.sqlite import get_db
                    db = get_db()
                    db._connect().execute(
                        "INSERT OR REPLACE INTO kv_cache (key, value) VALUES (?, ?)",
                        (f"taptap_url:{game_name}", app_url),
                    )
                    db._connect().commit()
                except Exception:
                    pass
                return app_url

            return None

    except Exception:
        return None


def resolve_batch(game_names: list[str]) -> dict[str, str | None]:
    """Resolve TapTap URLs for a list of game names. Returns {name: url}."""
    results: dict[str, str | None] = {}
    for i, name in enumerate(game_names):
        url = resolve_taptap_url(name)
        results[name] = url
        status = "✅" if url else "❌"
        print(f"  [{i+1}/{len(game_names)}] {status} {name[:40]}")
    return results


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) >= 3 and sys.argv[1] == "--game":
        url = resolve_taptap_url(sys.argv[2])
        if url:
            print(url)
        else:
            print(f"Not found: {sys.argv[2]}")
            sys.exit(1)

    elif len(sys.argv) >= 2 and sys.argv[1] == "--batch":
        from src.storage.sqlite import get_db
        db = get_db()
        date = db.get_available_dates()[0]
        changes = db.get_changes_by_date(date)
        from src.pipeline.runner import _filter_track_changes
        track = _filter_track_changes(changes)

        known: set[str] = set()
        rows = db._connect().execute(
            "SELECT game_name FROM taptap_new_games WHERE taptap_url != ''"
        ).fetchall()
        known |= {r["game_name"] for r in rows}
        rows = db._connect().execute(
            "SELECT key FROM kv_cache WHERE key LIKE 'taptap_url:%'"
        ).fetchall()
        known |= {r["key"].replace("taptap_url:", "") for r in rows}

        missing = [c.get("game_name", "") for c in track
                   if c.get("game_name", "") not in known]
        print(f"Track games: {len(track)}, missing URLs: {len(missing)}")
        if missing:
            resolve_batch(missing[:10])
    else:
        print("Usage:")
        print("  python -m src.tools.taptap_resolver --game '保卫萝卜4'")
        print("  python -m src.tools.taptap_resolver --batch")
