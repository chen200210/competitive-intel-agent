# CLAUDE.md — 技术速查表

## 目录地图

```
OA/
├── src/
│   ├── agents/          # 8个模块 (briefer拆分为6+calibrator+base)
│   │   ├── base.py          # Agent基类: tool-use loop + JSON强制 + Pydantic校验
│   │   ├── briefer.py       # 日报编排入口 (含昨日新游badge+强制纳入逻辑)
│   │   ├── market_pipeline.py # 新闻流水线规则层: 关键词过滤+去重+新鲜度+疲劳
│   │   ├── scorer.py        # AI批量摘要+四维打分+来源多样性选择 (含Calibrator集成)
│   │   ├── render.py        # 飞书卡片markdown生成 (零AI, 新游+排名+市场+热点, 含_split_markdown_entries共享拆分器)
│   │   ├── enrichment.py    # 外部内容抓取: 正文提取+og:image补全
│   │   ├── dedup.py         # reported_items表读写 (三层去重TTL)
│   │   └── calibrator.py    # 反馈驱动校准: 消费user_feedback → LLM分析 → calibration_params (RecGPT LLM-as-Judge模式)
│   ├── pipeline/         # 纯规则管道 (零AI成本)
│   │   ├── differ.py          # 对比昨日排名，算rank_change和attention_score
│   │   ├── story_picker.py    # 从变动中识别5类故事(跃升/黑马/暴跌/爬升/异动)
│   │   ├── track_filter.py    # 赛道规则引擎(塔防/肉鸽/割草→关注, 女性向/二次元→排除)
│   │   ├── loader.py          # CSV → rankings表
│   │   ├── runner.py          # 全流水线编排 (Phase 0→1→2→3)
│   │   ├── audit.py           # 飞书卡片零token质量审计(三板块: 新游/市场/排名)
│   │   ├── token_utils.py     # 共享: 标题分词提取 (游戏名/主题词/去重令牌)
│   │   └── source_constants.py # 新闻源常量化 (NewsSource类+别名+权重+helper函数)
│   ├── feishu/           # 飞书推送
│   │   ├── pusher.py          # 卡片推送+图片上传
│   │   ├── bot.py             # 机器人交互(意图路由+卡片反馈处理)
│   │   └── card_builder.py    # 卡片交互元素(反馈按钮等)
│   ├── storage/
│   │   └── sqlite.py          # 所有CRUD + DDL (WAL模式, FK约束)
│   ├── tools/             # src内独立工具
│   │   ├── taptap_resolver.py  # TapTap搜索+模糊匹配(fuzzy_match_game_name共享函数)
│   │   ├── db_query.py         # DB查询工具
│   │   └── image_fetch.py      # 图片获取
│   └── config.py             # pydantic-settings, 读.env
├── tools/scrapers/       # 榜单抓取 (独立于src/)
│   ├── base.py               # ChartScraper基类
│   ├── diandian_batch.py     # 模式B: Playwright + Chrome profile
│   ├── taptap_new_games.py   # TapTap新游日历(含track_filter分类)
│   ├── steam_ports.py        # Steam移植检测(两路径: TapTap页面+搜索引擎兜底)
│   ├── news_feeds.py         # 游戏资讯头条(17173/3DM/游戏陀螺/游戏日报/GameLook)
│   ├── pocketgamer_biz.py    # 海外移动游戏商业新闻(PG.biz RSS, 英文→AI中文摘要)
│   └── bilibili_creators.py  # B站UP主动态(Playwright+API拦截,含AI字幕/标签)
├── scripts/              # 独立工具脚本
│   └── score_news.py         # 反馈加权新闻打分 (AI批量评分+同源去重)
├── prompts/              # Agent prompts (briefer.yaml, summarizer.yaml, calibrator.yaml)
├── data/
│   ├── raw/                  # 抓取CSV落这里
│   ├── intel.db              # SQLite主库
│   ├── bilibili_creators.yaml # B站UP主关注列表
│   └── competitor_list.yaml  # 竞品列表 + track_config(含genre_boost)
└── docs/                 # 设计文档
```

## 流水线阶段

