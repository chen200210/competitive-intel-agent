# 代码冗余审计

> 审计框架按 5 层递进：Scraper → Pipeline → Feishu → Agent → 全项目死代码。
> 每层独立审计，完成后打 ✅。

---

## 审计框架

| 层 | 范围 | 重点 | 状态 |
|----|------|------|------|
| 1. Scraper | 6 个 scraper + base | HTTP 请求、去重写入、日期过滤、CSV 输出 | ✅ 完成 |
| 2. Pipeline | differ / story_picker / cross_chart / track_filter / loader / runner / audit | 纯规则模块是否有重叠的数据遍历逻辑 | ✅ 完成 |
| 3. Feishu | bot / pusher / card_builder | 卡片构建逻辑是否分散重复 | ✅ 完成 |
| 4. Agent | base / briefer | 仅 2 个 agent，预期冗余度低 | ✅ 完成 |
| 5. 全项目 | 所有 .py 文件 | 死代码（未被调用的函数/类/导入）、废弃的 import | ✅ 完成 |

---

## 1. Scraper 层

### 🔴 高优先级：可消除的重复代码

#### 1.1 `_get_client()` — 3 个文件一模一样 ✅ 已修复 (2026-06-24)

| 文件 | 行号 |
|------|------|
| `tools/scrapers/taptap_new_games.py` | 81-96 |
| `tools/scrapers/steam_ports.py` | 78-93 |
| `tools/scrapers/news_feeds.py` | 72-87 |

三处完全相同的逻辑：同一个 UA、同一组 Accept headers、`httpx.Client(timeout=20)`。唯一名义差异是 timeout 参数名（`20.0` vs `FETCH_TIMEOUT`），但实际值都是 20 秒。

**建议**: 提到 `ChartScraper` 基类，子类只覆盖 `_timeout` 属性即可。

```python
# base.py — 新增
class ChartScraper:
    _timeout: float = 20.0
    _user_agent: str = "Mozilla/5.0 ..."

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={...},
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
            )
        return self._client
```

---

#### 1.2 `_clean()` + `EXTRA_COLUMNS` — 3 个文件一模一样 ✅ 已修复 (2026-06-24)

| 文件 | 行号 |
|------|------|
| `tools/scrapers/taptap_new_games.py` | 70-79 |
| `tools/scrapers/steam_ports.py` | 95-102 |
| `tools/scrapers/news_feeds.py` | 89-97 |

三处都是同样的模式：

```python
def _clean(self, raw_rows, date):
    cleaned = super()._clean(raw_rows, date)
    for raw, clean in zip(raw_rows, cleaned):
        for col in self.EXTRA_COLUMNS:
            val = raw.get(col, "")
            if val is not None and val != "":
                clean[col] = str(val)
    return cleaned
```

**建议**: 合并进 `ChartScraper._clean()` 基类实现：基类做完标准列映射后，自动检查 `self.EXTRA_COLUMNS` 并 merge，子类不需要覆盖。

---

#### 1.3 `_sync_to_db()` market_news 写入 — 2 处高度重复 ✅ 已修复 (2026-06-24)

| 文件 | 行号 | 目标表 |
|------|------|--------|
| `tools/scrapers/news_feeds.py` | ~~739-795~~ → 553-587 | `market_news` |
| `tools/scrapers/pocketgamer_biz.py` | ~~176-225~~ → 176-202 | `market_news` |

**修复方案**: 在 `sqlite.py` 新增 `insert_market_news_deduped(records, date)` 方法，封装"加载已有 URL + 去重 + 批量插入"三段式。两个 scraper 的 `_sync_to_db` 瘦身为纯数据映射：
- `news_feeds`: CSV DictReader → record dict → `db.insert_market_news_deduped()`
- `pocketgamer_biz`: RSS items dict → record dict → `db.insert_market_news_deduped()`
- SQL 不一致（`WHERE url IS NOT NULL` vs 无）一并统一

