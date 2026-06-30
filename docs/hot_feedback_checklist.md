# HOT_FEEDBACK Test Checklist

> 适用范围：`HOT_FEEDBACK_DESIGN.md` 对应的热点反馈能力  
> 目标：验证“热点感兴趣 → 双写入库 → same-day boost → 午后追更 → 长期统计边界”整条链路正确

---

## 1. 测试目标

本次测试覆盖以下关键能力：

1. 热点点击反馈能正确写入两条数据轨道
2. 同天热点点击能即时影响关键词权重
3. 长期市场反馈与短期热点反馈不会互相污染
4. 午后 `--follow-up` 模式只推真正新增且值得追更的内容
5. 去重、事务、过期清理、配置覆盖等工程边界正确

---

## 2. 优先级定义

- `P0`：主链路正确性，失败会导致功能不可上线
- `P1`：核心体验正确性，失败会导致功能看起来“没生效”或“行为异常”
- `P2`：追更模式与边界行为
- `P3`：工程完善、日志、配置、清理

---

## 3. P0 核心链路

### P0-1 首次点击热点时原子双写成功
- 场景：用户第一次点击某条热点的 `感兴趣`
- 操作：触发一次热点点击回调
- 期望：
  - `user_feedback` 新增一条记录
  - `feedback_type = 'hot_click'`
  - `content_source = 'hot'`
  - `hot_feedback_sessions` 新增一条 session
  - 返回成功提示
- 验证方式：查数据库 + 看回调返回/日志
- 状态：`[ ]`

### P0-2 同用户同 URL 同天重复点击会被去重
- 场景：同一用户在同一天重复点击同一热点
- 操作：连续触发两次相同点击
- 期望：
  - 第一条成功写入
  - 第二条返回 `duplicate` 或等价状态
  - `hot_feedback_sessions` 不重复插入
  - `user_feedback` 不重复插入
- 验证方式：查数据库
- 状态：`[ ]`

### P0-3 双写失败时不会留下半条数据
- 场景：写入其中一张表时发生异常
- 操作：人为制造 DB 写入失败或 mock 异常
- 期望：
  - 两张表要么都成功，要么都失败
  - 不出现只写了 `user_feedback` 或只写了 `hot_feedback_sessions`
- 验证方式：单测 / mock / DB 检查
- 状态：`[ ]`

### P0-4 `content_source` 字段不破坏旧反馈语义
- 场景：已有市场新闻 👍/👎 流程继续运行
- 操作：分别触发市场新闻点赞、点踩、热点点击
- 期望：
  - 市场新闻：`content_source = 'market'`
  - 热点点击：`content_source = 'hot'`
  - `feedback_type` 原语义保持不变
- 验证方式：查数据库
- 状态：`[ ]`

### P0-5 Calibrator 不读取热点点击
- 场景：库中同时存在市场反馈和热点点击
- 操作：执行 Calibrator 聚合
- 期望：
  - 聚合结果只包含 `content_source = 'market'`
  - 不读取 `feedback_type = 'hot_click'`
- 验证方式：聚合结果校验 / SQL 验证
- 状态：`[ ]`

---

## 4. P1 权重生效

### P1-1 same-day boost 能在当天生效
- 场景：当天用户点击某热点后重新收集关键词
- 操作：点击热点后执行 `collect_hot_keywords()`
- 期望：
  - 对应关键词权重上升
  - 上升来自 `hot_feedback_sessions`
- 验证方式：比较点击前后关键词权重
- 状态：`[ ]`

### P1-2 historical boost 排除今天
- 场景：同一天内已经有热点点击记录
- 操作：执行历史点击统计
- 期望：
  - 今天的点击不进入 historical boost
  - 只由 same-day boost 计算
- 验证方式：检查统计结果
- 状态：`[ ]`

### P1-3 same-day boost 与 historical boost 不双重计数
- 场景：同一关键词今天刚被点击，同时历史窗口内也有旧点击
- 操作：执行权重计算
- 期望：
  - 今日点击只算一次
  - 历史点击只统计历史天
  - 最终权重符合设计公式
- 验证方式：手工构造数据对账
- 状态：`[ ]`

### P1-4 same-day boost 有上限
- 场景：同一关键词当天被多次点击
- 操作：插入/触发多次 same-day 点击
- 期望：
  - boost 累加
  - 不超过配置上限，例如 `2.0`
- 验证方式：查最终 boost 值
- 状态：`[ ]`

### P1-5 过期 session 不参与权重计算
- 场景：48h 之前的热点 session 仍留在库中
- 操作：执行 active session 读取 / 权重计算
- 期望：
  - `expires_at <= now` 的记录不参与 same-day boost
- 验证方式：查结果
- 状态：`[ ]`

---

## 5. P2 午后追更

### P2-1 `--hot-only --follow-up` 能进入独立模式
- 场景：执行午后追更命令
- 操作：运行 `python -m src.pipeline.runner --hot-only --follow-up`
- 期望：
  - 进入 follow-up 分支
  - 使用 follow-up 专属参数
