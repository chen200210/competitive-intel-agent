"""
Pipeline runner — orchestrates the full daily report flow.

New flow (2026-06-23):
  Phase 0A: Scrape (parallel) → 6 scrapers (diandian, taptap, steam_ports, news_feeds, pocketgamer_biz, bilibili)
  Phase 0B: Loader → all CSV → DB
  Phase 0C: Track Filter → tag all games
  Phase 1:  Differ → StoryPicker (pure rules)
  Phase 2:  Briefer (reads DB directly, generates Feishu card)
  Phase 3:  Card Audit + Push → Feishu

Usage:
    python -m src.pipeline.runner --date 2026-06-22
    python -m src.pipeline.runner --date 2026-06-22 --force
    python -m src.pipeline.runner --scrape --push oc_xxx
"""

from __future__ import annotations

import json
import sys
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from src.storage.sqlite import get_db
from src.types import PipelineRunStats


def _run_parallel(tasks: list[tuple[str, Any, tuple, dict]], max_workers: int = 8) -> list[dict]:
    """Run tasks in parallel. Each task is (name, fn, args, kwargs)."""
    import time as _time
    results = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), max_workers)) as ex:
        futures = {}
        for name, fn, args, kw in tasks:
            def _timed(fn=fn, args=args, kw=kw):
                t0 = _time.monotonic()
                try:
                    result = fn(*args, **kw)
                    elapsed = _time.monotonic() - t0
                    return {"status": "ok", "elapsed": elapsed, "result": result}
                except Exception as e:
                    elapsed = _time.monotonic() - t0
                    return {"status": "error", "elapsed": elapsed, "error": str(e)}
            futures[ex.submit(_timed)] = name
        for f in as_completed(futures):
            name = futures[f]
            try:
                r = f.result()
                results.append({"name": name, **r})
            except Exception as e:
                results.append({"name": name, "status": "error", "elapsed": 0, "error": str(e)})
    return results


