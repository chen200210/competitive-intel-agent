# AI 冗余使用审计 — 2026-06-25

> 审计目标：找出全项目中**可以用纯代码替代的 AI/LLM 调用**，减少不必要的 token 消耗。
> 审计范围：全部 AI Agent 调用点（`scorer.py` summarizer agent、`calibrator.py` calibrator agent、`scripts/score_news.py`）
> 原则：只标记"代码可以做到同等或可接受质量"的 AI 使用。
>
> **已排除**：
> - ~~RED-1: AI 判词 verdict~~ — 保留。判词捕获了代码拿不到的正文语义（如标题不含"肉鸽"但正文含，判词写"国风策略肉鸽，直接相关赛道"），是 AI 推理路径的可观测性窗口，边际成本 ~15 token/条，值。
> - ~~RED-6: AI 标签分配~~ — 保留。标签中 `track_direct`/`playable_reference` 等依赖正文语义理解，规则覆盖不全，且与摘要同一次 API 调用，边际成本极低。
>
> **已完成 (2026-06-26)**：
> - ✅ ~~RED-2: AI 重复检测~~ — 删除 prompt duplicates 段落 + `SummarizerOutput.duplicates` 字段 + 55 行 DFS 处理代码。`_is_same_story()` 接管跨语言去重。
> - ✅ ~~RED-4: 分布重试循环~~ — `_MAX_DIST_RETRIES = 3` → `1`。一次不通过直接走 `_apply_score_fallback()`。
> - ✅ ~~RED-5: 低质候选预筛~~ — 新增 `_PREFILTER_SIGNAL_THRESHOLD = 15` + `_body_cache` 缓存，AI 调用前跳过明显低质候选。

---

## 🔴 高优先级（每次日报都浪费）

### RED-2: AI 重复检测（duplicates）— 代码层已有三层去重，AI 层是第四层冗余

**位置**: `prompts/summarizer.yaml:143` → `src/agents/scorer.py:85,603-657`

**现状**: Prompt 要求 AI 输出重复新闻的索引对：
```yaml
# summarizer.yaml
**重复检测**：如果多条候选讲的是同一个事件（例如中文翻译和英文原文
都报道了同一条彭博社新闻），在 duplicates 数组中列出它们的索引对，
如 [[2,7], [3,5]]。没有发现重复就写 []。
```

**已有防护**（代码层三层去重，AI 是第四层）:

| 层级 | 位置 | 机制 | 覆盖场景 |
|------|------|------|----------|
| Phase A | `market_pipeline.py` | URL 精确匹配 + headline token 去重 | 同源重复 |
| Phase A | `dedup.py` | `reported_items` 表 7-30 天 TTL | 跨天重复 |
| Phase C | `scorer.py:_is_same_story()` | AI summary Jaccard 相似度 + 命名实体重叠 | **跨语言重复**（如中文翻译 vs 英文原文） |

**为什么 AI 层是冗余的**: `_is_same_story()` 在 `_select_top_n()` 中已经逐条检查（line 895），且它用 AI 生成的中文摘要做输入——两个语言的新闻摘要后都是中文，Jaccard bigram 相似度比 AI 在 prompt 里用原标题判断更可靠。`_select_top_n` 是贪心遍历，遇到重复就 skip 不占 slot，不会出现"重复挤掉好内容"。

**去重体系（去掉 AI 层后）**:

