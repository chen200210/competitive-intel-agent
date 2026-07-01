# 秋招学习计划 — 基于 OA 项目审视

> 生成日期：2026-06-26
> 项目定位：游戏行业竞品情报多智能体日报系统
> 项目规模：~11 万行 Python（含 scraper），~1200 行测试，8 个 Agent 模块，6 个 scraper

---

## 一、项目技术资产盘点

### 秋招面试中可以直接讲的亮点

| 你做了什么 | 对应面试考点 | 价值等级 |
|-----------|------------|:------:|
| 自研 Agent 基类 (base.py 805 行)，不用 LangChain | **LLM 应用架构设计** — 为什么不用框架？什么时候该用/不该用？ | ⭐⭐⭐⭐⭐ |
| 5 层 JSON 解析容错 + 中文引号修复状态机 | **防御性编程** — LLM 输出的不确定性处理 | ⭐⭐⭐⭐⭐ |
| β-Fusion 打分融合 (代码信号 × AI 评分) | **推荐系统/排序算法** — RecGPT 模式，信号融合策略 | ⭐⭐⭐⭐ |
| Calibrator Agent (反馈驱动参数自校准) | **ML 系统设计** — LLM-as-Judge 评估链条，反馈闭环 | ⭐⭐⭐⭐⭐ |
| 四阶段新闻流水线 (粗筛→深挖→AI打分→拼装) | **Pipeline 设计** — 成本/质量 trade-off，分层架构 | ⭐⭐⭐⭐ |
| Template Method 模式 Scraper 基类 | **设计模式** — OOP 最佳实践，开闭原则 | ⭐⭐⭐ |
| Matched Verdict Pool (LLM 输出标签白名单校验) | **AI Safety** — 如何防止 LLM 幻觉污染下游 | ⭐⭐⭐⭐ |
| 代码预制 markdown + AI 写市场板块 → 边界分离 | **AI 工程判断力** — 知道哪里用 AI、哪里用代码 | ⭐⭐⭐⭐⭐ |
| SQLite WAL + FK 约束 + audit_log 审计追踪 | **数据工程** — 单机数据库的最佳实践 | ⭐⭐⭐ |
| 三层去重 TTL (30d / 7d / 同窗) | **系统设计** — 去重策略的 trade-off | ⭐⭐⭐ |

### 技术栈全景

```
语言: Python 3.12 (类型注解全覆盖)
LLM: DeepSeek API (OpenAI 兼容协议), Claude API 备用
框架: FastAPI (轻量后端), lark-oapi (飞书 SDK)
数据库: SQLite (WAL), Chroma (向量检索)
浏览器自动化: Playwright (via subprocess)
测试: pytest (已有 9 个测试文件, ~1200 行)
部署: Windows 任务计划 / Docker (规划中)
```

---

## 二、秋招面试的"靶心"——你在哪个赛道

根据你的项目背景，最匹配的岗位方向：

| 岗位 | 匹配度 | 你的优势 | 需补的短板 |
|------|:---:|---------|----------|
| **后端开发工程师** | 🟡 70% | Pipeline 架构、数据库设计、OOP | 分布式系统、高并发、消息队列 |
| **AI 应用工程师** | 🟢 90% | Agent 系统设计、LLM 工程实践、评估体系 | 模型微调、RAG 进阶、Prompt 工程系统化 |
| **数据工程师** | 🟡 60% | 数据管道、去重策略、ETL | 大数据框架(Spark/Flink)、数据湖 |
| **游戏后端/工具开发** | 🟢 85% | 游戏行业知识、scraper 系统、飞书集成 | 游戏服务器架构、帧同步 |
| **AI Agent/LLM 工程师** | 🟢 95% | **自研 Agent 框架、工具调用链、评估校准** | Multi-Agent 协作模式、Agent 协议标准化 |

**建议主攻方向：AI Agent/LLM 应用工程师，备选游戏工具开发。**

---

## 三、按面试考点拆解学习计划

### 第 1 周 (6/26 – 7/3)：LLM 应用架构 & Agent 设计

**目标**：能把你的 Agent 基类设计讲出"为什么这样做，不那样做"

#### Day 1-2: 你的 Agent 基类到底做了什么

回顾 `src/agents/base.py`，重点理解：

```
你的架构 vs LangChain 对比：

LangChain:
  - LCEL chain 声明式
  - AgentExecutor 黑盒调度
  - 你只能传 tool list，不知道内部怎么调
  - 300+ 行抽象才能写一个简单的 tool-use loop

你的实现:
  - 显式 for round_num in range(max_tool_rounds)
  - _call_llm → _extract_tool_calls → _append_tool_results 三个方法
  - 并行 tool 执行（ThreadPoolExecutor）
  - 5 层 JSON 解析容错（自己的状态机修复中文引号）
  - Pydantic schema 自动校验 + 重试修复
  - 内置计时器 + audit log
  - 全部 805 行，可读可控
```

