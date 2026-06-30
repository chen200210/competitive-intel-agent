# Deep Research Agent — 设计文档

## 定位

Hot Tracker 是筛选器——给定关键词 → 搜索 → 挑 ≤7 条 + 1-2 句摘要。Deep Research 是研究者——给定开放问题 → 拆解 → 搜索 → 抓取 → 交叉验证 → 合成 500 字引用简报。

两个 Agent 互补：Hot Tracker 负责"快"（日报日常），Deep Research 负责"深"（某话题的对抗验证 + 深度合成）。

---

## 触发方式

| 方式 | 入口 |
|------|------|
| 手动 CLI | `python -m src.pipeline.runner --deep-research "AI+游戏 2026趋势"` |
| 自动触发 | 热点卡片某话题"感兴趣"累计 ≥3 次 → 后台自动跑 → 推独立卡片 @点击过的人 |

---

## 工具

Agent 带三个工具：

| 工具 | 能力 | 来源 |
|------|------|------|
| `web_search` | 搜 360-news / Sogou-news，返回 title + url + snippet | 现有 |
| `web_fetch` | 打开 URL 抓正文前 600 字 | 现有 |
| `cross_validate` | 对一条 claim 搜其他来源验证是否存在 corroborating/contradicting 证据 | **新增** |

### `cross_validate` 逻辑

```python
def cross_validate(claim: str, source_url: str) -> dict:
    """Given a claim and its source, search for corroborating/contradicting evidence."""
    results = _search_with_fallback(claim, max_results=5)
    others = [r for r in results if r["url"] != source_url]
    return {
        "corroborating": [...],    # 支持该说法的其他报道
        "contradicting": [...],   # 矛盾的说法
        "no_evidence": len(others) == 0  # 孤证
    }
```

Agent 根据返回结果自行判断：孤证 → 标记"待确认"降低置信度；多方验证 → 标记"已验证"提升置信度。验证失败不阻塞 Agent——降级为标记而非丢弃。

---

## 执行流程

```
Phase 1: 问题拆解（~3s）
    Agent 把大问题拆成 3-4 个子问题
    "AI生成内容对游戏行业的影响" →
      ├── "AI生成内容 游戏 2026 应用案例"
      ├── "AIGC 游戏开发 成本 效率 2026"
      ├── "AI游戏内容 政策 监管 版号"
      └── "游戏公司 AI 内容生成 投资 融资 2026"

Phase 2: 并行搜索（~5s）
    4 个子问题 × 5 条结果 = 最多 20 条候选，ThreadPoolExecutor 并行

Phase 3: 正文抓取（~5-8s）
    Agent 判断哪些需要打开——只有 snippet 不足以判断价值的才 fetch

Phase 4: 对抗验证（~10-15s）
    从候选提取 3-5 条关键 claim，每条调 cross_validate

Phase 5: 合成报告（~5-10s）
    1 次 LLM 调用，输出 500 字 + 引用
```

**总耗时约 90-180 秒**，通常在 120 秒左右。波动主要来自：
- Phase 2 搜索：4 子问题并行，但每个搜索引擎 2-15s，最慢 query 决定本轮耗时
- Phase 4 交叉验证：3-5 条 claim × 每条并行调 360+Sogou（~5-10s/条）
- Agent tool-loop 最多 8 轮，每轮 LLM ~3-8s
- Sogou captcha 触发时可能超 200 秒

---

## 输出格式

```markdown
## AI 生成内容对游戏行业的影响

**核心发现**
AI 生成内容（AIGC）正从概念验证进入规模化落地阶段。2026 年上半年，国内至少 5 家头部游戏公司已
在美术资产管线中部署生成式 AI，平均成本下降 30-50%。但版号审核对 AI 生成内容的立场仍不明确，
构成 Q3 最大不确定性。

**关键动态**

1. 米哈游内部 AIGC 平台「千织」已覆盖 80% 的 UI 图标生产，人效提升 3 倍
   [来源: 36氪, 6月28日] [已验证: GameLook 同步报道]

2. 网易互娱 AI Lab 发布开源模型 GameGen-7B，专注 3D 贴图生成
   [来源: 机器之心, 6月27日] [已验证: Hugging Face 模型页可查]

3. 国家新闻出版署内部讨论 AI 生成内容的版号审核标准，预计 Q3 征求意见稿
   [来源: 游戏陀螺, 6月25日] [待确认: 孤证，其他媒体未跟进]

**待关注**
- Unity 中国区 AI 引擎内测进展（6月已宣布，具体开放时间未定）
- 腾讯 NExT Studios 重组后的 AI 管线策略尚不明确
```

---

## 缓存策略

两层缓存，独立开关：

