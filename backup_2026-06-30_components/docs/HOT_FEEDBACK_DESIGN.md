# 热点反馈设计（可实施版）

> 更新日期：2026-06-29
> 目标：把“热点感兴趣反馈”整理成一版可以直接进入开发的最终建议稿，消除与现有 `user_feedback` / `hot_tracker` / `--hot-only` 实现的冲突。

---

## 1. 设计目标

当前系统里有两类完全不同的反馈信号：

| 维度 | 市场新闻反馈 | 热点反馈 |
|------|--------------|----------|
| 信号性质 | 长期偏好 | 短期兴趣 |
| 生效节奏 | 天级 | 当天、小时级 |
| 消费方 | Calibrator / scorer | Hot Tracker / 午后追更 |
| 典型问题 | “用户长期更喜欢塔防/独游/AI 工具新闻” | “用户今天对某个热点话题还想看更多” |

如果继续把两类信号完全混在一个消费逻辑里，会出现两个问题：

1. 热点点击被 14 天窗口平滑，无法形成“当天闭环”
2. Calibrator 容易吃进 `hot_click` 这类短期事件信号，污染长期偏好

本设计的目标是：

1. 热点点击在当天 `--hot-only` / `--follow-up` 中立即生效
2. 长期偏好和短期兴趣分轨处理，但保留升级通道
3. 不降低热点筛选质量底线，只调整“权重、时效、候选量”

---

## 2. 最终方案概览

### 2.1 总体原则

1. `user_feedback` 继续保留，作为统一审计表
2. `user_feedback.feedback_type` 保持现义：动作类型，不改名、不改语义
3. 新增 `user_feedback.content_source`，标识反馈来源：`market` / `hot`
4. 新增 `hot_feedback_sessions`，作为“短期热点即时加成”的专用表
5. Calibrator 只消费 `market + thumbs_up/thumbs_down`
6. Hot Tracker 的当天即时 boost 只读 `hot_feedback_sessions`
7. 历史热点兴趣统计仍可从 `user_feedback` 聚合，但必须排除“今天”，避免和即时 boost 双重计数

### 2.2 架构图

```text
用户点击“感兴趣”
    │
    ├── 市场新闻
    │      └── user_feedback
    │             feedback_type = thumbs_up / thumbs_down
    │             content_source = market
    │
    │      └── Calibrator（仅消费 market）
    │              └── calibration_params.topic_boosts
    │
    └── 热点新闻
           ├── user_feedback
           │      feedback_type = hot_click
           │      content_source = hot
           │
           └── hot_feedback_sessions
                  └── 当天 / 48h 内即时 boost
                         └── collect_hot_keywords()
                                └── search_hot_topics()
                                       └── 午后 follow-up 推送
```

---

## 3. 与现有实现对齐后的关键决策

### 3.1 不再复用 `feedback_type` 表示来源

现有代码中，`user_feedback.feedback_type` 已经明确表示动作类型：

- `thumbs_up`
- `thumbs_down`
- `hot_click`

所以最终方案是：

- 保留 `feedback_type`
- 新增 `content_source`

```sql
ALTER TABLE user_feedback
ADD COLUMN content_source TEXT NOT NULL DEFAULT 'market';
-- 值: 'market' | 'hot'
```

字段职责：

| 字段 | 含义 |
|------|------|
| `feedback_type` | 用户动作类型 |
| `content_source` | 反馈对应的内容来源 |

这是本设计最重要的落地约束，避免和现有统计、索引、Calibrator SQL 冲突。

### 3.2 `user_feedback` 与 `hot_feedback_sessions` 的分工

`user_feedback`：

- 统一审计
- 统计历史热点点击
- 支持深度研究自动触发
- 支持后续“短期兴趣升级为长期偏好”

`hot_feedback_sessions`：

- 只解决“当天/48h 内即时 boost”
- 不承担长期统计主表角色
- 允许被定期清理

### 3.3 即时 boost 与历史 boost 分离，避免双重计数

最终采用：

1. `hot_feedback_sessions` 负责“今天”的即时 boost
2. `user_feedback(hot_click)` 负责“历史天”的轻量累计 boost
3. `collect_hot_keywords()` 计算权重时：
   - 历史点击：排除今天
   - 今日点击：只读 active session

这样一次点击不会同时被 `+0.2` 和 `+0.5` 重复累计。

---

## 4. 数据模型

