"""
Competitive Intelligence Agent — FastAPI application entry point.

Run with:
    python run.py
    python run.py --task daily-report --date 2026-06-16

For now (Week 1): manual CSV import + diff pipeline.
"""

import sys
import argparse
from pathlib import Path


def cmd_import(args: argparse.Namespace) -> None:
    """Import a CSV/Excel file into the database."""
    from src.pipeline.loader import import_file

    file_path = args.file
    date = args.date  # optional override
    chart_type = args.chart_type  # optional override
    result = import_file(file_path, date, chart_type)
    print(result)


def cmd_diff(args: argparse.Namespace) -> None:
    """Run Differ for a given date."""
    import json
    from src.pipeline.differ import diff_with_yesterday

    result = diff_with_yesterday(args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def cmd_stories(args: argparse.Namespace) -> None:
    """Run Story Picker for a given date."""
    import json
    from src.pipeline.story_picker import pick_stories_for_date

    result = pick_stories_for_date(args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the FastAPI server."""
    import uvicorn
    from src.config import settings

    print(f"Starting server at http://{settings.host}:{settings.port}")
    uvicorn.run("src.main:app", host=settings.host, port=settings.port, reload=True)


def cmd_test(args: argparse.Namespace) -> None:
    """Quick sanity check: import + diff if data available."""
    from src.storage.sqlite import get_db

    db = get_db()
    dates = db.get_available_dates()
    print(f"Database: {db.db_path}")
    print(f"Available dates: {dates}")

    if len(dates) >= 2:
        print(f"\nRunning diff for latest date: {dates[0]}")
        from src.pipeline.differ import diff_with_yesterday
        import json
        result = diff_with_yesterday(dates[0])
        print(json.dumps({
            "date": result["date"],
            "prev_date": result.get("prev_date"),
            "day_type": result["day_type"],
            "overview": result["overview"],
            "change_count": len(result["changes"]),
        }, ensure_ascii=False, indent=2))
    else:
        print("Need at least 2 days of data to run diff. Import CSV files first.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Competitive Intelligence Agent System"
    )
    sub = parser.add_subparsers(dest="command")

    # import
    p_import = sub.add_parser("import", help="Import a CSV/Excel file")
    p_import.add_argument("--file", required=True, help="Path to CSV/Excel file")
    p_import.add_argument("--date", help="Date override (YYYY-MM-DD)")
    p_import.add_argument("--chart-type", help="Chart type override (热门榜/免费榜/...)")

    # diff
    p_diff = sub.add_parser("diff", help="Run Differ for a date")
    p_diff.add_argument("--date", required=True, help="Date to diff (YYYY-MM-DD)")

    # stories
    p_stories = sub.add_parser("stories", help="Run Story Picker for a date")
    p_stories.add_argument("--date", required=True, help="Date to pick stories for")

    # serve
    sub.add_parser("serve", help="Start FastAPI server")

    # test (default)
    sub.add_parser("test", help="Quick sanity check")

    args = parser.parse_args()

    if args.command == "import":
        cmd_import(args)
    elif args.command == "diff":
        cmd_diff(args)
    elif args.command == "stories":
        cmd_stories(args)
    elif args.command == "serve":
        cmd_serve(args)
    else:
        cmd_test(args)


if __name__ == "__main__":
    main()
