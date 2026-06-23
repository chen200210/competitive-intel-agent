# 竞品简报新格式 — 架构改造设计

> 本文档描述现有系统为支持新简报格式需要做的架构变更。
> 最后更新：2026-06-22

---

## 一、核心设计原则

> **Scraper 抓到的结构化数据，直接展示，不让 AI 经手。AI 只用在两件事上：理解和判断。**

- 新游数据（下载量/评分/tags）→ Scraper 已结构化，Briefer 直接读 DB
- 新闻头条（标题/URL/来源）→ Scraper 已抓好，Briefer 直接展示
- 排名变动 → 纯规则 Pipeline 产出，Briefer 直接取用
- 设计洞察 → 需要理解游戏机制，DesignAnalyst 来做
- 每日概况 → 需要判断哪些值得关注，OverviewScanner + Researcher + Verifier 来做

### 1.1 赛道规则

| 规则 | 说明 |
|------|------|
| 赛道 | 塔防 / 肉鸽 → 必然关注 |
| 可无视 | 女性向 / 二次元 → 排除（除非同时命中赛道） |
| 覆盖规则 | 赛道命中 > 可无视命中。例：明日方舟（塔防+二次元）→ 关注 |

### 1.2 三项新信息源

| 信息源 | 内容 | 触发方式 | 特殊规则 |
|--------|------|----------|----------|
| TapTap 今日新游 | 游戏名、下载量、评分、tags | 每日抓取 | 用户点击后异步跑点点数据搜索 |
| Steam 移植手游 | 移植判定 | 每日检测 | **不论赛道是否沾边都调查** |
| 游侠/17173 头条 | 头条新闻 + 赛道新闻 | 每日抓取 | 仅新闻，不含分析 |

### 1.3 报告新结构

```
📊 今日概况           (保留 — OverviewScanner + Researcher + Verifier，只处理赛道游戏)
🆕 新游关注           (新增 — TapTap新游 + Steam移植 + 未上线新游，Scraper数据直读)
📰 市场变动           (新增 — 游侠/17173头条 + 赛道新闻，Scraper数据直读)
📊 排名变动           (保留 — Pipeline纯规则产出)
🎮 设计洞察           (保留 — DesignAnalyst，去掉风险自审)
🔴 竞争风险           (保留 — 在研公司表)
```

---

## 二、核心新增模块（基础设施）

### 2.1 赛道规则引擎 `src/pipeline/track_filter.py`

**职责**：纯逻辑模块，不消耗 token。所有游戏数据流过时打标。

**接口**：

```python
def classify_game(
    game_name: str,
    genre: str = "",
    tags: list[str] | None = None,
    theme: str = "",
) -> str:
    """返回 'track' | 'neutral' | 'ignored'
    
    优先级:
    1. 赛道关键词命中 → 'track' (无论是否命中可无视)
    2. 可无视关键词命中 → 'ignored' (除非步骤1已命中)
    3. 其他 → 'neutral'
    """

def filter_games(games: list[dict]) -> dict[str, list[dict]]:
    """分类: {'track': [...], 'neutral': [...], 'ignored': [...]}"""

def should_include(game: dict) -> bool:
    """是否应进入简报 (track=True, neutral=True, ignored=False)"""
```

**关键词配置**（来自 `competitor_list.yaml`）：

```yaml
track_config:
  genres: ["塔防", "TD", "Tower Defense", "肉鸽", "Roguelike", "Roguelite"]
  ignored_categories: ["女性向", "二次元", "乙女"]
  track_overrides_ignore: true
  steam_port_always_include: true
```

### 2.2 四个 Scraper

| Scraper | 输出 | 模式 |
|---------|------|------|
| `diandian_batch.py` | 点点 iOS+Android 全 10 榜 CSV | 模式 B (Playwright) |
| `taptap_new_games.py` | TapTap 今日新游 CSV | 模式 A (ChartScraper) |
| `steam_ports.py` | Steam 移植手游 CSV | 模式 A (ChartScraper) |
| `news_feeds.py` | 游侠/17173 头条 CSV | 模式 A (ChartScraper) |

### 2.3 点点数据按需搜索 `tools/diandian_search.py`

**职责**：独立脚本，用户点击飞书卡片 "🔍 查点点数据" 按钮时触发。结果缓存 7 天。

**触发链路**：

```
用户点击 "🔍 查点点数据"
  → 飞书回调 (action callback)
  → bot.py 解析 game_name
  → 异步调用 diandian_search.py
  → 飞书回复结果卡片
```

---

## 三、修改现有模块

### 3.1 `data/competitor_list.yaml`

追加 `track_config` 区块（同 2.1）。

### 3.2 `src/storage/sqlite.py`

追加 5 张新表 DDL + CRUD：
- `taptap_new_games` — TapTap 每日新游
- `steam_port_games` — Steam 移植手游
- `market_news` — 市场新闻
- `diandian_search_cache` — 点点搜索缓存
- `unreleased_games` — 未上线新游追踪