```
候选新闻进入 pipeline
        │
        ▼
┌─ Phase A 规则去重（AI 调用之前）────────────────────┐
│                                                      │
│  ① URL 精确匹配（market_pipeline.py）                 │
│    同 URL → 直接丢弃                                  │
│                                                      │
│  ② headline token 去重（market_pipeline.py）          │
│    同语言标题提取令牌 → 令牌重叠 ≥ 阈值 → 丢弃         │
│                                                      │
│  ③ reported_items 表（dedup.py, 7-30天 TTL）         │
│    跨天重复 → INSERT OR IGNORE → 丢弃                 │
│                                                      │
│  这三层管：同源重复、同语言重复、跨天重复              │
│  这三层不管：跨语言重复（中文新闻 vs 英文新闻          │
│              报道同一事件，URL不同，标题不同语言）      │
└──────────────────────────────────────────────────────┘
        │ 幸存者
        ▼
    AI Summarizer 调用（只做摘要+打分+标签，不做去重）
        │ 全部带 AI 摘要
        ▼
┌─ _select_top_n() 代码去重（AI 调用之后）─────────────┐
│                                                      │
│  ④ _is_same_story()（scorer.py:895）                 │
│     贪心选取时逐条检查：                               │
│     · AI 中文摘要 → 字符 bigram Jaccard ≥ 0.15 → 重复 │
│     · AI 中文摘要 → 命名实体重叠 ≥ 2 个 → 重复        │
│     · 回退：标题命名实体重叠 ≥ 2 个 → 重复            │
│                                                      │
│  这一层接管：跨语言重复（原来 AI 在 prompt 里做的事）  │
│  而且它用 AI 摘要做输入，比 AI 在 prompt 里用原标题   │
│  判断更可靠——两个语言的新闻摘要后都是中文，可比       │
└──────────────────────────────────────────────────────┘
```

**具体改动（3 处）**:

1. **`prompts/summarizer.yaml`** — 删除重复检测段落（约 50 字）
2. **`src/agents/scorer.py:85`** — `SummarizerOutput` 删除 `duplicates` 字段
3. **`src/agents/scorer.py:603-657`** — 删除 AI duplicates 处理块（建图→DFS→保留最高分→删其余，共 55 行）

**预估节省**: prompt ~80 字 + AI output duplicates 数组（可变，平均 ~30 token）+ 代码简化 55 行

---

## 🟠 中优先级（定期运行，累计浪费可观）

### RED-3: Calibrator 用 LLM 分析结构化反馈数据 — 统计任务，不需要语义理解

**位置**: `src/agents/calibrator.py:314-336` → `prompts/calibrator.yaml`

**现状**: Calibrator 将用户反馈聚合为结构化表格，然后调 LLM 分析：
```
| 来源 | 👍 | 👎 | 倾向 | 赛道 | +标签 | -标签 | 新闻标题 |
| 3DM  | 5  | 0  | 👍偏好 | 🏷️ | track_direct | | 塔防新游... |
| GL   | 0  | 3  | 👎排斥 |     | | off_track | | 二次元... |
```

LLM 输出数值参数：
```json
{
  "topic_boosts": {"独立游戏": 5, "二次元": -10},
  "dim_weights": {"track": 45, "density": 35, "insight": 20},
  "findings": [{"pattern": "...", "evidence_count": 5, ...}]
}
```

**为什么不需要 AI**: 这是**统计推断**，不是语义理解。Prompt 中的决策规则已经明确到可以直接翻译成代码：

| Prompt 规则 | 代码实现难度 |
|-------------|-------------|
| ≥3 次 👎 同一话题 → topic_boosts 降权 ≥10 | 按 pos_label 分组统计 down 数 |
| ≥5 次 👍 同一话题 → topic_boosts 加权 ≥5 | 按 pos_label 分组统计 up 数 |
| dim_weights 单次调整 ≤15% | `clamp(delta, -15, 15)` |
| dim_weights 总和必须为 100 | 归一化 |
| 来源权重不可变 | 不处理 source 维度 |

**LLM 唯一不可替代的部分**: `findings` 数组中的自然语言解释——但这个字段仅写入 `calibration_params.summary` 供人类阅读，不影响任何自动化决策。

**替代方案**: 规则引擎计算 topic_boosts + dim_weights（0 token），`findings` 改为代码生成的模板化描述，或保留可选 LLM 调用仅生成 summary 文本。

**预估节省**: 每次校准 **数千 token**，Calibrator 80%+ 的 AI 成本可削减。

---