def run_pipeline(date: str, force: bool = False, verbose: bool = False) -> dict[str, Any]:
    """Run the full daily pipeline for a given date.

    Skips steps that already have results in the DB (unless force=True).

    Returns a dict with keys: date, total_ms, steps, card, warnings, fatal.
    Callers should check result['fatal'] and exit non-zero when True.
    """
    db = get_db()
    steps: list[dict[str, Any]] = []
    pipeline_warnings: list[str] = []  # non-fatal issues collected for health summary
    t_total = _time.monotonic()

    def _step(name: str, fn, *args, **kwargs) -> Any:
        t0 = _time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            ms = int((_time.monotonic() - t0) * 1000)
            msg = f"{name} failed: {e}"
            pipeline_warnings.append(msg)
            steps.append({"name": name, "ms": ms, "status": "error", "error": str(e)})
            print(f"  [FAIL]  {name} ({ms}ms) — {e}")
            return {"error": str(e)}
        ms = int((_time.monotonic() - t0) * 1000)
        status = "ok"
        if isinstance(result, dict) and result.get("error"):
            status = "error"
            pipeline_warnings.append(f"{name}: {result.get('error', 'unknown')}")
        elif isinstance(result, dict) and result.get("skipped"):
            status = "skipped"
        steps.append({"name": name, "ms": ms, "status": status})
        tag = {"ok": "[OK]", "skipped": "[SKIP]", "error": "[FAIL]"}.get(status, "[?]")
        print(f"  {tag:6s} {name} ({ms}ms)" if status != "skipped" else f"  {tag:6s} {name}")
        return result

    # ═════════════════════════════════════════════════════════════
    # Phase 1: Data Pipeline (zero AI cost)
    # ═════════════════════════════════════════════════════════════
    print(f"\n{'='*50}")
    print(f"  Pipeline: {date}")
    print(f"{'='*50}")
    print("\n── Phase 1: Data Pipeline ──")

    # 1. Differ
    changes = db.get_changes_by_date(date)
    if changes and not force:
        diff_result = {"skipped": True, "hint": f"{len(changes)} changes already computed"}
    else:
        if force and changes:
            with db._connect() as conn:
                conn.execute("DELETE FROM changes WHERE date = ?", (date,))
                # Clear news dedup records for the date so re-runs
                # (especially --brief-only) don't accumulate stale
                # URL blocks from previous brief() → save_reported_news calls.
                conn.execute(
                    "DELETE FROM reported_items WHERE item_type IN ('news','news_h')"
                    " AND reported_date = ?", (date,)
                )
                # Clean non-ranking data accidentally imported from scraper CSVs.
                conn.execute("DELETE FROM rankings WHERE platform = ''")
                conn.commit()
            print("  [FORCE] Deleted existing changes + non-ranking data")
        from src.pipeline.differ import diff_with_yesterday
        diff_result = diff_with_yesterday(date)
        changes = diff_result.get("changes", [])
    _step("Differ", lambda: diff_result)
    day_type = diff_result.get("day_type", "normal")

    # 2. Story Picker
    from src.pipeline.story_picker import pick_stories_for_date
    story_result = _step("Story Picker", pick_stories_for_date, date)
    stories = story_result.get("stories", [])
    if verbose and stories:
        for s in stories[:3]:
            print(f"         [{s.get('story_type','?')}] {s.get('story_headline','?')[:60]}")

    # 3. Track Filter — filter changes to track-relevant games only
    from src.pipeline.track_filter import filter_track_changes
    track_changes = filter_track_changes(changes)
    if verbose:
        print(f"         track filter: {len(changes)} changes → {len(track_changes)} track-relevant")

    # ═════════════════════════════════════════════════════════════
    # Phase 1.5: Hot Topic Search (DDG-first via VPN, fallback to domestic engines)
    # ═════════════════════════════════════════════════════════════
    from src.pipeline.hot_tracker import search_hot_topics
    hot_result = _step("Hot Topic Search", search_hot_topics, date, force=force)
    if hot_result.get("warnings"):
        for w in hot_result["warnings"]:
            pipeline_warnings.append(f"Hot Topic: {w}")
    if verbose and hot_result.get("total_found"):
        print(f"         hot topic search: found {hot_result['total_found']} articles across"
              f" {hot_result.get('keywords_searched', 0)} keywords")
        if not hot_result.get("vpn_ok"):
            print(f"         [WARN] DDG unreachable (VPN down), used fallback engines")

    # ═════════════════════════════════════════════════════════════
    # Phase 2: Briefer (reads DB directly — all scraper + pipeline data)
    # ═════════════════════════════════════════════════════════════
    print("\n── Phase 2: Briefer ──")

    from src.agents.briefer import brief_from_db
    brief_result = _step("Briefer", brief_from_db, date, verbose, warnings=pipeline_warnings)
    card = brief_result.get("card", {})

    # Phase 4.5: Card Audit (zero token)
    if card:
        from src.pipeline.audit import audit_card, AuditContext

        # Gather bilibili video URLs for URL validation (they're merged into
        # market_news later in brief_from_db, so audit doesn't see them by default)
        bilibili_news: list[dict[str, Any]] = []
        try:
            bvideos = db.get_bilibili_videos_by_date(date)
            if bvideos:
                from src.agents.briefer import _bilibili_to_news
                bilibili_news = _bilibili_to_news(bvideos)
        except Exception as e:
            print(f"  [WARN] Failed to fetch bilibili videos for audit: {e}", file=sys.stderr)

        audit_ctx = AuditContext(
            taptap_games=db.get_taptap_games_by_date(date),
            steam_ports=db.get_steam_ports_by_date(date),
            market_news=list(db.get_market_news_by_date(date)) + bilibili_news,
        )
        audit_result = audit_card(card, audit_ctx)
        card = audit_result.fixed_card
        if audit_result.fixes_applied:
            for fix in audit_result.fixes_applied:
                print(f"  [FIX]  {fix}")
        if audit_result.warnings:
            for w in audit_result.warnings:
                print(f"  [WARN] {w}")
        if audit_result.failures:
            for f in audit_result.failures:
                print(f"  [FAIL] {f}")
        if audit_result.fixes_applied or audit_result.failures:
            _step("Card Audit", lambda: {"score": audit_result.score, "passed": audit_result.passed})

    # ═════════════════════════════════════════════════════════════
    # Summary + FATAL check
    # ═════════════════════════════════════════════════════════════
    total_ms = int((_time.monotonic() - t_total) * 1000)
    total_s = total_ms / 1000
    ai_steps = [s for s in steps if s["status"] != "skipped" and s["name"] not in
                ("Differ", "Story Picker")]
    skipped = sum(1 for s in steps if s["status"] == "skipped")
    errors = sum(1 for s in steps if s["status"] == "error")

    # ── FATAL classification ──
    # Briefer produced no card → pipeline is useless, treat as fatal.
    fatal = not card
    if fatal:
        pipeline_warnings.insert(0, "FATAL: Briefer produced no card — pipeline output is empty")

    # ── Save pipeline_runs record for monitoring ──
    try:
        phases_json = json.dumps(steps, ensure_ascii=False)
        error_summary = "; ".join(pipeline_warnings[:5]) if pipeline_warnings else ""
        db.insert_pipeline_run(
            date=date,
            phases_json=phases_json,
            exit_code=1 if fatal else 0,
            error_summary=error_summary[:500],
            total_ms=total_ms,
        )
    except Exception as e:
        print(f"  [WARN] insert_pipeline_run failed: {e}", file=sys.stderr)
        # best-effort monitoring, never break the pipeline

    print(f"\n{'='*50}")
    print(f"  Pipeline complete: {total_s:.1f}s total")
    print(f"  Steps: {len(steps)} ({skipped} skipped, {errors} errors)")
    if fatal:
        print(f"  [FATAL] Pipeline failed — no card produced")
    if pipeline_warnings:
        print(f"  Warnings: {len(pipeline_warnings)}")
        for w in pipeline_warnings[:3]:
            print(f"    - {w[:100]}")
    if ai_steps:
        ai_ms = sum(s.get("ms", 0) for s in ai_steps)
        print(f"  AI cost: {ai_ms/1000:.1f}s ({len(ai_steps)} agent calls)")
    print(f"{'='*50}")

    return {
        "date": date,
        "total_ms": total_ms,
        "steps": steps,
        "card": card,
        "warnings": pipeline_warnings,
        "fatal": fatal,
    }


