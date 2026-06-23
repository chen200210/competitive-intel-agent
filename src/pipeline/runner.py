"""
Pipeline runner — orchestrates the full daily report flow.

New flow (2026-06-22):
  Phase 0A: Scrape (parallel) → 4 scrapers
  Phase 0B: Loader → all CSV → DB
  Phase 0C: Track Filter → tag all games
  Phase 1:  Differ → StoryPicker → CrossChart (pure rules)
  Phase 2B: OverviewScanner → Researcher ‖ Verifier (track games only)
  Phase 3:  DesignAnalyst (no risk_mirror)
  Phase 4:  Briefer (reads DB directly + all agent outputs)
  Phase 5:  Push → Feishu card

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


def _run_parallel(tasks: list[tuple[str, Any, tuple, dict]], max_workers: int = 8) -> list[dict]:
    """Run tasks in parallel. Each task is (name, fn, args, kwargs)."""
    results = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), max_workers)) as ex:
        futures = {ex.submit(fn, *args, **kw): name for name, fn, args, kw in tasks}
        for f in as_completed(futures):
            name = futures[f]
            try:
                f.result()
                results.append({"name": name, "status": "ok"})
            except Exception as e:
                results.append({"name": name, "status": "error", "error": str(e)})
    return results


def run_pipeline(date: str, force: bool = False, verbose: bool = False) -> dict[str, Any]:
    """Run the full daily pipeline for a given date.

    Skips steps that already have results in the DB (unless force=True).
    """
    db = get_db()
    steps: list[dict[str, Any]] = []
    t_total = _time.monotonic()

    def _step(name: str, fn, *args, **kwargs) -> Any:
        t0 = _time.monotonic()
        result = fn(*args, **kwargs)
        ms = int((_time.monotonic() - t0) * 1000)
        status = "ok"
        if isinstance(result, dict) and result.get("error"):
            status = "error"
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
                conn.execute(
                    "DELETE FROM research_results WHERE change_id IN "
                    "(SELECT id FROM changes WHERE date = ?)", (date,)
                )
                conn.execute("DELETE FROM changes WHERE date = ?", (date,))
                conn.commit()
            print("  [FORCE] Deleted existing changes + research for re-run")
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

    # 3. Cross Chart
    from src.pipeline.cross_chart import analyze_cross_chart, get_signals_for_date
    cross_result = _step("Cross Chart", analyze_cross_chart, date)
    cross_signals = get_signals_for_date(date)
    if verbose:
        print(f"         signals={cross_result.get('signals_found', 0)}")

    # 3b. Track Filter — filter changes to track-relevant games only
    track_changes = _filter_track_changes(changes)
    if verbose:
        print(f"         track filter: {len(changes)} changes → {len(track_changes)} track-relevant")

    # Filter cross-chart signals to only track-relevant games
    track_names = {c.get("game_name", "").lower() for c in track_changes}
    cross_signals = [s for s in cross_signals
                     if s.get("game_name", "").lower() in track_names]

    # ═════════════════════════════════════════════════════════════
    # Phase 2: AI Agents (track games only, no Analyst)
    # ═════════════════════════════════════════════════════════════
    print("\n── Phase 2: AI Agents ──")

    # 4. Overview Scanner — only track-relevant changes
    overview = db.get_daily_overview(date)
    if overview and not force:
        recommended = json.loads(overview.get("recommended_focus_json", "[]"))
        _step("Overview Scanner", lambda: {"skipped": True, "hint": f"{len(recommended)} focus items"})
    else:
        from src.agents.overview_scanner import scan
        overview_result = _step(
            "Overview Scanner", scan, date, "iOS", None, track_changes, stories,
            cross_chart_signals=cross_signals, verbose=verbose,
        )
        overview = overview_result
        recommended = overview_result.get("recommended_focus", [])

    # 5. Researcher — parallel for each recommended item
    existing_research = _get_existing_research_bundle_ids(db)
    researcher_tasks: list[tuple[str, Any, tuple, dict]] = []
    for item in recommended:
        bid = item.get("bundle_id", "")
        if bid in existing_research and not force:
            steps.append({
                "name": f"Researcher: {item.get('game_name', bid)}",
                "ms": 0, "status": "skipped",
            })
            continue
        from src.agents.researcher import research
        change_lookup = _find_change_by_bundle_id(changes, bid)
        researcher_tasks.append((
            f"Researcher: {item.get('game_name', bid)}",
            research,
            (change_lookup or item, item.get("reason", ""), False),
            {},
        ))
        existing_research.add(bid)

    if researcher_tasks:
        print(f"\n  -> Running {len(researcher_tasks)} Researchers in parallel...")
        t0 = _time.monotonic()
        res = _run_parallel(researcher_tasks)
        steps.extend(res)
        elapsed = int((_time.monotonic() - t0) * 1000)
        print(f"  <- Researchers done ({elapsed}ms)")

    # 6. Verifier — parallel for each unverified research result
    research_rows = db._connect().execute(
        "SELECT id, findings_json, verified_json FROM research_results"
    ).fetchall()
    verifier_tasks: list[tuple[str, Any, tuple, dict]] = []
    for row in research_rows:
        if row["verified_json"] and not force:
            continue
        try:
            findings = json.loads(row["findings_json"])
            if "parse_error" in findings or not findings.get("findings"):
                continue
        except Exception:
            continue
        from src.agents.verifier import verify
        verifier_tasks.append((
            f"Verifier: {findings.get('game', '?')}", verify,
            (findings,), {"verbose": False},
        ))

    if verifier_tasks:
        print(f"\n  -> Running {len(verifier_tasks)} Verifiers in parallel...")
        t0 = _time.monotonic()
        res = _run_parallel(verifier_tasks)
        steps.extend(res)
        elapsed = int((_time.monotonic() - t0) * 1000)
        print(f"  <- Verifiers done ({elapsed}ms)")

    # ═════════════════════════════════════════════════════════════
    # Phase 3: Design Analyst (no risk_mirror)
    # ═════════════════════════════════════════════════════════════
    print("\n── Phase 3: Design Analyst ──")

    analysis = db.get_analysis_report(date)
    design_done = bool(analysis and analysis.get("design_analysis_json") and not force)
    for row in research_rows:
        try:
            findings = json.loads(row["findings_json"])
            if "parse_error" in findings:
                continue
            has_design = any(
                f.get("design_tags")
                for f in findings.get("findings", [])
            )
            if has_design and not design_done:
                from src.agents.design_analyst import analyze as design_analyze
                _step(
                    f"Design Analyst: {findings.get('game', '?')}",
                    design_analyze, findings, verbose=verbose,
                )
                design_done = True
            elif has_design and design_done:
                _step("Design Analyst", lambda: {"skipped": True, "hint": "already computed"})
                break
        except Exception:
            continue

    # ═════════════════════════════════════════════════════════════
    # Phase 4: Briefer (reads DB directly + all agent outputs)
    # ═════════════════════════════════════════════════════════════
    print("\n── Phase 4: Briefer ──")

    from src.agents.briefer import brief_from_db
    brief_result = _step("Briefer", brief_from_db, date, verbose)
    card = brief_result.get("card", {})

    # Phase 4.5: Card Audit (zero token)
    if card:
        from src.pipeline.audit import audit_card, AuditContext
        audit_ctx = AuditContext(
            taptap_games=db.get_taptap_games_by_date(date),
            steam_ports=db.get_steam_ports_by_date(date),
            market_news=db.get_market_news_by_date(date),
            unreleased_games=db.get_unreleased_games_by_date(date),
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
    # Summary
    # ═════════════════════════════════════════════════════════════
    total_ms = int((_time.monotonic() - t_total) * 1000)
    total_s = total_ms / 1000
    ai_steps = [s for s in steps if s["status"] != "skipped" and s["name"] not in
                ("Differ", "Story Picker", "Cross Chart")]
    skipped = sum(1 for s in steps if s["status"] == "skipped")

    print(f"\n{'='*50}")
    print(f"  Pipeline complete: {total_s:.1f}s total")
    print(f"  Steps: {len(steps)} ({skipped} skipped)")
    if ai_steps:
        ai_ms = sum(s.get("ms", 0) for s in ai_steps)
        print(f"  AI cost: {ai_ms/1000:.1f}s ({len(ai_steps)} agent calls)")
    print(f"{'='*50}")

    return {
        "date": date,
        "total_ms": total_ms,
        "steps": steps,
        "card": card,
    }


# ═════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════

def _filter_track_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter changes to track-relevant games.

    Uses track_filter.classify_game() with available data, falling back
    to keyword matching when genre/tags are unavailable.
    """
    try:
        from src.pipeline.track_filter import classify_game
    except ImportError:
        return changes

    filtered = []
    for c in changes:
        game_name = c.get("game_name", "")
        if not game_name:
            continue
        # Try with whatever data we have
        genre = c.get("category", "")
        label = classify_game(game_name, genre=genre)
        if label == "track":
            filtered.append(c)
    return filtered