### 3.3 `src/agents/overview_scanner.py`

**改动**：只处理赛道游戏，不再扫全量。

- Scanner 不再从所有 changes 中选 5-8 条，而是只接收 track_relevant=true 的变动
- 去掉行业大环境新闻搜索（news_feeds 已覆盖）
- 保留 cross_chart_context（跨榜信号对赛道游戏仍然有价值）
- recommended_focus 数量按赛道游戏实际数量动态调整

### 3.4 `src/agents/design_analyst.py` + `prompts/design_analyst.yaml`

**改动**：
- 删除风险自审（risk_mirror）维度——风险判断由人来做
- 删除商业化分析（monetization_deep_dive）维度——不做商业化调查

**保留的维度**：核心玩法亮点、留存机制、可借鉴点、竞争差异、赛道可行性、竞争风险评估、题材热度趋势、市场验证信号

### 3.5 搜索方案

**问题**：通用搜索引擎（Tavily/Bing/搜狗）对中国游戏 query 效果差，返回词典释义而非游戏信息。

**方向**：用定向站点抓取替代通用搜索。Researcher 需要的信息（版本更新、玩法、玩家评价）都可以从 TapTap 游戏页直接获取——页面是 SSR 的，httpx 就能读。不需要搜全网。

**当前引擎优先级**：搜狗 → Bing → DDG（Tavily 额度已用完）

### 3.6 `prompts/briefer.yaml` + `src/agents/briefer.py`

**重大改动**：Briefer 从 "融合 Agent 输出的路由" 变成 "融合一切数据源的排版引擎"。

**旧逻辑**：只接收 Agent 输出（overview / business_analysis / design_analysis）→ 拼成卡片

**新逻辑**：
1. 直接从 DB 读取 Scraper 数据（TapTap 新游 / Steam 移植 / 市场新闻）
2. 接收 Pipeline 产出（排名变动 / 跨榜信号）
3. 接收 Agent 产出（OverviewScanner / Researcher / Verifier / DesignAnalyst）
4. 按新格式六板块组装卡片

```python
def brief_from_db(date: str, verbose: bool = False) -> dict:
    db = get_db()
    
    # ── Scraper 数据（直读 DB，不经 AI）──
    taptap_games = db.get_taptap_games_by_date(date)      # 🆕 新游关注
    steam_ports = db.get_steam_ports_by_date(date)         # 🆕 新游关注
    unreleased = db.get_unreleased_games_by_date(date)     # 🆕 新游关注
    market_news = db.get_market_news_by_date(date)          # 🆕 市场变动
    
    # ── Pipeline 产出（纯规则）──
    changes = db.get_changes_by_date(date)
    cross_signals = db.get_cross_chart_signals(date) or []
    
    # ── Agent 产出（有 token）──
    overview = db.get_daily_overview(date)
    design_analysis = _run_design_analyst_if_needed(date)  # Phase 3
    
    return brief(
        date=date,
        taptap_games=taptap_games,
        steam_ports=steam_ports,
        unreleased=unreleased,
        market_news=market_news,
        changes=changes,
        cross_signals=cross_signals,
        overview=overview,
        design_analysis=design_analysis,
        verbose=verbose,
    )
```

**新卡片板块模板**：

```
📊 今日概况    — OverviewScanner 产出（赛道游戏推荐 + 跨榜信号）
🆕 新游关注    — TapTap 新游(下载量/评分/tags) + Steam移植 + 未上线新游
📰 市场变动    — 游侠/17173 头条 + 赛道关键词匹配的新闻
📊 排名变动    — Pipeline 纯规则产出（精简展示）
🎮 设计洞察    — DesignAnalyst 产出（无 risk_mirror）
🔴 竞争风险    — 在研公司表
```

### 3.6 `src/pipeline/runner.py`

**流程重构**：

```
Phase 0A: Scrape（并行，0 token）
├── diandian_batch     → rankings CSV
├── taptap_new_games   → TapTap 新游 CSV
├── steam_ports        → Steam 移植 CSV
└── news_feeds         → 头条新闻 CSV

Phase 0B: Loader（0 token）
    全部 CSV → 入库 (rankings / taptap_new_games / steam_port_games / market_news)

Phase 0C: Track Filter（0 token，纯规则）
    所有游戏打标 → track / neutral / ignored

Phase 1: Differ → StoryPicker → CrossChart（0 token，纯规则，不变）

Phase 2B: OverviewScanner → Researcher ‖ Verifier（有 token）
    只处理赛道游戏（不再跑 Analyst）

Phase 3A: NewsCurator（有 token）🆕
    读取 market_news → 主题聚类 + 今日必读 → NewsBrief

Phase 3B: ImageCurator（有 token）🆕
    从 Researcher 截图选 1-2 张 → 上传飞书 → image_keys

Phase 3C: DesignAnalyst（有 token，删 risk_mirror）

Phase 4: Briefer（有 token）
    直读 DB Scraper 数据 + NewsCurator/ImageCurator/DesignAnalyst 产出 + Pipeline 数据 → 新格式卡片

Phase 5: Push → 飞书卡片

Phase 6: QualityAuditor（每周，有 token）🆕
    回溯一周 Verifier/Scanner/Researcher 质量 → 红绿灯报告
```