**改动量**: sqlite.py +45 行；news_feeds -30 行；pocketgamer_biz -25 行。净减 ~10 行，去重逻辑不再分裂。

---

### 🟡 中优先级：死代码

#### 1.4 `news_feeds.py` 里的 3 个死方法 ✅ 已修复 (2026-06-24)

| 方法 | 行号 | 说明 |
|------|------|------|
| `_scrape_gamersky()` | 186-242 | 游侠资讯源已于 2026-06-24 移除（CLAUDE.md 确认），代码残留 |
| `_search_track_news_via_360()` | 442-483 | 旧版 360 搜索引擎实现，未被任何地方调用 |
| `_search_track_news()` | 485-527 | 类内定义了，但 `scrape()` 主方法（101-182 行）没有调用它 |

**建议**: 直接删除，约 120 行代码。

---

#### 1.5 `news_feeds.py` 重复 `except` 块（疑似 bug）✅ 已修复 (2026-06-24)

```python
# news_feeds.py:149-153
        except Exception as e:
            print(f"  [WARN] GameLook抓取失败: {e}")
        except Exception as e:              # ← 永远不可达
            print(f"  [WARN] 赛道新闻搜索失败: {e}")
```

第二个 `except` 永远不会执行。推测原本 GameLook 后面还有一段赛道新闻搜索代码，代码被删后 except 残留。

**建议**: 删除第 152-153 行。

---

### 🟢 低优先级：结构不一致

#### 1.6 `bilibili_creators.py` 未继承 `ChartScraper`

是唯一不继承基类的 scraper。有自己的 `_write_csv()`（565-578 行）和 `run()` 方法（63-182 行）。CSV 写入逻辑与基类 `_write_csv()` 功能重复但实现不同：

- 基类：`csv.DictWriter` 用 `all_keys` 收集稀疏列
- bilibili：手写 `CSV_COLUMNS` 列表做 `row = {k: v.get(k, "")}`

**建议**: 由于 bilibili 流程确实特殊（Playwright + API 拦截 + 日期范围过滤 + 富字段），不强求继承。但 CSV 写入可以复用基类或抽成独立工具函数。

---

#### 1.7 User-Agent 字符串出现在 5 个文件

同一段 Chrome UA 字符串复制了 5 次：

| 文件 | 行号 |
|------|------|
| `taptap_new_games.py` | 86-88 |
| `steam_ports.py` | 83-85 |
| `news_feeds.py` | 77-79 |
| `bilibili_creators.py` | 203-204, 291-293 |
| `pocketgamer_biz.py` | 82-84 |

**建议**: 随 #1.1（`_get_client` 进基类）自然解决。`bilibili_creators.py` 和 `pocketgamer_biz.py` 如果不动基类，至少可以把 UA 抽成一个常量。

---

#### 1.8 `sys.path.insert` 模式重复 4 次

| 文件 | 行号 |
|------|------|
| `taptap_new_games.py` | 27-29 |
| `steam_ports.py` | 33-35 |
| `news_feeds.py` | 34-36 |
| `pocketgamer_biz.py` | 28-30 |

全是一样的 3 行：`_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent` + `sys.path.insert`。

**评估**: 可接受。每个 scraper 需要能 `python -m tools.scrapers.xxx` 独立运行，这个 boilerplate 有存在理由。如果后续统一用 `pip install -e .` 方式运行，可以删掉。

---

### 📊 Scraper 层汇总

| # | 问题 | 严重度 | 状态 |
|---|------|--------|------|
| 1.1 | `_get_client()` 3 处重复 → 基类 | 高 | ✅ 已修复 |
| 1.2 | `_clean()`+EXTRA_COLUMNS 3 处重复 → 基类 | 高 | ✅ 已修复 |
| 1.3 | `_sync_to_db` market_news 2 处重复 → `sqlite.py` 统一方法 | 高 | ✅ 已修复 |
| 1.4 | `news_feeds` 3 个死方法 | 中 | ✅ 已修复 |
| 1.5 | `news_feeds` 重复 except 块 | 中 | ✅ 已修复 |
| 1.6 | bilibili 未继承基类 | 低 | ⬜ 不强求 |
| 1.7 | UA 字符串 5 处复制 | 低 | ⬜ 随 1.1 解决 3/5 |
| 1.8 | `sys.path.insert` 4 处 | 低 | ⬜ 可接受 |