## 4.1 `user_feedback` 变更

### 新增字段

```sql
ALTER TABLE user_feedback
ADD COLUMN content_source TEXT NOT NULL DEFAULT 'market';
```

### 推荐取值

| 场景 | feedback_type | content_source |
|------|---------------|----------------|
| 市场新闻点赞 | `thumbs_up` | `market` |
| 市场新闻点踩 | `thumbs_down` | `market` |
| 热点“感兴趣” | `hot_click` | `hot` |

### Calibrator 消费约束

Calibrator SQL 必须显式过滤：

```sql
WHERE uf.content_source = 'market'
  AND uf.feedback_type IN ('thumbs_up', 'thumbs_down')
```

不能再依赖“`hot_click` 不参与 CASE WHEN 统计，因此看起来没影响”这种隐式行为。

---

## 4.2 新表 `hot_feedback_sessions`

```sql
CREATE TABLE IF NOT EXISTS hot_feedback_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    headline TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    open_id TEXT NOT NULL DEFAULT '',
    session_date TEXT NOT NULL,                    -- YYYY-MM-DD
    boost REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL DEFAULT (datetime('now', '+48 hours'))
);

CREATE INDEX IF NOT EXISTS idx_hot_feedback_keyword_date
ON hot_feedback_sessions(keyword, session_date);

CREATE INDEX IF NOT EXISTS idx_hot_feedback_expires
ON hot_feedback_sessions(expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_hot_feedback_dedup
ON hot_feedback_sessions(url, open_id, session_date)
WHERE url != '' AND open_id != '';
```

### 字段说明

| 字段 | 用途 |
|------|------|
| `keyword` | 该热点卡片关联的关键词 |
| `headline` | 被点击热点标题，用于审计和排查 |
| `url` | 热点 URL |
| `open_id` | 用户级去重 |
| `session_date` | 点击归属日期 |
| `boost` | 本次点击可带来的即时加成 |
| `expires_at` | 48h 过期时间 |

### 为什么必须有 `open_id`

没有 `open_id`，无法防止同一用户对同一热点重复刷权重。

---

## 5. 写入链路

## 5.1 最终写入原则

热点点击时，必须原子写入两条轨道：

1. `user_feedback`：长期审计与升级统计
2. `hot_feedback_sessions`：即时 boost

这两个写入必须在同一事务里完成。

## 5.2 推荐接口

不建议在 `bot.py` 里手动连续调两个 DB 方法。最终建议是把逻辑收口到 `sqlite.py`：

```python
def record_hot_topic_feedback(
    *,
    date: str,
    target_date: str,
    news_url: str,
    keyword: str,
    chat_id: str,
    open_id: str,
) -> str:
    """
    原子写入:
      1. user_feedback(feedback_type='hot_click', content_source='hot')
      2. hot_feedback_sessions

    返回:
      'inserted' | 'duplicate' | 'error'
    """
```

这样好处有两个：

1. 事务边界集中在存储层
2. bot 层不用关心“双写顺序”和异常恢复

## 5.3 `headline` 来源

最终建议：**不把 `headline` 放进按钮 payload**。

原因：

1. payload 越短越稳
2. 当前按钮已有 `news_url`
3. 可在回调时通过 `hot_topic_news` 用 `url + target_date` 回查 `headline`

因此推荐在 `record_hot_topic_feedback()` 内部：

1. 先根据 `news_url` / `target_date` 查 `hot_topic_news`
2. 找到 `headline` 后再写入 `hot_feedback_sessions`
3. 查不到则写空字符串并打 `WARN`

---

## 6. 热点权重计算

## 6.1 权重组成

单个热点关键词最终权重：

```text
final_weight = base_weight + historical_click_boost + same_day_session_boost
```

其中：

- `base_weight`：现有来源榜单/预设关键词基础权重
- `historical_click_boost`：来自历史 `user_feedback.hot_click`
- `same_day_session_boost`：来自 `hot_feedback_sessions`

## 6.2 历史点击加成

保留当前“每次点击 +0.2”的思路，但必须排除今天：

```python
historical_clicks = db.get_hot_keyword_click_stats(
    days=14,
    exclude_date=date,
)
```

推荐规则：

- 窗口：14 天
- 单次加成：`+0.2`
- 目的：反映“最近两周持续有人感兴趣”

## 6.3 当天即时加成

当天 boost 只读 active sessions：

