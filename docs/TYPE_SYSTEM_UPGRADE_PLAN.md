# 类型系统渐进式升级方案

> 创建日期：2026-06-26
> 背景：[REVIEW-2026-06-25-INTERVIEW.md](REVIEW-2026-06-25-INTERVIEW.md) — P1 问题：类型系统"有但没用到位"
> 状态：设计方案，待实施

---

## 一、问题诊断

### 1.1 表面现象

项目有 253 个函数写了完整的类型注解（`from __future__ import annotations`），但内部数据流几乎全是 `dict[str, Any]`：

```python
def brief_from_db(date: str, ...) -> dict[str, Any]:  # 返回什么？调用方不知道
    top_news: list[dict[str, Any]]  # 这个 dict 有哪些 key？没人知道
```

游戏、新闻、排名变动是不同的数据实体，但在代码里它们都是 `dict[str, Any]`——编译器帮不了你，IDE 补全不了，重构时 key 改名全靠全局搜索。

### 1.2 深层问题：隐式的"数据富化链"

比一般项目更严重的是，Pipeline 阶段会**渐进式地往 dict 里塞 key**，形成了没有文档记录的隐式数据流：

```
Phase A 规则粗筛 → dict 有 url, title, source, publish_date
Phase B 深挖正文 → dict 多了 body, body_length, og_image
Phase C AI 打分   → dict 又多了 ai_score, summary, pos_label, neg_label, signal_score, fused_score
Phase D 拼装卡片 → dict 再多了 image_url, feedback_buttons
```

每一层读上游的 key、写下层的 key，但没有任何地方声明过"这个 dict 在 Phase C 之后长什么样"。新人接手只能靠 `print(d.keys())` 调试。

### 1.3 当前 Pydantic 使用的断裂

项目已在 LLM 边界使用 Pydantic 做输出校验（`scorer.py`、`calibrator.py`），但数据流是断裂的：

```
LLM 输出 → Pydantic 校验 → model_dump() 转 dict → 下游回到 dict[str, Any] 黑暗森林
```

Pydantic 的类型信息在 `model_dump()` 那一刻就丢失了。

---

## 二、三类可选方案

### 方案 A：TypedDict — 零运行时开销，渐进式

```python
from typing import TypedDict, NotRequired

class RawNewsItem(TypedDict):
    url: str
    title: str
    source: str
    publish_date: str

class ScoredNewsItem(RawNewsItem):
    ai_score: int
    summary: str
    pos_label: NotRequired[str]
    neg_label: NotRequired[str]
    signal_score: float
    fused_score: float
```

| 维度 | 评价 |
|------|------|
| 运行时开销 | **零** — 本质还是 dict，`d["url"]` 照写不误 |
| 破坏性 | **零** — 只改函数签名的类型注解，dict 访问语法不变 |
| 继承支持 | ✅ `class ScoredNewsItem(RawNewsItem)` 自然表达数据富化链 |
| 可选字段 | ✅ `NotRequired` 让 IDE 知道你用 `.get("body")` 可能返回 None |
| 静态检查 | mypy/pyright 能检查 key 名拼写错误 |
| 运行时校验 | ❌ 没有 — `d["ai_scroe"]`（typo）TypedDict 不报错，只有静态检查时才会 |
| 默认值/方法 | ❌ 不支持 |

**适合场景**：函数签名和返回值类型标注，作为全项目的"类型语言"。

### 方案 B：dataclass — 有运行时行为，点语法

```python
from dataclasses import dataclass

@dataclass
class NewsItem:
    url: str
    title: str
    source: str
    publish_date: str
    body: str | None = None
    ai_score: int = 0
    summary: str = ""
    pos_label: str | None = None
```