### 3.7 `src/feishu/bot.py` + `card_builder.py`

- **bot.py**：新增 `diandian_search` action callback 处理
- **card_builder.py**：新增 `build_new_game_card_entry()` + `build_diandian_search_button()`

---

## 四、数据流全景

```
                         ┌──────────────────────┐
                         │  Phase 0A: Scrape     │
                         │  4 Scraper 并行        │  ← 0 token
                         └───┬──┬──┬──┬─────────┘
                             │  │  │  │
              ┌──────────────┤  │  │  ├──────────────┐
              ▼              ▼  ▼  ▼                 ▼
         rankings.csv   taptap_*.csv   steam_*.csv   news_*.csv
              │              │  │  │                 │
              └──────────────┼──┼──┼─────────────────┘
                             │  │  │
                      ┌──────┴──┴──┴──────┐
                      │  Phase 0B: Loader  │  ← 0 token
                      │  全部 CSV 入库      │
                      └─────────┬──────────┘
                                │
                      ┌─────────┴──────────┐
                      │  Phase 0C: Track    │  ← 0 token
                      │  Filter (打标)       │
                      └─────────┬──────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
              ▼                 ▼                 ▼
    ┌─────────────────┐  ┌──────────┐  ┌──────────────────┐
    │  Phase 1:        │  │  Briefer  │  │  Phase 2B:       │
    │  Differ          │  │  直读 DB  │  │  OverviewScanner  │
    │  StoryPicker     │  │  (Scraper │  │  → Researcher     │
    │  CrossChart      │  │   数据)   │  │  → Verifier       │
    └────────┬────────┘  └─────┬─────┘  └────────┬─────────┘
             │                 │                 │
             │            ┌────┴────┐            │
             │            │TapTap   │            │
             │            │Steam    │            │
             │            │News     │            │
             │            │Unreleased│           │
             │            └─────────┘            │
             │                 │                 │
             └─────────────────┼─────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │  Phase 3: Design     │  ← 有 token
                    │  Analyst (无risk)    │
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    │  Phase 4: Briefer    │  ← 有 token
                    │  融合全部 → 卡片     │
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    │  Phase 5: Push       │  ← 0 token
                    │  飞书推送              │
                    └─────────────────────┘
```

---

## 五、与旧设计的关键区别

| | 旧设计 | 新设计 |
|---|---|---|
| Agent 数量 | 8 个 | **8 个**（OverviewScanner / Researcher / Verifier / DesignAnalyst / Briefer / **NewsCurator / ImageCurator / QualityAuditor**） |
| 砍掉的 Agent | — | NewGameWatcher、MarketNewsScanner、Analyst |
| 新增的 Agent | — | NewsCurator（新闻策展）、ImageCurator（图片管道）、QualityAuditor（质量审计） |
| Scraper 数据路径 | Scraper → Agent → Briefer | Scraper → **Briefer 直读 DB**（NewsCurator 做摘要，Briefer 用摘要） |
| Phase 2B 范围 | 全量 games | **只处理赛道游戏** |
| 老游戏更新 | 独立 Phase | **砍掉**（新闻 Scraper 自然覆盖） |
| 风险自审 | DesignAnalyst 产出 | **砍掉**（人来判断） |
| 新闻展示 | 原文照搬 | **NewsCurator 策展** → 主题聚类 + 今日必读 |
| 卡片图片 | 无 | **ImageCurator 选图上传** → 飞书卡片嵌入 |
| 质量反馈 | 无 | **QualityAuditor 每周审计** → 红绿灯报告 |

> 三个新 Agent 的完整设计见 [`docs/NEW_AGENTS_DESIGN.md`](NEW_AGENTS_DESIGN.md)

---

## 六、不变的文件

| 文件 | 原因 |
|------|------|
| `src/pipeline/loader.py` | CSV 列映射机制兼容 |
| `src/pipeline/differ.py` | 排名对比逻辑不变 |
| `src/pipeline/story_picker.py` | 故事检测规则不变 |
| `src/pipeline/cross_chart.py` | 跨榜分析不变 |
| `src/agents/researcher.py` | 五维调研逻辑不变 |
| `src/agents/verifier.py` | 核验逻辑不变 |
| `src/agents/base.py` | Agent 基类不变 |
| `src/tools/*` | 工具层不变 |
| `src/config.py` | 配置中心不变 |
| `tools/scrapers/base.py` | Scraper 基类不变 |
| `tools/scrapers/diandian_batch.py` | 榜单抓取不变 |