```python
same_day_boosts = db.get_active_hot_feedback_boosts(date=date)
```

推荐规则：

- 单次点击：`+0.5`
- 同一关键词当天总加成上限：`2.0`
- 只读 `expires_at > datetime('now')`

## 6.4 伪代码

```python
historical_clicks = db.get_hot_keyword_click_stats(days=14, exclude_date=date)
same_day_boosts = db.get_active_hot_feedback_boosts(date=date)

for kw in keywords:
    key = kw["keyword"]
    base = kw["weight"]
    history_boost = historical_clicks.get(key, 0) * 0.2
    same_day_boost = same_day_boosts.get(key, 0.0)
    kw["weight"] = round(base + history_boost + same_day_boost, 2)
```

---

## 7. 搜索与筛选策略

## 7.1 不降低质量底线

无论普通模式还是 boost 模式，都保持：

- `min_ai_score` 不变
- AI 筛选逻辑不变
- 只放宽候选召回，不放宽质量门槛

## 7.2 有即时 boost 时允许轻微放宽召回

推荐策略：

| 条件 | max_results | max_age |
|------|-------------|---------|
| 普通关键词 | 5 | 7 天 |
| 当天有 boost 的关键词 | 6 | 14 天 |

因此 `search_hot_topics()` 最终建议增加显式参数，而不是依赖模块常量：

```python
def search_hot_topics(
    date: str,
    force: bool = False,
    max_age_days: int = 7,
    max_results_per_keyword: int = 5,
    selected_limit: int = 7,
    follow_up: bool = False,
) -> dict[str, Any]:
    ...
```

这样午后模式才能覆盖默认时效门槛。

---

## 8. 午后追更模式

## 8.1 目标定义

午后模式不是“再推一遍热点卡”，而是：

1. 用上午收集到的反馈做同天闭环
2. 只关注“下午新增的、值得追更的热点”

## 8.2 CLI 设计

推荐新增参数：

```bash
python -m src.pipeline.runner --hot-only --follow-up --push oc_xxx
```

语义分工：

- `--hot-only`：只跑热点链路，不跑全日报
- `--follow-up`：午后追更模式

## 8.3 午后模式行为定义

### 普通 `--hot-only`

- 作为独立热点重跑工具
- 可用于手工重刷
- 仍允许最多 7 条

### `--hot-only --follow-up`

- 只取当天新增
- 只推 2-4 条
- 去重上午已推 URL
- 使用 same-day boost
- 少于 2 条则不推

## 8.4 上午 `selected` 的保护

当前 `mark_hot_topic_selected()` 会先清空当天 `selected`，因此 follow-up 模式必须先快照上午结果：

推荐流程：

1. `get_hot_topic_news_by_date(date, selected=True)` 取上午 selected URLs
2. 作为 `morning_selected_urls`
3. 强制重搜
4. 如果下午 AI 筛选成功，用新结果做 follow-up 候选
5. 如果下午筛选失败，不覆盖上午 selected 状态

最终建议：

- follow-up 不复用 `selected` 字段来表达“下午最终入选”
- 下午追更候选应在内存中过滤后直接推送
- 避免污染上午日报的 `selected` 语义

也就是说：

1. `selected=True` 仍然表示“上午日报主卡入选”
2. 午后追更不修改它

这是一个实现边界上的重要决策。

## 8.5 午后时效约束

`_MAX_NEWS_AGE_DAYS=1` 不足以保证“只取当天”，因为 `"17小时前"` 在下午仍会被解析成 `0` 天。

最终建议：

1. follow-up 模式增加专门的“是否当天”判断函数
2. 优先用 `publish_date == today`
3. 若无 `publish_date`，再看相对时间字符串
4. 包含以下模式的内容直接排除：
   - `昨天`
   - `前天`
   - `N天前`
5. `"N小时前"` 只在 `N < 当前小时数` 且未跨日时保留

如果现阶段不想做复杂时间推断，建议采用更稳妥版本：

1. follow-up 模式优先只保留有明确 `publish_date == today` 的条目
2. 没有明确发布日期的结果，只作为候选补位，不进首选

## 8.6 去重范围

午后 follow-up 去重范围：

1. 上午已推热点 URL
2. 当天市场新闻 URL

不去重对象：

1. 上午搜到但没入选的热点候选
2. 历史天热点

这样做的原因是：

- 只挡“用户已经看过的”
- 不挡“上午因为排序靠后而未展示的”