# ═════════════════════════════════════════════════════════════
# Phase 0: Scrape (CLI helper)
# ═════════════════════════════════════════════════════════════

def _run_phase0_scrape(date: str, skip: list[str] | None = None) -> None:
    """Run all scrapers in parallel, then import all CSVs.

    Skips scrapers whose data for the target date is already in the DB.
    Cleans up CSV files that don't match the target date after import.

    Before scraping, clears today's news records so each run starts
    with a clean slate — no stale dedup data blocking new content.
    """
    import subprocess

    project_root = Path(__file__).resolve().parent.parent.parent
    scrapers_dir = project_root / "tools" / "scrapers"
    skip_set = set(skip or [])

    # ── Clean today's news data so scrapers + pipeline start fresh ──
    db = get_db()
    # Preserve user feedback counters across the DELETE→re-INSERT cycle.
    # Scrapers re-insert via INSERT OR REPLACE which resets useful_count
    # and useless_count to 0 (they are not in the INSERT column list).
    _counter_map: dict[str, tuple[int, int]] = {}
    try:
        conn = db._connect()
        rows = conn.execute(
            "SELECT url, useful_count, useless_count FROM market_news WHERE date = ?",
            (date,),
        ).fetchall()
        for r in rows:
            uc = r["useful_count"] or 0
            dc = r["useless_count"] or 0
            if uc or dc:
                _counter_map[r["url"]] = (uc, dc)
        conn.execute("DELETE FROM market_news WHERE date = ?", (date,))
        conn.execute(
            "DELETE FROM reported_items WHERE item_type IN ('news','news_h')"
            " AND reported_date = ?", (date,)
        )
        conn.commit()
        if _counter_map:
            print(f"  [CLEAN] Saved {len(_counter_map)} feedback counters,"
                  f" cleared today's news + dedup records for {date}")
        else:
            print(f"  [CLEAN] Cleared today's news + dedup records for {date}")
    except Exception as e:
        print(f"  [WARN] News cleanup failed: {e}")

    # ── Pre-check: skip scrapers with data already in DB ──
    scraper_db_table = {
        "diandian_batch.py":      "rankings",
        "taptap_new_games.py":    "taptap_new_games",
        "steam_ports.py":         "steam_port_games",
        "news_feeds.py":          "market_news",
        "bilibili_creators.py":   "bilibili_videos",
        "pocketgamer_biz.py":     "market_news",
    }

    # Per-scraper WHERE clause for shared-table pre-checks.
    # Defaults to "date = ?" — overridden when two scrapers write the same table.
    scraper_where: dict[str, str] = {
        "news_feeds.py":        "date = ? AND source != 'pocketgamer.biz'",
        "pocketgamer_biz.py":   "date = ? AND source = 'pocketgamer.biz'",
    }

    scraper_scripts = [
        ("diandian_batch.py", ["--platform", "ios"]),
        ("taptap_new_games.py", []),
        ("steam_ports.py", []),
        ("news_feeds.py", []),
        ("pocketgamer_biz.py", []),
        ("bilibili_creators.py", ["--headless"]),
    ]

    # Filter out skipped + already-have-data scrapers
    active_scripts: list[tuple[str, list[str]]] = []
    for script, extra_args in scraper_scripts:
        name = script.replace(".py", "")
        if name in skip_set or script in skip_set:
            print(f"  [SKIP] {script} — user-requested skip")
            continue
        table = scraper_db_table.get(script)
        if table:
            where_clause = scraper_where.get(script, "date = ?")
            sql = f"SELECT COUNT(*) as cnt FROM {table} WHERE {where_clause}"
            row = db._connect().execute(sql, (date,)).fetchone()
            if row and row["cnt"] > 0:
                print(f"  [SKIP] {script} — {row['cnt']} rows for {date} already in {table}")
                continue
        active_scripts.append((script, extra_args))

    if not active_scripts:
        print(f"  All scrapers skipped — data already exists for {date}")
        return

    print("\n── Phase 0A: Scrape (parallel) ──")
    scrape_tasks: list[tuple[str, Any, tuple, dict]] = []
    for script, extra_args in active_scripts:
        script_path = scrapers_dir / script
        if not script_path.exists():
            print(f"  [SKIP] {script} — not found")
            continue
        cmd = [sys.executable, str(script_path)] + extra_args
        env = {**__import__('os').environ, "PYTHONIOENCODING": "utf-8"}
        scrape_tasks.append((
            script, subprocess.run, (cmd,), {
                "cwd": str(project_root),
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "env": env,
            },
        ))

    if scrape_tasks:
        results = _run_parallel(scrape_tasks, max_workers=4)
        for r in results:
            status = r["status"]
            tag = "[OK]" if status == "ok" else "[FAIL]"
            elapsed = r.get("elapsed", 0)
            print(f"  {tag:6s} {r['name']:<30s} ({elapsed:.0f}s)")
            if status == "error":
                err_msg = r.get('error', '')
                # Print full error (at least first 500 chars) so scraper failures
                # like B站 timeouts aren't silently lost behind truncated output.
                print(f"         {err_msg[:500]}")
                if len(err_msg) > 500:
                    print(f"         ... ({len(err_msg)} total chars, truncated)")
            # subprocess.run doesn't raise on non-zero exit (no check=True),
            # so we must inspect the CompletedProcess returncode ourselves.
            if status == "ok" and hasattr(r.get("result", None), "returncode"):
                proc = r["result"]
                if proc.returncode != 0:
                    stderr_out = (proc.stderr or "")[:500]
                    print(f"  [WARN] {r['name']} exit code {proc.returncode}")
                    if stderr_out.strip():
                        print(f"         {stderr_out}")

    # ── Restore feedback counters after scraper re-insert ──
    if _counter_map:
        db = get_db()
        try:
            conn = db._connect()
            restored = 0
            for url, (up, down) in _counter_map.items():
                cur = conn.execute(
                    "UPDATE market_news SET useful_count = ?, useless_count = ?"
                    " WHERE url = ? AND date = ?",
                    (up, down, url, date),
                )
                if cur.rowcount:
                    restored += 1
            conn.commit()
            if restored:
                print(f"  [RESTORE] Restored feedback counters for {restored} news items")
        except Exception as e:
            print(f"  [WARN] Counter restore failed: {e}")

    # ── Phase 0.5: Hot Keywords ──
    print("\n── Phase 0.5: Hot Keywords ──")
    try:
        from src.pipeline.hot_tracker import collect_hot_keywords
        kw_result = collect_hot_keywords(date)
        if kw_result.get("keywords"):
            print(f"  [OK] Collected {kw_result['count']} hot keywords"
                  f" from {kw_result.get('sources', [])}")
        else:
            print(f"  [WARN] No hot keywords collected — hot topic section will be skipped")
    except Exception as e:
        print(f"  [WARN] Hot keyword collection failed: {e}")

    # ── Phase 0B: Loader ──
    print("\n── Phase 0B: Loader ──")
    from src.pipeline.loader import import_csv, extract_date_from_filename
    from src.config import settings
    raw_dir = settings.data_raw_dir
    existing_dates = set(get_db().get_available_dates())
    imported = 0
    for f in sorted(raw_dir.glob("*.csv")):
        try:
            file_date = extract_date_from_filename(str(f))
            if file_date not in existing_dates:
                n = import_csv(str(f), date=file_date)
                imported += n.get("imported", 0)
            else:
                print(f"    [SKIP] {f.name} — date {file_date} already in DB")
        except Exception as e:
            print(f"    [WARN] {f.name}: {e}")
    print(f"  Loader: {imported} new records imported")

    # ── Phase 0C: Cleanup old CSVs ──
    # Delete CSV files that don't match the target date.
    # Data is already in the DB, so these are just clutter.
    deleted = 0
    for f in sorted(raw_dir.glob("*.csv")):
        try:
            file_date = extract_date_from_filename(str(f))
            if file_date != date:
                f.unlink()
                deleted += 1
        except ValueError:
            # Files without a recognizable date — keep them (might be test output)
            pass
    if deleted:
        print(f"  Cleanup: removed {deleted} old CSV(s) from {raw_dir}")


