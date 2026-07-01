# AI 协作指南

> 如何让我写出更少 bug 的代码。最后更新：2026-06-24

---

## 一、AI 协作铁律

硬规则，违反即为错误：

- 禁止 `except Exception: pass` — 至少 `print(f"[WARN] {e}", file=sys.stderr)`
- 涉及 DB 写入前，确认目标表的数据生命周期（每日刷新 or 永久累积）
- 读取 DB 数据时优先复用已有 API 函数，不要手写 SQL 重复实现
- 写完代码后必须验证：给出可复制粘贴执行的验证命令
- 改动 runner.py / briefer.py 后必须跑一次 `--brief-only -v` 确认不报错

---

## 二、AI 已知盲区

以下是我处理复杂系统时的结构性弱点。了解这些可以让你在 prompt 里提前防范。

### 1. 看不到数据生命周期

我会把不同生命周期的数据放在同一行里处理，做 DELETE 时不追踪该行是否还被其他消费者依赖。

**触发场景**：新增 DELETE/TRUNCATE 操作、修改共享表的写入逻辑

**你该怎么防**：
- 让我设计新功能时明确指出数据生命周期 — "新闻每天刷新，反馈永久累积"
- 问我 "这个数据哪些消费者在读取？生命周期多长？"

### 2. 会重复实现同一个逻辑

已有正确的 API 函数，我会绕过它直接写 SQL，导致列名错误或逻辑不一致。Scraper 和 Briefer 各写一套去重，互相冲突。

**真实案例**：`brief_from_db` 手写 SQL 读 `cross_chart_signals` 表，写错了列名 `signals_json`（实际是 `charts_json`）。而 `runner.py` 用 `get_signals_for_date()` API 正确读取。同一个逻辑被实现了两遍，第二遍是错的。

**触发场景**：新增功能需要读取已有数据

**你该怎么防**：
- "读跨榜信号，已有代码怎么做的？先 grep 再写"
- 要求我先展示已有的相关 API，确认没有重复造轮子再动笔

### 3. 倾向于静默吞异常

`except Exception: pass` 让功能悄悄失效，没有痕迹。

**触发场景**：任何 try/except 块

**你该怎么防**：
- 全局搜索 `except.*pass` 定期清理
- code review 时重点检查异常处理

### 4. 测试思维弱

不会主动验证自己写的代码是否跑通，只依赖静态推理。

**触发场景**：写完新代码后

**你该怎么防**：
- 让我给出一条可复制粘贴的验证命令
- "写完就跑一次 --brief-only -v，确认不报错"

### 5. SQL 事务边界模糊

SELECT 检查和 INSERT 写入不在同一事务内，两个并发请求可能同时通过检查。

**真实案例**：`increment_news_feedback` — 去重 SELECT 在事务 1，INSERT 在事务 2，中间有竞态窗口。

**触发场景**：任何"先检查再写入"的 DB 操作

**你该怎么防**：
- "检查 + 写入在一个事务里，用 INSERT OR IGNORE + rowcount 判断"
- 去重要求我用唯一索引 + INSERT OR IGNORE，不要手动 SELECT → INSERT

### 6. 字符串匹配判断过于脆弱

用 `"bilibili" in source` 判断来源类型，未来任何包含 "bilibili" 字符串的非 B站 源都会被误判。

**触发场景**：对 source、category、platform 等枚举字段做判断

**你该怎么防**：
- "来源判断用精确匹配 `src == 'bilibili'`，不要用 `in`"
- 要求我给关键枚举值定义常量，所有代码引用常量而非裸字符串

### 7. 分不清 system prompt 和 user_template 的格式化边界

`Agent.run()` 只对 `user_template` 调 `.format(**kwargs)`，`system_prompt` 是静态模板原样发送。
当我把动态数据（反馈表格、当前参数、日期范围）放在 system prompt 的 YAML 段时，LLM 收到的是字面占位符 `{feedback_table}` 而非真实数据。不会报错——LLM 会把 `{feedback_days}` 当作普通文本读过去，然后用零信号产生看起来合理的输出。

