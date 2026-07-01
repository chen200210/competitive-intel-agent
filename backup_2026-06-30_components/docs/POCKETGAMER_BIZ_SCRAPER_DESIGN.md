# PocketGamer.biz Scraper — 设计文档

> 海外移动游戏商业新闻源集成设计。最后更新：2026-06-24

---

## 一、源站概况

**PocketGamer.biz** 是 Steel Media 旗下的移动游戏商业媒体，聚焦全球移动游戏行业新闻。

| 维度 | 评价 |
|------|------|
| **内容定位** | 移动游戏商业侧：投融资/并购/人事/数据报告/新游Soft Launch/行业趋势 |
| **更新频率** | 日均 7-8 篇，RSS 保留最近 ~50 篇（约 7 天） |
| **内容质量** | 高 — 原创编辑团队，文章有作者署名+行业背景，非通稿堆砌 |
| **与赛道关系** | 间接但高价值 — 虽然不直接报道塔防/肉鸽/割草玩法，但覆盖品类趋势、买量数据、厂商动态 |

### 1.1 分类体系

| 分类 | 占比 | 信号价值 | 示例 |
|------|:----:|:--------:|------|
| **News** | 60% | ⭐⭐⭐ | 公司人事、裁员、新游上线、数据报告 |
| **Features** | 24% | ⭐⭐⭐⭐ | 区域行业报告、深度分析、趋势研判 |
| **Industry Voices** | 8% | ⭐⭐ | 行业人士专栏/观点 |
| **Data** | 6% | ⭐⭐⭐⭐⭐ | AppMagic/Newzoo 收入数据、下载排行 |

---

## 二、技术发现

### 2.1 RSS 源（主入口）

**Feed URL**: `https://www.pocketgamer.biz/index.rss`

标准 RSS 2.0，无需认证，无分页。直接用 `xml.etree.ElementTree` 或 `feedparser` 解析。

**字段映射**:

```
RSS <item>
├── <title>           → headline          标题
├── <link>            → url               原文链接（也是 guid）
├── <guid>            → guid              唯一标识符，去重键
├── <description>     → excerpt           摘要（CDATA，~150-300字，含截断标记 ...[MORE]）
├── <pubDate>         → publish_date      RFC 2822 格式: "Tue, 23 Jun 2026 12:40:00 +0100"
├── <category>        → news_category     News | Features | Industry Voices | Data
├── <media:content>   → image_url        文章配图（660px宽，可升级为 1200px）
└── <enclosure>       → (冗余)           同 media:content
```

**关键特性**:
- `guid` 字段 === `link` 字段，天然唯一键 — 去重用 `reported_items` 表 `item_key=guid`
- `pubDate` 精确到秒，带时区偏移（+0100 = 英国夏令时 BST）
- 无分页 — 一次请求拿全 50 条
- **不支持分类子订阅** — `/news/index.rss`、`/data/index.rss` 等均返回 404，只有主 feed

### 2.2 文章详情页

**深挖路径**（用于 Phase B `_deep_fetch`）:

**路径 A — JSON-LD**（优先）:
```html
<script type="application/ld+json">
[{
  "@type": "Article",
  "headline": "...",
  "datePublished": "2026-06-23T12:40:00+01:00",
  "author": { "@type": "Person", "name": "Aaron Astle", "url": "..." },
  "keywords": ["Hotta Studio", "Gacha", "Revenue", "RPG", ...],
  "image": "https://media.pocketgamer.biz/.../xxx_l1200.jpg",
  "publisher": { "@type": "Organization", "name": "PocketGamer.biz" }
}]
```

**路径 B — HTML 正文**:
- 正文容器: `<div class="body">` → 多个 `<p>` 标签
- 典型长度: ~300-800 英文词（10 段左右）
- 无 paywall，完整公开

### 2.3 与现有源的差异

| 维度 | 国内源（17173/3DM等） | PocketGamer.biz |
|------|----------------------|-----------------|
| **发现方式** | HTML 列表页解析 `<a>` 标签 | RSS 2.0 结构化解析 |
| **去重键** | URL | guid（=== URL） |
| **日期来源** | 从 DOM 父元素/URL 推测 | RSS `<pubDate>` 精确字段 |
| **分类** | 无统一分类 | 4 种明确分类 |
| **作者** | 无 | JSON-LD `author.name` |
| **关键词** | 无 | JSON-LD `keywords[]` |
| **配图** | 无 | RSS `<media:content>` + JSON-LD `image` |
| **正文提取** | `<article>` 或通用 `<p>` | `<div class="body"> > <p>` |
| **语言** | 中文 | 英文（需 AI 摘要时转述为中文） |

---

## 三、Scraper 设计

### 3.1 架构决策

**独立 Scraper，不走 ChartScraper 基类**。

理由：
- `ChartScraper` 的设计假设是"榜单数据"（有 rank/bundle_id/chart_type 等字段），新闻源不匹配
- 现有 `news_feeds.py` 虽然继承了 `ChartScraper`，但实际上大量覆盖了基类行为
- PocketGamer.biz 直接用 RSS 解析，不需要 HTML 列表页解析框架