```
Phase 0A: Scrape  — 并行抓取 6个scraper (diandian_batch 提供榜单排名数据, pocketgamer_biz 海外移动游戏商业新闻)
Phase 0B: Loader — CSV导入 rankings表 (taptap/news/steam/bilibili各自_scrape时直接写DB)
Phase 0C: Cleanup — 删除非当天日期的CSV文件 (数据已入库)
Phase 0D: Track  — 赛道规则打标 (track_filter, 纯规则, 0 token)
Phase 1:  Differ → StoryPicker (纯规则, 0 token)
Phase 2:  Briefer (1个AI Agent: Summarizer打分+摘要+标签 → 代码拼装markdown, 0 token)
Phase 3:  Card Audit + Push → 飞书 (含图片上传: B站封面优先, 新闻og:image兜底, 上限3张)
```

CLI入口: 
```bash
python -m src.pipeline.runner --date YYYY-MM-DD --scrape --force --push CHAT_ID
python -m src.pipeline.runner --date YYYY-MM-DD --scrape --skip diandian_batch
```

## 关键抽象

### Briefer — 日报生成 (`src/agents/briefer.py` + 5 个子模块)

- **架构 (2026-06-25 拆分)**: 从 1263 行单文件拆为 6 模块，briefer.py 仅剩 ~400 行编排逻辑。
  - `briefer.py` — 入口+编排：`brief()` / `brief_from_db()` / `_bilibili_to_news()` / `_yesterday_shown_games()`
  - `market_pipeline.py` — 新闻流水线规则层：`filter_news()` → `apply_fatigue()` → `deep_fetch()`
  - `scorer.py` — AI 批量摘要+四维打分+来源多样性选择：`ai_summarize_and_judge()` / `_select_top_n()`
  - `render.py` — 飞书卡片 markdown 生成（零 AI）：新游 / 排名 / 市场 / 热点四大板块。含 `_split_markdown_entries()` 共享拆分器 + `_match_new_game()` 昨日新游匹配
  - `enrichment.py` — 外部内容抓取：`fetch_article_body()` / `enrich_news_images()`
  - `dedup.py` — `reported_items` 表读写：三层去重 TTL
- **模块间依赖**: briefer → (market_pipeline, scorer, render, dedup) → (enrichment, storage.sqlite)。无循环。
- **对外接口不变**: `runner.py` 只 import `brief_from_db`。
- **卡片 JSON 由代码拼装**，AI 只写市场变动板块 markdown。新游和排名零 AI 参与，不会出现排版错乱。
- **新游关注**: `_build_new_games_md()` 代码预制 — Steam+TapTap 合并展示，重叠游戏走 TapTap 入口+[Steam]标记
- **排名变动**: `_build_ranking_md()` 代码预制 — 赛道上榜表格，游戏名自动关联 TapTap 链接。**昨日新游 badge**: 若游戏名匹配昨日简报展示的新游（通过 `fuzzy_match_game_name()` 三级模糊匹配），标记 `🔴【昨日新游】`。同时 `brief_from_db()` 将昨日新游**强制纳入排名表格**（prepend 到 sector_changes 头部），确保不被赛道过滤或 `[:12]` 切片丢弃。
- **热点追踪**: `build_hot_topic_elements()` — 逐条渲染：每条热点独立 markdown block + `感兴趣` 按钮紧跟其后（与市场板块同样的 per-item interleave 模式）。上限 7 条。
- **市场变动**: 四阶段流水线（见下），1 个 AI Agent（Summarizer 打分+摘要+标签）→ 代码直拼 markdown。`build_market_elements()` 逐条插入配图 + 👍/👎 反馈按钮。
- **共享工具**: `_split_markdown_entries()` 在 render.py — 按 `\n\n` 拆分 markdown，通过 predicate 分类 header/entry 块，供 `build_market_elements` 和 `build_hot_topic_elements` 共用。`fuzzy_match_game_name()` 在 `src/tools/taptap_resolver.py` — 三级策略（精确→基础名→双向包含），供 `_match_new_game` 和 `resolve_taptap_urls` 共用。
- **去重**: 三种类型 — `taptap` / `steam` / `bilibili`，统一走 `reported_items` 表
- **图片嵌入**: `_build_market_elements()` 拆分 market_md → 按 URL 匹配 top_news → 每条新闻插入对应配图（B站封面 / news og:image）+ 每条新闻末尾注入 👍/👎 反馈按钮。有图就插 `<img>` 紧跟 markdown 块，无图跳过。best-effort，失败不阻塞
- **用户反馈**: 每条市场新闻独立 👍/👎 按钮，热点新闻独立 `感兴趣` 按钮，点击后 bot 回调 `increment_news_feedback()` / `record_hot_topic_click()` → 自增计数 + 写 `user_feedback` 表做审计追踪