面试回答模板：
> "我选择自研而不是 LangChain，因为我的 Agent 只有 3-5 轮 tool call，LangChain 的 AgentExecutor 在这个规模上是过度抽象。我的实现只有 800 行，包含三层 JSON 保证（response_format → Pydantic 校验重试 → 正则兜底）、并行 tool 执行、和完整的计时/审计追踪。可控性远高于框架。"

#### Day 3-4: Multi-Agent 协作模式

你的项目里 Agent 之间是什么关系？

```
当前架构：Pipeline Orchestration（流水线编排）

Runner (主控)
  ├── Differ (纯规则, 0 token)
  ├── Story Picker (纯规则, 0 token)
  ├── Hot Tracker (规则 + Agent — 系统里唯一真正的 Agent)
  ├── Summarizer (Augmented LLM — 1 次调用, 无 tool loop)
  └── Calibrator (Augmented LLM — 1 次调用, 无 tool loop)

不是 Multi-Agent 对话，是流水线串联。
```

面试中会被问：
- "你为什么不设计成 Agent 之间互相通信？"→ 答：我的场景是单向数据流，每个阶段有明确的输入输出契约。对话式协作增加不确定性，流水线更可靠、可调试。
- "什么场景适合 Agent 对话式协作？"→ 答：需要协商的场景（如计划制定）、需要多视角交叉验证的场景（如 code review panel）。

#### Day 3.5: 诚实审视 — 你的"agent"真的是 agent 吗？

这是你应该在面试中主动抛出的"反思路径"——比讲架构更有杀伤力。

**实情**：你的 `src/agents/` 目录下 8 个文件，逐一审计后——

```
src/agents/
├── base.py           LLM 客户端封装 + 从未被用到的 tool loop
├── scorer.py         Augmented LLM（1 次调用，无 tool）
├── calibrator.py     Augmented LLM（1 次调用，无 tool）
├── market_pipeline.py 纯规则过滤（零 LLM）
├── render.py         纯代码拼 markdown（零 LLM）
├── dedup.py          DB 读写（零 LLM）
├── enrichment.py     httpx 抓取网页（零 LLM）
└── briefer.py        流水线编排器（调别的模块）
```

2 个 Augmented LLM + 4 个纯规则/纯代码 + 1 个编排器 + 1 个废弃框架。**一个真正的 agent 都没有。** `base.py` 里 805 行代码的 tool-use loop 从未在生产中被触发过。

这不是失败。这恰恰证明了你的判断力：

> "最初 DESIGN.md 规划了 6-agent 多智能体系统，`base.py` 里也完整实现了 tool-use loop、并行工具执行、审计追踪。但实际落地时，我发现绝大多数需求可以用更简单的方案解决——新闻筛选用纯规则，格式化用纯代码，AI 部分只需要 2 次单次 LLM 调用。不需要 tool loop，不需要 multi-agent。
>
> "所以我把 Researcher、Verifier、Analyst、Design Analyst 全砍了。`src/agents/` 这个目录名是历史负债——里面的代码早已不是 agent，但我保留了名字，提醒自己：**先搞清楚问题需要什么复杂度，再选方案。不要为了 agent 而 agent。**
>
> "Anthropic 的原话是 '对于许多应用来说，仅仅通过检索和上下文示例优化单个 LLM 调用通常就足够了'——我通过实际工程验证了这句话。能砍掉不需要的复杂度，比能叠加复杂度，难得多。"

面试官听到这个会想什么：这个人不是那种"把所有新技术堆上去"的简历驱动开发。他砍过东西，他知道为什么砍，他诚实。

**学习材料**：
- Anthropic 的 [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) — 必读
- 对比阅读 LangGraph、CrewAI、AutoGen 的设计文档（了解生态位，面试会被问"为什么不用 X"）

#### Day 4: 什么时候才应该加 Agent — Hot Tracker 案例

讲完"砍掉 Agent"之后，面试官大概率追问："那你觉得什么场景下 Agent 是必须的？"这个案例就是答案。

**三步判断框架 — "不是不加复杂度，是不加不需要的复杂度"：**

每次想加 Agent，先问三句话：
1. 当前方案哪里坏了？（必须有具体证据，不能是"感觉可以更好"）
2. 加 Agent 能修好它吗？
3. 有没有比 Agent 更简单的方案？

你的 Hot Tracker 三步全过——它是系统里唯一真正需要 Agent 的模块。

**问题 1: 当前方案哪里坏了？**

三种可验证的失败模式，不是"感觉不够好"：