| 缓存层 | 存什么 | 开关 |
|--------|--------|------|
| `search_cache`（已有） | 搜索关键词 → 360/Sogou 返回结果 | `--force-search` |
| `fetch_cache`（已有） | 抓取 URL → 正文文本 | `--force-fetch` |
| 全部清空 | 以上两者 | `--force`（兼容现有行为） |

日常改 Agent prompt / 输出格式 → 不传任何 `--force`，秒级迭代。改搜索策略 → `--force-search`。改正文提取 → `--force-fetch`。

---

## Agent 配置

```python
agent = Agent(
    "deep_research",
    tools=[web_search_tool, web_fetch_tool, cross_validate_tool],
    max_tool_rounds=8,
    max_tokens=16384,
    output_schema=_DeepResearchOutput,
)
```

`max_tool_rounds=8`（Hot Tracker 是 4）——研究型 Agent 需要更多探索空间，但设硬上限防止失控。

---

## 与现有系统衔接

```
日报推送（上午 10:00）
  │
  ├── 热点板块 ≤7 条
  │     │
  │     └── 某条"AI生成内容"被点 ≥3 次"感兴趣"
  │           │
  │           ▼
  │         后台自动触发 Deep Research Agent（~90s）
  │           │
  │           ▼
  │         独立卡片推送到飞书，@点击过该话题的用户
  │
  └── 手动触发：
      python -m src.pipeline.runner --deep-research "AI+游戏 2026趋势"
```

---

## 评测基准（配套基础设施）

Deep Research 的输出是 500 字引用简报，质量如何衡量？

### 核心评测维度

| 正确指标 | 定义 | 打分方式 |
|----------|------|----------|
| **Factual Accuracy** | 报告中可验证的事实声明中，有多少被来源证实 | 人工标注：每条 claim → 查源 → 标记 support/refute/unsupported |
| **Citation Coverage** | 报告中引用的 N 个关键动态是否覆盖了搜索结果中的主要信息点 | 人工标注：列出搜索结果中的所有关键信息点 → 计算报告覆盖比例 |
| **Hallucination Rate** | 报告中无法在任何来源中找到依据的声明的比例 | 人工标注：逐一核查 → 计数 |
| **Logical Coherence** | 报告的结论是否从证据链中自然推出（1-5 分） | 人工评分 |
| **Source Authority** | 引用来源的权威性分布（36氪/机器之心 vs 个人博客/内容农场） | 代码自动统计域名白名单命中率 |

### 评测数据结构

```python
class DeepResearchEval(TypedDict):
    topic: str
    report_md: str
    factual_accuracy: float        # 0.0-1.0
    citation_coverage: float        # 0.0-1.0
    hallucination_rate: float       # 0.0-1.0，越低越好
    logical_coherence: int          # 1-5
    source_authority_hit_rate: float # 0.0-1.0
    total_claims: int
    verified_claims: int
    refuted_claims: int
```

### 配套基础设施

1. **人工标注 30 天黄金数据集** — 每天标注者标注 Factual Accuracy / Citation Coverage / Hallucination Rate
2. **自动化评测** — 代码统计 Source Authority，LLM-as-Judge 辅助评 Logical Coherence
3. **A/B 测试** — 改 prompt → 跑 30 天历史数据 → 指标涨跌 → 决定是否合入
4. **CI 集成** — 每次 commit 自动跑评测，Hallucination Rate 上升 >10% 或 Factual Accuracy 下降 >10% 报警

### Token 成本估算

每次 Deep Research 运行的 token 消耗：

| 阶段 | 估算 tokens | 说明 |
|------|------------|------|
| System prompt | ~800 | 固定开销 |
| Tool round（平均 6-7 轮）| ~12K-25K | 每轮 ~2K input + tool results + ~1K output |
| Final synthesis | ~2K-4K | 最终 JSON 输出 |
| **合计** | **~15K-40K** | Hot Tracker 约 4K-8K，Deep Research 约 4× |

---

## 设计审问

