"""Sync Chinese news CSV to market_news table."""
import csv
from src.storage.sqlite import get_db

db = get_db()
records = []
csv_path = "data/raw/全平台_资讯_20260624.csv"

with open(csv_path, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        h, u = row.get("headline", ""), row.get("url", "")
        if not h or not u:
            continue
        track_str = row.get("track_relevant", "0")
        try:
            track = bool(int(track_str)) if track_str else False
        except (ValueError, TypeError):
            track = False
        records.append({
            "date": "2026-06-24",
            "headline": h,
            "source": row.get("source", ""),
            "url": u,
            "category": row.get("news_category", ""),
            "related_game": row.get("related_game", ""),
            "track_relevant": track,
            "publish_date": row.get("publish_date", ""),
        })

print(f"Parsed: {len(records)} records from CSV")
n = db.insert_market_news_deduped(records, "2026-06-24")
print(f"Inserted: {n} new records")

rows = db._connect().execute(
    "SELECT source, COUNT(*) as cnt FROM market_news WHERE date=? GROUP BY source",
    ("2026-06-24",),
).fetchall()
for r in rows:
    print(f"  {r['source']}: {r['cnt']}")
print(f"Total: {sum(r['cnt'] for r in rows)}")