### B站 UP 主监控 (`tools/scrapers/bilibili_creators.py`)
- Playwright + Chrome profile，支持 headless(--headless)
- 获取: 标题/完整简介/AI中文字幕/标签/播放量/点赞/收藏/投币/弹幕/时长
- 日期筛选: 今天+昨天，兜底2条最新
- 去重: `reported_items` 表(type=bilibili)
- 配置: `data/bilibili_creators.yaml`

### Steam移植检测 (`tools/scrapers/steam_ports.py`)
- **路径A**: TapTap页面 Steam集成JSON标记(`steam_review_with_comment`等) → 确定（无条件信任）
- **路径B**: 文本关键词 + 页面必须同时提到 "steam"。关键词: `steam移植` / `端游移植` / `pc移植` 等。**若无 steam 提及则拒绝** — 解决"端游品质+玩法移植"误标问题
- **路径C**: 搜索引擎兜底 — **仅对无TapTap链接的游戏**
- 每次 sync 先 `DELETE WHERE date=?` 再 insert，旧误标不会残留
- 数据落 `steam_port_games` 表，去重走 `reported_items`(type=steam)
- 多平台信号(`双端上线`等)在 Path B 可撤销 Steam 端口判定

### 市场变动 — 四阶段新闻流水线

**Phase A — 规则粗筛** (`market_pipeline.filter_news()`):
关键词过滤 → 新鲜度门禁(7天) → 赛道排除 → URL/标题去重 → 返回全部幸存者（不限制数量，由 AI 打分做质量判断）

**Phase B — 深挖正文** (`market_pipeline.deep_fetch()`):
- 非B站新闻: 访问原文 URL，提取正文前 ~500 字（BeautifulSoup，礼貌间隔 0.5s）
- B站视频: 跳过（已有 AI 字幕，素材充足）

**Phase C — AI 批量摘要+打分+标签** (`scorer.ai_summarize_and_judge()`, prompt: `summarizer.yaml`):
- 一次性批处理所有候选，生成 3-5 句摘要 + 0-100 总分 + pos_label/neg_label（Matched Verdict Pool）
- 单总分 LLM 打分（0-100），四档锚定：80-100 赛道游戏本身 | 60-80 AI/小游戏可借鉴 | 40-60 游戏相关非赛道 | 0-40 空洞/无关
- **代码层信号提取**（零 token）：正文长度 + 事实密度 + 时效标记 + 是否汇总 → β-fusion 融合 (`0.3×signal + 0.7×AI`)
- **强制分布** + 重试兜底: AI 必须满足 ≥25% 低于 40 分 + ≤30% 高于 60 分，不满足自动重试（最多 3 次），全失败代码层强制修正
- **Calibrator 集成**: topic_boosts 用户偏好调节 → 分布检查（最终防线）
- 质量门禁: `min_ai_score`（默认40）以下不入选；按分取 `top_n` 条（默认7），受来源多样性约束
- **标签持久化**: pos_label/neg_label 写回 `market_news` 表供 Calibrator 做 `label × feedback` 交叉分析

**Phase D — 代码拼装 markdown** (briefer.py, 零 AI):
`brief()` 中纯 Python 代码将精选后的 7 条新闻（含 AI 摘要 + 标签 + 原文链接）拼为飞书 markdown。排序: track_relevant 优先 → ai_score 降序。Briefer 的 LLM 排版调用已移除。

### Track Filter (`src/pipeline/track_filter.py`)
- 纯规则引擎，0 token。三类 track 关键词: 塔防 / 肉鸽 / 割草 (含中英文变体)
- Steam移植自动纳入(`steam_port_always_include`)
- 优先级: Steam移植→track > 赛道关键词→track > 可无视关键词→ignored > neutral
- TapTap scraper 写入前调用 `classify_game()` 打标

