"""查看已存储的日报。用法: python view_reports.py [日期]"""
import sys
sys.path.insert(0, ".")
from src.storage.sqlite import get_db

db = get_db()
date_filter = sys.argv[1] if len(sys.argv) > 1 else None

with db._connect() as conn:
    sql = "SELECT date, new_games_md, market_md, ranking_md FROM analysis_reports"
    params = ()
    if date_filter:
        sql += " WHERE date = ?"
        params = (date_filter,)
    sql += " ORDER BY date"

    for r in conn.execute(sql, params):
        print(f"{'='*60}")
        print(f"  {r['date']}")
        print(f"{'='*60}")
        print()
        if r['new_games_md']:
            print("🆕 新游关注")
            print(r['new_games_md'])
            print()
        if r['market_md']:
            print("📰 市场变动")
            print(r['market_md'])
            print()
        if r['ranking_md']:
            print("📊 排名变动")
            print(r['ranking_md'])
            print()