| 维度 | 评价 |
|------|------|
| IDE 补全 | ✅ `d.url` — 拼写错误变编译错误 |
| 默认值 | ✅ 字段默认值 + `__post_init__` 校验 |
| 内置方法 | ✅ `__repr__`、`__eq__` 自动生成 |
| 破坏性 | 🔴 **全项目 `d["url"]` → `d.url`**，涉及几十个文件上千行改动 |
| 运行时开销 | 轻微 — 每个实例多 ~56 bytes |
| 序列化 | 需 `dataclasses.asdict()` 才能转回 dict（给下游用） |

**适合场景**：关键跨模块边界的 DTO，不宜全量迁移。

### 方案 C：Pydantic BaseModel — 已在用但范围过窄

| 维度 | 评价 |
|------|------|
| 运行时校验 | ✅ 字段级 validator，类型强保障 |
| 序列化 | ✅ `model_dump()` / `model_validate()` |
| JSON Schema | ✅ 自动生成 |
| 运行时开销 | 🔴 每个字段都有 validator 开销 |
| 数据流断裂 | 🔴 `model_dump()` 后类型信息丢失，下游又回到 dict |

**适合场景**：LLM 输入/输出边界（已在使用），不建议扩展到内部数据传递。

---

## 三、推荐方案：分两层，渐进推进

### 总体原则

**让类型系统服务于关键接口的清晰性，而不是追求每一行代码的类型完美。**

- 第一层：TypedDict 做全项目"类型语言" — 零破坏，立刻见效
- 第二层：dataclass/Pydantic 做关键 DTO — 有破坏性，但有合约价值

---

### 第一层（立即做，~3-4 小时）：核心 TypedDict 定义

#### 3.1 定义 8-10 个 TypedDict，覆盖 Pipeline 核心数据流

所有 TypedDict 放在一个新建文件 `src/types.py` 中，作为全项目的类型字典。

##### 1. `RankingEntry` — Loader 导入 + Differ 输入

```python
class RankingEntry(TypedDict):
    """单条排名记录（rankings 表一行）"""
    date: str
    platform: str          # ios / android
    chart_type: str        # 热门榜 / 免费榜 / 畅销榜
    rank: int
    game_name: str
    bundle_id: str         # 可能为 "0"
    developer: str         # 可能为 "0"
```

对应阶段：`Loader` 写入 `rankings` 表前，`Differ` 读取 `rankings` 表后。

##### 2. `ChangeRecord` — Differ 产出

```python
class ChangeRecord(RankingEntry):
    """排名变动记录（changes 表一行）"""
    rank_change: int       # 正=上升，负=下降
    prev_rank: int
    attention_score: float
    day_type: str          # normal / volatile / weekend
    volatility: float
```

对应阶段：`Differ` 产出，`StoryPicker` 消费。继承 `RankingEntry`。

##### 3. `RawNewsItem` — 新闻粗筛后

```python
class RawNewsItem(TypedDict):
    """规则粗筛后的新闻候选（market_pipeline Phase A 产出）"""
    url: str
    title: str
    source: str            # 必须引用 NewsSource 常量
    publish_date: str      # YYYY-MM-DD
    snippet: str           # RSS/列表页摘要
```

对应阶段：`market_pipeline.filter_news()` 产出。

##### 4. `EnrichedNewsItem` — 深挖正文后

```python
class EnrichedNewsItem(RawNewsItem):
    """深挖正文后的新闻候选（market_pipeline Phase B 产出）"""
    body: NotRequired[str]          # 提取的正文前 ~500 字
    body_length: NotRequired[int]   # 正文长度（字符数）
    og_image: NotRequired[str]      # og:image 封面图 URL
    is_bilibili: NotRequired[bool]  # 是否 B 站视频（跳过正文提取）
```

对应阶段：`market_pipeline.deep_fetch()` 产出。继承 `RawNewsItem`。

##### 5. `ScoredNewsItem` — AI 评分后