### Loader (`src/pipeline/loader.py`)
- 只导入排行榜 CSV（文件名含 热门榜/免费榜/畅销榜 等关键词）
- 非排行榜 CSV（资讯/Steam移植/新游/B站）自动跳过 — 这些 scraper 各自写 DB，不需 Loader
- 日期从文件名自动提取，榜单类型自动识别

### 去重 (`reported_items` 表)
- 统一 DB 表，`UNIQUE(item_key, item_type)`
- `item_type`: `taptap`(游戏名) / `steam`(游戏名) / `bilibili`(BVID) / `news`(URL) / `news_h`(标题令牌)
- `INSERT OR IGNORE`，30天自动清理(`prune_reported()`)
- 新游板块：taptap 和 steam 各自独立去重，重叠游戏合并显示不重复

## 数据库核心表

| 表 | 用途 | 唯一键 |
|----|------|--------|
| rankings | CSV导入的每日排名快照 | (date, platform, chart_type, bundle_id) |
| changes | Differ产出的变动记录 | (date, platform, chart_type, bundle_id) |
| analysis_reports | Briefer卡片JSON | date |
| taptap_new_games | TapTap新游(含track_relevant) | (date, game_name) |
| steam_port_games | Steam移植手游 | (date, game_name) |
| market_news | 游戏资讯头条(含publish_date新鲜度+pos_label/neg_label标签+反馈计数) | (date, url) |
| bilibili_videos | B站UP主视频(含AI字幕,20字段) | (bvid, date) |
| reported_items | 去重记录(taptap/steam/bilibili/news/news_h) | (item_key, item_type) |
| user_feedback | 用户反馈(日报👍👎+热点click,含news_url+keyword关联) | — |
| calibration_params | Calibrator校准参数(版本化,topic_boosts+dim_weights) | version |
| audit_logs | Agent工具调用审计 | — |

## 当前进度 (2026-06-25)

**已完成**: Differ, StoryPicker, Briefer, Loader, Runner, 飞书推送, track_filter, Card Audit, 用户反馈

**最新升级 (2026-06-25) #8 — 昨日新游badge + 热点逐条渲染 + 代码清理**:
- **昨日新游 badge (P0)**: 排名变动表格新增 `🔴【昨日新游】` 标记。`_yesterday_shown_games()` 复现昨日简报筛选逻辑（track-relevant 或 top-5 下载量），`fuzzy_match_game_name()` 三级模糊匹配（精确→基础名→双向包含）。`brief_from_db()` 将昨日新游 prepend 到 sector_changes 头部，确保不被 `[:12]` 切片丢弃。
- **热点逐条渲染**: `build_hot_topic_elements()` 改为 per-item interleave 模式（markdown block + `感兴趣` 按钮紧跟），与市场板块一致的渲染模式。
- **共享工具提取**: `fuzzy_match_game_name()` 从 `_match_new_game` 提取到 `src/tools/taptap_resolver.py`，消除与 `resolve_taptap_urls` 的重复匹配逻辑。`_split_markdown_entries()` 提取为 `render.py` 模块级函数，供 `build_market_elements` 和 `build_hot_topic_elements` 共用。
- **代码清理**: 移除 `sqlite.py` 死代码 `get_new_game_names_by_date()`（被 `_yesterday_shown_games` 替代）。`_match_new_game` 异常处理从裸 `except Exception` 收敛为 `except ImportError` + stderr 日志。
- **Code Review 第三轮 7 bug 修复**: CRITICAL 强制纳入游戏被 `[:12]` 切片丢弃 → prepend 修复。HIGH 异常静默吞 → 改为 ImportError + warn。MEDIUM 死代码/长度守卫缺漏/策略3双向不完整。

**历史升级 (2026-06-25) #7 — P2/P3/P4 + 第二轮 code review**:
- Matched Verdict Pool (P2) · Briefer AI 调用移除 (P3) · CoT 推理步骤 (P4) · Code Review 7 bug 修复 (C8-C14)