---

## 9. 短期信号升级为长期偏好

## 9.1 升级目标

热点点击本质上是短期兴趣，但如果某个关键词在 14 天内多次被点击，说明它可能已经接近长期关注方向。

## 9.2 升级规则

推荐规则：

- 窗口：14 天
- 阈值：同一关键词 `>= 3` 次热点点击
- 数据源：`user_feedback`
- 过滤条件：
  - `content_source = 'hot'`
  - `feedback_type = 'hot_click'`

## 9.3 升级方式

不建议直接“偷偷改写”已有 `topic_boosts`。

最终建议：

1. 在 Calibrator 运行前，先聚合一批 `emerging_interest` 候选
2. 把它们作为额外输入传给 Calibrator 或规则层
3. 由 Calibrator/规则层统一生成新的 `topic_boosts`

这样能避免：

- 热点模块直接写 `calibration_params`
- 参数来源混乱
- 难以解释某个 topic_boost 是怎么来的

如果第一版想更轻量，也可以先不做自动升级，只先保留统计接口。

---

## 10. 配置建议

不建议继续把该配置塞进 `competitor_list.yaml`。

推荐新建：

`data/hot_feedback.yaml`

```yaml
boost_per_click: 0.5
max_same_day_boost: 2.0
session_ttl_hours: 48
historical_window_days: 14
historical_boost_per_click: 0.2
promotion_threshold: 3
promotion_window_days: 14
boosted_max_results: 6
boosted_max_age_days: 14
follow_up_push_min_items: 2
follow_up_push_max_items: 4
follow_up_only_today: true
```

好处：

1. 关注点分离
2. 热点策略迭代不污染竞品/赛道配置
3. 更方便单独调优

---

## 11. 最小可上线版本

## Phase 0：迁移安全

必须先做：

1. `user_feedback` 新增 `content_source`
2. Calibrator SQL 显式过滤 `market + thumbs_up/down`

## Phase 1：同天闭环 MVP

实现：

1. `hot_feedback_sessions` 表
2. `record_hot_topic_feedback()` 原子双写
3. `collect_hot_keywords()` 叠加 same-day boost
4. 历史点击统计排除今天

这一步做完后，就已经能实现“上午点了感兴趣，下午再跑热点时权重立刻变”

## Phase 2：午后追更模式

实现：

1. `--follow-up` flag
2. follow-up 独立时效规则
3. 上午 selected 快照保护
4. 2-4 条门禁
5. 去重上午已推 + 市场新闻

## Phase 3：工程完善

实现：

1. `prune_hot_feedback_sessions()`
2. 升级统计接口
3. 更完整测试
4. 更细粒度日志

---

## 12. 推荐测试清单

至少补这些测试：

| 测试场景 | 验证点 |
|---------|--------|
| `record_hot_topic_feedback` 去重 | 同用户同 URL 同天只能插一次 session |
| `record_hot_topic_feedback` 事务 | `user_feedback` 与 `hot_feedback_sessions` 要么都成功，要么都失败 |
| `get_active_hot_feedback_boosts` | 只读取 `expires_at > now` 的 session |
| `get_hot_keyword_click_stats(exclude_date=...)` | 今天点击不进入历史 boost |
| `collect_hot_keywords` | same-day boost 与 historical boost 不双算 |
| `search_hot_topics(... follow_up=True)` | 可覆盖默认 `max_age_days` |
| 午后 follow-up 去重 | 正确排除上午 selected URL |
| 午后 follow-up 失败回退 | 不清空上午 `selected` |
| Calibrator 聚合 | 不读取 `content_source='hot'` 或 `feedback_type='hot_click'` |

---

## 13. 最终结论

这版“可实施版”的核心取舍是：

1. **不推翻现有 `user_feedback` 语义，只补 `content_source`**
2. **短期热点即时 boost 单独建表，不让 Calibrator 直接消费**
3. **当天 boost 和历史 boost 分开计算，严格避免双重计数**
4. **午后追更是独立模式，不复用上午 `selected` 语义**

如果后面按这份文档开发，最容易踩的大坑基本都已经提前规避掉了：

1. schema 冲突
2. Calibrator 污染
3. 双写不一致
4. same-day 权重双算
5. 午后模式把上午 selected 清空

这版建议稿可以直接作为后续实现的设计基线。

---

## 14. Implementation Checklist