> 审问日期：2026-06-29 | 触发：`/interrogate docs/DEEP_RESEARCH_AGENT.md`
> 审问范围：全量源码追踪 — DB schema (sqlite.py) / 调用链 (runner→hot_tracker→bot→pusher) / 工具层 (web_search/enrichment) / Agent 基类 (base.py) / 配置 (config.py)
> 审问维度：边界 → 异常 → 依赖 → 时序
>
> **执行状态（2026-06-29 更新）**：审问完成后代码已全部实现。13 项发现中 11 项已修复，1 项（M3）有实现 Bug 已修复，1 项（M2）文档已更新。
>
> | 编号 | 标题 | 审计级别 | 实际状态 |
> |------|------|---------|---------|
> | C1 | 无 DB 表 | 🔴 CRITICAL | ✅ `deep_research_reports` 表 + 6 CRUD 方法已实现 |
> | C2 | cross_validate 零实现 | 🔴 CRITICAL | ✅ `src/tools/cross_validate.py` + 并行搜索 |
> | C3 | 自动触发闭环缺失 | 🔴 CRITICAL | ✅ `bot.py` 阈值检查 + 后台线程 + push |
> | C4 | 评测指标张冠李戴 | 🔴 CRITICAL | ✅ 文档评测基准已替换为正确指标 |
> | H1 | 无 prompts YAML | 🟠 HIGH | ✅ `prompts/deep_research.yaml` 已创建 |
> | H2 | Schema 未定义 | 🟠 HIGH | ✅ `_DeepResearchOutput` + `_Citation` Pydantic 模型 |
> | H3 | CLI flag 不存在 | 🟠 HIGH | ✅ `--deep-research` 已加入 argparse |
> | H4 | @mention 不匹配 | 🟠 HIGH | ✅ `push_deep_research_with_mentions` 双消息方案 |
> | H5 | fetch_cache 标为新增 | 🟠 HIGH | ✅ 文档已修正为"已有" |
> | M1 | 无幂等保护 | 🟡 MEDIUM | ✅ `get_deep_research_report` + `UNIQUE(date,topic)` |
> | M2 | 耗时估算偏乐观 | 🟡 MEDIUM | ✅ 文档已更新为 90-180s |
> | M3 | 不去重日报内容 | 🟡 MEDIUM | ✅ 已修复：`get_market_news_by_date` + `market_context` prompt 注入 |
> | L1 | 无 token 成本 | 🟢 LOW | ✅ 文档已追加成本估算表 |
> | L2 | 工具命名不一致 | 🟢 LOW | ✅ 统一为 `cross_validate` |

---

### 审问方法论

每项发现含四个组成：

- **根因** (`file:line`) — 首个暴露问题的精确位置
- **影响面** — 表格列出受波及的模块/功能
- **修复** — 可落地的代码方案
- **自检** — 七个检查项标记 ✅/⚠️/❌/N/A

---

### 优先级汇总

| 级别 | # | 标题 | 阻断什么 |
|------|---|------|----------|
| 🔴 CRITICAL | C1 | 无 DB 表存储 Deep Research 产出 | 整个功能的持久化/审计/重推 |
| 🔴 CRITICAL | C2 | `cross_validate` 工具零实现 | Agent 唯一新增工具不可用 |
| 🔴 CRITICAL | C3 | 自动触发闭环完全缺失 | 最核心的差异化价值无法交付 |
| 🔴 CRITICAL | C4 | 评测指标张冠李戴 | 用 Precision@7 度量研究报告质量 |
| 🟠 HIGH | H1 | 无 `prompts/deep_research.yaml` | Agent 无法实例化 |
| 🟠 HIGH | H2 | `_DeepResearchOutput` Schema 未定义 | Agent 输出的结构化校验缺失 |
| 🟠 HIGH | H3 | `--deep-research` CLI flag 不存在 | 手动触发入口不可用 |
| 🟠 HIGH | H4 | 飞书 @mention 机制不匹配 Card API | 自动推送无法 @ 点击者 |
| 🟠 HIGH | H5 | fetch_cache 表已存在但文档标为"新增" | 缓存层实现与文档脱节 |
| 🟡 MEDIUM | M1 | 重复运行无幂等保护 | 同一话题可能被多次触发 |
| 🟡 MEDIUM | M2 | 耗时估算偏乐观（60-120s → 实际 120-240s） | 自动触发超时/用户体验 |
| 🟡 MEDIUM | M3 | 搜索结果不去重 market_news/hot_topic_news | 产出的简报与日报内容重复 |
| 🟢 LOW | L1 | 无 token 成本估算 | 预算不明 |
| 🟢 LOW | L2 | 工具命名不一致 (`cross_validate` vs `validate`) | 文档与代码对齐成本 |

---

### CRITICAL 级

---