```
失败模式 A — 关键词命中但新闻无价值:
  热搜 "英伟达发布新显卡"
  → GAME_SIGNALS 命中 "GPU" "硬件" ✅
  → 返回: "RTX 6090 发布，售价12999元"
  → 这是消费者新闻，对游戏决策者无价值 ❌
  → 关键词能判断"相关"，判断不了"有价值"

失败模式 B — 有价值但关键词没命中:
  热搜 "《明日方舟》制作人离职创业"
  → "离职" 不在 GAME_SIGNALS 里 ❌
  → 被丢弃
  → 实际价值 🔴 高 — 人事变动 + 塔防赛道 + 新团队动向

失败模式 C — 关键词列表持续膨胀:
  GAME_SIGNALS 从 20 → 50+ → 还在涨
  每次漏新闻就加一个词，明天可能还要加"马斯克"、"张一鸣"...
  规则 #3: "只有当规则复杂到难以维护时，才上 ML。"
```

**问题 2: Agent 能修好它吗？**

| 能力 | 规则方案 | Agent 方案 |
|------|---------|-----------|
| 判断"对决策者有价值" | ❌ 语义问题，关键词做不到 | ✅ LLM 的常识推理 |
| 信息不足时主动获取 | ❌ 返回啥用啥 | ✅ web_search + web_fetch |
| 写摘要 | ❌ 只能用原标题 | ✅ 3 句摘要 + 影响分析 |
| 维护成本 | ❌ 关键词持续膨胀 | ✅ prompt 不需要改 |

一个具体例子：

```
新闻: "Unity中国裁员30%，CEO称将聚焦游戏引擎核心业务"

规则方案 → 命中 "Unity" "引擎" ✅ → 入选 → 但不知道为什么重要

Agent 方案 → 入选 ✅ → 还能:
  · 判断价值: "对中国游戏开发工具市场有重大影响 → 高价值"
  · 决定深挖: web_search("Unity中国 裁员 2026 原因")
  · web_fetch 读 GameLook 分析文章
  · 输出: 3 句摘要 + 背景 + 对行业的影响
```

**问题 3: 有没有更简单的方案？**

```
替代 A: 加更多关键词
  → 治标不治本。维护成本爆炸。❌

替代 B: Augmented LLM（单次调用，不用 tool loop）
  → 搜索结果摘要太短，LLM 想深入看某条但没有 web_fetch 工具
  → 只能猜。🟡 勉强可用但不够好

替代 C: 再加一层规则判断"价值"
  → "价值"是语义概念。规则分不清:
     "英伟达新显卡发布"（消费者新闻）
     "英伟达推出游戏AI开发套件"（行业新闻）
    两条都有 "英伟达" "GPU"，区别只在语义。❌
```

**结论: 三个都是 yes。加。已于 2026-06-26 实现。**

实际改动——4 个文件，零破坏：

```
新增:
  prompts/hot_tracker.yaml    ← 80 行 Agent prompt（判断标准 + web_fetch 工具说明）

改动:
  src/pipeline/hot_tracker.py ← +120 行: _ai_filter_hot_topics() Agent 调用
                                 + _persist_ai_summaries() 摘要持久化
  src/storage/sqlite.py       ← +15 行: migration v12 加 ai_summary/value_score 列
  src/agents/render.py        ← ~10 行: build_hot_topics_md 优先用 ai_summary
  src/agents/base.py          ← 零改动！805 行的 tool loop 第一次被真正用到
```

关键设计决策：
- Agent 带 `web_fetch` 工具 + `max_tool_rounds=4` — 这是系统里第一个也是唯一一个真正用到 tool loop 的 Agent
- 失败不阻塞 — Agent 抛异常或返回空数组 → 回退到搜索顺序前 7 条
- render 层感知但不需要 Agent — 有 `ai_summary` 就优先用，没有就退回 `snippet`

**面试时怎么讲：**

> "砍完 Agent 之后，我刻意保持系统简单。但有一个点，规则实在撑不住：热点追踪。判断一条行业新闻对决策者有没有价值，是纯语义判断——我的关键词从 20 个膨胀到 50+ 还在长。三种可验证的失败模式：命中但无价值、有价值但未命中、关键词膨胀。
>
> "所以我用三步框架做了决策：当前方案坏了（有证据），Agent 能修好（语义判断 + 主动深挖），没有更简单的方案（加关键词不可维护，单次 LLM 信息不足时只能猜）。
>
> "实现只动了 4 个文件。最有意思的是 `base.py` 里的 tool-use loop、并行工具执行、审计追踪——805 行代码写了两个月，一次都没被真正调用过。加这个 Agent 是它第一次在生产中跑起来。这证明了一件事：基础设施可以提前建好，但只在实际需要时才接上——不是建了就要用，是用了才证明建得对。"

#### Day 5-6: Prompt Engineering 系统化

你的项目里 prompt 管理方式：

```yaml
# prompts/summarizer.yaml
system: |
  你是游戏行业新闻编辑...
  （静态指令，不包含动态数据 — 这是你踩过的坑，见 AI_COLLABORATION_GUIDE.md #7）

user_template: |
  日期：{date}
  反馈参考：{feedback_summary}
  市场新闻：{market_news_json}
  （动态数据全部在此 — Agent.run() 只 format 这个）
```