**真实案例**: `prompts/calibrator.yaml` 的 system 段包含了 `{feedback_days}`、`{feedback_table}`、`{current_params}` 三个占位符。Calibrator agent 跑了多次，每次都"成功"输出校准参数——但所有输出都是基于零反馈信号的幻觉。因为 system prompt 从来没被 format 过。

**触发场景**: 新增 Agent prompt YAML，把动态数据放在 `system:` 段

**你该怎么防**:
- "prompt YAML 的 system 段只放静态指令，动态数据全部放 user_template"
- 写完新 prompt YAML 后，验证命令: `python -c "from src.agents.base import load_prompt; p = load_prompt('xxx'); assert '{' not in p['system'].replace('{{','').replace('}}',''), 'LEAK: format placeholder in system prompt'"`

---

## 三、集成检查清单

每次修改必须逐条追踪数据流。局部改动不通读全局影响是 bug 的首要来源。

### 新增 Scraper

```
□ runner.py — scraper_db_table 字典（预检查用表名）
□ runner.py — scraper_where 字典（共享表的 source-specific WHERE）
□ runner.py — scraper_scripts 列表（并行执行入口）
□ runner.py — Phase 0A 注释里的 scraper 数量更新
□ briefer.py — game_media 白名单（_compact_news 源过滤）
□ briefer.py — source_weights 字典（_score_news_item 来源权威分）
□ briefer.py — news_block_keywords（确认不会误杀新源的标题）
□ briefer.py — _fetch_article_body 选择器（新源的文章页结构适配）
□ briefer.py — _select_diverse 是否需 overseas 多样性保障
□ CLI 验证 — python -m tools.scrapers.xxx 单独跑通
□ 全链路验证 — runner --scrape 跑完查 DB 来源分布
```

### 新增/修改 DELETE 操作

```
□ 全局 grep 被删表的 SELECT/WRITE 方 → 列出所有消费者
□ 消费者中哪些依赖被删的数据 → 逐一确认是否需要改
□ user_feedback ↔ market_news 这类跨表依赖尤其危险
□ 确认清理范围：只清当天(date=?)还是全表 → 注释写清楚
□ 确认清理时机：在 scraper 前还是 briefer 后 → 影响谁读得到
```

### 修改 DB 表结构

```
□ grep 全仓所有对该表的 INSERT / UPDATE / SELECT / DELETE
□ 逐一确认 SQL 是否与新 schema 兼容
□ migration 写在 sqlite.py 的 _run_migrations 里，加 try/except
□ ALTER TABLE ADD COLUMN 必须带默认值或允许 NULL
□ 新列是否需要回填历史数据 → 写 UPDATE 语句
```

### 修改 Reporter/Briefer 管线

```
□ 新字段是否在 market_news → _compact_news → _deep_fetch → AI scorer 全链路可用
□ _compact_news 的过滤链（源白名单→关键词→去重→track→新鲜度）每步对新数据是否生效
□ AI prompt (summarizer.yaml / briefer.yaml) 是否需要更新提示词
□ 去重键（item_type + item_key）是否与现有类型冲突
□ 多样性保障（_select_diverse 的 per-source cap / overseas floor）是否配平
```

### 修改 reported_items 逻辑

```
□ 写入方是谁：scraper._sync_to_db vs briefer.mark_reported → 不要两头写
□ 读取方是谁：_load_reported_news / _load_reported_news_headlines
□ TTL 是否合适：news=30天, news_seen=7天, news_h=30天
□ scraper 的 mark_reported 会阻止同次 briefer 读到 → 删掉 scraper 端的标记
□ 清理时区分类型：steam/taptap 跨跑持久化，news 类每天清
```

### 跨模块 API 复用

```
□ 读取已有数据时，先 grep 是否有现成的 API 函数
□ 不要在 briefer.py 里手写 SQL 读 cross_chart_signals / taptap_new_games 等表
□ 正确做法：调用 get_signals_for_date() / get_taptap_games_by_date() 等封装好的函数
□ 如果已有 API 不满足需求，扩展 API 而非绕过它
```