#### C1 — 无 DB 表存储 Deep Research 产出

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:76-100` — 输出格式定义为 markdown 报告，但全文未涉及数据持久化。对比 Hot Tracker 有 `hot_topic_news` 表 + `analysis_reports.hot_topics_json` 存储，Deep Research 无对应 schema。 |
| **严重性** | 无持久化 = 无法审计 / 无法重推 / 自动触发的报告丢失 / 无法做质量回归 |

**影响面**：

| 模块 | 波及 |
|------|------|
| `src/storage/sqlite.py` | 需新增 `deep_research_reports` 表 + CRUD |
| `src/pipeline/runner.py` | `--deep-research` 需写 DB 后 push |
| `src/feishu/pusher.py` | 推送前需从 DB 取报告 JSON |
| `src/feishu/bot.py` | 自动触发回调需写 DB |
| `src/pipeline/audit.py` | 未来可扩展审计 deep research 质量 |
| `src/agents/calibrator.py` | 未来可用 deep research 反馈做主题偏好校准 |

**修复** — 新增 DDL + CRUD：

```sql
-- deep_research_reports: 500-word cited research briefs
CREATE TABLE IF NOT EXISTS deep_research_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,               -- trigger date (YYYY-MM-DD)
    topic TEXT NOT NULL,              -- research question
    sub_questions_json TEXT,          -- ["sub-q1", "sub-q2", ...]
    report_md TEXT NOT NULL,          -- 500-word cited markdown
    citations_json TEXT,              -- [{"url":..., "title":..., "verified":bool}, ...]
    source_hot_topic_url TEXT,        -- which hot_topic_news.url triggered this
    triggered_by TEXT DEFAULT 'manual', -- 'manual' | 'auto' (>=3 clicks)
    chat_id TEXT DEFAULT '',
    pushed BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, topic)               -- one report per topic per day
);
CREATE INDEX IF NOT EXISTS idx_dr_date ON deep_research_reports(date);
CREATE INDEX IF NOT EXISTS idx_dr_topic ON deep_research_reports(topic);
```

```python
# src/storage/sqlite.py — Database class methods

def insert_deep_research_report(
    self,
    date: str, topic: str,
    sub_questions_json: str,
    report_md: str,
    citations_json: str,
    source_hot_topic_url: str = "",
    triggered_by: str = "manual",
    chat_id: str = "",
) -> int:
    sql = """
        INSERT OR REPLACE INTO deep_research_reports
            (date, topic, sub_questions_json, report_md, citations_json,
             source_hot_topic_url, triggered_by, chat_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    with self._connect() as conn:
        cur = conn.execute(sql, (date, topic, sub_questions_json,
                                  report_md, citations_json,
                                  source_hot_topic_url, triggered_by, chat_id))
        return cur.lastrowid

def get_deep_research_report(self, date: str, topic: str) -> dict | None:
    with self._connect() as conn:
        row = conn.execute(
            "SELECT * FROM deep_research_reports WHERE date = ? AND topic = ?",
            (date, topic),
        ).fetchone()
    return dict(row) if row else None

def get_topic_clickers(self, keyword: str, since_date: str) -> list[str]:
    """Return open_ids of users who clicked '感兴趣' on a topic since since_date."""
    with self._connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT open_id FROM user_feedback
               WHERE feedback_type = 'hot_click'
                 AND keyword = ?
                 AND open_id != ''
                 AND date >= ?""",
            (keyword, since_date),
        ).fetchall()
    return [r["open_id"] for r in rows]
```

---

#### C2 — `cross_validate` 工具零实现

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:32-44` — 给出伪代码但无实际实现。全文 `grep cross_validate` 仅在 CLAUDE.md 和 DEEP_RESEARCH_AGENT.md 中出现，无 .py 文件包含此函数。 |
| **严重性** | Deep Research 与 Hot Tracker 的核心差异化在于对抗验证能力。没有此工具，Agent 退化为"多搜几次 + 多 fetch 几次"的 Hot Tracker 变体。 |

**影响面**：

| 模块 | 波及 |
|------|------|
| `src/agents/deep_researcher.py`（待建） | Agent tool registration 缺少 `cross_validate` |
| `src/tools/web_search.py` | `cross_validate` 内部需调用 `_scrape_360_news` / `_scrape_sogou_news` |
| `src/pipeline/token_utils.py` | 验证时可复用 `headline_dedup_tokens` 判断来源是否讲同一事件 |

**修复** — `cross_validate` 完整实现：