**历史升级 (2026-06-25) #6 — Calibrator + β-Fusion**:
- Calibrator Agent (P0) · β-Fusion (P1) · Code Review 7 bug 修复 (C1-C7)

**历史升级 (2026-06-25) #5**:
- Briefer 拆分为 6 模块 · 新闻源常量化 · YAML 化 · 114 测试 · 错误策略+监控

**历史升级 (2026-06-23 ~ 2026-06-24, #1-#4)**:
跨榜信号移除 · Windows GBK 兼容 · 去重策略统一 · 反馈竞态修复 · 静默异常消除 ·
token_utils 共享模块 · score_news 批量修复 · PocketGamer.biz 海外源 · 新闻去重大修 ·
CSV 自动清理 · 飞书卡片按钮修复 · 用户反馈系统。净减 ~2300 行，详见 `git log --oneline`。

**赛道**: 塔防 / 肉鸽 / 割草 (三赛道, 权重相同, 中英文变体)

**待做**: Docker → Hot Topic 热点追踪增强 → 每日全自动 cron

### Calibrator Agent

> 设计文档: [docs/RECGPT_APPLICATION.md](docs/RECGPT_APPLICATION.md) — RecGPT模式应用指南
> 原始设计: [docs/CALIBRATOR_DESIGN.md](docs/CALIBRATOR_DESIGN.md)
> Prompt: `prompts/calibrator.yaml` (Role→Input→CoT Steps→Requirements→Matched Pool→Output)
> 触发: `python -m src.pipeline.runner --calibrate --calibrate-days 14` (需 ≥30 条反馈)

## 编码铁律

- Python 3.12类型注解必须写; 函数返回值用Pydantic BaseModel, 不用dict
- Agent prompt从YAML加载, 不硬编码在.py里
- 中间结果全部落SQLite (WAL模式, FK约束)
- 去重用 `reported_items` 表，不用 JSON 文件
- 新游关注和排名变动用代码预生成markdown，AI不碰内容
- 新Scraper自己写DB (`_sync_to_db`)，不依赖 Loader
- **新闻源名称**: 必须从 `src.pipeline.source_constants` 引用 `NewsSource` 常量，禁止硬编码来源名字符串（如 `"3DM"`/`"游戏陀螺"`）。判断来源类型用 `is_bilibili()`/`is_overseas()` helper，不用 `in` 子串匹配。新增来源只需改 `source_constants.py` 一个文件。
- **异常处理**: `except Exception` 只有两种场景允许——重试也没用、失败不影响核心输出。其余必须至少 `print([WARN], stderr)`。禁止裸 `except Exception: pass`。
- AI 协作规则 & 集成检查清单 → [AI_COLLABORATION_GUIDE.md](docs/AI_COLLABORATION_GUIDE.md)
- 已知 Bug 列表 → [BUG_LOG【已修复】.md](docs/BUG_LOG【已修复】.md)

## 常用命令

```bash
# 全流水线（不传日期=今天）
python -m src.pipeline.runner --scrape --force
# 推送飞书
python -m src.pipeline.runner --scrape --force --push oc_xxx
# 指定日期 + 跳过慢速scraper
python -m src.pipeline.runner --date 2026-06-24 --scrape --force --skip diandian_batch
# 只看分析不抓取
python -m src.pipeline.runner --date 2026-06-24 --brief-only -v
# 看AI打分明细（stderr输出）
python -m src.agents.briefer --date 2026-06-24 -v
# 校准器（分析反馈调参，需≥30条反馈）
python -m src.pipeline.runner --calibrate --calibrate-days 14 -v
python -m src.agents.calibrator --days 14 -v
# 单独跑各scraper
python -m tools.scrapers.taptap_new_games
python -m tools.scrapers.steam_ports
python -m tools.scrapers.news_feeds
python -m tools.scrapers.bilibili_creators --headless
python -m tools.scrapers.diandian_batch --platform ios
# 赛道规则测试
python -m src.pipeline.track_filter --test
python -m src.pipeline.track_filter --game "明日方舟" --tags "塔防,二次元"
# 单独推送日报
python -m src.feishu.pusher push-daily oc_xxx 2026-06-24
```