**继承**: 直接继承 `ChartScraper` 以复用 CSV 输出和 `_sync_to_db` 模式，但简化内部实现。

或者更干净的做法：**写成一个独立模块**，像 `steam_ports.py` 和 `taptap_new_games.py` 那样自包含，只暴露 `run_scrape(date)` 函数，内部直接写 `market_news` 表。

**推荐后者** — 轻量、自包含、零 CSV 依赖。

### 3.2 数据模型

**RSS → 内部 dict → market_news 表**:

```python
@dataclass
class PGNewsItem:
    guid: str              # 唯一标识符 (=URL)
    headline: str          # 标题
    url: str               # 原文链接
    excerpt: str           # RSS description（清理后，去 CDATA 标记）
    publish_date: str      # "2026-06-23" 格式
    category: str          # News | Features | Industry Voices | Data
    image_url: str         # 文章配图
    author: str = ""       # 深挖后填充（JSON-LD）
    keywords: list[str] = []  # 深挖后填充（JSON-LD）
    body_text: str = ""    # 深挖后填充（HTML 正文前 ~500 字）
```

**写入 `market_news` 表**:

```sql
INSERT OR IGNORE INTO market_news
  (date, headline, source, url, category, related_game, track_relevant, publish_date, excerpt, image_url)
VALUES
  (?, ?, 'pocketgamer.biz', ?, ?, '', 0, ?, ?, ?)
```

- `track_relevant` 初始为 0——英文标题无法走中文 track_filter 关键词。赛道相关性由 Summarizer AI 在 Phase C 的四维打分中自然判断（`track_score` 维度）。
- `related_game` 可从 JSON-LD `keywords[]` 中提取（深挖后补充）。

### 3.3 去重策略

三级去重：

| 层级 | 键 | 说明 |
|------|----|------|
| **RSS 层** | `guid` | 同次拉取内去重（RSS 本身不应有重复） |
| **DB 层** | `url` | 跨天去重 — `market_news` 表查已有 URL |
| **管线层** | `reported_items` | `news_seen` 类型（7天 TTL）+ `news` 类型（30天 TTL，top 7 用） |

去重键用 `url` 而非 `guid`——与现有 `news_feeds.py` 的 `_sync_to_db` 一致。

### 3.4 日期筛选

RSS 包含 ~7 天数据。筛选逻辑：

```python
# pubDate 解析: "Tue, 23 Jun 2026 12:40:00 +0100"
# 筛选: publish_date in (today, yesterday)
# 去重: 用 reported_items 表拦截已见条目
```

保留今天+昨天的条目。如果今天条目不足 5 条则扩展到最近 3 天。

### 3.5 深挖正文（可选，与现有 `_deep_fetch` 模式对齐）

Briefer 的 Phase B `_deep_fetch()` 需要原文正文。PocketGamer.biz 深挖：

```python
def deep_fetch(item: dict) -> dict:
    """Fetch article body from PocketGamer.biz article page."""
    url = item["url"]
    resp = httpx.get(url, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # JSON-LD → author + keywords
    import json
    for script in soup.find_all("script", type="application/ld+json"):
        data = json.loads(script.string)
        for block in data if isinstance(data, list) else [data]:
            if block.get("@type") == "Article":
                author = block.get("author", {}).get("name", "")
                item["author"] = author
                item["keywords"] = block.get("keywords", [])
    
    # Body text
    body_div = soup.find("div", class_="body")
    if body_div:
        paragraphs = body_div.find_all("p")
        body_text = " ".join(p.get_text(strip=True) for p in paragraphs)
        item["body_text"] = body_text
    
    return item
```

---

## 四、管线集成

### 4.1 整体流程

```
Phase 0: Scrape
  pocketgamer_biz.run_scrape(date)
    → 拉 RSS (index.rss)
    → 筛选今天+昨天的条目
    → 去重 (market_news 已有 URL 跳过)
    → 写入 market_news 表
    → reported_items 标记 (type=news_seen, TTL 7天)

Phase 1: Briefer brief_from_db()
  → db.get_market_news_by_date(date)  ← 自动包含 pocketgamer.biz 条目
  → _compact_news() 规则粗筛      ← _score_news_item() 需要加 pocketgamer 来源权重
  → _deep_fetch() 深挖正文        ← 需要新增 pocketgamer.biz 的 fetch 逻辑
  → _ai_summarize_and_judge()     ← AI 看到英文标题+摘要，自然判断赛道相关性
  → 格式化输出
```

### 4.2 改动点清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `tools/scrapers/pocketgamer_biz.py` | **新建** | Scraper 主体 |
| `src/pipeline/runner.py` | 修改 | Phase 0A 并行列表加 `pocketgamer_biz` |
| `src/agents/briefer.py` | 修改 | ① `_score_news_item()` 加 `pocketgamer.biz: 0.35` 来源权重 ② `_deep_fetch()` 分派 pocketgamer.biz URL |
| `src/storage/sqlite.py` | 不改 | `market_news` 表 `source` 字段是字符串，直接存 `pocketgamer.biz` |
| `data/competitor_list.yaml` | 不改 | 英文新闻不需要 track_config |
| `CLAUDE.md` | 修改 | 更新档案表+目录地图 |