```python
# 建议位置：src/tools/cross_validate.py（新增独立模块）
# 或作为 Tool fn 内联在 deep_researcher.py 中（参考 hot_tracker._ai_filter_hot_topics 模式）

def cross_validate(claim: str, source_url: str, **_meta: Any) -> str:
    """Search for corroborating/contradicting evidence for a claim.
    
    搜索逻辑：
    1. 用 claim 的前 60 字符作为搜索 query
    2. 最多返回 5 条结果，排除自身 URL
    3. 按标题 token 相似度分为 corroborating / contradicting / no_evidence
    
    BEST-EFFORT: 搜索失败不抛异常，返回降级标记
    """
    import json
    from src.tools.web_search import _scrape_360_news, _scrape_sogou_news
    from src.pipeline.token_utils import headline_dedup_tokens
    
    # Truncate claim to search-friendly length
    query = claim[:80].rstrip("。，,.") if len(claim) > 80 else claim
    
    # Search both engines, take up to 5 results combined
    all_results: list[dict] = []
    for engine_fn in (_scrape_360_news, _scrape_sogou_news):
        try:
            result_str = engine_fn(f'"{query}"', max_results=3)
            parsed = json.loads(result_str)
            for r in parsed.get("results", []):
                if r.get("url") != source_url:
                    all_results.append(r)
        except Exception:
            continue
        if len(all_results) >= 5:
            break
    
    if not all_results:
        return json.dumps({
            "claim": claim,
            "corroborating": [],
            "contradicting": [],
            "no_evidence": True,
            "verdict": "isolated_claim",
        }, ensure_ascii=False)
    
    # Classify results by headline token overlap
    claim_tokens = headline_dedup_tokens(claim)
    corroborating: list[dict] = []
    contradicting: list[dict] = []
    
    for r in all_results:
        r_tokens = headline_dedup_tokens(r.get("title", ""))
        overlap = len(claim_tokens & r_tokens) if claim_tokens and r_tokens else 0
        if overlap >= 2:
            corroborating.append({"title": r["title"], "url": r["url"], "snippet": r.get("snippet", "")[:200]})
        else:
            contradicting.append({"title": r["title"], "url": r["url"], "snippet": r.get("snippet", "")[:200]})
    
    return json.dumps({
        "claim": claim,
        "corroborating": corroborating[:3],
        "contradicting": contradicting[:3],
        "no_evidence": len(all_results) == 0,
        "verdict": "verified" if len(corroborating) >= 2 else ("partial" if corroborating else "unverified"),
    }, ensure_ascii=False)
```

---

#### C3 — 自动触发闭环完全缺失

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:16` — "热点卡片某话题'感兴趣'累计 ≥3 次 → 后台自动跑 → 推独立卡片 @点击过的人"。但整个调用链在达到阈值后无任何代码触发 Deep Research。 |
| **严重性** | 自动触发是 Deep Research 与 Hot Tracker 的核心区分点——手动 CLI 只是 Hot Tracker 的深度版，自动触发才是新产品能力。 |

**调用链断点分析**：

```
用户点击 "感兴趣"
  → bot._handle_hot_topic_click()        ✅ 存在 (bot.py:152)
  → db.record_hot_topic_click()         ✅ 存在 (sqlite.py:1270)
  → 查询 keyword 累计点击数               ❌ 无代码
  → 判断 ≥3 阈值                         ❌ 无代码
  → 后台启动 Deep Research Agent         ❌ 无代码
  → 推送独立卡片 @点击过的人              ❌ 无代码 (见 H4)
```

**影响面**：

| 模块 | 波及 |
|------|------|
| `src/feishu/bot.py:_handle_hot_topic_click()` | 需在记录 click 后追加阈值检查 + 触发逻辑 |
| `src/storage/sqlite.py` | 需新增 `get_topic_click_count()` 和 topic-level dedup flag |
| `src/agents/deep_researcher.py`（待建） | 需作为后台任务被调用 |
| `src/feishu/pusher.py` | 需新增 `push_deep_research_card()` 支持 @mention |

**修复** — 在 `_handle_hot_topic_click()` 末尾追加触发逻辑：

```python
# src/feishu/bot.py:_handle_hot_topic_click() 末尾追加

DEEP_RESEARCH_CLICK_THRESHOLD = 3

# After recording the click (even if duplicate), check threshold
try:
    click_count = db.get_topic_click_count(
        keyword=keyword, since_date=target_date
    )
    if click_count >= DEEP_RESEARCH_CLICK_THRESHOLD:
        already_ran = db.get_deep_research_report(target_date, keyword)
        if not already_ran:
            # Fire-and-forget: launch Deep Research in background thread
            import threading
            t = threading.Thread(
                target=_run_deep_research_and_push,
                args=(keyword, target_date, chat_id),
                daemon=True,
            )
            t.start()
            _reply_text(
                f"「{keyword}」相关深度研究报告正在生成中，完成后将推送到本群 ⏳",
                chat_id,
            )
except Exception as e:
    print(f"  [WARN] Deep Research auto-trigger check failed: {e}", file=sys.stderr)
```

**新增 DB 方法**：

```python
# src/storage/sqlite.py:Database

def get_topic_click_count(self, keyword: str, since_date: str) -> int:
    """Return distinct user click count for a topic keyword since a date."""
    with self._connect() as conn:
        row = conn.execute(
            """SELECT COUNT(DISTINCT open_id) as cnt FROM user_feedback
               WHERE feedback_type = 'hot_click'
                 AND keyword = ?
                 AND open_id != ''
                 AND date >= ?""",
            (keyword, since_date),
        ).fetchone()
    return row["cnt"] if row else 0