> 本节把设计拆成可执行清单。组织方式：
> `Phase → 文件 → 具体任务 → 验收标准`

## Phase 0：迁移安全与消费边界

### 目标

先把最容易出事故的 schema 和 Calibrator 边界钉死，确保后续开发不会污染现有反馈体系。

### 涉及文件

- `src/storage/sqlite.py`
- `src/agents/calibrator.py`

### 任务清单

#### 0.1 `user_feedback` 增加 `content_source`

文件：

- `src/storage/sqlite.py`

任务：

1. 新增一个 migration（建议下一个版本号）
2. 给 `user_feedback` 增加 `content_source TEXT NOT NULL DEFAULT 'market'`
3. 保证老数据自动回填为 `market`

验收标准：

1. 老库升级后不报错
2. `feedback_type` 原有语义完全不变
3. 历史 `thumbs_up` / `thumbs_down` / `hot_click` 查询结果不变

#### 0.2 Calibrator 显式过滤非市场反馈

文件：

- `src/agents/calibrator.py`

任务：

1. 修改 `_aggregate_feedback()`
2. 只消费：
   - `content_source = 'market'`
   - `feedback_type IN ('thumbs_up', 'thumbs_down')`

验收标准：

1. `hot_click` 不进入 Calibrator 聚合
2. SQL 逻辑不再依赖隐式 CASE WHEN 忽略
3. 校准输出与“纯市场反馈”口径一致

### Phase 0 测试

1. 构造一条 `hot_click + content_source=hot`，确认 `_aggregate_feedback()` 不读到它
2. 构造一条 `thumbs_up + content_source=market`，确认 `_aggregate_feedback()` 正常读到

---

## Phase 1：同天闭环 MVP

### 目标

实现“上午点了感兴趣，下午重跑热点时立刻加权”的最小闭环。

### 涉及文件

- `src/storage/sqlite.py`
- `src/feishu/bot.py`
- `src/pipeline/hot_tracker.py`

### 任务清单

#### 1.1 新建 `hot_feedback_sessions` 表

文件：

- `src/storage/sqlite.py`

任务：

1. 在新 migration 中创建 `hot_feedback_sessions`
2. 建立以下索引：
   - `keyword + session_date`
   - `expires_at`
   - `(url, open_id, session_date)` 去重唯一索引

验收标准：

1. 表结构包含：
   - `keyword`
   - `headline`
   - `url`
   - `open_id`
   - `session_date`
   - `boost`
   - `created_at`
   - `expires_at`
2. 支持同用户同 URL 同天去重
3. `expires_at` 由 SQLite 默认值自动生成

#### 1.2 新增热点 feedback 原子双写接口

文件：

- `src/storage/sqlite.py`

任务：

1. 新增统一方法，例如 `record_hot_topic_feedback(...)`
2. 在一个事务里同时写：
   - `user_feedback`
   - `hot_feedback_sessions`
3. 若 `news_url` 可回查到 `hot_topic_news`，自动补 `headline`
4. 返回统一状态：
   - `inserted`
   - `duplicate`
   - `error`

验收标准：

1. 单次点击时两张表都成功写入
2. 重复点击时按预期返回 `duplicate`
3. 任一写入失败时不会留下半条数据

#### 1.3 Bot 改为调用统一双写接口

文件：

- `src/feishu/bot.py`

任务：

1. 修改 `_handle_hot_topic_click()`
2. 不再直接调用旧的 `record_hot_topic_click()` 单表写入逻辑
3. 改为调用新的原子双写接口

验收标准：

1. 用户点击一次热点按钮后：
   - `user_feedback` 有 `hot_click`
   - `hot_feedback_sessions` 有对应 session
2. 机器人回复逻辑不变
3. 深度研究自动触发逻辑不受影响

#### 1.4 增加 active session 读取接口

文件：

- `src/storage/sqlite.py`

任务：

1. 新增读取方法，例如：
   - `get_active_hot_feedback_sessions(date: str)`
   - `get_active_hot_feedback_boosts(date: str)`
2. 只返回：
   - `session_date = 指定日期`
   - `expires_at > datetime('now')`

验收标准：

1. 过期 session 不参与 boost
2. 结果可直接用于 `keyword -> boost` 聚合

#### 1.5 历史热点点击统计排除今天

文件：

- `src/storage/sqlite.py`
- `src/pipeline/hot_tracker.py`

任务：

