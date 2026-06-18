# 搜索缓存与审计日志设计

## 背景

Overview Scanner 每跑一次做 15 次搜索，Researcher 跑每个 focus 条目还要再搜 5 维度 × 2 平台 = 10+ 次。大量 query 和 URL 是重复的，搜索结果却全部丢弃，下一个 Agent 从头搜。

Agent 的决策过程（调了什么工具、看到了什么数据、为什么推荐/跳过）目前是黑盒，事后无法审计。

## 要存什么

### 1. 搜索缓存 (`search_cache`)

目的：同一个 query 不搜两次。

| 字段 | 说明 |
|------|------|
| `query_hash` | MD5(query + date)，唯一键 |
| `query` | 原始搜索词 |
| `engine` | `bing` / `ddg` |
| `results_json` | 返回的 `[{title, url, snippet, rank}]` |
| `result_count` | 命中了多少条 |
| `called_by` | 哪个 Agent 调的（`overview_scanner` / `researcher` / ...） |
| `searched_at` | 搜索时间 |

**逻辑**：Agent 调 `web_search` → 先查 `search_cache` → 命中且距今 <24h → 直接返回缓存结果 → 没命中 → Bing 搜 → 写入缓存。

24 小时后不命中，因为同一天的日报流程内没必要过期，但跨天的新闻需要重新搜。

### 2. 网页抓取缓存 (`fetch_cache`)

目的：同一个 URL 不抓两次。

| 字段 | 说明 |
|------|------|
| `url_hash` | MD5(url)，唯一键 |
| `url` | 完整 URL |
| `title` | 页面标题 |
| `text` | 提取的纯文本正文（截断前 5000 字符） |
| `text_length` | 原始正文长度 |
| `status_code` | HTTP 状态码 |
| `fetched_at` | 抓取时间 |

**逻辑**：Agent 调 `web_fetch` → 先查 `fetch_cache` → 命中且距今 <7 天 → 直接返回 → 没命中 → 抓取 → 写入。

7 天是因为网页内容更新慢，同一篇公告/新闻一周内没必要重抓。

### 3. Agent 审计日志 (`agent_audit_log`)

目的：完整记录每次 Agent 运行的工具调用过程，可事后审计"Agent 看到了什么才做出这个判断"。

| 字段 | 说明 |
|------|------|
| `id` | 自增主键 |
| `agent_name` | `overview_scanner` / `researcher` / `verifier` / ... |
| `run_id` | 同一次运行共享一个 UUID |
| `target_date` | 日报日期（`2026-06-16`） |
| `round_num` | 第几轮 tool call |
| `tool_name` | `web_search` / `web_fetch` / `db_query` |
| `tool_args_json` | 调用参数 |
| `tool_result_json` | 返回值（截断前 2000 字符） |
| `tool_result_length` | 返回值完整长度 |
| `cache_hit` | 是否命中缓存（NULL = 非缓存类工具） |
| `latency_ms` | 工具调用耗时 |
| `created_at` | 时间戳 |

**逻辑**：Agent 的 `_execute_tool` 方法在执行前后各打一个点，写入 audit_log。这个不影响 Agent 逻辑，纯粹是旁路记录。

### 4. Agent 产物的版本管理

现有的几张表（`daily_overviews`、`research_results`、`analysis_reports`）已经是产物存储，但缺一个字段：

**给每张产物表加 `run_id` 字段**，关联到 `agent_audit_log.run_id`。这样你查出某天的简报有问题，能顺藤摸瓜找到是哪个 Agent、哪次搜索、看到了什么假新闻导致判断失误。

## 依赖关系

```
run_id (UUID，每次触发日报流程生成一个)
  ├── agent_audit_log (N 条，每个工具调用一条)
  │     ├── 命中 → search_cache / fetch_cache
  │     └── 未命中 → 真实搜索 → 写入缓存
  │
  ├── daily_overviews.run_id
  ├── research_results.run_id
  ├── analysis_reports.run_id
  └── brief_card_json → 飞书推送
```

## 对现有模块的改动范围

| 模块 | 改动 |
|------|------|
| `src/storage/sqlite.py` | 加 3 张表 DDL + CRUD 方法 |
| `src/agents/base.py` | `_execute_tool` 前后写 audit_log；run_id 生成 |
| `src/tools/web_search.py` | 调之前查 `search_cache`，调之后写 |
| `src/tools/web_fetch.py` | 调之前查 `fetch_cache`，调之后写 |
| `src/agents/*.py` | `run()` 传入 `run_id` |
| 现有产物表 | DDL 加 `run_id` 列（可选，不阻塞） |

## 不做的事情

- 不做向量化：「搜索过没」是精确匹配，不用相似度检索
- 不做缓存自动清理：SQLite 本地库，一个月几百条搜索，空间忽略不计
- 不做跨天复用策略：先做简单的，后续 CHECK 环节的渠道有效性分析再优化