def _get_existing_research_bundle_ids(db) -> set[str]:
    """Get bundle_ids that already have research results."""
    ids: set[str] = set()
    rows = db._connect().execute(
        "SELECT findings_json FROM research_results"
    ).fetchall()
    for row in rows:
        try:
            data = json.loads(row["findings_json"])
            bid = data.get("bundle_id", "")
            if bid and "parse_error" not in data:
                ids.add(bid)
        except Exception:
            pass
    return ids


def _find_change_by_bundle_id(
    changes: list[dict[str, Any]], bundle_id: str
) -> dict[str, Any] | None:
    for c in changes:
        if c.get("bundle_id") == bundle_id:
            return c
    return None


def _resolve_tap_urls(track_changes: list[dict[str, Any]]) -> None:
    """Resolve TapTap app URLs for track games missing them.

    Checks taptap_new_games + kv_cache first, only runs Playwright
    for games without cached URLs.  One-time cost per game.
    """
    try:
        from src.storage.sqlite import get_db
        db = get_db()

        # Collect known URLs
        rows = db._connect().execute(
            "SELECT game_name, taptap_url FROM taptap_new_games WHERE taptap_url != ''"
        ).fetchall()
        known_urls: dict[str, str] = {r["game_name"]: r["taptap_url"] for r in rows}
        rows2 = db._connect().execute(
            "SELECT key, value FROM kv_cache WHERE key LIKE 'taptap_url:%'"
        ).fetchall()
        for r in rows2:
            known_urls[r["key"].replace("taptap_url:", "")] = r["value"]

        missing = [c.get("game_name", "") for c in track_changes
                   if c.get("game_name", "") and c["game_name"] not in known_urls
                   and "/app/" not in known_urls.get(c["game_name"], "")]

        if not missing:
            return

        print(f"\n  Resolving TapTap URLs for {len(missing)} games...")
        from src.tools.taptap_resolver import resolve_taptap_url
        for i, name in enumerate(missing):
            url = resolve_taptap_url(name)
            status = "✅" if url else "❌"
            print(f"    [{i+1}/{len(missing)}] {status} {name[:40]}")
    except Exception:
        pass  # URL resolution is optional — don't block pipeline