**Scraper 层高优先级全部修复。实际变更**: 净减 ~200 行（删除 ~280 行重复/死代码，新增 ~80 行集中实现）。

---

## 2. Pipeline 层 ✅ 完成

### 🔴 高优先级：重复实现

#### 2.1 `_classify_day` / `classify_day` — 2 处重复 ✅ 已修复 (2026-06-24)

| 文件 | 行号 | 函数名 |
|------|------|--------|
| `src/pipeline/differ.py` | 153-178 | `classify_day(total, up, down, ...)` |
| `src/agents/briefer.py` | ~~1438-1451~~ 已删除 | `_classify_day(changes)` |

**修复**: briefer.py 删除 `_classify_day`，改为从 differ 导入 `classify_day`。同时修复了 `total` 参数语义错误（原用 `len(changes)` 导致 volatility 恒为 1.0，现用 `SELECT COUNT(*) FROM rankings`）。

---

#### 2.2 `_filter_track_changes` — 3 处重复 ✅ 已修复 (2026-06-24)

| 文件 | 行号 | 实现方式 |
|------|------|----------|
| `src/pipeline/runner.py` | ~~213-234~~ 已删除 | → `track_filter.filter_track_changes()` |
| `src/agents/briefer.py` | ~~1454-1500~~ 已删除 | → `track_filter.filter_track_changes()` |
| `src/tools/taptap_resolver.py` | ~~139~~ 已修改 | → `track_filter.filter_track_changes()` |

**修复**: 在 `track_filter.py` 新增统一的 `filter_track_changes()`（含 `classify_game()` + YAML `monitored_games`），三个调用方全部改为 import 此函数。

---

#### 2.3 跨榜信号读取 — 两处不一致，一处有 Bug ✅ 已修复 (2026-06-24)

| 文件 | 行号 | 方式 |
|------|------|------|
| `src/pipeline/runner.py` | 126 | `get_signals_for_date(date)` ✅ |
| `src/agents/briefer.py` | ~~282-293~~ | ~~手写 SQL `SELECT signals_json`~~ → `get_signals_for_date(date)` ✅ |

**修复**: briefer.py 删除手写 SQL（`signals_json` 列不存在，静默失败），改为 `from src.pipeline.cross_chart import get_signals_for_date`。

---

### 🟡 中优先级

#### 2.4 `differ.py` 的 `GENRE_BOOSTS` 死代码 + `_load_track_config` 无缓存 ✅ 已修复 (2026-06-24)

| 变量 | 行号 | 说明 |
|------|------|------|
| `MONITORED_HIGH_PRIORITY` | 22 | 空 set，预留接口但从未被填充 — 保留 |
| `GENRE_BOOSTS` | 25-29 | ~~定义了 3 个 boost 但从未使用~~ 已删除 |
| `_load_track_config()` | 68 | **已加 `@functools.lru_cache(maxsize=1)`** — 每次 pipeline 从 ~135 次 YAML 读盘降为 1 次 |

---

### 📊 Pipeline 层汇总

| # | 问题 | 严重度 | 预计删除行数 |
|---|------|--------|------------|
| 2.1 | `_classify_day` 重复 → 复用 differ | 高 | ~15 行删除 |
| 2.2 | `_filter_track_changes` 3 处 → 1 处 | 高 | ~50 行删除 |
| 2.3 | 跨榜信号 SQL 列名错误 | 高 | ~10 行替换 | ✅ 已修复 (2026-06-24) |
| 2.4 | `GENRE_BOOSTS` 死代码 | 中 | ~5 行删除 | ✅ 已修复 (2026-06-24) |

**Pipeline 层预计净减: ~80 行**

---

## 3. Feishu 层 ✅ 完成

