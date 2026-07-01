"""Diagnose why Chinese news isn't appearing in briefer output."""
import sys
sys.path.insert(0, ".")

from src.storage.sqlite import get_db
from src.agents.market_pipeline import filter_news
from src.agents.dedup import load_reported_news, load_reported_news_headlines
from src.pipeline.source_constants import is_valid_source, is_bilibili
from datetime import date as dt_date

db = get_db()
target_date = "2026-06-24"

# Load all news
news = db.get_market_news_by_date(target_date)
print(f"Total market_news: {len(news)}")

# Check source distribution
sources = {}
for n in news:
    src = n.get("source", "?")
    sources[src] = sources.get(src, 0) + 1
for s, c in sorted(sources.items(), key=lambda x: -x[1]):
    print(f"  {s}: {c}")

# Check publish_date
no_date = [n for n in news if not n.get("publish_date")]
print(f"\nMissing publish_date: {len(no_date)}")
if no_date:
    for n in no_date[:3]:
        print(f"  [{n.get('source','?')}] {n.get('headline','')[:60]}")

# Check reported URLs
reported_urls = load_reported_news()
reported_headlines = load_reported_news_headlines()
print(f"\nReported URLs: {len(reported_urls)}")
print(f"Reported headline tokens: {len(reported_headlines)}")

# Run filter_news and trace
import re

filtered = 0
blocked_source = 0
blocked_url = 0
blocked_headline = 0
blocked_keywords = 0
blocked_track = 0
blocked_freshness = 0
passed = 0

for n in news:
    source = (n.get("source", "") or "").lower()
    url = (n.get("url", "") or "").lower()

    if not is_valid_source(source):
        blocked_source += 1
        continue

    headline = n.get("headline", "")
    is_bili = is_bilibili(source) or "bilibili" in url
    normalized = re.sub(r'[?#].*$', '', url)

    if normalized and normalized in reported_urls:
        blocked_url += 1
        continue

    from src.agents.dedup import headline_dedup_tokens
    dedup_tokens = headline_dedup_tokens(headline)
    if dedup_tokens and any(t in reported_headlines for t in dedup_tokens):
        blocked_headline += 1
        continue

    if not is_bili:
        news_block_keywords = ["AirPods", "iPhone", "iPad", "MacBook", "Apple Watch",
            "电动滑板车", "电视", "耳机", "音箱", "手表", "Prime Day", "特惠精选",
            "世界杯", "足球", "NBA", "英超", "西甲", "欧冠",
            "演唱会", "张靓颖", "明星", "八卦", "走光", "抄袭",
            "芝麻街", "Netflix", "电影", "预告", "剧透",
            "礼包", "广告", "赛马大会", "抢号",
            "Alienware", "ROG新品", "游戏电脑", "大促", "立省",
            "史低", "新史低", "平史低", "白菜价", "白嫖", "喜加",
            "夏促", "冬促", "春促", "秋促", "打折", "促销",
            "免费领", "限免", "免费玩",
            "捆绑包", "折扣推荐", "史低推荐",
            "音游", "节奏游戏", "Steam新品节", "游戏节",
            "人民币", "美元", "售价", "定价", "价格"]
        if any(kw.lower() in headline.lower() for kw in news_block_keywords):
            blocked_keywords += 1
            continue

    # Freshness
    pub_date = n.get("publish_date", "")
    if not pub_date:
        pub_date = target_date
    from src.agents.market_pipeline import _is_within_days
    if not _is_within_days(pub_date, target_date, days=7):
        blocked_freshness += 1
        continue

    passed += 1

print(f"\nFilter breakdown:")
print(f"  Passed: {passed}")
print(f"  Blocked - source whitelist: {blocked_source}")
print(f"  Blocked - URL dedup: {blocked_url}")
print(f"  Blocked - headline tokens: {blocked_headline}")
print(f"  Blocked - keywords: {blocked_keywords}")
print(f"  Blocked - freshness: {blocked_freshness}")

# Now run actual filter_news
candidates = filter_news(news, target_date=target_date)
print(f"\nfilter_news result: {len(candidates)} candidates")
for c in candidates[:5]:
    print(f"  [{c.get('source','?')}] {c.get('headline','')[:80]}")
