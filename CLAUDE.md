# CLAUDE.md — 技术速查表

## 目录地图

```
OA/
├── src/
│   ├── agents/          # 6个AI Agent (DeepSeek via OpenAI SDK)
│   │   ├── base.py          # Agent基类: tool-use loop + JSON强制 + Pydantic校验
│   │   ├── overview_scanner.py  # 每日行业新闻搜索 → daily_overviews表
│   │   ├── researcher.py       # 五维调研(事件/玩法/玩家/设计/在研) → research_results表
│   │   ├── verifier.py         # 信息可信度核验 → research_results.verified_json
│   │   ├── analyst.py          # 商业因果推理+7天趋势 → analysis_reports表
│   │   ├── design_analyst.py   # 玩法拆解+值不值得做+竞争风险 → analysis_reports表
│   │   └── briefer.py          # 融合全部分析→飞书卡片JSON → analysis_reports表
│   ├── pipeline/         # 纯规则管道 (零AI成本)
│   │   ├── differ.py          # 对比昨日排名，算rank_change和attention_score
│   │   ├── story_picker.py    # 从变动中识别5类故事(跃升/黑马/暴跌/爬升/异动)
│   │   ├── cross_chart.py     # 跨榜信号检测
│   │   ├── track_filter.py    # 赛道规则引擎(塔防/肉鸽→关注, 女性向/二次元→排除)
│   │   ├── loader.py          # CSV → rankings表
│   │   └── runner.py          # 全流水线编排 (Phase 0→1→2→3→4)
│   ├── feishu/           # 飞书推送
│   │   ├── pusher.py          # 卡片推送+图片上传
│   │   └── bot.py             # 机器人交互(含点点搜索回调)
│   ├── storage/
│   │   └── sqlite.py          # 所有CRUD + DDL (WAL模式, FK约束)
│   ├── tools/            # Agent可调用的工具函数
│   │   ├── web_search.py / web_fetch.py / image_fetch.py / db_query.py
│   └── config.py             # pydantic-settings, 读.env
├── tools/scrapers/       # 榜单抓取 (独立于src/)
│   ├── base.py               # ChartScraper基类: scrape()→_clean()→_write_csv()
│   ├── diandian_batch.py     # 模式B: Playwright + Chrome profile登录态
│   └── taptap_new_games.py   # 模式A: httpx + Nuxt SSR JSON解析
├── prompts/              # Agent system/user YAML模板 (6个)
├── data/
│   ├── raw/                  # 抓取CSV落这里
│   ├── intel.db              # SQLite主库
│   └── competitor_list.yaml  # 竞品列表 + track_config
└── docs/                 # 设计文档(NEW_REPORT_ARCHITECTURE.md等)
```

## 流水线阶段

```
Phase 0A: Scrape  — 并行抓取 (diandian_batch ‖ taptap_new_games ‖ steam_ports ‖ news_feeds)
Phase 0B: Loader  — CSV导入 (rankings + taptap + steam + news 表)
Phase 0C: Track   — 赛道规则打标 (track_filter, 纯规则, 0 token)
Phase 1:  Differ → StoryPicker → CrossChart (纯规则, 0 token)
Phase 2B: OverviewScanner → Researcher ‖ Verifier (只处理赛道游戏)
Phase 3:  DesignAnalyst (玩法设计分析, 不做商业化)
Phase 4:  Briefer (新六板块格式, 直读DB Scraper数据)
Phase 5:  Push → 飞书卡片
```

CLI入口: `python -m src.pipeline.runner --date YYYY-MM-DD [--force] [--scrape] [--push CHAT_ID]`

## 关键抽象

### Scraper 两种模式
- **模式 A (ChartScraper)**: 子类设 `platform`/`chart_type`/`column_map`，实现 `scrape()→list[dict]`。基类自动完成 `_clean()`(列映射+bundle_id补全) 和 `_write_csv()`(标准文件名)。用于简单HTTP抓取。
- **模式 B (Playwright)**: 独立脚本，手动管理 Playwright + Chrome profile。用于需要登录态/DOM滚动的复杂抓取(diandian_batch)。不继承ChartScraper。