### 🟡 中优先级

#### 3.1 `push_card` / `push_to_user` / `upload_images_for_card` — card unwrap 重复 3 次 ✅ 已修复 (2026-06-24)

**修复**: 提取 `_unwrap_card(card)` 私有函数，4 处调用点全部改用此 helper（含 `upload_images_for_card` 的 unwrap + re-wrap 两处）。

---

### 🟢 低优先级

#### 3.2 `send_message` 的 `chat_id`/`receive_id` 参数重叠

两个参数做同一件事，可以合并为 `target_id` + `target_type`。

---

### 📊 Feishu 层汇总

| # | 问题 | 严重度 | 预计删除行数 |
|---|------|--------|------------|
| 3.1 | card unwrap 3 处 → 1 个函数 | 中 | ~6 行净减 | ✅ 已修复 (2026-06-24) |
| 3.2 | `chat_id`/`receive_id` 参数重叠 | 低 | ~5 行简化 | ⬜ 不强求 |

**Feishu 层预计净减: ~10 行**（已经很精简）

---

## 4. Agent 层 ✅ 完成

### 🔴 高优先级：运行时报错

#### 4.1 `researcher.py` 已删除但 `bot.py` 仍引用 ✅ 已修复 (2026-06-24)

**修复**: 删除 `_handle_deep_research` 函数（~30行），从 `INTENT_PROMPT`、`handlers` dict、欢迎消息中移除 `deep_research` intent。

---

### 🟡 中优先级

#### 4.2 `base.py` JSON 修复逻辑可独立模块

`base.py` 总共 805 行，其中 JSON 解析+修复占了 ~200 行（`_parse_json`, `_repair_json`, `_fix_inner_quotes`, `_is_cjk_or_alnum`）。

**评估**: 不强求。DeepSeek 确实偶尔产格式有误的 JSON，这套修复机制有价值。但可考虑抽成 `src/agents/json_repair.py`。

---

### 📊 Agent 层汇总

| # | 问题 | 严重度 | 预计删除行数 |
|---|------|--------|------------|
| 4.1 | `_handle_deep_research` 死代码 | 高 | ~35 行删除 |
| 4.2 | JSON repair 可独立模块 | 低 | 0 行（不强求） |

**Agent 层预计净减: ~35 行**

---

## 5. 全项目死代码扫描 ✅ 完成

### 🔴 高优先级：DB 死表

#### 5.1 确认已废弃的表 ✅ 已修复 (2026-06-24)

**修复**: 从 `SCHEMA_SQL` 删除 6 张死表 DDL（daily_overviews, research_results, conversations, channel_effectiveness, in_development_tracking, prompt_versions），删除对应的 CRUD 方法，清理 `_migrate_v3` 中死表引用，更新 `db_query.py` 示例。

#### 5.2 确认在用的表

| 表名 | 调用方 |
|------|--------|
| `agent_audit_log` | `base.py._execute_tool` → `db.insert_audit_log()` ✅ |
| `search_cache` | `web_search.py` → `db.get_cached_search()` / `db.cache_search()` ✅ |
| `fetch_cache` | `web_fetch.py` → `db.get_cached_fetch()` / `db.cache_fetch()` ✅ |
| `conversations` | `bot.py` 定义了 `log_conversation` 但 `_process_message` 未调用 — **半废弃** |

#### 5.3 `analysis_reports` 废弃列

| 列名 | 状态 |
|------|------|
| `research_ids` | 废弃 — Researcher 已砍 |
| `report_json` | 废弃 — Analyst 已砍 |
| `design_analysis_json` | 废弃 — Design Analyst 已砍 |

这三列在 `upsert_analysis_report` 中保留和传递，但从未被读取。

---

### 🟡 中优先级：孤立模块 & 失效测试

#### 5.4 孤立工具模块

| 模块 | 说明 |
|------|------|
| `src/tools/game_info.py` | `fetch_game_info` 无外部调用方 |
| `src/tools/web_fetch.py` | `web_fetch` 无外部调用方（briefer 用自己的 `_fetch_article_body`） |