def run_hot_only(date: str, push_chat_id: str | None = None) -> dict[str, Any]:
    """Afternoon hot-topic refresh — re-collect keywords, re-search, push standalone card.

    Unlike the full pipeline, this only touches hot-topic phases and does not
    re-scrape or re-run Differ / StoryPicker / Briefer.

    Returns:
        {"date": date, "keywords": [...], "hot_items": [...], "pushed": bool}
    """
    from datetime import datetime as _dt
    from src.pipeline.hot_tracker import collect_hot_keywords, search_hot_topics
    from src.agents.render import build_hot_topics_md, build_hot_topic_elements

    print(f"\n{'='*50}")
    print(f"  🔥 Hot-Only Update: {date}")
    print(f"{'='*50}")

    db = get_db()
    try:
        with db._connect() as conn:
            conn.execute("DELETE FROM hot_topic_news WHERE date = ?", (date,))
            conn.commit()
        print("  [CLEAN] Cleared today's hot topic results")
    except Exception as e:
        print(f"  [WARN] Failed to clear today's hot topics: {e}")

    # ── Phase 0.5: Re-collect hot keywords (afternoon trends differ from morning) ──
    print("\n── Hot Keywords (re-collect) ──")
    kw_result = collect_hot_keywords(date)
    keywords = kw_result.get("keywords", [])
    if not keywords:
        print("  [WARN] No hot keywords collected — aborting hot-only update")
        return {"date": date, "keywords": [], "hot_items": [], "pushed": False}
    print(f"  [OK] {kw_result['count']} keywords from {kw_result.get('sources', [])}")
    if keywords:
        kw_tags = " · ".join(k["keyword"] for k in keywords[:5])
        print(f"       {kw_tags}")

    # ── Phase 1.5: Re-search (force to bypass morning cache) ──
    print("\n── Hot Topic Search (force re-search) ──")
    hot_result = search_hot_topics(date, force=True)
    total_found = hot_result.get("total_found", 0)
    print(f"  [OK] Found {total_found} articles across"
          f" {hot_result.get('keywords_searched', 0)} keywords")
    if hot_result.get("warnings"):
        for w in hot_result["warnings"]:
            print(f"  [WARN] {w}")

    # ── Read AI-selected items from DB ──
    hot_items = db.get_hot_topic_news_by_date(date, selected=True, limit=7)
    hot_keyword_names = [k["keyword"] for k in keywords]

    if not hot_items:
        # Fallback: unselected items by search order
        hot_items = db.get_hot_topic_news_by_date(date, selected=False, limit=7)
        print(f"  [INFO] No AI-selected items, using search-order fallback ({len(hot_items)} items)")

    # ── Print selected items to terminal ──
    if hot_items:
        print(f"\n── Selected Hot Topics ({len(hot_items)} items) ──")
        for i, item in enumerate(hot_items, 1):
            headline = item.get("headline", "") or item.get("title", "") or "(no title)"
            source = item.get("source", "") or item.get("search_engine", "")
            score = item.get("value_score", "?")
            summary = item.get("ai_summary", "") or item.get("snippet", "") or ""
            url = item.get("url", "") or "(no url)"
            summary_short = summary[:100] + "…" if len(summary) > 100 else summary
            print(f"  {i}. [{score}] {headline}")
            print(f"     {source} — {summary_short}")
            print(f"     URL: {url}")

    # ── Build standalone hot-topic card ──
    now = _dt.now().strftime("%H:%M")
    hot_md = build_hot_topics_md(hot_items, hot_keyword_names) if hot_items else ""
    hot_elements = build_hot_topic_elements(hot_md, hot_items, date=date) if hot_items else []

    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"🕐 更新于 {now} · 关键词重新采集 · 搜索强制刷新",
        },
    ]
    elements.extend(hot_elements)

    card_data: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"🔥 午间热点速报 | {date}"},
                "template": "red",
            },
            "elements": elements,
        },
    }

    pushed = False
    if push_chat_id and (hot_items or hot_md):
        print(f"\n── Push ──")
        from src.feishu.pusher import push_card
        push_result = push_card(card_data, push_chat_id)
        if push_result.get("success"):
            print(f"  [OK] Hot-only card pushed to {push_chat_id}")
            pushed = True
        else:
            print(f"  [FAIL] Push failed: {push_result.get('error', 'unknown')}")

    print(f"\n{'='*50}")
    print(f"  Hot-Only complete: {len(hot_items)} items, pushed={pushed}")
    print(f"{'='*50}")

    return {
        "date": date,
        "keywords": keywords,
        "hot_items": hot_items,
        "pushed": pushed,
    }