- 验证方式：CLI 输出 / 日志
- 状态：`[ ]`

### P2-2 follow-up 不清空上午 `selected`
- 场景：上午已有已选热点
- 操作：执行 follow-up
- 期望：
  - 上午 `selected=True` 的记录仍然保留原语义
  - 下午流程不覆盖上午主卡选择状态
- 验证方式：查数据库
- 状态：`[ ]`

### P2-3 follow-up 只保留当天新闻
- 场景：候选中混有昨天/前天/隔夜内容
- 操作：执行 follow-up 筛选
- 期望：
  - `昨天` / `前天` / `N天前` 被排除
  - 昨晚“17小时前”这类隔夜内容不会误入
- 验证方式：构造测试数据
- 状态：`[ ]`

### P2-4 follow-up 去重上午已推热点 URL
- 场景：上午主卡已推过某热点
- 操作：下午再次搜索到该 URL
- 期望：
  - 不会再次进入 follow-up 推送
- 验证方式：结果集检查
- 状态：`[ ]`

### P2-5 follow-up 去重当天市场新闻 URL
- 场景：同一条内容上午已出现在市场新闻板块
- 操作：下午热点搜索再次搜到
- 期望：
  - follow-up 不重复推送
- 验证方式：结果集检查
- 状态：`[ ]`

### P2-6 上午落选候选仍允许下午入选
- 场景：某热点上午搜到但未上卡，下午因 boost 提升更重要
- 操作：执行 follow-up
- 期望：
  - 该候选仍有机会入选
- 验证方式：结果集检查
- 状态：`[ ]`

### P2-7 follow-up 少于 2 条时不推送
- 场景：下午真正新增的合格热点只有 0-1 条
- 操作：执行 follow-up 推送逻辑
- 期望：
  - 不推卡
  - 输出明确日志或返回状态
- 验证方式：CLI 输出 / 日志
- 状态：`[ ]`

### P2-8 follow-up 最多推 4 条
- 场景：下午新增热点很多
- 操作：执行 follow-up
- 期望：
  - 最终推送条数不超过 4
- 验证方式：结果集检查
- 状态：`[ ]`

---

## 6. P3 工程完善

### P3-1 过期 session 可被清理
- 场景：数据库中存在过期热点 session
- 操作：执行清理方法
- 期望：
  - 只删除过期记录
  - 不删除当天有效记录
- 验证方式：查数据库
- 状态：`[ ]`

### P3-2 配置项可覆盖默认参数
- 场景：调整配置文件中的 boost / 窗口 / 门禁参数
- 操作：加载配置并执行相关逻辑
- 期望：
  - 新配置生效
  - 默认值在缺省时仍可回退
- 验证方式：配置加载 + 结果校验
- 状态：`[ ]`

### P3-3 headline 回查失败有明确日志
- 场景：点击热点时 `hot_topic_news` 中查不到对应 headline
- 操作：触发一次回调
- 期望：
  - 不影响主流程
  - 打出明确 warn 日志
- 验证方式：日志检查
- 状态：`[ ]`

### P3-4 follow-up 缺上午基线时有明确诊断
- 场景：上午没有 selected 记录
- 操作：执行 follow-up
- 期望：
  - 有清晰 warn/info
  - 行为符合设计预期
- 验证方式：日志检查
- 状态：`[ ]`

### P3-5 AI filter 失败时 fallback 行为可观测
- 场景：AI 筛选失败或输出不合法
- 操作：触发 fallback
- 期望：
  - 日志能区分是 AI 失败还是结果为空
  - fallback 行为明确
- 验证方式：日志检查
- 状态：`[ ]`

---

## 7. 手工验证建议顺序

建议按以下顺序手测：

1. 先测一次热点点击双写
2. 再测 same-day boost 是否生效
3. 再测 Calibrator 边界
4. 再测 `--follow-up`
5. 最后测清理、配置、日志

这样能最快发现主链路问题。

---

## 8. 建议补充的自动化测试

建议至少落成以下自动化测试：

1. `record_hot_topic_feedback` 成功双写
2. `record_hot_topic_feedback` 重复点击去重
3. `record_hot_topic_feedback` 事务回滚
4. `get_active_hot_feedback_boosts` 过滤过期 session
5. `get_hot_keyword_click_stats(exclude_date=...)` 排除今天
6. `collect_hot_keywords` 权重不双算
7. `search_hot_topics(... follow_up=True)` 时效覆盖
8. Calibrator 聚合过滤 `hot_click`

---

## 9. 完成标准

以下项目全部通过，才算本功能完成：

- [ ] 热点点击双写正确
- [ ] 同用户重复点击去重正确
- [ ] same-day boost 生效
- [ ] historical boost 不双算
- [ ] Calibrator 不读热点点击
- [ ] follow-up 只推当天新增
- [ ] follow-up 不覆盖上午 `selected`
- [ ] 过期 session 能清理
- [ ] 关键路径有基础自动化测试