```python
class ScoredNewsItem(EnrichedNewsItem):
    """AI 评分后的新闻候选（scorer Phase C 产出）"""
    ai_score: int                    # 0-100 LLM 综合分
    summary: str                     # 3-5 句 AI 摘要
    pos_label: NotRequired[str]      # 正面标签 (Matched Verdict Pool)
    neg_label: NotRequired[str]      # 负面标签 (Matched Verdict Pool)
    signal_score: float              # 代码信号分 (0-100)
    fused_score: float               # β-fusion 融合分 (0.3×signal + 0.7×AI)
    verdict: NotRequired[str]        # AI 判词
    track_relevant: NotRequired[bool]
```

对应阶段：`scorer.ai_summarize_and_judge()` 产出。继承 `EnrichedNewsItem`。

##### 6. `NewGameEntry` — 新游

```python
class NewGameEntry(TypedDict):
    """新游条目（TapTap 新游日历 / Steam 移植）"""
    date: str
    game_name: str
    platform: str          # taptap / steam
    url: NotRequired[str]  # TapTap 链接
    tags: NotRequired[list[str]]
    track_relevant: NotRequired[bool]
    genre: NotRequired[str]
    developer: NotRequired[str]
```

对应阶段：`taptap_new_games` / `steam_ports` scraper 产出，`briefer._build_new_games_md()` 消费。

##### 7. `HotTopicItem` — 热点

```python
class HotTopicItem(TypedDict):
    """热点条目（Hot Tracker Agent 产出）"""
    keyword: str
    title: str
    url: str
    snippet: str
    ai_summary: NotRequired[str]    # Agent 生成的摘要
    value_score: NotRequired[int]   # Agent 0-100 价值评分
    source: NotRequired[str]
```

对应阶段：`hot_tracker.collect_hot_topics()` 产出，`render.build_hot_topic_elements()` 消费。

##### 8. `BriefContext` — Briefer 编排上下文

```python
class BriefContext(TypedDict):
    """日报编排完整上下文（brief_from_db() 返回值）"""
    date: str
    new_games: list[NewGameEntry]
    sector_changes: list[ChangeRecord]  # 赛道排名变动
    top_news: list[ScoredNewsItem]      # 精选后的市场新闻
    hot_topics: list[HotTopicItem]      # 热点
    yesterday_new_games: NotRequired[list[str]]  # 昨日新游名列表（用于 badge）
    day_type: NotRequired[str]
    volatility: NotRequired[float]
```

对应阶段：`briefer.brief_from_db()` 返回值，`render` 和 `pusher` 消费。

##### 9. `FeedbackRecord` — 用户反馈（可选，如果 bot.py 改得动）

```python
class FeedbackRecord(TypedDict):
    """用户反馈记录（user_feedback 表一行）"""
    date: str
    feedback_type: str     # like / dislike / hot_click
    news_url: NotRequired[str]
    keyword: NotRequired[str]
    fidelity_score: NotRequired[float]
    fidelity_flags: NotRequired[list[str]]
```

##### 10. `PipelineRunStats` — Runner 监控（可选）

```python
class PipelineRunStats(TypedDict):
    """Runner 一次全流水线的运行统计"""
    date: str
    phases: dict[str, bool]       # phase → success
    phase_durations: dict[str, float]  # phase → seconds
    total_duration: float
    error_messages: list[str]
    scrape_results: dict[str, bool]   # scraper_name → success
```

#### 3.2 TypedDict 之间的继承关系

```
RankingEntry
  └── ChangeRecord

RawNewsItem
  └── EnrichedNewsItem
        └── ScoredNewsItem

NewGameEntry    (独立)
HotTopicItem    (独立)
FeedbackRecord  (独立)
PipelineRunStats (独立)

BriefContext    (组合以上所有 — 日报编排的"根类型")
```

#### 3.3 实施步骤