# ═════════════════════════════════════════════════════════════
# Phase 0: Scrape (CLI helper)
# ═════════════════════════════════════════════════════════════

def _run_phase0_scrape() -> None:
    """Run all scrapers in parallel, then import all CSVs."""
    import subprocess

    project_root = Path(__file__).resolve().parent.parent.parent
    scrapers_dir = project_root / "tools" / "scrapers"

    scraper_scripts = [
        ("diandian_batch.py", ["--platform", "ios"]),
        ("taptap_new_games.py", []),
        ("steam_ports.py", []),
        ("news_feeds.py", []),
    ]

    print("\n── Phase 0A: Scrape (parallel) ──")
    scrape_tasks: list[tuple[str, Any, tuple, dict]] = []
    for script, extra_args in scraper_scripts:
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
            print(f"  {tag:6s} {r['name']}")
            if status == "error":
                print(f"         {r.get('error', '')[:120]}")

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


# ═════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Run full daily pipeline")
    parser.add_argument("--date", type=str, default=None, help="Date YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="Re-run all steps")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--brief-only", action="store_true", help="Print only the final card")
    parser.add_argument("--scrape", action="store_true", help="Auto-scrape data before running")
    parser.add_argument("--push", type=str, default=None, metavar="CHAT_ID",
                        help="Push card to Feishu chat after completion")
    args = parser.parse_args()

    # ── Phase 0: Scrape + Load (optional) ──
    if args.scrape:
        _run_phase0_scrape()

    date_arg = args.date
    if date_arg is None:
        db = get_db()
        dates = db.get_available_dates()
        if not dates:
            print("No data. Import CSV first or use --scrape.")
            sys.exit(1)
        date_arg = dates[0]

    result = run_pipeline(date_arg, force=args.force, verbose=args.verbose)

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