### 4.3 来源权重设计

在 `_score_news_item()` 的 `source_weights` 字典中：

```python
"pocketgamer.biz": 0.35,  # 低于游戏陀螺(0.5)、高于3DM(0.3)
```

理由：
- 游戏陀螺 0.5 — 中文深度行业分析，直接对口
- **PocketGamer.biz 0.35** — 英文原创编辑，商业数据导向，但中文译读有距离
- 3DM 0.3 — 偏玩家向新闻
- 17173 0.1 — 偏泛娱乐

### 4.4 深挖分派

`_deep_fetch()` 目前只处理非 B 站的 URL（统一用 BeautifulSoup 抓原文）。PocketGamer.biz 的正文提取逻辑（JSON-LD + `div.body > p`）可以直接嵌入现有分支，通过 URL domain 判断：

```python
if "pocketgamer.biz" in url:
    return _deep_fetch_pocketgamer(item)
else:
    return _deep_fetch_generic(item)  # 现有逻辑
```

---

## 五、实现计划

### Step 1: 创建 Scraper 模块 `tools/scrapers/pocketgamer_biz.py`

- [ ] `run_scrape(date)` — 入口函数
- [ ] `_fetch_rss()` — 拉 RSS，解析 `xml.etree.ElementTree`
- [ ] `_filter_by_date(items, date)` — 按 pubDate 筛选今天+昨天
- [ ] `_clean_description(desc)` — 清理 CDATA 中的 HTML 标签和 `...[MORE]` 截断
- [ ] `_sync_to_db(items, date)` — 写 `market_news` + 去重
- [ ] CLI 入口 `__main__`

### Step 2: 集成到 Runner

- [ ] `runner.py` Phase 0A 并行列表加 `pocketgamer_biz`
- [ ] `--skip pocketgamer_biz` 支持

### Step 3: 适配 Briefer Pipeline

- [ ] `_score_news_item()` 加来源权重
- [ ] `_deep_fetch()` 加 PocketGamer.biz 分派

### Step 4: 验证

- [ ] 单独跑 scraper: `python -m tools.scrapers.pocketgamer_biz`
- [ ] 全链路跑: `python -m src.pipeline.runner --scrape --force -v`
- [ ] 检查日报中是否出现高质量英文新闻摘要

---

## 六、后续扩展

### 6.1 其他海外源（同样适合 RSS/HTML 抓取）

| 源 | Feed/入口 | 价值 |
|----|-----------|------|
| **GamesIndustry.biz** | RSS: `/feed/` | 综合性游戏商业新闻，比 PG.biz 覆盖面更广 |
| **MobileGamer.biz** | HTML 首页 | 移动游戏行业新闻，轻量补充 |
| **Gematsu** | RSS: `/feed/` | 日韩新游宣发情报，与 TapTap 数据互补 |

### 6.2 AI 摘要的中文转述

PocketGamer.biz 是英文内容。Summarizer AI（`summarizer.yaml` prompt）需要在摘要时做中文转述。现有 prompt 已支持中文输出，无需特别改动——只需在 prompt 中加一句提示：

> "对于英文来源的新闻，请用中文输出摘要（3-5句），保留关键数据和专有名词的英文原文。"

---

## 附录 A: RSS Feed 示例

```xml
<item>
  <title>Neverness to Everness steers towards $50m in two months on mobile</title>
  <link>https://www.pocketgamer.biz/neverness-to-everness-steers-towards-50m-in-two-months-on-mobile/</link>
  <guid>https://www.pocketgamer.biz/neverness-to-everness-steers-towards-50m-in-two-months-on-mobile/</guid>
  <description><![CDATA[Supernatural RPG Neverness to Everness is approaching $50 million
    in mobile player spending two months on from release... [MORE]]]></description>
  <pubDate>Tue, 23 Jun 2026 12:40:00 +0100</pubDate>
  <media:content url="https://media.pocketgamer.biz/images/140005/89244/..._l660.jpg" medium="image" />
  <enclosure url="https://media.pocketgamer.biz/images/140005/89244/..._l660.jpg" length="1" type="image/jpeg" />
  <category>Data</category>
</item>
```

## 附录 B: JSON-LD 结构化数据示例

```json
{
  "@type": "Article",
  "headline": "Neverness to Everness steers towards $50m in two months on mobile",
  "datePublished": "2026-06-23T12:40:00+01:00",
  "author": { "@type": "Person", "name": "Aaron Astle" },
  "keywords": ["Hotta Studio", "Perfect World Entertainment", "China", "Gacha", "Revenue", "RPG"],
  "image": "https://media.pocketgamer.biz/images/140005/89244/..._l1200.jpg",
  "publisher": { "@type": "Organization", "name": "PocketGamer.biz" }
}
```