1. **新建 `src/types.py`**，粘贴上述 8-10 个 TypedDict 定义
2. **改函数签名**，不改函数体：
   - `def filter_news(...) -> list[RawNewsItem]`
   - `def deep_fetch(news: list[RawNewsItem]) -> list[EnrichedNewsItem]`
   - `def ai_summarize_and_judge(news: list[EnrichedNewsItem]) -> list[ScoredNewsItem]`
   - `def brief_from_db(...) -> BriefContext`
   - `def collect_hot_topics(...) -> list[HotTopicItem]`
   - `def _build_new_games_md(games: list[NewGameEntry]) -> str`
3. **跑 mypy/pyright**，解决类型不匹配（TypedDict 兼容 dict 访问语法，大部分零改动通过）
4. **不用改函数体内的 `d["url"]` 写法** — TypedDict 的访问语法就是 dict 语法

#### 3.4 收益

- IDE 在 `news[0]["` 时自动补全 `url` / `title` / `source` / `publish_date` 等合法 key
- 重构：改 TypedDict 的字段名 → mypy 全局报错 → 精确到行，不用肉眼搜索
- 新人接手：打开 `src/types.py` 就能看到全项目的数据结构全景图
- 零运行时成本：`isinstance(d, ScoredNewsItem)` 永远返回 `False`（TypedDict 只是类型标注），但你也不需要运行时检查

---

### 第二层（择机做，~4-6 小时）：关键接口 DTO 化

选 **3-5 个跨模块边界的函数返回值**，换成 dataclass 或 Pydantic。这些是模块间的"合约"。

#### 4.1 候选接口

| 函数 | 当前返回 | 建议改为 |
|------|---------|---------|
| `briefer.brief_from_db()` | `dict[str, Any]` | `BriefResult` (dataclass) |
| `scorer.ai_summarize_and_judge()` | `list[dict[str, Any]]` | `list[ScoredItem]` (dataclass) |
| `hot_tracker.collect_hot_topics()` | `list[dict[str, Any]]` | `list[HotTopic]` (dataclass) |
| `market_pipeline.filter_news()` | `list[dict[str, Any]]` | `list[FilteredNews]` (dataclass) |

#### 4.2 为什么第二层用 dataclass 而非 Pydantic？

- 这些函数**不在 LLM 边界**，不需要 Pydantic 的 JSON Schema 生成和 validator
- dataclass 的 `d.url` 点语法在**阅读体验**上优于 dict 和 TypedDict
- 开销比 Pydantic 低一个数量级
- 需要传给下游 dict-API 时，`dataclasses.asdict(d)` 一键转换

#### 4.3 注意事项

- 第二层的改动是**破坏性的**：调用方 `d["url"]` → `d.url`，需要全局替换
- 建议**一次改一个接口**，每次改完跑测试 + 手动跑一次日报验证
- 不要同时在 TypedDict（第一层）和 dataclass（第二层）上定义同一实体 — 会导致维护负担翻倍。选择其中一个

---

## 四、什么不该做

1. **不要全量迁移到 dataclass**：253 个函数 × 平均 3-5 个 dict 参数/返回值 = 上千处 `d["key"]` → `d.key` 改动。风险高，收益递减。
2. **不要把所有 Pydantic model 串联起来**：LLM 输出的 Pydantic model `model_dump()` 后保持 dict，到 TypedDict 类型注解即可。不需要 Pydantic model 一路传到底。
3. **不要追求"每个 dict 都有类型"**：函数内部的局部 dict（存活范围 ≤ 10 行）不需要 TypedDict。TypedDict 只用于**跨函数边界**的数据结构。

---

## 五、相关文档

- [REVIEW-2026-06-25-INTERVIEW.md](REVIEW-2026-06-25-INTERVIEW.md) — P1 问题原始评审
- [audit-2026-06-25-engineering【待修复】.md](audit-2026-06-25-engineering【待修复】.md) — 工程缺陷审计
- [RECGPT_APPLICATION.md](RECGPT_APPLICATION.md) — RecGPT 模式应用指南（β-Fusion 等数据流上下文）