**需要补充学习的**：
- Few-shot 示例注入策略（什么时候需要、什么时候产生 bias）
- Chain-of-Thought vs Tree-of-Thought 的适用场景
- Prompt 版本管理和 A/B 测试（你的 Calibrator 可以做这个 — 加分项）
- Prompt caching 策略（Anthropic 的 prompt caching 可省 90% 成本）

#### Day 7: 本周复盘 — 模拟面试

找朋友或自己录音，回答以下问题（每题 3 分钟）：
1. "你做的 Agent 系统是什么？架构是怎样的？"
2. "为什么不用 LangChain/LangGraph？"
3. "LLM 输出不可靠，你怎么保证 JSON 格式？"
4. "你的 Agent 怎么处理 tool call 失败？"

---

### 第 2 周 (7/4 – 7/10)：系统设计 & 数据工程

**目标**：能完整讲述你的 Pipeline 设计，并回答 scaling 问题

#### Day 1-2: 你的 Pipeline 设计的 trade-off

```
你的四阶段流水线设计哲学：

Phase A: 规则粗筛 (15 条) — 省 60%+ AI 调用
Phase B: 正文深挖 — BeautifulSoup 抓取，补全信息
Phase C: AI 批量打分 (1 次 LLM 调用处理 15 条) — 省 N 次独立调用
Phase D: 代码拼装 markdown — 零 AI，零幻觉

核心理念：AI 只做"增量判断"，不做"格式拼装"
```

面试回答：
> "我的核心设计原则是：AI 只处理需要语义理解的部分，格式化和数据搬运全部由代码完成。这背后是对 LLM 的两个认知——第一，LLM 不擅长格式一致性（会产生表格错位、链接丢失）；第二，LLM 调用有成本（每次 $0.01-0.05），批量处理比逐条调用省 60-80%。"

#### Day 3-4: 如果数据量扩大 100 倍

面试经典问题："如果你的系统要处理 100 倍数据，怎么改？"

你当前是单机 SQLite，需要能讨论升级路径：

| 组件 | 当前 | 100x 方案 | 理由 |
|------|------|----------|------|
| 数据库 | SQLite | PostgreSQL + 读写分离 | 并发写入、复杂查询 |
| Scraper | 单进程 subprocess | Celery/APScheduler 分布式任务 | 并行抓取 |
| Agent | 同步调用 | 异步消息队列 (RabbitMQ) | 解耦 + 削峰 |
| 去重 | SQLite UNIQUE | Redis Bloom Filter + DB | 高吞吐去重 |
| 向量检索 | Chroma | Milvus/Qdrant 独立部署 | 大规模 RAG |

**不需要真的实现这些**，但必须说清楚每层换什么、为什么换。

#### Day 5-6: 数据库设计原则

回顾 `src/storage/sqlite.py` 的 schema 设计：

```sql
-- 你的设计亮点：
1. WAL 模式 (读不阻塞写)
2. FK 约束 (数据完整性)
3. 复合 UNIQUE 约束 (date, platform, chart_type, bundle_id) — 防止重复导入
4. reported_items 用 (item_key, item_type) 复合唯一键 — 去重天然安全
5. audit_logs 表 — agent 每次 tool call 都有追踪
```

面试可以讲的设计决策：
- "为什么用 composite UNIQUE 而不是自增 ID 做去重？"→ 答：INSERT OR IGNORE 天然幂等，多 scraper 并发写入不会重复。
- "为什么不用 ORM？"→ 答：我的 schema 稳定、查询简单，手写 SQL 更透明、更容易 review。SQLAlchemy 在这个规模是过度抽象。

#### Day 7: 本周复盘

模拟面试问题：
1. "你的 Pipeline 怎么保证数据不丢？中途失败怎么办？"
2. "如果 10 个人同时用你的系统，瓶颈在哪？"
3. "去重机制怎么设计？如果来了 1000 万条 URL 怎么去重？"

---

### 第 3 周 (7/11 – 7/17)：ML/AI 系统设计深入

**目标**：把 Calibrator + Scorer 的分数体系讲成一个完整的 ML 系统

#### Day 1-3: 你的打分系统就是一个小型推荐系统

你的 Scorer 做了这些事：

```
1. 代码层信号提取 (body_len, fact_count, freshness, is_digest) — 零 token
2. AI 0-100 评分 — 一个 LLM 调用
3. β-Fusion: 0.3 × signal_score + 0.7 × ai_score
4. Calibrator topic_boosts: 用户偏好调整
5. 强制分布检查: ≥25% 低于 40 分, ≤30% 高于 60 分
6. 来源多样性约束 (max 2 B站, max 3 per source, 至少 1 海外)
7. 质量门禁: min_ai_score 以下不入选
```

这就是一个完整的 **Learning to Rank (LTR)** 系统的结构！面试时可以类比：