### RED-4: 分布强制检查的重试循环 — 代码 fallback 已完善但每次仍重试最多 3 次

**位置**: `src/agents/scorer.py:393,444-506`

**现状**:
```python
_MAX_DIST_RETRIES = 3

for attempt in range(_MAX_DIST_RETRIES):
    agent = Agent("summarizer", ...)
    result = agent.run(...)        # ← 每次重试是完整 LLM 批处理调用（15-20条候选）
    ...
    dist_ok, dist_msg, _ = _check_distribution(scores, len(candidates))
    if dist_ok:
        break
else:
    _apply_score_fallback(all_items, total)
```

**为什么重试是浪费**:
- `_apply_score_fallback()` 已经是**完整、可靠的代码层修正**：底部 25% 封顶 39 分，顶部 30% 封顶 64 分，保留相对排序
- 分布检查在流程中跑了**两次**——AI 重试循环一次（line 487），β-fusion + topic_boosts 之后又一次（line 596）
- 第二次检查如果不过，**仍然会调用 `_apply_score_fallback`**——即使 AI 重试全通过，最终仍可能被代码层修正覆盖

**替代方案**:
- 方案 A（推荐）: `_MAX_DIST_RETRIES` 从 3 降为 1，一次不过直接走 `_apply_score_fallback`
- 方案 B（激进）: 去掉重试循环，AI 打分 → β-fusion → topic_boosts → `_apply_score_fallback` 一步到位

**预估节省**: 节省 **2 次完整批处理 LLM 调用**（每次 15-20 条候选，共数千 token）

---

## 🟡 低优先级（边际优化）

### RED-5: 明显低质候选无预筛 — signal_score 已提取但不用于过滤

**位置**: `src/agents/scorer.py:422-433`（构建 `items_json`）

**现状**: 所有候选无条件打包送 AI，包括：
- `body` 为空字符串的（无正文可摘要）
- `is_digest=true` 的（周报/汇总，非首发信息）
- `freshness="去年"` 的（时效性极差）

`_extract_body_signals()` 在 AI 调用**之前**就已经能判断低质内容：
- `body_len=0` + `is_digest=true` → signal_score < 15，几乎肯定低质
- `freshness` 为 "去年" → 时效性为 0，signal 极低

**替代方案**: 在构建 `items_json` 前加一道预筛——signal_score < 阈值的候选直接给默认字段（`ai_score=10, ai_summary=""`），不送 AI。这些候选原本就会在质量门禁被 `min_ai_score` 筛掉，送 AI 是纯粹浪费。

**预估节省**: 按每天 30-50 条候选、约 20-30% 为明显低质估算，减少 **20-30% 的 AI 输入 token**。

---

## 统计

| 编号 | 问题 | 严重度 | 每次日报/校准节省 | 替代难度 |
|------|------|--------|------------------|----------|
| RED-2 | AI 重复检测 duplicates | 🔴 高 | ~110 token/次 + 代码简化 55 行 | 低（已有代码层 `_is_same_story`） |
| RED-3 | Calibrator LLM 分析 | 🟠 中 | 数千 token/次校准 | 中（需写统计规则） |
| RED-4 | 分布重试循环 | 🟠 中 | 数千 token/次 | 低（改常量即可） |
| RED-5 | 低质候选无预筛 | 🟡 低 | 20-30% 输入 token | 低（加 if 判断） |

---

## 实施建议

**第一阶段（低成本高收益）**:
1. ✅ RED-2: 删除 prompt duplicates 段落 + SummarizerOutput.duplicates 字段 + AI duplicates 处理代码 (2026-06-26)
2. ✅ RED-4: `_MAX_DIST_RETRIES` 降为 1 (2026-06-26)

**第二阶段（需验证）**:
3. ✅ RED-5: 加 signal_score 预筛 + `_body_cache` 缓存优化 (2026-06-26)
4. RED-3: 实现统计规则引擎，与 LLM 输出并行对比 2-3 次校准后再决定是否完全切换