#### 5.5 失效测试文件

| 文件 | 引用 |
|------|------|
| `tests/test_researcher_output.py` | `from src.agents.researcher import ...` ❌ 模块不存在 |
| `tests/test_researcher_smoke.py` | `from src.agents.researcher import ...` ❌ 模块不存在 |
| `tests/test_timing.py` | `from src.agents.researcher import ...` ❌ 模块不存在 |

---

### 📊 全项目死代码汇总

| # | 问题 | 严重度 | 预计删除行数 |
|---|------|--------|------------|
| 5.1 | 5 个废弃 DB 表 DDL | 高 | ~60 行 |
| 5.3 | `analysis_reports` 3 个废弃列 | 中 | 需 migration | ✅ 已修复 (2026-06-24) |
| 5.4 | `game_info.py` + `web_fetch.py` 孤立模块 | 中 | ~180 行 | ✅ 已修复 (2026-06-24) |
| 5.5 | 5 个失效测试文件 | 中 | ~300 行 | ✅ 已修复 (2026-06-24) |

**全项目死代码预计净减: ~540 行**

---

## 📊 全 5 层总汇总

| 层 | 状态 | 高优问题数 | 预计净减行数 |
|----|------|---------|------------|
| 1. Scraper | ✅ | 3 | ~170 行 |
| 2. Pipeline | ✅ | 3 | ~80 行 |
| 3. Feishu | ✅ | 0 | ~10 行 |
| 4. Agent | ✅ | 1 | ~35 行 |
| 5. 全项目 | ✅ | 1 | ~540 行 |

**全项目预计净减: ~835 行代码**（含测试文件 ~300 行）

---

## 🔧 第二轮修复清单 — 全部完成 (2026-06-24)

### 立即修复（运行时崩溃）
1. ✅ **bot.py `_handle_deep_research`** — 引用不存在的 `researcher` 模块，用户触发即崩溃
2. ✅ **briefer.py 跨榜信号 SQL** — 列名错误，改为 `cross_chart.get_signals_for_date()`

### 本次优先（消除重复）
3. ✅ **`_filter_track_changes` 三合一** — 统一到 `track_filter.filter_track_changes()`
4. ✅ **`_classify_day` 去重** — briefer 复用 `differ.classify_day`
5. ✅ **`_get_client()` 提到基类** — 已在 ChartScraper 基类统一

### 可择机清理
6. ✅ **废弃 DB 表 DDL** — 6 张表 + analysis_reports 3 列已清理
7. ✅ **`game_info.py` + `web_fetch.py`** — 孤立模块已删除
8. ✅ **失效测试文件** — 5 个已删除 (researcher x2 + verifier x2 + timing)
9. ✅ **`_sync_to_db` 去重统一** — `insert_market_news_deduped()` 集中到 sqlite.py

### 本轮新增（审计文档未记录）
10. ✅ **Feishu pusher `_unwrap_card`** — 4 处重复 → 1 个 helper
11. ✅ **`GENRE_BOOSTS` 死代码** — differ.py 已删除

---

## 🔧 第三轮：代码质量深度清理（待执行）

> 第二轮完成后，高/中优先级已清零。第三轮聚焦**代码质量 & 一致性**，不再是简单删重复/死代码。
> 扫描范围：40+ 源文件，12 维度。

---

### 🔴 高优先：Bug 风险 / 数据正确性

#### 3.1 `analysis_reports` 废弃列仍写入

`sqlite.py` 的 `upsert_analysis_report()` 仍接受并存储 `research_ids`、`report_json`、`design_analysis_json` 三个参数，但全项目**没有任何代码读取**这三列。每份日报都浪费存储写入空值。

| 位置 | 说明 |
|------|------|
| `src/storage/sqlite.py:551-562` | `upsert_analysis_report()` 仍保留三个废弃字段 |

**建议**: 从函数签名和 INSERT SQL 中删除三列（需 migration 或重建表）。

---