def _print_deep_research_report(result: dict[str, Any]) -> None:
    """Pretty-print a Deep Research report to the terminal.

    Shows the 500-word markdown report prominently, followed by
    key findings, confidence, and citations.
    """
    if not result.get("success"):
        print(f"\n[FAIL] Deep Research failed: {result.get('error', 'unknown')}")
        return

    report_md = result.get("report_md", "")
    key_findings = result.get("key_findings", [])
    citations = result.get("citations", [])
    confidence = result.get("confidence", "medium")
    cached = result.get("cached", False)
    topic = result.get("topic", "")

    confidence_label = {"high": "🟢 高", "medium": "🟡 中", "low": "🔴 低"}.get(
        confidence, f"🟡 {confidence}"
    )

    print()
    print("=" * 64)
    print(f"  🔬 深度研究报告")
    print(f"  话题: {topic}")
    print(f"  置信度: {confidence_label}" + ("  (缓存)" if cached else ""))
    print("=" * 64)
    print()
    print(report_md)
    print()

    if key_findings:
        print("─" * 48)
        print("  💡 核心发现")
        for i, f in enumerate(key_findings, 1):
            print(f"    {i}. {f}")
        print()

    if citations:
        print("─" * 48)
        print(f"  📎 引用来源 ({len(citations)} 条)")
        for i, c in enumerate(citations[:10], 1):
            url = c.get("url", "")
            title = c.get("title", "") or url
            verified = "✅" if c.get("verified") else "⏳"
            print(f"    {i}. {verified} {title}")
            if url:
                print(f"       {url}")
        print()

    print("─" * 48)
    print(f"  report_id: {result.get('report_id', 'N/A')}")
    if result.get("push_success"):
        print(f"  已推送到飞书 ✅")
    print("=" * 64)