1. 扩展 `get_hot_keyword_click_stats()`，支持 `exclude_date`
2. `collect_hot_keywords()` 调用时排除当前 `date`

验收标准：

1. 同一点击不会同时进入 historical boost 和 same-day boost
2. 今天的 boost 只来自 `hot_feedback_sessions`

#### 1.6 `collect_hot_keywords()` 叠加 same-day boost

文件：

- `src/pipeline/hot_tracker.py`

任务：

1. 在现有 base weight / historical click weight 之外
2. 读取 `same_day_boosts`
3. 计算最终 `kw["weight"]`

验收标准：

1. 当天点击后，再跑 `collect_hot_keywords()`，目标关键词权重上升
2. 上升幅度符合配置规则
3. 不出现重复累计

### Phase 1 测试

1. 同用户同 URL 同天连续点击两次，第二次必须 `duplicate`
2. 同关键词今天 2 次点击，same-day boost 正确累计但不超过上限
3. `collect_hot_keywords()` 中同一点击不会被 `+0.2` 和 `+0.5` 双算

---

## Phase 2：午后追更模式

### 目标

把“下午追更”从当前泛化的 `--hot-only` 里拆成一个可控、稳定、不破坏上午结果的模式。

### 涉及文件

- `src/pipeline/runner.py`
- `src/pipeline/hot_tracker.py`
- `src/storage/sqlite.py`
- `src/agents/render.py`

### 任务清单

#### 2.1 为 CLI 增加 `--follow-up`

文件：

- `src/pipeline/runner.py`

任务：

1. `argparse` 增加 `--follow-up`
2. `run_hot_only()` 增加对应参数，例如：
   - `follow_up: bool = False`

验收标准：

1. `--hot-only` 与 `--hot-only --follow-up` 行为可区分
2. 默认 `--hot-only` 旧行为尽量保持兼容

#### 2.2 `search_hot_topics()` 支持按调用覆盖时效与数量

文件：

- `src/pipeline/hot_tracker.py`

任务：

1. 扩展 `search_hot_topics()` 参数：
   - `max_age_days`
   - `max_results_per_keyword`
   - `selected_limit`
   - `follow_up`
2. 把这些参数透传到 `_filter_by_age()` 和候选选取逻辑

验收标准：

1. follow-up 可指定 `max_age_days=1`
2. 不再依赖模块常量 `_MAX_NEWS_AGE_DAYS` 做硬编码

#### 2.3 保护上午 `selected`

文件：

- `src/pipeline/runner.py`
- `src/storage/sqlite.py`

任务：

1. `run_hot_only(follow_up=True)` 开始前快照上午 `selected=True` 的 URL
2. follow-up 期间不要覆盖上午 `selected` 语义
3. 下午推送候选在内存中独立过滤，不回写 `selected`

验收标准：

1. 下午失败时上午日报结果不丢
2. 上午 `selected` 仍只表示“日报主卡入选”

#### 2.4 follow-up 只取当天新增

文件：

- `src/pipeline/hot_tracker.py`

任务：

1. 新增“是否当天新闻”判断逻辑
2. follow-up 模式优先依据 `publish_date == today`
3. 至少排除：
   - `昨天`
   - `前天`
   - `N天前`
4. 不再简单用 `"N小时前" -> age=0` 代表“当天”

验收标准：

1. 昨晚/隔夜文章不会混入 follow-up
2. 当天下午新发文章可以进入 follow-up

#### 2.5 follow-up 去重规则

文件：

- `src/pipeline/runner.py`
- `src/storage/sqlite.py`

任务：

1. follow-up 去重范围：
   - 上午已推热点 URL
   - 当天市场新闻 URL
2. 不去重上午未上卡候选

验收标准：

1. 用户上午已看过的热点不会下午再推
2. 上午落选但下午因权重变化变得更重要的候选仍有机会入选

#### 2.6 follow-up 推送门禁

文件：

- `src/pipeline/runner.py`
- `src/agents/render.py`

任务：

1. follow-up 最多推 4 条
2. 少于 2 条不推
3. 卡片标题与正文样式独立于上午日报

验收标准：

1. 0-1 条新增时静默不推
2. 2-4 条时推送“午后热点新增”卡

### Phase 2 测试

1. `--hot-only --follow-up` 时，上午 `selected` 不会被清零污染
2. follow-up 结果数少于 2 时不推送
3. follow-up 结果会排除上午已推 URL
4. `"17小时前"` 这类隔夜内容不会误入 follow-up