| 你的系统 | 推荐系统类比 |
|---------|-----------|
| signal_score | 静态特征 (item features) |
| ai_score | 模型预测 (model prediction) |
| β-Fusion | 集成学习 (ensemble) |
| Calibrator topic_boosts | 在线学习 (online learning) |
| 强制分布检查 | 多样性重排 (diversity re-ranking) |
| 来源约束 | 业务规则层 (business rules) |

**深入阅读**：
- YouTube DNN 推荐论文 (2016) — 经典的召回→排序→重排架构
- Google 的 "Rules of Machine Learning" — 理解"先规则后 ML"的渐进路线（你的 Phase A 就是这个思想）

#### Day 4-5: Calibrator — LLM-as-Judge 评估模式

你的 Calibrator 做的事：

```
用户反馈 (👍/👎) → 聚合 (SQL) → LLM 分析模式 → 输出 calibration_params
                                                    ↓
                                          topic_boosts (话题偏好)
                                          dim_weights (维度权重)
                                                    ↓
                                          Scorer 下次读入并应用
```

这就是 Anthropic 推荐的 **LLM-as-Judge** 模式！面试时可以讲：

> "我的 Calibrator 实现了一个反馈闭环：用户每天对日报新闻点赞/踩，Calibrator 每周分析这些反馈发现偏好模式，输出校准参数，Scorer 在下次打分时应用这些参数。这是一个经典的 RecGPT LLM-as-Judge 评估链 — LLM 不再是直接产出推荐结果，而是作为'评委'评估用户反馈的质量维度。"