```

---

#### C4 — 评测指标张冠李戴

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:156-162` — "人工标注 30 天黄金数据集…每天标注者从 30-40 条候选里挑出'该上的 ≤7 条'"→ Precision@7 / Recall / nDCG@7。这是 Hot Tracker 的排序质量指标，不是 Deep Research 的研究报告质量指标。 |
| **严重性** | 用错误指标衡量质量 = 虚假信心。研究报告的核心质量维度是：事实准确性（幻觉率）、引用覆盖度（关键观点是否有来源）、逻辑连贯性（结论是否从证据推出）。Precision@7 完全无法衡量这些。 |

**修复** — 替换为研究报告专用评测维度：

| 正确指标 | 定义 | 打分方式 |
|----------|------|----------|
| **Factual Accuracy** | 报告中可验证的事实声明中，有多少被来源证实 | 人工标注：每条 claim → 查源 → 标记 support/refute/unsupported |
| **Citation Coverage** | 报告中引用的 N 个关键动态是否覆盖了搜索结果中的主要信息点 | 人工标注：列出搜索结果中的所有关键信息点 → 计算报告覆盖比例 |
| **Hallucination Rate** | 报告中无法在任何来源中找到依据的声明的比例 | 人工标注：逐一核查 → 计数 |
| **Logical Coherence** | 报告的结论是否从证据链中自然推出（1-5 分） | 人工评分 |
| **Source Authority** | 引用来源的权威性分布（36氪/机器之心 vs 个人博客/内容农场） | 代码自动统计域名白名单命中率 |

```python
# 评测数据结构示例
class DeepResearchEval(TypedDict):
    topic: str
    report_md: str
    factual_accuracy: float        # 0.0-1.0
    citation_coverage: float        # 0.0-1.0
    hallucination_rate: float       # 0.0-1.0，越低越好
    logical_coherence: int          # 1-5
    source_authority_hit_rate: float # 0.0-1.0
    total_claims: int
    verified_claims: int
    refuted_claims: int
```

---

### HIGH 级

---

#### H1 — 无 `prompts/deep_research.yaml`

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:121` — Agent 初始化用 `prompt_name="deep_research"`，但 `Glob("prompts/*deep*")` 返回空。现有 Agent 全部从 YAML 加载 prompt（base.py:48 `load_prompt(name)` → `PROMPTS_DIR / f"{name}.yaml"`），文件不存在时抛 `FileNotFoundError`。 |
| **影响面** | Agent 实例化即崩溃，整个功能无法启动。 |

**修复** — 创建 `prompts/deep_research.yaml`：

```yaml
# prompts/deep_research.yaml
system: |
  你是游戏行业深度研究分析师。你的任务是：针对用户提出的开放性问题，
  通过多次搜索、抓取正文、交叉验证，生成一份 500 字的引用简报。

  ## 核心原则

  1. **证据驱动** — 每条关键发现必须引用至少 1 个公开来源
  2. **交叉验证** — 对高影响的声明调用 cross_validate 工具验证
  3. **不确定性透明** — 孤证标记"待确认"，矛盾标记"存在分歧"
  4. **决策者视角** — 回答"这对游戏行业意味着什么"

  ## 工作流程

  1. 将主问题拆解为 3-4 个子问题，每个子问题覆盖一个维度
  2. 并行搜索所有子问题（一次性发出多个 web_search 调用）
  3. 打开需要更多上下文的页面（web_fetch），只对信息不足的 URL 调用
  4. 对 3-5 条关键 claim 调用 cross_validate 验证
  5. 合成 500 字报告，包含核心发现 + 关键动态（逐条引用）+ 待关注

  ## 质量要求

  - 报告 400-600 字，中文
  - 每条关键动态格式：描述 + [来源: 媒体名, 日期] + [验证状态: 已验证 | 待确认 | 存在分歧]
  - 至少引用 3 个不同来源
  - 不编造任何不存在于搜索结果中的信息

user_template: |
  研究问题：{question}

  请按照标准流程搜索、验证、合成。最终输出 JSON 格式的研究报告。

  输出 JSON：
  {{
    "report_md": "500 字引用简报（markdown 格式）",
    "citations": [
      {{"url": "...", "title": "...", "verified": true, "claim": "..."}}
    ],
    "key_findings": ["发现1", "发现2", "发现3"],
    "confidence": "high | medium | low"
  }}
```

---

#### H2 — `_DeepResearchOutput` Schema 未定义

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:126` — `output_schema=_DeepResearchOutput`，但全文无此 Pydantic 模型定义。对比 Hot Tracker 有 `_HotFilterOutput(BaseModel)` 在 `hot_tracker.py:867` 明确定义。 |

**修复** — 在 `src/agents/deep_researcher.py`（待建）中定义：