# ═════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Run full daily pipeline")
    parser.add_argument("--date", type=str, default=None, help="Date YYYY-MM-DD (default: today)")
    parser.add_argument("--force", action="store_true", help="Re-run all steps")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--brief-only", action="store_true", help="Print only the final card")
    parser.add_argument("--scrape", action="store_true", help="Auto-scrape data before running")
    parser.add_argument("--skip", type=str, default="", help="Comma-separated scrapers to skip (e.g. 'diandian_batch,news_feeds')")
    parser.add_argument("--push", type=str, default=None, metavar="CHAT_ID",
                        help="Push card to Feishu chat after completion")
    parser.add_argument("--calibrate", action="store_true",
                        help="Run Calibrator agent (feedback-driven scoring parameter tuning)")
    parser.add_argument("--calibrate-days", type=int, default=14,
                        help="Days of feedback to analyze for calibration (default 14)")
    parser.add_argument("--hot-only", action="store_true",
                        help="Afternoon hot-topic refresh only (re-collect keywords, re-search, push standalone card)")
    parser.add_argument("--deep-research", type=str, default=None, metavar="QUESTION",
                        help="Run Deep Research Agent on a topic (e.g. 'AI+游戏 2026趋势')")
    args = parser.parse_args()

    date_arg = args.date
    if date_arg is None:
        from datetime import date as dt_date
        date_arg = dt_date.today().strftime("%Y-%m-%d")

    # ── Hot-Only (standalone afternoon refresh) ──
    if args.hot_only:
        result = run_hot_only(date_arg, push_chat_id=args.push)
        if args.brief_only:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        sys.exit(0)

    # ── Deep Research (standalone — runs instead of pipeline) ──
    if args.deep_research:
        from src.agents.deep_researcher import run_deep_research
        dr_result = run_deep_research(
            question=args.deep_research,
            date=date_arg,
            push_chat_id=args.push,
            verbose=args.verbose,
        )
        if args.brief_only:
            print(json.dumps(dr_result, ensure_ascii=False, indent=2, default=str))
        else:
            _print_deep_research_report(dr_result)
        sys.exit(0 if dr_result.get("success") else 1)

    # ── Calibrator (standalone — runs instead of pipeline) ──
    if args.calibrate:
        if args.scrape or args.push or args.skip:
            print(
                "[WARN] --calibrate runs standalone. "
                "--scrape / --push / --skip flags are ignored.",
                file=sys.stderr,
            )
        from src.agents.calibrator import run_calibrator
        calib_result = run_calibrator(
            days=args.calibrate_days,
            end_date=date_arg,
            verbose=args.verbose,
        )
        print(json.dumps(calib_result, ensure_ascii=False, indent=2, default=str))
        sys.exit(0)

    # ── Phase 0: Scrape + Load (optional) ──
    if args.scrape:
        skip_list = [s.strip() for s in args.skip.split(",") if s.strip()]
        _run_phase0_scrape(date_arg, skip=skip_list)

    result = run_pipeline(date_arg, force=args.force, verbose=args.verbose)

    # ── Fatal check ──
    if result.get("fatal"):
        print("\n[FATAL] Pipeline failed — see warnings above for details.", file=sys.stderr)
        sys.exit(1)

    # ── Phase 5: Push (optional) ──
    if args.push:
        print(f"\n── Phase 5: Push ──")
        from src.feishu.pusher import push_daily_card, push_card

        db = get_db()
        report = db.get_analysis_report(date_arg)
        if report and report.get("brief_card_json"):
            card_data = json.loads(report["brief_card_json"])
            card = card_data.get("card", card_data)
            push_result = push_card(card, args.push)
        else:
            push_result = push_daily_card(args.push, date_arg)

        if push_result.get("success"):
            print(f"  [OK] Pushed to {args.push}")
        else:
            print(f"  [FAIL] Push failed: {push_result.get('error', 'unknown')}")

    if args.brief_only:
        print(json.dumps(result["card"], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result["card"], ensure_ascii=False, indent=2, default=str))
