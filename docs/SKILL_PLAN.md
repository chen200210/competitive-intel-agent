# 可封装 Skill 汇总

> 所有 Skill 都是对已有功能的快捷封装。优先级按日活频率和当前痛点排序。

---

## Skill 1：`/daily-report` — 日报一键生成+推送

**日活频率**：每天 1 次

**当前操作**：
```bash
python -m src.pipeline.runner --scrape --push oc_xxx --verbose
```
然后翻 JSON、查数据库、去飞书看卡片质量。

**Skill 做什么**：
1. 确认 `data/raw/` 下有没有今天的 CSV（没有则提示先爬数据）
2. `python -m src.pipeline.runner --scrape --push oc_xxx`
3. 不在终端 dump 完整 JSON，而是输出结构化摘要：
   - 数据管道：导入 N 条、Differ 发现 M 条变动、Story Picker 选出 K 条候选
   - AI Agent：Scanner 选了 X 条 focus（其中跨榜 Y 条、赛道匹配 Z 条）、Researcher 并行跑了 N 个、Verifier 核验了 M 条
   - 耗时明细：各 Phase 和 Agent 的耗时
   - 飞书推送：成功/失败
4. 最后问一句"日报已推送，要看摘要还是跳过？"

**依赖的功能**：Runner、Pusher（均已就绪）

---

## Skill 2：`/audit <date>` — 日报质量审计

**日活频率**：每天 1 次（日报跑完后）

**当前操作**：人工翻 JSON → 看 Scanner 选了什么 → 查有没有漏掉赛道游戏 → 看 Researcher 有没有 parse error → 查数据库各表状态

**Skill 做什么**：
1. 自动检查并输出红绿灯报告：

| 检查项 | 红灯条件 | 绿灯条件 |
|--------|---------|---------|
| 跨榜信号覆盖率 | recommended_focus 中跨榜信号 = 0 | ≥ 2 条 |
| 赛道匹配覆盖 | 塔防/微恐/冰河游戏在 skip 里 | 至少 2 条在 focus 里，0 条误杀 |
| Researcher 健康度 | 有 parse_error | 全部正常解析 |
| 各 Agent 耗时 | 某 Agent 超正常范围 2 倍 | 在正常范围内 |
| 数据完整性 | rankings 为 0 或 changes 为空 | 有数据 |
| 推送状态 | pusher 报错 | 推送成功 |

2. 如果某项目黄灯或红灯，自动给出排查建议（"Scanner 选跨榜信号为 0 → 检查 cross_chart_signals 是否有数据"）

**依赖的功能**：Runner 输出、数据库各表、Scanner recommended_focus（均已就绪）

---

## Skill 3：`/research <游戏名或bundle_id>` — 单游戏深度调研

**日活频率**：按需（几天一次）

**当前操作**：
```bash
python -m src.agents.researcher --change '{"game_name":"鸣潮","bundle_id":"...",...}'
```

**Skill 做什么**：
1. 如果只给了游戏名，自动从数据库查最近的 change 记录，构造完整的 change dict
2. 调 Researcher → Verifier → Analyst → Design Analyst
3. 输出精简版调研报告（不 dump JSON）：
   - 事件层：发生了什么
   - 玩法层：更新了什么
   - 设计洞察：可借鉴的点
   - 竞争风险：在研竞品
4. 问要不要把这个游戏加到 `competitor_list.yaml` 的监控列表

**依赖的功能**：Researcher、Verifier、Analyst、Design Analyst（均已就绪）

---

## Skill 4：`/tune-prompt <agent_name>` — Prompt 迭代辅助

**日活频率**：调 prompt 时使用（集中式，不频繁）

**当前操作**：
1. 改 YAML
2. `python -m src.agents.xxx --date xxx --verbose`
3. 看 JSON 输出，对比上次输出
4. 重复

**Skill 做什么**：
1. 记录当前 prompt 的 hash，自动备份
2. 跑 Agent → 只展示关键字段（不 dump 完整 JSON）
   - Scanner：recommended_focus 数量 + 类型分布 + 跨榜信号数
   - Researcher：findings 数量 + 维度覆盖 + fetch 成功率
   - Briefer：卡片元素数量 + 是否含赛道区域 + 是否含竞争风险表
3. 展示当前结果和上次结果的 diff
4. 问"保留这次 prompt？"→ 是则覆盖，否则回滚

**依赖的功能**：各 Agent 的 CLI 入口（均已就绪）

---

## Skill 5：`/import-data` — 数据导入 + 验证

**日活频率**：有新数据源时（偶尔）

**当前操作**：
```bash
python -m src.pipeline.loader --file data/raw/xxx.csv
# 然后查数据库验证
```

**Skill 做什么**：
1. 扫描 `data/raw/` 下所有未导入的 CSV/XLSX
2. 自动提取日期和榜单类型
3. 逐个导入，输出摘要：文件名 → 导入 N 条 → 平台/榜单/日期
4. 导入完成后自动跑 Differ，输出变动数量
5. 如果某个文件已经有同日同榜数据，问"覆盖还是跳过？"

**依赖的功能**：Loader、Differ（均已就绪）

---

## 优先级和时间估算

| 顺序 | Skill | 投入时间 | 日活频率 | 理由 |
|:---:|--------|:---:|:---:|------|
| 🔴 1 | `/daily-report` | 30min | 每天 1 次 | 日活最高，省去手敲命令行 + 翻 JSON |
| 🔴 2 | `/audit` | 30min | 每天 1 次 | 日报跑完必做的质控，目前零自动化 |
| 🟡 3 | `/research` | 20min | 几天 1 次 | 飞书 Bot 已有类似功能，终端版做补充 |
| 🟢 4 | `/import-data` | 15min | 偶尔 | 不频繁，但写起来简单 |
| ⚪ 5 | `/tune-prompt` | 30min | 集中式 | prompt 调完几轮后就不常用了 |

---

## 实施建议

按顺序做，做完一个用几天再决定下一个做不做。

1 和 2 一起做——跑完日报立刻审计，两个 Skill 的数据源是同一份 Runner 输出。做完后你每天的工作流程变成：

```
/daily-report  →  等 2 分钟  →  /audit  →  红绿灯全绿 → 去飞书看卡片
```

如果某个灯是黄/红，才需要手动介入排查。全绿的话 30 秒收工。