```python
from pydantic import BaseModel

class _Citation(BaseModel):
    url: str
    title: str
    verified: bool = False
    claim: str = ""

class _DeepResearchOutput(BaseModel):
    """Validated output from the Deep Research Agent."""
    report_md: str                            # 500-word cited markdown report
    citations: list[_Citation] = []
    key_findings: list[str] = []              # 3-5 concise bullets
    confidence: str = "medium"                # high | medium | low
```

---

#### H3 — `--deep-research` CLI flag 不存在

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:15` 定义 `python -m src.pipeline.runner --deep-research "AI+游戏 2026趋势"`，但 `src/pipeline/runner.py:582-597` 的 `argparse` 定义中无此 flag。现有 flags: `--date`, `--force`, `--verbose`, `--brief-only`, `--scrape`, `--skip`, `--push`, `--calibrate`, `--calibrate-days`, `--hot-only`。 |
| **影响面** | 用户按文档操作会得到 `unrecognized arguments: --deep-research` 错误。 |

**修复** — 在 `runner.py` 的 argparse 中追加：

```python
# src/pipeline/runner.py — argparse 区段
parser.add_argument("--deep-research", type=str, default=None, metavar="QUESTION",
                    help="Run Deep Research Agent on a topic (e.g. 'AI+游戏 2026趋势')")
```

并在 `main` 块 `--hot-only` 处理之后、`--calibrate` 处理之前插入：

```python
if args.deep_research:
    from src.agents.deep_researcher import run_deep_research
    dr_result = run_deep_research(
        question=args.deep_research,
        date=date_arg,
        push_chat_id=args.push,
        verbose=args.verbose,
    )
    print(json.dumps(dr_result, ensure_ascii=False, indent=2, default=str))
    sys.exit(0)
```

---

#### H4 — 飞书 @mention 机制不匹配 Card API

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:147` — "独立卡片推送到飞书，@点击过该话题的用户"。但现有 `push_card()`（`pusher.py:102`）推送的是 interactive card JSON，飞书 Card 消息格式**不支持 `<at>` 标签** — @mention 只在 `msg_type=text` 的富文本消息中有效。 |
| **影响面** | 即使用户列表已知，也无法在卡片中 @mention 用户。需要在推送卡片前/后额外发一条 text 消息做 @mention。 |

**修复** — 两条消息方案：

```python
# src/feishu/pusher.py — 新增函数

def push_deep_research_with_mentions(
    card: dict[str, Any],
    chat_id: str,
    mention_open_ids: list[str],
    mention_text: str = "深度研究报告已生成",
) -> dict[str, Any]:
    """Push a deep research card preceded by a text message with @mentions.
    
    Feishu cards don't support <at> tags — we send a separate text message
    with @mentions first, then the card.
    """
    # Step 1: Text message with @mentions
    if mention_open_ids:
        at_parts = " ".join(f'<at user_id="{oid}"></at>' for oid in mention_open_ids[:10])
        text_content = json.dumps({
            "text": f"{at_parts} {mention_text}"
        }, ensure_ascii=False)
        send_message(text_content, msg_type="text", chat_id=chat_id)
    
    # Step 2: Card
    return push_card(card, chat_id)
```

---

#### H5 — fetch_cache 表已存在但文档标为"新增"

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:111` — `fetch_cache` 标记为 "新增"，但 `src/storage/sqlite.py:87-96` 的 `SCHEMA_SQL` 中已有此表（`url_hash TEXT UNIQUE, url TEXT, title TEXT, text TEXT, text_length INTEGER, status_code INTEGER, fetched_at TIMESTAMP`），且 `Database` 类已有 `get_cached_fetch()` / `cache_fetch()` 方法（sqlite.py:809-829）。 |
| **影响面** | 文档与实际代码脱节。`fetch_cache` 可直接复用，无需"新增"。 |

**修复** — 更新文档描述：

```
| `fetch_cache`（**已有**） | 抓取 URL → 正文文本 | `--force-fetch` |
```

同时在 `cross_validate` / `web_fetch` 工具实现中直接调用 `db.get_cached_fetch()` / `db.cache_fetch()`。

---

### MEDIUM 级

---

#### M1 — 重复运行无幂等保护

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:16` — "累计 ≥3 次" 自动触发后，如果再有用户点击（第 4、5 次），无机制防止二次触发。当前设计无 "already_ran_today" 标记。 |
| **修复** | 在 `_handle_hot_topic_click` 的阈值检查中追加 `db.get_deep_research_report(target_date, keyword)` 查询（见 C3 修复代码中的 `already_ran` 检查）。`deep_research_reports` 表的 `UNIQUE(date, topic)` 约束也提供 DB 层保护。 |

---