### Agent 基类 (`src/agents/base.py`)
- 用 **DeepSeek** (OpenAI-compatible SDK)，不是Claude
- Prompt从YAML加载: `prompts/{name}.yaml` → system + user_template
- Tool-use loop: LLM决定调工具→Agent并行执行→LLM继续，最多5轮
- JSON强制: `response_format=json_object` + Pydantic schema校验 + 正则兜底
- 所有工具调用写入audit_logs表(_execute_tool内fire-and-forget)

### Track Filter (`src/pipeline/track_filter.py`)
纯规则引擎，不消耗token。优先级: Steam移植→track > 赛道关键词→track > 可无视关键词→ignored > neutral

## 数据库核心表

| 表 | 用途 | 唯一键 |
|----|------|--------|
| rankings | CSV导入的每日排名快照 | (date, platform, chart_type, bundle_id) |
| changes | Differ产出的变动记录 | (date, platform, chart_type, bundle_id) |
| daily_overviews | OverviewScanner输出 | date |
| research_results | Researcher+Verifier输出 | change_id |
| analysis_reports | Analyst+DesignAnalyst+Briefer输出 | date |
| taptap_new_games | TapTap新游日历抓取 | (date, game_name) |
| steam_port_games | Steam移植手游 | (date, game_name) |
| market_news | 游侠/17173头条 | (date, url) |
| diandian_search_cache | 点点搜索7天缓存 | — |
| unreleased_games | 未上线新游追踪 | (date, game_name) |
| audit_logs | 每次Agent工具调用的审计日志 | — |

CRUD方法在sqlite.py的Database类中，参照现有模式: `insert_*()` / `get_*_by_date()` / `cache_*()`。

## 当前进度 (2026-06-22)

**已完成**: Differ, StoryPicker, CrossChart, OverviewScanner, Researcher, Verifier, DesignAnalyst, Briefer, Loader, Runner, 飞书推送, track_filter赛道引擎

**数据采集（4 个 Scraper）**:
- 模式 A (ChartScraper): taptap_new_games, steam_ports, news_feeds → 标准CSV
- 模式 B (Playwright): diandian_batch → iOS+Android 全10榜
- 独立脚本: diandian_search → 按需搜索+7天缓存

**steam_ports 检测逻辑**: TapTap页面直接抓取 → Steam集成标记(steam_review_with_comment等) > 文本关键词(移植/端游移植) > 组合信号(端游+移植)。Steam URL/tags不再抓取。

**搜索方案**: 360搜索(主) → 搜狗(降级) → Bing(降级)。360搜索对中国游戏query效果好，返回游戏攻略/下载/评测而非词典释义。Tavily已弃用(额度用完+中文弱)。

**砍掉的模块**: Analyst, NewGameWatcher, MarketNewsScanner — Scraper数据直读DB不经AI。设计分析不做商业化。

**待做**: 定时调度 + Docker

## 编码铁律

- Python 3.12类型注解必须写; 函数返回值用Pydantic BaseModel, 不用dict
- Agent prompt从YAML加载, 不硬编码在.py里
- 每个模块可独立测试: `python -m src.xxx --test` 或 `python -m tools.scrapers.xxx`
- 中间结果全部落SQLite (WAL模式, FK约束)
- 真实榜单有100款游戏, 数据只有排名没有收入/下载量
- 原始CSV的bundle_id和developer可能为"0", Loader需处理为fallback:{game_name}
- 新Scraper: 模式A继承ChartScraper, 模式B独立脚本

## 常用命令

```bash
python -m src.pipeline.runner --date 2026-06-22 --force          # 全流水线
python -m src.pipeline.runner --scrape --push oc_xxx             # 抓取+推送
python -m tools.scrapers.taptap_new_games                        # 单独跑TapTap抓取
python -m tools.scrapers.diandian_batch --platform ios            # 单独跑点点抓取
python -m src.pipeline.track_filter --test                       # 赛道规则测试
python -m src.pipeline.track_filter --game "明日方舟" --tags "塔防,二次元"
```