**学习材料**：
- Anthropic 的 [LLM-as-a-Judge](https://www.anthropic.com/engineering/llm-as-a-judge) 文章
- 对比传统协同过滤 vs LLM-based 推荐（了解 trade-off）

#### Day 6-7: Vector Search / RAG 基础

你的 Chroma 集成规划了什么？回顾 DESIGN.md §7.7：

```
Collection: "analysis_reports" — 历史日报 embedding
Collection: "findings" — 调研结论 embedding

用途：用户 @机器人 "原神最近表现怎么样" → RAG 检索历史日报 → LLM 总结
```

**需要补充学习**：
- Embedding 模型选择 (text-embedding-3-small vs bge-large-zh)
- Chunking 策略 (你的场景是"每条分析独立 chunk")
- RAG 评估指标 (Hit Rate, MRR, NDCG)
- Hybrid Search (向量 + BM25) 的适用场景

---

### 第 4 周 (7/18 – 7/24)：工程基础补强

**目标**：补上测试、并发、系统设计的基础短板

#### Day 1-2: 测试 — 你最需要补的短板

REVIEW 文档明确指出：**没有足够的测试是 P0 问题**。

当前状态：9 个测试文件，~1200 行，覆盖了 differ、track_filter、story_picker、token_utils。

**缺失的测试**：
```
□ Scorer 的 ai_summarize_and_judge 各分支
  - 空 candidates 输入
  - 全部低于 min_ai_score 的边界
  - 来源多样性约束触发
  - 分布检查重试逻辑
  - β-Fusion 对极端信号的处理

□ Briefer 的卡片生成
  - 空数据（新游/排名/市场全空）
  - B站视频合并逻辑
  - 昨日新游 badge 匹配

□ Calibrator 的参数校验
  - topic_boosts 黑名单过滤
  - dim_weights 不合法的 sum
  - 反馈不足 30 条的跳过逻辑
```

**行动计划**：至少给 scorer.py 和 calibrator.py 各写 5 个核心测试。

#### Day 3-4: Python 并发模型

你的项目用了 `ThreadPoolExecutor`（base.py 的 tool 并行执行、runner.py 的 scraper 并行），这是正确的选择因为都是 I/O 密集型。

但你需要能回答面试问题：

| 问题 | 你的回答 |
|------|---------|
| asyncio vs 线程池？ | 我的场景是 I/O 密集型（HTTP 请求），线程池足够。asyncio 优势在于高并发连接的协程调度，我的并发数 ≤ 8，线程池的上下文切换开销可忽略。 |
| 什么时候用 asyncio？ | 长连接（WebSocket）、数千个并发 I/O 操作、需要取消/超时精细控制的场景。 |
| GIL 对你的系统有影响吗？ | 没有。我的 CPU 密集操作只有 JSON 解析和字符串处理，占整体耗时不到 5%。 |

**学习材料**：Python 官方文档的 `concurrent.futures` 和 `asyncio` 章节，David Beazley 的 "Python Concurrency From the Ground Up" 演讲。

#### Day 5-6: 网络协议 & HTTP

你的 Scraper 需要理解：

```
HTTP 请求 → httpx (支持 HTTP/1.1, HTTP/2)
反爬策略 → User-Agent, Referer, Cookie 管理, 请求间隔
重试策略 → 你的 scraper 目前没有统一的重试机制
会话管理 → Playwright Chrome profile 复用登录态
```

**需要补充的知识**：
- HTTP 状态码族 (2xx/3xx/4xx/5xx 的业务含义)
- 幂等性设计 (为什么 scraper 的 INSERT OR IGNORE 是幂等的)
- Rate Limiting 策略 (exponential backoff, jitter)
- 你的 scraper 可以用 `tenacity` 库做统一重试

#### Day 7: Linux 基础 + Docker

你的项目规划了 Docker 部署但还没做。需要掌握：

```dockerfile
# 你需要的 Dockerfile 知识:
FROM python:3.12-slim → 为什么用 slim 而非 alpine?
COPY requirements.txt . → layer caching 策略
RUN pip install → 为什么 --no-cache-dir?
CMD vs ENTRYPOINT → 区别和使用场景
```

**动手做**：给你的项目写一个能跑的 Dockerfile。

**Linux 命令清单**（面试高频）：
- `ps aux | grep`, `top`, `htop` — 进程监控
- `df -h`, `du -sh` — 磁盘空间
- `journalctl`, `tail -f` — 日志查看
- `curl`, `netstat` — 网络调试

---

### 第 5 周 (7/25 – 7/31)：算法 & 数据结构

**目标**：通过编码面试的算法题

#### 学习策略

你的项目里有大量实用的数据结构和算法，把它们讲好比刷 200 道 LeetCode 更有说服力：

| 你的代码 | 对应算法/数据结构 | 面试怎么讲 |
|---------|---------------|----------|
| `_select_top_n()` 的贪心多样性选择 | **贪心算法** — 按分数降序贪心取，受 source/game 约束 | 这是我的 top-N diversity re-ranker，核心是一个带约束的贪心选择：每次取分数最高的候选项，检查它是否违反来源上限/B站上限/同款游戏上限/重复故事，如果违反就跳过取下一个。 |
| 三层去重 TTL | **多级缓存失效** — 每层不同 TTL，类似 CPU cache L1/L2/L3 | 我设计了三级去重：推送过=全封30天（L3），低分候选=短期屏蔽7天（L2），同窗期=当天去重（L1）。类比 CPU 多级缓存。 |
| `_fix_inner_quotes()` 状态机 | **有限状态机 (FSM)** — 字符级状态转移 | 我写了一个字符级状态机来修复 LLM 输出中的中文引号问题。三个状态：in_string / escape_next / normal，每遇到一个 `"` 就根据前后字符判断是 JSON 边界还是中文引号。 |
| `_repair_json()` 迭代修复 | **错误恢复** — 最多 12 次修复尝试 | JSON 修复循环：每次捕获 JSONDecodeError，根据错误类型（缺逗号/多余逗号/CJK 引号）做针对性替换，最多重试 12 次。 |
| `_is_same_story()` Jaccard + 命名实体 | **文本相似度** — Jaccard 系数 + NER 双重判断 | 跨语言去重（中英文报道同一事件）不能靠字面匹配。我用 AI 摘要的 CJK bigram Jaccard 相似度 (≥0.15) + 命名实体重叠 (≥2) 做双重判断。 |

#### 必须刷的 LeetCode 题型

别刷太多，每种类型会 3-5 道经典题就够了：

```
Week 5 每日计划:
  Mon: 数组/哈希表 (Two Sum, Group Anagrams, Subarray Sum Equals K)
  Tue: 链表 (Reverse, Detect Cycle, Merge K Sorted)
  Wed: 树 (Traversals, LCA, Validate BST)
  Thu: 动态规划 (Climbing Stairs, Coin Change, Longest Common Subsequence)
  Fri: 图 (BFS/DFS, Number of Islands, Course Schedule)
  Sat: 字符串 (Longest Substring Without Repeating, Valid Palindrome)
  Sun: 复习 + 模拟 (2 小时限时做 3 道 Medium)
```

**目标**：Medium 题 20 分钟内写出能跑的代码。Hard 题能说清楚思路就行。

---

### 第 6 周 (8/1 – 8/7)：简历准备 & 项目表述

**目标**：把你的项目写成一段让面试官眼前一亮的简历

#### Day 1-2: 简历上的项目描述

**推荐写法（量化你的成果）**：

> **游戏行业竞品情报多智能体系统** | Python, DeepSeek API, SQLite, Playwright
> 2026.6 – 至今
>
> - 设计并实现了一个**自研 AI Agent 框架**（805 行），包含 tool-use 循环、三层 JSON 输出保证（response_format + Pydantic 校验 + 正则兜底）、并行工具执行和完整的审计追踪。替代 LangChain 后代码量减少 70%
> - 构建了**四阶段新闻流水线**：规则粗筛（省 60% AI 调用）→ 正文深挖 → AI 批量打分（1 次 LLM 调用处理全部候选）→ 代码直拼飞书卡片（零 AI 幻觉风险）
> - 实现了 **β-Fusion 打分融合算法**：将零 token 代码层信号（正文长度/事实密度/时效）与 LLM 语义评分按 0.3:0.7 加权融合，辅以**强制分布检查**防止 LLM 评分坍缩到安全中间值
> - 设计了 **Calibrator 反馈校准 Agent**（RecGPT LLM-as-Judge 模式）：分析用户 👍/👎 反馈 → 发现偏好模式 → 输出校准参数 → Scorer 自动调整打分权重，形成**自主学习闭环**
> - 开发了 6 个 Playwright/httpx 游戏数据抓取器，Template Method 模式统一基类；SQLite WAL 模式数据库，三层去重 TTL 策略

#### Day 3-4: 面试自我介绍

**1 分钟版本**（任何面试的第一问）：

> "我做了[多久]的[领域]开发，最有代表性的项目是一个游戏行业竞品情报多智能体日报系统。核心挑战是——怎么用 LLM 从每天几十条游戏新闻中自动筛选出对决策者最有价值的 7 条，并保证输出质量。
>
> 我的方案是四阶段流水线：规则粗筛省掉 60% 的 AI 调用，AI 批量打分代替逐条处理，然后把分数和我从正文提取的结构化信号做 β 融合，防止 LLM 给空洞文章打高分。最后所有格式化内容由代码生成，AI 只输出市场分析那段文字——这样保证格式零出错。
>
> 系统还有一个反馈校准 Agent，分析用户的点赞/踩数据自动调整打分权重。整个系统 ~11 万行代码，每天早上 9 点自动推送日报到飞书群。
>
> 我觉得做这个项目最大的收获是理解了 'AI 工程判断力'——知道什么该用 AI、什么该用代码、什么该用规则。"

**技术深挖准备**（面试官听到上面的描述，大概率追问的点）：
1. "β-Fusion 的 0.3 怎么定的？"→ 答：我参考了 RecGPT 的 β 值选择方法论。我们的任务比 RecGPT 更依赖语义理解，所以 AI 权重更高（0.7），但保留足够的代码信号拉力（0.3）防止 AI 跑偏。实际跑了几十次日报，0.3 这个值没有出现明显需要调整的情况。
2. "强制分布的 25%/30% 阈值为什么这么设？"→ 答：阈值来自对几十次 AI 打分分布的实际观察。如果不强制，LLM 倾向于把所有候选打到 50-70 分的安全区间。25% 低于 40 分确保 AI 必须做出"不合格"判断，30% 高于 60 分上限防止 AI 过度乐观。
3. "Calibrator 怎么防止 LLM 输出非法参数？"→ 答：Pydantic model_validator 做三层校验：topic_boosts 的值域限制在 [-20, 20]、dim_weights 的 key 必须是 track/density/insight 三个维度、sum 必须是 100。非法输出自动丢弃或重置为默认值并记录 warn 日志。

#### Day 5-6: 准备"你有什么要问我们的"问题

每次面试最后你也要问面试官，好问题加分：

**关于团队和技术**：
- "团队目前在 AI 方面主要在探索哪些方向？"
- "目前 LLM 应用在工程上最大的挑战是什么？"
- "团队 code review 和测试的实践是怎样的？"

**关于成长**：
- "新人入职后的 ramp-up 计划是怎样的？"
- "去年加入的应届生现在在做什么？"

#### Day 7: 整体模拟面试

找朋友或在镜子前完整走一遍：
1. 1 分钟自我介绍
2. 项目技术深挖（至少 10 个追问）
3. 1-2 道算法题（手写）
4. 系统设计（"设计一个游戏推荐系统"）
5. 反问环节

---

### 第 7 周 (8/8 – 8/14)：项目补强 & 包装

**目标**：在秋招开始前把项目打磨到"简历加分项"标准

#### 必做的 5 件事

1. **补充核心测试**（最优先）
   ```
   至少再加 10-15 个测试:
   - test_scorer.py (β-Fusion 极端值、分布检查、来源多样性)
   - test_calibrator.py (参数校验、反馈不足跳过)
   - test_dedup.py (三层去重边界)
   ```

2. **写 Dockerfile**
   ```dockerfile
   # 能一键跑起来
   docker build -t oa-intel .
   docker run --env-file .env oa-intel
   ```

3. **README 加 System Design Diagram**
   ```
   用 Mermaid 画一下数据流和 Agent 架构图，
   放在 README 顶部，面试官一眼能看懂全貌。
   ```

4. **GitHub 整理**
   ```
   - commit history 要干净（squash 掉 WIP/fix 类 commit）
   - README 有架构图 + 快速开始指南
   - .env.example 齐全
   - 去敏感信息（API key 不能提交）
   ```

5. **录制一个 3 分钟 Demo 视频**（可选但加分）
   ```
   展示: 运行 scraper → 日报生成 → 飞书推送 → @机器人交互
   ```

---

## 四、面试公司定向准备

根据你的项目背景，推荐投递顺序：

### Tier 1 — 重点准备

| 公司 | 部门/岗位 | 为什么匹配 | 额外准备 |
|------|---------|----------|---------|
| **字节跳动** | 游戏 AI/朝夕光年 | 游戏行业 + AI 应用 | 推荐系统基础、AB 测试 |
| **腾讯** | IEG/游戏 AI Lab | 游戏赛道知识 | C++ 基础（腾讯偏好 C++） |
| **网易** | 游戏 AI/伏羲 Lab | 游戏 + Agent | MLOps 基础 |
| **米哈游** | 游戏工具/AI 应用 | 游戏行业知识深 | Go 语言基础（米哈游偏好 Go） |

### Tier 2 — 广泛投递

| 公司 | 岗位方向 | 你的优势 |
|------|---------|---------|
| 美团 | 后端/AI 应用 | Pipeline 架构能力 |
| 阿里 | 搜索推荐/AI | 打分排序系统 |
| 百度 | 文心/AI 应用 | LLM 工程经验 |
| 小红书 | 搜索/推荐 | 内容理解 + 打分 |
| B站 | 游戏/推荐 | 游戏行业 + 内容系统 |
| MiniMax / 月之暗面 | Agent 开发 | 自研 Agent 框架 |

### Tier 3 — 外企 & 其他

| 公司 | 注意事项 |
|------|---------|
| Microsoft | 算法题要求高，需要系统刷 LeetCode |
| NVIDIA | 看重 C++/CUDA，你的 Python 背景要补 |
| Unity/Epic | 游戏引擎方向，需要 C++ 基础 |

---

## 五、每周时间分配建议

```
工作日（每天 3-4 小时）：
  上午 (1.5h): 算法刷题 (2 道 Medium)
  下午 (1.5h): 理论学习/看技术文章
  晚上 (1h):  项目补强（写测试/Docker/README）

周末（每天 6-8 小时）：
  上午 (3h): 模拟面试（找同学互面）
  下午 (3h): 项目深度工作（重写核心模块/补充文档）
  晚上 (1-2h): 投简历 + 复盘
```

---

## 六、资源清单

### 必读文章

| 文章 | 对应你的项目 |
|------|-----------|
| [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) — Anthropic | Agent 架构设计 |
| [LLM-as-a-Judge](https://www.anthropic.com/engineering/llm-as-a-judge) — Anthropic | Calibrator 设计 |
| [Prompt Caching with Claude](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) — Anthropic | 成本优化 |
| [Rules of Machine Learning](https://developers.google.com/machine-learning/guides/rules-of-ml) — Google | Pipeline 设计哲学 |
| [What I Talk About When I Talk About Query Optimizer](https://www.youtube.com/watch?v=5LcarPCCRDM) — CMU | 数据库优化器理解 |

### 推荐书籍

| 书 | 为什么读 |
|----|---------|
| 《Designing Data-Intensive Applications》(DDIA) | 系统设计圣经，面试必问 |
| 《System Design Interview》(Alex Xu) | 系统设计面试实战 |
| 《剑指 Offer》 | 算法题+OOP+C++基础 |

### 刷题平台

- LeetCode 中国站 (leetcode.cn) — 主刷
- 牛客网 (nowcoder.com) — 看面经、模拟笔试
- 代码随想录 (programmercarl.com) — 算法题解

---

## 附录：这个项目当前的技术短板（需要你诚实面对）

| 短板 | 严重程度 | 面试被问到的概率 | 怎么应对 |
|------|:---:|:---:|------|
| 没有系统级测试 | 🔴 高 | 60% | 承认 + 说明补测计划 |
| 没有 CI/CD | 🟡 中 | 30% | 了解 GitHub Actions 基本概念 |
| 没有结构化日志/监控 | 🟡 中 | 40% | 了解 OpenTelemetry 概念 |
| 没有用到消息队列 | 🟢 低 | 50% | 了解 Kafka/RabbitMQ 适用场景 |
| 不是微服务架构 | 🟢 低 | 40% | 能解释为什么单体更适合你的场景 |
| 没有 Redis 缓存 | 🟡 中 | 40% | 了解 Redis 数据结构和适用场景 |

**诚实策略**：被问到短板时，不要说"我还没来得及做"，而是说 "我当前阶段选择了 X 因为 Y，如果数据量/用户数增长到 Z，我的计划是 W"。这比假装什么都会强 100 倍。

---

> **最重要的建议**：你的项目就是一个完整的 AI Agent 系统——从数据采集到 LLM 调用到反馈闭环。把它讲好比刷 300 道算法题更有说服力。面试官见过太多"调了个 API"的项目，但没见过几个"自研了 Agent 框架 + 打分融合算法 + 反馈校准闭环"的项目。**这不是一个 toy project，这是 production-grade 的思想。**