#### 3.2 TapTap URL 解析三套实现 ✅ 已修复 (2026-06-24)

**修复**: 在 `src/tools/taptap_resolver.py` 新增 `resolve_taptap_urls(game_names) → dict[str, str]` 统一函数，内含 DB 双源查询 + 三策略匹配（精确→歧义拆分→子串）。三个调用方全部改用此函数：
- `runner.py _resolve_tap_urls()` — DB 查已知 URL → Playwright 补缺失（保留唯一差异点）
- `briefer.py _get_taptap_urls()` — 改为 1 行调用
- `briefer.py _compact_changes()` — 改为 2 行调用

**净效果**: briefer.py -75 行，runner.py -8 行，taptap_resolver.py +75 行。净减 ~8 行，消除两套独立实现。

---

### 🟡 中优先：维护性 / 一致性

#### 3.3 UA 字符串 5 处各有差异

同一段 Chrome UA 出现在 5 个文件，但 header 组合不同：

| 文件 | 行号 | 差异 |
|------|------|------|
| `src/tools/web_search.py` | 24-27 | 仅 UA |
| `src/tools/image_fetch.py` | 23-26 | 仅 UA |
| `tools/scrapers/base.py` | 97-100 | UA + Accept + Accept-Language |
| `src/feishu/pusher.py` | 229 | UA（无 Accept 头） |
| `src/agents/briefer.py` | 1454-1458 | UA + Accept + Accept-Language |

Scraper 层的 `_get_client()` 已在第一轮统一（1.1），但 **非 scraper 的 HTTP 请求**（web_search, image_fetch, pusher, briefer）没有跟上。

**建议**: 在 `src/config.py` 新增 `HTTP_HEADERS` 常量，所有非 scraper HTTP 调用方统一引用。

---

#### 3.4 `print()` vs `logging` 割裂

| 用 `logging` 的模块 | 用 `print()` 的模块 |
|---------------------|---------------------|
| `src/feishu/bot.py` | `src/pipeline/runner.py` |
| `src/feishu/pusher.py` | `src/agents/briefer.py` |
| | `src/storage/sqlite.py` (migration 输出) |
| | `src/pipeline/loader.py` |
| | `tools/scrapers/base.py` |
| | `src/pipeline/audit.py` |

12 个库模块中 10 个用 `print()` — 调用方无法控制日志级别，Docker/定时调度环境下 stdout 会被污染。

**建议**: `storage/`、`pipeline/`、`agents/` 模块统一用 `logging.getLogger(__name__)`。`tools/scrapers/` CLI 脚本可以保留 `print()`。

---

#### 3.5 分数阈值散落各处

| 阈值 | 文件 | 含义 |
|------|------|------|
| `STRONG_RANK = 15` | `cross_chart.py:31` | 强排名阈值 |
| `[3, 5, 10, 30, 50]` | `differ.py:55-66` | 排名区间分档 |
| `rank_change >= 15` | `differ.py:235` | 大变动阈值 |
| `>= 15` / `>= 20` | `story_picker.py:72/105` | 跃升/暴跌阈值 |
| `-2, -3, -5, -10, -15` | `audit.py` (9 处) | 卡片审计扣分值 |
| `SIGNIFICANT_DELTA = 25` | `cross_chart.py:35` | 跨榜显著差异 |

同一概念（"多大算大"）的阈值分散在 5 个文件中，调参时需要同步改多处。

**建议**: 集中到 `src/config.py` 或新建 `src/pipeline/scoring.py`。

---

### 🟢 低优先：代码结构

#### 3.6 `news_block_keywords` 两份拷贝

| 文件 | 行号 | 说明 |
|------|------|------|
| `src/agents/briefer.py` | 624-658 | `news_block_keywords` + `bilibili_block_keywords`（完整版） |
| `src/pipeline/audit.py` | 81-89 | `NON_GAME_KEYWORDS`（子集版 — 用于卡片审计过滤） |

audit 版是 briefer 版的子集。Briefer 已经在入库前过滤了，audit 再过滤一次是冗余的。

