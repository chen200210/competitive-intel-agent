"""Audit script for daily report quality — reads current DB schema."""
import json, sys, re
from collections import Counter
from src.storage.sqlite import get_db

db = get_db()
dates = db.get_available_dates()
if not dates:
    print("NO_DATA")
    sys.exit(1)

latest = dates[0]
print(f"DATE: {latest}")

conn = db._connect()
conn.row_factory = __import__("sqlite3").Row

# ── data freshness ──
checks = [
    ("rankings",    "SELECT COUNT(*) as n FROM rankings WHERE date=?"),
    ("taptap",      "SELECT COUNT(*) as n FROM taptap_new_games WHERE date=?"),
    ("steam",       "SELECT COUNT(*) as n FROM steam_port_games WHERE date=?"),
    ("market_news", "SELECT COUNT(*) as n FROM market_news WHERE date=?"),
    ("bilibili",    "SELECT COUNT(*) as n FROM bilibili_videos WHERE date=?"),
]
for label, sql in checks:
    row = conn.execute(sql, (latest,)).fetchone()
    print(f"SOURCE: {label} = {row['n']}")

pg = conn.execute(
    "SELECT COUNT(*) as n FROM market_news WHERE date=? AND source LIKE '%pocketgamer%'",
    (latest,),
).fetchone()[0]
print(f"SOURCE: pocketgamer_biz = {pg}")

# ── source diversity ──
ar = conn.execute(
    "SELECT new_games_md, market_md, ranking_md FROM analysis_reports WHERE date=?",
    (latest,),
).fetchone()

if ar and ar["market_md"]:
    market = ar["market_md"] or ""
    sources = re.findall(r"— ([A-Za-z0-9.]+|[一-鿿]+)", market)
    sc = Counter(sources)
    print(f"SOURCE_DIST: {json.dumps(dict(sc), ensure_ascii=False)}")
    top = sc.most_common(1)
    if top:
        print(f"TOP_SOURCE: {top[0][0]} = {top[0][1]}")
    has_new = bool(ar["new_games_md"])
    has_market = bool(market)
    has_ranking = bool(ar["ranking_md"])
    print(f"SECTIONS: new={has_new} market={has_market} ranking={has_ranking}")
else:
    print("SOURCE_DIST: {}")
    print("SECTIONS: new=False market=False ranking=False")

# ── track coverage ──
track_changes = conn.execute(
    "SELECT COUNT(*) as n FROM changes WHERE date=?",
    (latest,),
).fetchone()[0]
track_news = conn.execute(
    "SELECT COUNT(*) as n FROM market_news WHERE date=? AND track_relevant=1",
    (latest,),
).fetchone()[0]
print(f"TRACK_CHANGES: {track_changes}")
print(f"TRACK_NEWS: {track_news}")

# ── cross-lang risk ──
cn = conn.execute(
    "SELECT COUNT(*) as n FROM market_news WHERE date=? AND source NOT LIKE '%pocketgamer%'",
    (latest,),
).fetchone()[0]
en = conn.execute(
    "SELECT COUNT(*) as n FROM market_news WHERE date=? AND source LIKE '%pocketgamer%'",
    (latest,),
).fetchone()[0]
print(f"CROSS_LANG: CN={cn} EN={en}")

# ── pushed count + non-game (scan final market_md, not raw table) ──
if ar and ar["market_md"]:
    market = ar["market_md"] or ""
    if "暂无新闻" in market or "暂无相关" in market:
        print("PUSHED_NEWS: 0 (placeholder - report not generated or AI returned empty)")
        print("NON_GAME_TOTAL: 0")
    else:
        pushed = len(re.findall(r"→ \[原文\]", market))
        print(f"PUSHED_NEWS: {pushed}")

        non_game_kw = [
            "AirPods", "iPhone", "iPad", "MacBook",
            "电视", "耳机", "音箱", "手表",
            "世界杯", "足球", "NBA", "欧冠",
            "演唱会", "明星", "八卦", "Netflix",
            "电影", "礼包", "广告", "抢号",
        ]
        ng_total = 0
        for kw in non_game_kw:
            if kw.lower() in market.lower():
                print(f"NON_GAME: {kw} 出现在日报中")
                ng_total += 1
        print(f"NON_GAME_TOTAL: {ng_total}")
else:
    print("PUSHED_NEWS: 0")
    print("NON_GAME_TOTAL: 0")

conn.close()