---

## Phase 3：工程完善与长期信号升级

### 目标

把 MVP 补完整，增强可维护性、配置能力和后续演化空间。

### 涉及文件

- `src/storage/sqlite.py`
- `src/pipeline/hot_tracker.py`
- `src/pipeline/runner.py`
- `src/agents/calibrator.py`
- `src/config.py`
- `data/`

### 任务清单

#### 3.1 清理过期 session

文件：

- `src/storage/sqlite.py`
- `src/pipeline/runner.py`

任务：

1. 新增 `prune_hot_feedback_sessions()`
2. 在合适的入口调用：
   - `run_pipeline`
   - `run_hot_only`
   - 或 cron 前置清理

验收标准：

1. 过期 48h 的 session 会被清理
2. 清理不影响当天 boost

#### 3.2 增加热点配置文件

文件：

- `data/hot_feedback.yaml`
- `src/config.py` 或对应配置加载层

任务：

1. 新建热点反馈专用配置文件
2. 将以下参数配置化：
   - `boost_per_click`
   - `max_same_day_boost`
   - `session_ttl_hours`
   - `historical_window_days`
   - `historical_boost_per_click`
   - `follow_up_push_min_items`
   - `follow_up_push_max_items`

验收标准：

1. 热点反馈参数不再散落硬编码
2. 不污染 `competitor_list.yaml`

#### 3.3 增加短期兴趣升级统计接口

文件：

- `src/storage/sqlite.py`
- `src/agents/calibrator.py` 或单独规则层

任务：

1. 新增 `get_hot_feedback_stats(...)`
2. 支持统计近 14 天热点关键词点击次数
3. 先只做统计，不强制第一版自动写入 `calibration_params`

验收标准：

1. 能输出“哪些关键词 14 天内 >= 3 次点击”
2. 后续可作为 Calibrator 输入

#### 3.4 日志与错误可观测性

文件：

- `src/pipeline/hot_tracker.py`
- `src/feishu/bot.py`
- `src/storage/sqlite.py`

任务：

1. 为以下路径增加更明确的 warn/info：
   - headline 回查失败
   - session 写入 duplicate
   - follow-up 无上午 selected 基线
   - AI filter 失败与 fallback 生效

验收标准：

1. 出问题时能快速区分是：
   - DB 问题
   - AI filter 问题
   - 去重问题
   - follow-up 时效问题

### Phase 3 测试

1. 过期清理只删过期 session
2. 配置值可覆盖默认权重参数
3. 热点统计接口能正确输出近 14 天点击次数

---

## 15. 推荐开发顺序

如果要最短路径上线，建议按这个顺序做：

1. Phase 0
2. Phase 1
3. 手工验证同天闭环
4. 再做 Phase 2
5. 最后补 Phase 3

原因：

1. Phase 0+1 就能产生真实用户价值
2. follow-up 模式耦合更多 UI/筛选/去重逻辑，适合第二步做
3. Phase 3 主要是工程完善，不阻塞 MVP

---

## 16. 建议的提交拆分

为了降低回归风险，推荐按 4 个 commit/PR 拆：

### Commit 1

主题：

`feedback schema: add content_source and narrow calibrator inputs`

包含：

1. `user_feedback.content_source`
2. Calibrator SQL 过滤
3. 对应测试

### Commit 2

主题：

`hot feedback mvp: add session table and atomic dual-write`

包含：

1. `hot_feedback_sessions`
2. DB 双写接口
3. bot 接入
4. same-day boost
5. 对应测试

### Commit 3

主题：

`hot follow-up mode: add afternoon incremental push flow`

包含：

1. `--follow-up`
2. 当天过滤
3. 上午 selected 保护
4. 2-4 条门禁
5. 对应测试

### Commit 4

主题：

`hot feedback polish: config, cleanup and promotion stats`

包含：

1. 配置文件
2. 清理任务
3. 统计接口
4. 日志增强

---

## 17. 完成定义

满足以下条件后，可以认为这套能力真正完成：

1. 用户上午点击热点“感兴趣”后，下午重跑热点时目标关键词权重明显提升
2. Calibrator 不再读到热点点击
3. 同用户不能对刷同一热点重复加权
4. follow-up 不会覆盖或清空上午日报结果
5. follow-up 推送只包含真正当天新增且值得追更的内容
6. 所有关键路径都有最基本的自动化测试覆盖
