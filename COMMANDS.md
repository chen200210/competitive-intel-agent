# 竞品情报系统 — 操作手册

## 目录

- [一、数据管道](#一数据管道)
- [二、Agent 单独运行](#二agent-单独运行)
- [三、全链路运行](#三全链路运行)
- [四、飞书推送](#四飞书推送)
- [五、查看结果](#五查看结果)
- [六、测试](#六测试)
- [七、每日自动推送逻辑](#七每日自动推送逻辑)

---

## 一、数据管道

### 导入 CSV 数据

```cmd
python -m src.pipeline.loader
```

从 `data/raw/` 读取 CSV 文件，导入 `rankings` 表。文件名格式：`ios_热门榜_20260616.csv`。

### 查看已导入的日期

```cmd
python -m src.storage.sqlite
```

### 对比昨日排名变动

```cmd
python -m src.pipeline.differ --date 2026-06-16
```

输出每天的变动清单 + attention_score + day_type。如果是第一天（无昨日数据），提示"首日数据已入库"。

### 预选故事（纯规则，零 API 消耗）

```cmd
python -m src.pipeline.story_picker --date 2026-06-16
```

从变动中自动识别 6 类故事（大幅跃升/黑马突围/断崖下跌/跨榜信号/持续爬升/品类异动），最多 8 条候选。

### 跨榜对照分析

```cmd
python -m src.pipeline.cross_chart --date 2026-06-16
```

同一天同一游戏在不同榜单的排名差异检测（需要 ≥2 种榜单类型的数据）。

---

## 二、Agent 单独运行

每个 Agent 可以独立测试，不依赖完整流水线。

### Overview Scanner（A0）— 全局扫描

```cmd
# 默认最新日期
python -m src.agents.overview_scanner

# 指定日期 + 详细日志
python -m src.agents.overview_scanner --date 2026-06-16 --verbose
```

搜当天行业新闻，判断波动率，决定哪些变动值得调研。每天必跑。

### Researcher（A1）— 深度调研

```cmd
# 调研最新日期最高关注度的变动
python -m src.agents.researcher

# 指定 DB 里的 change_id
python -m src.agents.researcher --change-id 1 --verbose

# 指定日期 + 游戏名
python -m src.agents.researcher --date 2026-06-16 --game "鸣潮"
```

五个维度搜索：事件/玩法/玩家/设计/在研。消耗 API token 最多（12 轮 tool call）。

### Verifier（B）— 可靠性核验

```cmd
# 核验最新的研究结果
python -m src.agents.verifier

# 指定 research_id
python -m src.agents.verifier --research-id 2 --verbose
```

对每条 finding 打三维度分（来源权威性/可交叉验证性/因果逻辑一致性），≥3 分放行。

### Analyst（C）— 商业分析

```cmd
# 分析最新日期
python -m src.agents.analyst

# 指定日期
python -m src.agents.analyst --date 2026-06-16 --verbose
```

纯推理 Agent，不需要搜索。分析因果、趋势、行业关联、影响评估。

### Design Analyst（C₂）— 玩法设计分析

```cmd
# 分析最新的调研结果
python -m src.agents.design_analyst

# 指定 research_id
python -m src.agents.design_analyst --research-id 2 --verbose
```

从决策者视角回答：为什么好玩、值不值得做、竞争风险在哪。6 个深度维度。

### Briefer（E）— 生成日报卡片

```cmd
# 生成最新日期的日报
python -m src.agents.briefer

# 指定日期
python -m src.agents.briefer --date 2026-06-16
```

融合商业分析 + 设计分析，输出飞书卡片 JSON。

---

## 三、全链路运行

### 一键跑完整流水线

```cmd
# 最新日期
python -m src.pipeline.runner

# 指定日期
python -m src.pipeline.runner --date 2026-06-16

# 看每步耗时
python -m src.pipeline.runner --date 2026-06-16 --verbose

# 强制全部重跑
python -m src.pipeline.runner --date 2026-06-16 --force
```

自动跳过已完成的步骤。顺序：

```
Phase 1: Differ → Story Picker → Cross Chart
Phase 2: Overview Scanner → Researcher → Verifier → Analyst → Design Analyst
Phase 3: Briefer
```

### 从爬取到推送一条命令

```cmd
python -m src.pipeline.runner --scrape --push oc_xxxxxxxxxxxxx
```

这条命令做了四件事：

```
Phase 0: diandian_batch.py 抓取 iOS 全 5 榜（免费/畅销/热门/下载/收入）→ 自动导入新 CSV
Phase 1: Differ → Story Picker → Cross Chart
Phase 2: Overview Scanner → Researcher → Verifier → Analyst → Design Analyst
Phase 3: Briefer 生成日报卡片
Phase 4: 推送到飞书群
```

已入库的数据自动跳过，只处理新增变动。

### 全部参数

| 参数 | 作用 |
|------|------|
| `--date 2026-06-16` | 指定日期（不传则用最新） |
| `--scrape` | 先跑爬虫抓取 iOS 全 5 榜（免费/畅销/热门/下载/收入），再自动导入新 CSV |
| `--push oc_xxx` | 完成后推送日报卡片到飞书群 |
| `--force` | 强制重跑所有步骤（不跳过已有数据） |
| `--verbose` / `-v` | 打印每步耗时 + Agent 工具调用过程 |
| `--brief-only` | 只输出卡片 JSON，不打印进度

---

## 四、飞书推送

### 测试推送连接

```cmd
# 列出机器人在的所有群
python -m src.feishu.pusher list-chats

# 发测试卡片到群
python -m src.feishu.pusher test-chat oc_xxxxxxxxxxxxx

# 查找用户 open_id
python -m src.feishu.pusher find-user your@email.com

# 发测试卡片给用户
python -m src.feishu.pusher test-user ou_xxxxxxxxxxxxx
```

### 推送日报卡片

```cmd
# 推送最新日报到群
python -m src.feishu.pusher push-daily oc_xxxxxxxxxxxxx

# 推送指定日期的日报
python -m src.feishu.pusher push-daily oc_xxxxxxxxxxxxx 2026-06-16
```

### 启动对话机器人

```cmd
python -m src.feishu.bot
```

保持终端开着。飞书上 @机器人 可以：
- **查历史**："原神上周表现怎么样"
- **做调研**："为什么 XXX 今天突然冲榜了"
- **做对比**："对比原神和鸣潮"

---

## 五、查看结果

### 查看 Researcher 的调研输出

```cmd
python tests/show_research.py
```

显示：游戏名、10 条 findings（含来源 URL、fetch_status、design_tags）、在研信号、搜索覆盖率。

### 直接查数据库

```cmd
python -m src.storage.sqlite
```

---

## 六、测试

```cmd
# Week 1 数据管道基础测试
python tests/test_week1.py

# db_query 工具安全验证
python tests/test_db_query.py

# cross_chart 信号检测
python tests/test_cross_chart.py

# Researcher 模板 + DB 集成（不调 LLM）
python tests/test_researcher_smoke.py

# Verifier 模板 + 数据通路（不调 LLM）
python tests/test_verifier_smoke.py

# 计时器精度验证
python tests/test_timing.py
```

---

## 七、每日自动推送逻辑

### 链路

```
08:00  数据获取（二选一）
         ├── 自动：python -m src.pipeline.runner --scrape  (先抓取再导入)
         └── 手动：从点点/七麦下载 CSV → 放入 data/raw/ → python -m src.pipeline.loader

09:00  日报生成 + 推送（一条命令）
         └── python -m src.pipeline.runner --scrape --push oc_xxx
               │
               ├── Scrape       — 自动抓取 iOS 游戏榜
               ├── Loader       — 导入新 CSV（已有日期 → 跳过）
               ├── Differ       — 对比昨日排名（已有 → 跳过）
               ├── Story Picker — 预选 8 条候选（纯规则，始终运行）
               ├── Cross Chart  — 跨榜信号检测（纯规则）
               ├── Overview Scanner — 行业扫描（已有 → 跳过）
               ├── Researcher   — 只调研新上榜/新变动的游戏（已有 → 跳过）
               ├── Verifier     — 只核验新调研结果（已有 → 跳过）
               ├── Analyst      — 商业分析（已有 → 跳过）
               ├── Design Analyst — 设计分析（已有 → 跳过）
               ├── Briefer      — 生成卡片
               └── Push         — 推送到飞书群
```

爬取 → 导入 → 分析 → 推送，一条命令。

### 智能跳过

Runner 每步先查 DB，只有缺失的数据才触发 AI 调用：
- 今天的新上榜游戏 → 新触发 Researcher
- 已调研过的游戏 → 直接跳过，不重复消耗 token
- 第一天数据量最多（全量调研），之后每天只有 5-8 款新增需要 AI

### Windows 任务计划配置

```
程序：E:\DOSH\OA\venv\Scripts\python.exe
参数：-m src.pipeline.runner --scrape --push oc_xxxxxxxxxxxxx
触发：每天 09:00
```