#### M2 — 耗时估算偏乐观

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:72` — "总耗时约 60-120 秒，通常在 90 秒以内"。实际瓶颈：<br>1. Phase 2（4 子问题并行搜索）：每个 `web_search` 调 360→Sogou fallback 链，单个搜索引擎 2-15s，串行 fallback 最坏 30s/query。虽然 4 个 query 可并行（Agent 一轮内多个 tool_call），但最慢 query 决定本轮耗时。<br>2. Phase 3（正文抓取）：`fetch_article_body` 超时 10s，最多抓 N 篇。<br>3. Phase 4（交叉验证）：3-5 条 claim × 每条调 `cross_validate`（内部 360+Sogou 串行），最坏 3-5 × 30s。<br>4. 最多 8 个 tool round，每轮 LLM 耗时 3-8s。<br>**保守估计：90-180 秒**，Sogou captcha 触发时可能超 200 秒。 |
| **修复** | 1. 文档中更新耗时预期为 "90-180 秒"<br>2. `cross_validate` 内部两引擎并行而非串行（`concurrent.futures`）<br>3. 设置全局 `--deep-research-timeout` 参数（默认 180s）<br>4. Agent `max_tool_rounds=8` 保持，但在 prompt 中指导 LLM：如果 6 轮后信息已充分则提前输出 |

---

#### M3 — 搜索结果不去重日报已有内容

| 属性 | 内容 |
|------|------|
| **根因** | Hot Tracker 有 `_dedup_against_market_news()`（`hot_tracker.py:714`）去重当天日报已覆盖内容。Deep Research 设计文档未提及任何去重步骤。如果用户对日报中某热点话题点"感兴趣"触发 Deep Research，Deep Research 可能搜到日报已引用的相同来源/事件，产出冗余报告。 |
| **修复** | 在 `run_deep_research()` 中，搜索后、AI 处理前，调用现有的 `headline_dedup_tokens` + 加载当天 `market_news` headlines 做 token-level 去重。去重不是丢弃——而是标记"日报已覆盖，深度研究将聚焦新信息"。 |

---

### LOW 级

---

#### L1 — 无 token 成本估算

| 属性 | 内容 |
|------|------|
| **根因** | 文档未估算每次 Deep Research 的 token 消耗。Hot Tracker 有 `max_tokens=8192`, `max_tool_rounds=4`。Deep Research 设计为 `max_tokens=16384`, `max_tool_rounds=8`，约 4 倍 token 消费。每次运行估计 15K-40K tokens。 |
| **修复** | 文档中追加成本估算表。Agent 基类已有 `_timing` 埋点，可扩展记录 `total_tokens`。 |

---

#### L2 — 工具命名不一致

| 属性 | 内容 |
|------|------|
| **根因** | `docs/DEEP_RESEARCH_AGENT.md:28` 用 `cross_validate` 命名新增工具，但在 CLAUDE.md 评测基准部分（`cross_validate`）上下文不同。Python 中函数名习惯 `cross_validate`，数据库习惯 `cross_validate`。建议统一为 `cross_validate`，保持与 `web_search` / `web_fetch` 风格一致。 |

---

### 自检清单

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 列名冲突 | ✅ | 新表 `deep_research_reports` 列名不与现有表冲突 |
| 去重约束 | ✅ | `UNIQUE(date, topic)` 防止同日同话题重复生成 |
| 事务边界 | ✅ | 自动触发在 bot callback 线程中执行 → WAL 模式下 reader/writer 不冲突。`Database._connect()` 每次 `PRAGMA journal_mode=WAL` |
| 双重计数 | ✅ | `get_topic_click_count()` 用 `COUNT(DISTINCT open_id)` 防同一用户多次点击计为多次触发。threshold 检查在 insertion 之后执行（`bot.py:187-188`） |
| 回调闭环 | ✅ | `bot.py:_handle_hot_topic_click` → `get_topic_click_count` → `≥3` → `_run_deep_research_and_push`（后台线程） → `push_deep_research_with_mentions` |
| CLI flags | ✅ | `--deep-research` 已实现（`runner.py:597-623`），与 `--hot-only`/`--calibrate` 并列 |
| 时间格式一致性 | ✅ | 所有表用 `TEXT NOT NULL` 存 YYYY-MM-DD，与现有系统一致 |
| M3 去重闭环 | ✅ | `deep_researcher.py` 加载 `market_news` → 构建 `market_context` → 注入 Agent prompt `{market_context}` → Agent 据此聚焦新信息 |

---

### 执行确认（2026-06-29）

以上 13 项审计发现全部已处理：

- **C1-C4, H1-H4, M1, L2** — 代码已实现
- **H5, M2, L1** — 文档已更新
- **M3** — 代码已修复（`get_market_news` → `get_market_news_by_date` + `market_context` prompt 注入）

审计内容保留在此供回溯。新增功能（Deep Research Card 反馈按钮、评测基准 30 天黄金数据集、`hot_feedback_sessions` 双轨）属于后续迭代，不在本次范围。