**建议**: audit 改为从 briefer 导入同一个 keyword list，或删除 audit 版（因为 briefer 已过滤）。

---

#### 3.7 `track_filter.py` 内联测试

`src/pipeline/track_filter.py:295-356` 有 ~60 行 `_run_tests()` 函数，直接在模块 `__main__` 块中执行。测试代码和生产代码混在同一个文件。

**建议**: 提取到 `tests/test_track_filter.py`。

---

#### 3.8 `briefer.py` 17 处重复 import

```python
from src.storage.sqlite import get_db
```

这一行在 `briefer.py` 的 **17 个不同函数**中重复出现。原因是避免模块级循环导入。

**建议**: 提为模块级懒加载 `_db = None` + `def _get_db()` helper，消除 16 处重复。

---

#### 3.9 测试覆盖空洞

核心模块零测试覆盖：

| 无测试的核心模块 | 行数 |
|-----------------|------|
| `src/pipeline/runner.py` | 449 |
| `src/storage/sqlite.py` | 1025 |
| `src/pipeline/audit.py` | 476 |
| `src/pipeline/track_filter.py` | 395 |
| `src/feishu/bot.py` | 402 |
| `src/feishu/pusher.py` | 438 |

现存 8 个测试文件覆盖 ~40 个源文件（覆盖率 ~20%）。

---

#### 3.10 超长函数（>80 行）

| 文件 | 行号 | 函数 | 行数 |
|------|------|------|------|
| `src/pipeline/runner.py` | 57 | `run_pipeline` | 147 |
| `src/pipeline/runner.py` | 252 | `_run_phase0_scrape` | 140 |
| `src/agents/briefer.py` | 60 | `brief` | 137 |
| `src/agents/briefer.py` | 609 | `_compact_news` | 128 |
| `src/pipeline/cross_chart.py` | 66 | `detect_signal` | 127 |
| `src/agents/briefer.py` | 1695 | `_ai_summarize_and_judge` | 112 |
| `src/agents/base.py` | 142 | `run` | 113 |
| `src/agents/briefer.py` | 1510 | `_apply_fatigue` | 108 |
| `src/agents/base.py` | 309 | `_append_tool_results` | 102 |
| `src/pipeline/differ.py` | 28 | `compute_attention_score` | 97 |
| `src/feishu/pusher.py` | 222 | `upload_image` | 87 |
| `src/tools/image_fetch.py` | 30 | `image_fetch` | 105 |

12 个函数超 80 行。最值得拆的是 `run_pipeline`（编排逻辑 vs 清理逻辑 vs 输出逻辑）、`detect_signal`（5 种信号模式各抽函数）、`brief`（4 阶段各抽函数）。

---

### 📊 第三轮汇总

| # | 问题 | 严重度 | 预计工作量 |
|---|------|--------|-----------|
| 3.1 | `analysis_reports` 废弃列 | 🔴 高 | ~15 行改动 + migration | ✅ 已修复 (上轮 5.3) |
| 3.2 | TapTap URL 三套实现 | 🔴 高 | ~80 行删除 + 统一 | ✅ 已修复 (2026-06-24) |
| 3.3 | UA 字符串 5 处 | 🟡 中 | ~20 行新增 config |
| 3.4 | print() → logging | 🟡 中 | ~50 行替换 |
| 3.5 | 分数阈值散落 | 🟡 中 | ~30 行集中 |
| 3.6 | news_block_keywords 双份 | 🟢 低 | ~10 行删除 |
| 3.7 | track_filter 内联测试 | 🟢 低 | ~60 行移动 |
| 3.8 | briefer.py 17 处 import | 🟢 低 | ~20 行净减 |
| 3.9 | 测试覆盖空洞 | 🟢 低 | 新增 6+ 测试文件 |
| 3.10 | 超长函数拆分 | 🟢 低 | 12 个函数重构 |

**预计总工作量**: 前 5 项（高+中优先）约 1-2 小时，全 10 项约 4-6 小时。
