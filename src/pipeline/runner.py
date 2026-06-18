"""
Pipeline runner — orchestrates the full daily report flow.

Usage:
    python -m src.pipeline.runner --date 2026-06-16
    python -m src.pipeline.runner --date 2026-06-16 --force  # re-run all steps
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
    from concurrent.futures import ThreadPoolExecutor, as_completed
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

    Flow: Differ → Story Picker → Cross Chart → Overview Scanner
          → Researcher → Verifier → Analyst → Design Analyst → Briefer

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

    # ──── Phase 1: Data Pipeline (zero AI cost) ────
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
            # Delete dependent rows before re-running Differ (FK constraint)
            with db._connect() as conn:
                conn.execute("DELETE FROM research_results WHERE change_id IN (SELECT id FROM changes WHERE date = ?)", (date,))
                conn.execute("DELETE FROM changes WHERE date = ?", (date,))
                conn.commit()
            print("  [FORCE] Deleted existing changes + research for re-run")
        from src.pipeline.differ import diff_with_yesterday
        diff_result = diff_with_yesterday(date)
        changes = diff_result.get("changes", [])
    _step("Differ", lambda: diff_result)
    day_type = diff_result.get("day_type", "normal")

    # 2. Story Picker (pure rules, always run — cheap)
    from src.pipeline.story_picker import pick_stories_for_date
    story_result = _step("Story Picker", pick_stories_for_date, date)
    stories = story_result.get("stories", [])
    if verbose and stories:
        for s in stories[:3]:
            print(f"         [{s.get('story_type','?')}] {s.get('story_headline','?')[:60]}")

    # 3. Cross Chart (pure rules, always run)
    from src.pipeline.cross_chart import analyze_cross_chart, get_signals_for_date
    cross_result = _step("Cross Chart", analyze_cross_chart, date)
    cross_signals = get_signals_for_date(date)
    if verbose:
        print(f"         signals={cross_result.get('signals_found', 0)}")

    # ──── Phase 2: AI Agents ────
    print("\n── Phase 2: AI Agents ──")

    # 4. Overview Scanner
    overview = db.get_daily_overview(date)
    if overview and not force:
        recommended = json.loads(overview.get("recommended_focus_json", "[]"))
        _step("Overview Scanner", lambda: {"skipped": True, "hint": f"{len(recommended)} focus items"})
    else:
        from src.agents.overview_scanner import scan
        overview_result = _step("Overview Scanner", scan, date, "iOS", None, changes, stories,
                                cross_chart_signals=cross_signals, verbose=verbose)
        overview = overview_result
        recommended = overview_result.get("recommended_focus", [])

    # 5. Researcher — parallel for each recommended item
    existing_research = _get_existing_research_bundle_ids(db)
    researcher_tasks: list[tuple[str, Any, tuple, dict]] = []
    for item in recommended:
        bid = item.get("bundle_id", "")
        if bid in existing_research and not force:
            steps.append({"name": f"Researcher: {item.get('game_name', bid)}", "ms": 0, "status": "skipped"})
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

    # 7. Analyst (business)
    analysis = db.get_analysis_report(date)
    if analysis and analysis.get("report_json") and not force:
        _step("Analyst (business)", lambda: {"skipped": True, "hint": "already computed"})
    else:
        from src.agents.analyst import analyze_from_db
        _step("Analyst (business)", analyze_from_db, date, "iOS", verbose)

    # 8. Design Analyst — for each research with design_tags
    design_done = False
    if analysis and analysis.get("design_analysis_json") and not force:
        design_done = True
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
                _step(f"Design Analyst: {findings.get('game', '?')}", design_analyze, findings, verbose=verbose)
                design_done = True
            elif has_design and design_done:
                _step("Design Analyst", lambda: {"skipped": True, "hint": "already computed"})
                break
        except Exception:
            continue

    # ──── Phase 3: Briefer ────
    print("\n── Phase 3: Briefer ──")

    from src.agents.briefer import brief_from_db
    brief_result = _step("Briefer", brief_from_db, date, verbose)
    card = brief_result.get("card", {})

    # ──── Summary ────
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


# ── CLI ─────────────────────────────────────────────────────────

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

    # ── Phase 0: Auto-scrape (optional) ──
    if args.scrape:
        print("\n── Phase 0: Scrape ──")
        import subprocess
        batch = Path(__file__).resolve().parent.parent.parent / "tools" / "scrapers" / "diandian_batch.py"
        if batch.exists():
            result = subprocess.run(
                [sys.executable, str(batch), "--platform", "ios"],
                cwd=str(batch.parent.parent.parent),
            )
            if result.returncode != 0:
                print("  [WARN] Scraper exited with non-zero — continuing")
        else:
            print(f"  [SKIP] Scraper not found at {batch}")

        # Auto-import any new CSV files in data/raw/
        print("  Scanning data/raw/ for new files...")
        from src.pipeline.loader import import_csv, extract_date_from_filename
        from src.config import settings
        raw_dir = settings.data_raw_dir
        existing_dates = set(get_db().get_available_dates())
        imported = 0
        for f in sorted(raw_dir.glob("*.csv")):
            try:
                date = extract_date_from_filename(str(f))
                if date not in existing_dates:
                    n = import_csv(str(f), date=date)
                    imported += n.get("imported", 0)
                else:
                    print(f"    [SKIP] {f.name} — date {date} already in DB")
            except Exception as e:
                print(f"    [WARN] {f.name}: {e}")
        print(f"  Loader: {imported} new records imported")

    date_arg = args.date
    if date_arg is None:
        db = get_db()
        dates = db.get_available_dates()
        if not dates:
            print("No data. Import CSV first or use --scrape.")
            sys.exit(1)
        date_arg = dates[0]

    result = run_pipeline(date_arg, force=args.force, verbose=args.verbose)

    # ── Phase 4: Push (optional) ──
    if args.push:
        print(f"\n── Phase 4: Push ──")
        from src.feishu.pusher import push_daily_card, push_card, upload_image

        # Read the card from DB
        db = get_db()
        report = db.get_analysis_report(date_arg)
        if report and report.get("brief_card_json"):
            card_data = json.loads(report["brief_card_json"])
            card = card_data.get("card", card_data)

            # 🆕 Embed game images for sector games
            from src.tools.image_fetch import image_fetch
            from src.feishu.pusher import upload_image as feishu_upload
            from src.agents.briefer import _find_sector_games
            sector = _find_sector_games(date_arg, db.get_changes_by_date(date_arg))
            sector.sort(key=lambda g: g.get("attention_score", 0), reverse=True)
            image_keys = []
            for game in sector[:3]:
                game_name = game.get("game_name", "")
                try:
                    from src.tools.web_search import web_search
                    search_result = json.loads(web_search(f"{game_name} taptap", 3))
                    urls = [r["url"] for r in search_result.get("results", [])
                            if "taptap.cn/app/" in r.get("url", "")]
                    if urls:
                        img_result = json.loads(image_fetch(urls[0]))
                        for img in img_result.get("images", [])[:1]:
                            up = feishu_upload(img["url"])
                            if up.get("success"):
                                image_keys.append(up["image_key"])
                                print(f"  [IMG] {game_name}: OK")
                                break
                except Exception as e:
                    pass  # images are optional — don't block push

            if image_keys:
                elements = card.get("elements", [])
                for key in image_keys[:3]:
                    elements.insert(1, {
                        "tag": "img", "img_key": key,
                        "alt": {"tag": "plain_text", "content": ""},
                    })
                card["elements"] = elements
                print(f"  [IMG] Embedded {len(image_keys)} image(s)")

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
