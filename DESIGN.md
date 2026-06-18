# 竞品情报智能体 (Competitive Intelligence Agent)

## 项目定位

面向游戏行业的竞品情报日报系统。基于 PDCA 工作方法设计，支持手动数据导入、AI 自动调研分析、飞书定时推送、人机交互问答。

**业务聚焦**：塔防（TD）品类 + 已被市场验证过的玩法。重点关注题材（微恐/冰河/火山爆发）和玩法方向（移动塔防、传统 TD 塔防、《移动城堡》like）。

**核心价值**：回答三个问题——
1. 这个方向值不值得做？
2. 市场上谁在做、做成什么样了？
3. 如果做，竞争风险在哪里？

**部署目标**：个人电脑可完整运行，除 LLM API 调用和飞书推送外全部本地化。

***

## 一、PDCA 方法论映射

| PDCA 环节    | 系统实现                   | AI 参与度 | 说明                  |
| ---------- | ---------------------- | ------ | ------------------- |
| **Plan**   | 竞品列表配置、监控维度设定          | 低      | 人工配置为主，AI 可辅助推荐关注维度 |
| **Do**     | 数据采集 + 调研 + 分析 + 简报生成  | **高**  | 核心流程，多 Agent 协作     |
| **Check**  | 信息可靠性核验 + 渠道拓展 + 系统自优化 | **高**  | 核心创新点，质量保障与自我进化     |
| **Action** | 飞书推送 + 交互问答            | 中      | 多形态分发与反馈闭环          |

### Check 环节详解（导师建议的核心方向）

#### Check-1：AI 对信息可靠性的复查

调研 Agent 搜到的信息在交给分析 Agent 之前，逐条进行可信度核验：

| 维度      | 评分标准                                    |
| ------- | --------------------------------------- |
| 来源权威性   | 官方公告(5) > 正规媒体(4) > 自媒体(3) > 论坛/匿名(2-1) |
| 可交叉验证性  | 多独立来源佐证(5) > 单源但详细(3) > 无法验证(1)         |
| 因果逻辑一致性 | 事件→数据变动的因果关系是否合理(1-5)                   |

只将 ≥3 分的信息送入分析 Agent，低可信度信息标记但不采纳。

#### Check-2：主动拓展信息渠道

**被动推荐**：

- 定期（每周/每月）回顾"数据大幅变动但调研无果"的 case
- AI 分析变动特征，推断最可能的信息来源
- 生成渠道拓展建议报告，推送给管理员决策

***

## 二、系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    个人电脑 (Windows/Mac/Linux)            │
│                                                           │
│  ┌──────────────┐                                        │
│  │ tools/       │  自动抓取（DOM 滚动，免 API）            │
│  │ diandian_    │──▶ data/raw/                           │
│  │ scroll.py    │                                        │
│  └──────┬───────┘                                        │
│         │                                                 │
│  ┌──────┴───────┐   ┌──────────────┐   ┌──────────────┐  │
│  │ 手动/自动下载的│   │   FastAPI    │   │   SQLite     │  │
│  │ CSV/Excel    │──▶│   后端服务    │──▶│  + Chroma    │  │
│  │ 榜单数据       │  │              │   │  本地数据库   │  │
│  └──────────────┘   │  - Agent 调度 │   └──────────────┘  │
│                     │  - 飞书 Webhook│                     │
│  ┌──────────────┐   │  - REST API  │   ┌──────────────┐  │
│  │ 竞品列表配置   │──▶│              │   │  Claude API  │  │
│  │ (YAML)        │   └──────┬───────┘   │  (云端调用)   │  │
│  └──────────────┘          │           └──────────────┘  │
│                            │                              │
└────────────────────────────┼──────────────────────────────┘
                             │
                       ngrok 内网穿透
                       (飞书回调用)
                             │
                             ▼
                   ┌─────────────────┐
                   │   飞书服务器      │
                   │  - 消息推送       │
                   │  - 机器人事件      │
                   └─────────────────┘
```

**核心原则**：所有数据和计算本地化，仅 LLM 调用和飞书推送走网络。

**数据约束**：原始 CSV 只包含当日排名快照，不含变动信息。排名变动由 Differ 模块对比历史数据计算得出，Day 1 导入的数据仅积累不触发分析。

**数据获取方式**：
- **自动抓取（推荐）**：`tools/diandian_scroll.py` 通过 DOM 滚动提取 iOS 游戏免费榜数据，不调 API 不触发反爬，输出 CSV 到 `data/raw/`。需先运行一次 `tools/diandian_auth.py` 保存登录态。
- **手动下载**：从点点数据/七麦等平台手动导出 CSV，放入 `data/raw/`。

***

## 三、技术选型

| 层面       | 选型                 | 理由                                  |
| -------- | ------------------ | ----------------------------------- |
| 后端框架     | **FastAPI**        | 轻量、async 原生、写 API 和 Webhook 统一      |
| Agent 编排 | **自研轻量编排**         | 仅 3-4 个 Agent，几百行代码足够，不需要 LangGraph |
| LLM      | **Claude API**     | 与 Claude Code 生态一致；备选国内模型可配置切换      |
| 结构化数据库   | **SQLite**         | 零配置、单文件、个人电脑完美适配                    |
| 向量数据库    | **Chroma** (嵌入式)   | 本地运行，pip install 即用，不需要 Docker      |
| 飞书 SDK   | **lark-oapi** (官方) | 发消息、收事件、长连接全封装                      |
| 内网穿透     | **ngrok** (免费版)    | 飞书事件回调需要一个公网 URL                    |
| 前端       | **无**              | 飞书消息卡片即 UI，不需要额外前端                  |

***

## 四、项目目录结构

```
competitive-intel-agent/
│
├── data/                            # 榜单数据
│   ├── raw/                         # CSV 文件（手动下载 或 自动抓取生成）
│   │   └── ios_game_free_rank_20260616.csv
│   ├── processed/                   # 解析后的结构化 JSON
│   ├── .diandian_chrome_profile/    # Chrome 登录态（自动抓取复用，已 gitignore）
│   └── competitor_list.yaml         # 竞品列表 + 关注维度配置
│
├── tools/                           # 数据抓取工具（独立于 src/ 核心管道）
│   ├── __init__.py
│   ├── diandian_auth.py             # 点点数据登录态保存（首次运行一次）
│   └── diandian_scroll.py           # iOS 游戏免费榜自动抓取（DOM 滚动提取）
│
├── src/
│   ├── __init__.py
│   ├── main.py                      # FastAPI 入口
│   ├── config.py                    # 配置（API key、路径等）
│   │
│   ├── pipeline/                    # 日报流水线
│   │   ├── __init__.py
│   │   ├── loader.py                # 读取 CSV/Excel，解析标准化
│   │   ├── differ.py                # 与昨日数据 diff，attention_score 计算
│   │   ├── story_picker.py          # 从 30+ 变动中筛选 5-8 个候选故事（纯规则，含跨榜信号）
│   │   └── runner.py                # 编排完整日报流程（定时/手动触发）
│   │
│   ├── agents/                      # 各 Agent 的 prompt 和调用逻辑
│   │   ├── __init__.py
│   │   ├── base.py                  # Agent 基类（LLM 调用 + tool use 封装）
│   │   ├── overview_scanner.py      # 全局扫描 Agent（每天必跑）
│   │   ├── researcher.py            # 调研 Agent（按需）
│   │   ├── analyst.py               # 商业分析 Agent（因果/趋势/影响）
│   │   ├── design_analyst.py         # 玩法设计分析 Agent（主策划视角）
│   │   ├── briefer.py               # 简报 Agent
│   │   └── verifier.py              # 可靠性核验 Agent
│   │
│   ├── tools/                       # Agent 可调用的工具
│   │   ├── __init__.py
│   │   ├── web_search.py            # 网络搜索
│   │   ├── web_fetch.py             # 网页内容抓取
│   │   ├── image_fetch.py           # 截图/图片抓取（简报用）
│   │   └── db_query.py              # 查询历史数据
│   │
│   ├── feishu/                      # 飞书集成
│   │   ├── __init__.py
│   │   ├── bot.py                   # 机器人消息处理（WebSocket 长连接）
│   │   ├── card_builder.py          # 飞书消息卡片拼装
│   │   └── pusher.py                # 消息推送
│   │
│   ├── storage/                     # 数据持久化
│   │   ├── __init__.py
│   │   ├── sqlite.py                # 结构化数据 CRUD
│   │   └── vector_store.py          # Chroma 向量存储
│   │
│   └── optimize/                    # Check 环节——自优化
│       ├── __init__.py
│       ├── prompt_optimizer.py      # Prompt 自优化
│       └── rule_optimizer.py        # 规则参数自优化
│
├── prompts/                         # Agent prompt 模板（YAML，外置可编辑）
│   ├── overview_scanner.yaml
│   ├── researcher.yaml
│   ├── analyst.yaml
│   ├── design_analyst.yaml
│   ├── briefer.yaml
│   └── verifier.yaml
│
├── frontend/                        # （可选）管理界面
│   └── index.html                   # 简易 Web 管理页
│
├── tests/
├── requirements.txt
├── .env                             # API keys（不提交 git）
├── .env.example
├── run.py                           # 一键启动
└── README.md
```

***

## 五、Agent 设计

### 5.1 真实数据与核心挑战

以 6.15 → 6.16 两天的真实数据为例：

```
6.15:                            6.16:
  1. 我的世界：移动版                1. 我的世界：移动版  (不变)
  2. Phigros                      2. 鸣潮             (↑1, 3→2)
  3. 鸣潮                          3. Phigros          (↓1, 2→3)
  4. 光·遇                         4. 光·遇            (不变)
  5. 异环                          5. 异环             (不变)
  6. 心动小镇                       6. 心动小镇          (不变)
  7. 翡翠经营模拟器                  7. 翡翠经营模拟器     (不变)
  8. 夜幕之下                       8. 夜幕之下          (不变)
  9. 单机推理杀                     9. 原神             (↑1,10→9)
 10. 原神                         10. 单机推理杀        (↓1, 9→10)
```

**关键发现**：

- 一天之内只发生了 4 次变动，全部是 ±1 位的微调
- 没有任何新上榜或掉榜
- **如果用旧的"变动 ≥3 位才触发 AI"逻辑，这一天不会有任何分析**
- 但这一天是有信息量的：鸣潮进前 3、Phigros 掉了、原神还在底部挣扎

**核心设计原则转变**：

```
旧思路：只有"大变动"才值得 AI 分析
新思路：AI 负责判断"今天有没有值得说的事"，大小变动 + 整体格局都需要 AI 参与
```

### 5.2 数据流总览

流水线不再是"有显著变动 → 触发 AI → 没变动 → 哑巴"。改成**每天必然触发一次 AI**，AI 自己判断今天说什么。

```
手动导入 CSV
     │
     ▼
┌───────────┐
│  Loader    │  纯脚本。解析 CSV，标准化落库。
└─────┬─────┘
      │ rankings 表
      ▼
┌───────────┐
│  Differ    │  纯脚本。对比昨日，算出每条变动。
│            │  同时计算"今日概况"：涨几个、跌几个、新进几个、掉榜几个。
└─────┬─────┘
      │ 变动清单 + 今日概况
      ▼
┌────────────────────────────────────────────────────────────────────┐
│                    AI 决策层                                        │
│                                                                     │
│  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌──────┐ │
│  │Overview  │   │Researcher│   │Verifier │   │Analyst  │   │Design│ │
│  │Scanner   │   │(按需)    │   │(按需)    │   │(商业)   │   │Analyst│ │
│  │每天必跑   │──▶│只调研     │──▶│核验调研  │──▶│因果+趋势 │   │(玩法) │ │
│  │整体搜索   │   │值得深挖的  │   │结果      │   │+影响    │   │设计洞察│ │
│  └─────────┘   └─────────┘   └─────────┘   └────┬────┘   └──┬───┘ │
│                                                  │           │      │
│                                                  └─────┬─────┘      │
│                                                        │            │
│                                              两份分析汇入 Briefer     │
└────────────────────────────────────────────────┬───────────────────┘
                                 │ 结构化分析报告
                                 ▼
                          ┌───────────┐
                          │  Briefer   │  每天必跑
                          │  简报 Agent │  生成飞书卡片
                          └─────┬─────┘
                                │
                                ▼
                          ┌───────────┐
                          │  Pusher    │  纯脚本
                          │  推送模块   │
                          └───────────┘
```

**数据规模**：真实榜单有 100 款游戏。每天 100 条记录，变动 20\~40 条。不可能全部用 AI 调研——在 Differ 和 AI 之间插入**纯逻辑层 Story Picker**，把 40 条变动筛成最多 8 个候选故事，再由 **Overview Scanner Agent 结合行业上下文决定最终的 5\~8 条**进入深度调研。

**Story Picker（纯脚本，无 AI）**——从一整天的变动中自动识别 **6 类**"值得讲的故事"（含跨榜信号）：

| 故事类型        | 检测逻辑                   | 为什么值得讲                  |
| ----------- | ---------------------- | ----------------------- |
| 🔺 **大幅跃升** | 排名上升 ≥ 10 位（不限区间）      | 可能有新版本/新活动/新玩法——最直接的信号  |
| 🆕 **黑马突围** | 新上榜直接进入前 50            | 是谁？怎么做到了？——这类信息最有价值     |
| 📉 **断崖下跌** | 排名下跌 ≥ 10 位或从前 30 掉榜   | 出了什么问题？Bug？舆情？被竞品挤压？    |
| 📐 **跨榜信号** | 同一游戏在不同榜单的排名差异 ≥ 25 位  | 单榜看不出，跨榜暴露本质——获客/变现/口碑的断裂点 |
| 📈 **持续爬升** | 连续 3+ 天排名上升（哪怕每天只 +2）  | 不是一天的偶然，而是一个趋势——睡着的巨人苏醒 |
| 🎯 **品类异动** | 同一品类/同一开发商有 ≥3 款游戏同向变动 | 不是单个游戏的事，是赛道级别的变化       |

**Story Picker 不依赖 AI，纯规则匹配，零 token 消耗。** 它保证每天最多选出 8 条候选故事（即使有 40 条变动）。最终 5-8 条的选择由 **Overview Scanner Agent** 结合行业上下文决定，安静日 5 条、剧烈波动日可放宽到 8 条。

### 5.3 Differ：位置感知的智能 Diff

Differ 的职责不只是算差值，还要给每条变动附加上下文，帮助后续 AI 判断"这值不值得关注"。

#### 5.3.1 基本对比逻辑

```python
def diff_with_yesterday(date: str) -> dict:
    """
    对比今天和昨天的排名，输出变动清单 + 今日概况。
    """
    today = db.query("SELECT * FROM rankings WHERE date = ?", date)
    yesterday = db.query("SELECT * FROM rankings WHERE date = ?", prev_date(date))
    
    today_map = {r.bundle_id: r for r in today}
    yesterday_map = {r.bundle_id: r for r in yesterday}
    
    changes = []
    overview = {"total": len(today), "up": 0, "down": 0, "new_entry": 0, "dropped_out": 0, "stable": 0}
    
    for bundle_id, t in today_map.items():
        y = yesterday_map.get(bundle_id)
        if y is None:
            overview["new_entry"] += 1
            change_type = "new_entry"
        elif t.rank < y.rank:
            overview["up"] += 1
            change_type = "up"
        elif t.rank > y.rank:
            overview["down"] += 1
            change_type = "down"
        else:
            overview["stable"] += 1
            change_type = "stable"
        
        changes.append(build_change_record(t, y, change_type, date))
    
    for bundle_id, y in yesterday_map.items():
        if bundle_id not in today_map:
            overview["dropped_out"] += 1
            changes.append(build_dropped_record(y, date))
    
    # 计算每个变动的"关注优先级"
    for c in changes:
        c["attention_score"] = compute_attention_score(c)
    
    return {
        "date": date,
        "overview": overview,
        "changes": sorted(changes, key=lambda c: c["attention_score"], reverse=True),
        "day_type": classify_day(overview),  # "quiet" | "normal" | "volatile"
    }
```

#### 5.3.2 位置感知的关注度评分

±1 位的变动在第 2 名和第 50 名意义完全不同。关注度评分不是看绝对差值，而是看"这个位置上的移动意味着什么"：

```python
def compute_attention_score(change: dict) -> float:
    """
    给每条变动打一个 0~10 的关注度分数。
    
    考虑因素：
    1. 排名区间权重（越高越受关注）
    2. 变动幅度（大跳 = 高关注，无论区间）
    3. 变动类型（新上榜/掉榜 > 排名移动 > 不变）
    4. Breakout 加分（从低位跳升是更强烈的信号）
    5. 是否在关注列表里
    """
    score = 0.0
    
    # 1. 排名区间权重
    rank = change.get("today_rank") or change.get("yesterday_rank")
    if rank <= 3:
        score += 5.0       # 前三名，最高权重
    elif rank <= 5:
        score += 3.5       # 前五名
    elif rank <= 10:
        score += 2.0       # 前十名
    elif rank <= 30:
        score += 1.0
    elif rank <= 50:
        score += 0.5       # 前 50 名，中等关注
    else:
        score += 0.2       # 50 名以后，基础分很低
    
    # 2. 变动类型
    change_type = change["change_type"]
    if change_type == "new_entry":
        score += 2.0       # 新上榜总是值得关注
        if rank <= 10:
            score += 3.0   # 直接冲进前十，重磅
        elif rank <= 50:
            score += 1.5   # 进入前 50，值得看一眼
    elif change_type == "dropped_out":
        yesterday_rank = change.get("yesterday_rank", 99)
        if yesterday_rank <= 10:
            score += 4.0   # 从高位掉榜，大事件
        elif yesterday_rank <= 30:
            score += 2.0
        else:
            score += 0.5
    elif change_type in ("up", "down"):
        delta = abs(change["rank_change"])
        
        # 头部区间小变动也重要
        if rank <= 5 and delta >= 1:
            score += 1.5
        elif rank <= 10 and delta >= 3:
            score += 1.0
        
        # 幅度加分（无论区间）
        if delta >= 20:
            score += 3.5   # 大跳——本身就是信号
        elif delta >= 10:
            score += 2.0
        elif delta >= 5:
            score += 1.0
        else:
            score += 0.3   # ±5 以内，很小的信号
    
    # 3. Breakout 加分：从低位大幅跃升，比同幅度在头部区间的变动更值得关注
    if change_type == "up":
        yesterday_rank = change.get("yesterday_rank", 99)
        delta = abs(change["rank_change"])
        # 从 30 名后跳升 ≥ 10 位 = breakout
        if yesterday_rank and yesterday_rank > 30 and delta >= 10:
            score += 2.0
        # 从 50 名后跳升 ≥ 5 位 = 显著 breakout
        if yesterday_rank and yesterday_rank > 50 and delta >= 5:
            score += 1.0
    
    # 4. 是否在重点关注列表
    if change["bundle_id"] in MONITORED_HIGH_PRIORITY:
        score += 1.0
    
    return min(score, 10.0)
```

**用 100 款榜单的典型变动验证**：

| 游戏               | 变动                            | 核心加分    | 总分                    | 解读 |
| ---------------- | ----------------------------- | ------- | --------------------- | -- |
| 翡翠经营模拟器 新上榜→第7   | 区间(2.0)+新上榜(2.0)+前10(3.0)     | **7.0** | 🔴 黑马，必讲              |    |
| 鸣潮 ↑1 (3→2)      | 区间(5.0)+头部位移(1.5)             | **6.5** | 🔴 头部变动，该讲            |    |
| 夜幕之下 ↑22 (37→15) | 区间(0.5)+幅度(3.5)+breakout(2.0) | **6.0** | 🔴 大跳，必讲              |    |
| 原神 ↓1 (9→10)     | 区间(2.0)+小幅(0.3)+关注列表(1.0)     | **3.3** | 🟡 可能被故事 4 捕获         |    |
| XX游戏 ↑3 (78→75)  | 区间(0.2)+小幅(0.3)               | **0.5** | ⚪ 直接被 Story Picker 过滤 |    |

#### 5.3.3 判断"今天是什么日子"

```python
def classify_day(overview: dict, total_monitored: int = 100) -> str:
    """
    把每天分成三类，决定 AI 的工作量。
    针对 100 款游戏的榜单优化阈值。
    """
    total = overview["total"]
    moved = overview["up"] + overview["down"] + overview["new_entry"] + overview["dropped_out"]
    volatility = moved / total if total > 0 else 0
    
    new_dropped = overview["new_entry"] + overview["dropped_out"]
    # 大幅变动的数量（用于更精细的判断）
    big_moves = overview.get("big_moves", 0)  # ≥15位的变动
    
    if volatility <= 0.1 and new_dropped <= 2 and big_moves == 0:
        return "quiet"      # ≤10% 变动，几乎无进出，无大跳
    elif volatility >= 0.3 or new_dropped >= 8 or big_moves >= 5:
        return "volatile"   # ≥30% 变动，或大量进出，或多大跳
    else:
        return "normal"     # 中间状态——最常见的日常
```

**你的 6.16 数据**：moved=4, total=10, volatility=0.4 → 刚好踩在 volatile 边缘（因为前10几乎都在动但幅度很小）。这种情况 Diffusion 标记为 normal。

#### 5.3.4 Differ 输出示例

```json
{
  "date": "2026-06-16",
  "day_type": "normal",
  "overview": {
    "total": 10,
    "up": 2,
    "down": 2,
    "new_entry": 0,
    "dropped_out": 0,
    "stable": 6
  },
  "changes": [
    {
      "game": "鸣潮",
      "bundle_id": "com.kurogame.mingchao",
      "developer": "库洛游戏",
      "today_rank": 2,
      "yesterday_rank": 3,
      "rank_change": 1,
      "change_type": "up",
      "attention_score": 7.5,
      "is_significant": true
    },
    {
      "game": "Phigros",
      "bundle_id": "com.PigeonGames.Phigros",
      "developer": "鸽游",
      "today_rank": 3,
      "yesterday_rank": 2,
      "rank_change": -1,
      "change_type": "down",
      "attention_score": 6.5,
      "is_significant": true
    },
    {
      "game": "原神",
      "bundle_id": "com.miHoYo.Yuanshen",
      "developer": "miHoYo",
      "today_rank": 9,
      "yesterday_rank": 10,
      "rank_change": 1,
      "change_type": "up",
      "attention_score": 3.5,
      "is_significant": false
    },
    {
      "game": "单机推理杀",
      "bundle_id": "com.longtime.mafia",
      "developer": "很长时间工作室",
      "today_rank": 10,
      "yesterday_rank": 9,
      "rank_change": -1,
      "change_type": "down",
      "attention_score": 2.5,
      "is_significant": false
    }
  ]
}
```

**is\_significant 的阈值**：attention\_score ≥ 5.0 → 进入 Story Picker 候选池。注意这个阈值是可调的，且后续 Check-3 自优化模块会根据历史反馈自动调整。

#### 5.3.5 Story Picker：从 40 条变动中选出 5\~8 个候选故事

Differ 产出的是"变动清单"（可能有 30\~40 条）。Story Picker 的作用是把它转化成**今日候选故事列表**（最多 8 条）。这是一个纯规则引擎，不消耗 AI token。最终 5\~8 条的选择权交给 Overview Scanner Agent。

**设计理念**：不是"变化最大的"最值得讲，而是"最有信息量的"最值得讲。

**五类故事及其检测规则**：

```python
def pick_stories(changes: list[dict], history: dict) -> list[dict]:
    """
    从全量变动清单中选出最多 5 个"今日故事"。
    
    五类故事优先级从高到低：
    """
    stories = []
    
    # 🔺 类型 1：大幅跃升（排名上升 ≥ 15 位）
    big_jumps = [c for c in changes 
                 if c["change_type"] == "up" and abs(c["rank_change"]) >= 15]
    for c in big_jumps:
        stories.append({
            **c,
            "story_type": "big_jump",
            "story_headline": f"{c['game']} 排名飙升 {abs(c['rank_change'])} 位",
            "story_angle": "是什么驱动了这次跃升？版本更新？活动？还是突然的自然增长？"
        })
    
    # 🆕 类型 2：黑马突围（新上榜直接进入前 50）
    black_horses = [c for c in changes
                    if c["change_type"] == "new_entry" and c["today_rank"] <= 50]
    for c in black_horses:
        stories.append({
            **c,
            "story_type": "black_horse",
            "story_headline": f"黑马 {c['game']} 首次上榜即进入第 {c['today_rank']} 位",
            "story_angle": "这款游戏是谁？什么玩法？怎么突然冲上来的？"
        })
    
    # 📉 类型 3：断崖下跌（排名下跌 ≥ 20 位，或从前 30 掉榜）
    cliff_drops = [c for c in changes
                   if (c["change_type"] == "down" and abs(c["rank_change"]) >= 20)
                   or (c["change_type"] == "dropped_out" and c.get("yesterday_rank", 99) <= 30)]
    for c in cliff_drops:
        stories.append({
            **c,
            "story_type": "cliff_drop",
            "story_headline": f"{c['game']} 排名{'暴跌' if c['change_type'] == 'down' else '掉榜'}",
            "story_angle": "出了什么问题？Bug？舆情危机？还是竞品挤压？"
        })
    
    # 📈 类型 4：持续爬升（连续 5+ 天排名上升）
    steady_climbers = find_steady_climbers(changes, history, min_days=5)
    for c in steady_climbers:
        stories.append({
            **c,
            "story_type": "steady_climber",
            "story_headline": f"{c['game']} 连续 {c['streak_days']} 天上升，从第 {c['start_rank']} 到第 {c['today_rank']}",
            "story_angle": "不是偶然的波动——持续爬升意味着什么？增长动力是什么？"
        })
    
    # 🎯 类型 5：品类异动（同一品类 ≥ 3 款游戏同向变动）
    cluster_moves = find_cluster_moves(changes, min_games=3)
    for cluster in cluster_moves:
        stories.append({
            **cluster,
            "story_type": "cluster_move",
            "story_headline": f"{cluster['genre']} 品类集体{cluster['direction']}",
            "story_angle": "不是单个游戏的事——整个赛道在变化。原因是什么？"
        })
    
    # 去重 + 排序 + 截断（最多 8 条候选，Agent 最终决定 5-8 条）
    stories = deduplicate_stories(stories)
    stories.sort(key=lambda s: story_priority(s), reverse=True)
    return stories[:8]
```

**Story Picker 输出示例**（假设某天的 100 款榜单中发生了这些变化，含跨榜信号）：

```json
{
  "date": "2026-06-16",
  "total_changes": 34,
  "stories_selected": 6,
  "stories": [
    {
      "story_type": "black_horse",
      "story_headline": "黑马「翡翠经营模拟器」首次上榜即进入第 7 位",
      "game": "翡翠经营模拟器",
      "bundle_id": "fallback:翡翠经营模拟器",
      "developer": "异度奇纪工作室",
      "today_rank": 7,
      "change_type": "new_entry",
      "story_angle": "独立工作室的模拟经营游戏突然进入前十——什么玩法打动了玩家？"
    },
    {
      "story_type": "cross_chart_signal",
      "story_headline": "「游戏 B」免费榜 #5 畅销榜 #48 热门榜 #12 — 获客强但变现能力严重滞后",
      "game_name": "游戏 B",
      "bundle_id": "com.example.gameb",
      "signal_pattern": "traffic_leak",
      "charts": {"免费榜": 5, "畅销榜": 48, "热门榜": 12},
      "story_angle": "高下载低付费的漏斗问题——是商业化设计缺陷还是产品阶段所致？",
      "threat_level": "medium"
    },
    {
      "story_type": "steady_climber",
      "story_headline": "「原神」连续 7 天上升，从第 12 位到第 9 位",
      "game": "原神",
      "streak_days": 7,
      "start_rank": 12,
      "today_rank": 9,
      "story_angle": "缓慢但持续的回暖——是活动驱动还是自然回流？"
    },
    {
      "story_type": "big_jump",
      "story_headline": "「夜幕之下」排名飙升 22 位",
      "game": "夜幕之下",
      "rank_change": 22,
      "today_rank": 15,
      "yesterday_rank": 37,
      "story_angle": "一天之内从 37 跳到 15——发生了什么？"
    },
    {
      "story_type": "cluster_move",
      "story_headline": "二次元开放世界品类 4 款游戏集体上升",
      "genre": "二次元/开放世界",
      "direction": "集体上升",
      "games": ["鸣潮", "原神", "异环", "幻塔"],
      "story_angle": "赛道级别的变化——是行业事件驱动还是用户迁移趋势？"
    },
    {
      "story_type": "cross_chart_signal",
      "story_headline": "「游戏 D」热门榜 #5 免费榜 #45 — 社区热度领先，口碑型增长信号",
      "game_name": "游戏 D",
      "bundle_id": "com.example.gamed",
      "signal_pattern": "word_of_mouth",
      "charts": {"免费榜": 45, "热门榜": 5},
      "story_angle": "社区热度能否转化为下载增长？什么内容在驱动口碑传播？",
      "threat_level": "medium"
    }
  ]
}
```

**注意**：Story Picker 输出最多 8 条候选。Overview Scanner Agent 结合行业新闻从候选中决定最终的 5\~8 条进入深度调研。未被选中的候选不计入 Researcher 调用。

**为什么 Story Picker 放在 Differ 之后、AI 之前？**

| 层级           | 做什么          | 消耗            |
| ------------ | ------------ | ------------- |
| Differ       | 100 条→30 条变动 | 纯计算，免费        |
| Story Picker | 30 条→最多 8 条候选 | 纯规则，免费        |
| AI 层         | 5\~8 条→深度分析   | 付费（API token） |

**AI 只处理被 Overview Scanner 选中的条目**（5-8 条）。Story Picker 提供最多 8 条候选，Overview Scanner Agent 结合行业上下文做出最终选择。没有被选中的变动（如排名第 78→76 的 ±2 位微调），不出现在后续任何 Agent 的输入中。Briefer 简报里可以用一行 "其余 29 条变动均为 ±5 位内的正常波动" 概括。

### 5.4 Overview Scanner（全局扫描 — Agent A0）

**职责**：不管今天有没有大变动，每天跑一次。搜索今日游戏行业整体动态，为后续分析提供背景信息。同时接收 Story Picker 选出的最多 8 条候选故事，结合行业上下文决定最终的 5\~8 条进入深度调研——安静日 5 条，剧烈波动日可放宽到 8 条。

**输入**：Story Picker 的候选故事列表（最多 8 条） + 今日概况 + 日期

```json
{
  "date": "2026-06-16",
  "day_type": "normal",
  "overview": { "total": 100, "up": 8, "down": 12, "new_entry": 3, "dropped_out": 2, "big_moves": 2 },
  "top3_today": ["我的世界：移动版", "鸣潮", "Phigros"],
  "platform": "iOS",
  "stories": [
    { "story_type": "black_horse", "story_headline": "...", "game": "翡翠经营模拟器", "today_rank": 7 }
  ]
}
```

**工具**：

- `web_search(query)` — 搜索"今日游戏行业新闻"
- `db_query(sql)` — 查本周/本月历史概况，判断当前是常态还是异常

**做的事**：

1. 搜索当天游戏行业相关新闻（版本更新、活动、政策、舆情）
2. 与历史数据对比：今天的波动率是正常水平还是异常？
3. 输出"今日背景摘要"

**输出**：

```json
{
  "industry_news_today": [
    {
      "headline": "国家新闻出版署发布6月游戏审批信息",
      "relevance": "industry_wide",
      "source_url": "https://...",
      "summary": "本次共有87款游戏获得版号，包括XX、YY等"
    }
  ],
  "volatility_context": {
    "today_volatility": 0.4,
    "week_average_volatility": 0.35,
    "assessment": "今日波动率与本周均值接近，属于正常范围"
  },
  "recommended_focus": [
    {
      "game": "鸣潮",
      "reason": "进入前3名，且处于2.2版本更新窗口期，建议深度调研"
    }
  ],
  "skip_deep_research_for": [
    {
      "game": "单机推理杀",
      "reason": "±1位波动在9-10名区间属于正常噪声，不建议浪费调研资源"
    }
  ]
}
```

**关键价值**：Overview Scanner 充当了"分配者"的角色——它告诉后续的 Researcher 哪些变动值得花 API 费用深挖，哪些只是噪声。

### 5.5 Researcher（调研 Agent — Agent A1，按需调用）

**职责**：仅被 Overview Scanner 推荐的变动才触发。针对具体变动进行深度搜索。

**触发条件**：Overview Scanner 的 `recommended_focus` 列表 + attention\_score ≥ 5.0 的变动。

**输入**：

```json
{
  "game": "鸣潮",
  "bundle_id": "com.kurogame.mingchao",
  "developer": "库洛游戏",
  "platform": "iOS",
  "today_rank": 2,
  "yesterday_rank": 3,
  "rank_change": 1,
  "change_type": "up",
  "date": "2026-06-16",
  "context_from_scanner": "进入前3名，处于2.2版本更新窗口期"
}
```

**工具**：

- `web_search(query)` — 搜索具体事件，**必须包含设计维度的 query**
- `web_fetch(url)` — 抓取全文（更新公告、玩家评测、攻略文章等），**必须检查返回值——空文本/403/超时 = 失败**
- `db_query(sql)` — 查该游戏历史排名趋势 + 搜索缓存（`search_cache`、`fetch_cache` 表）

**搜索策略——五个维度缺一不可**：

| 维度 | 搜索目的 | 示例 query（以塔防为例） |
|------|---------|----------------------|
| 事件层 | 发生了什么 | "XX塔防游戏 版本更新 6月" |
| 玩法层 | 更新了什么内容 | "XX塔防 新关卡 新防御塔 机制详解" |
| 玩家层 | 玩家怎么评价 | "XX塔防 玩家评价 社区反馈 TapTap评分" |
| 设计层 | 设计意图是什么 | "XX塔防 关卡设计 数值平衡 商业化分析" |
| **在研层** | **谁在做类似的？做到什么程度了？** | **"塔防手游 在研 2026" "微恐题材 新游 开发中" "XX公司 塔防项目 招聘"** |

**搜索平台要求**（每个维度至少覆盖 2 个）：
- **TapTap** — 玩家评分 + 评论 + 测试招募（服务端渲染，web_fetch 可读）
- **B站** — UP主评测 + 攻略视频 + 官方账号动态
- **小红书** — 玩家种草/避雷帖 + 新手入坑指南
- **微博** — 官方公告 + 玩家舆情 + 热搜事件
- **NGA/贴吧** — 核心玩家深度讨论 + 攻略 UGC
- **17173/游戏葡萄/GameLook** — 游戏媒体报道（web_fetch 通常可直接抓取正文）

**在研层搜索说明**：这是为"竞争风险评估"专门增加的维度。搜索目标包括：
- 招聘信息（某公司正在招塔防策划 → 他们在做塔防项目）
- 版号信息（某公司拿到了塔防类游戏版号）
- 测试信息（TapTap/好游快爆上的测试招募）
- 投资/收购信息（某公司投资了塔防工作室）
- 行业报道（游戏葡萄/GameLook 对在研产品的报道）

#### 5.5.1 来源可达性规则（V2 新增）

**核心原则：宁可少一条 finding，也不留一条找不到可读来源的 finding。**

**规则 1 — 每条 finding 必须有一个可读来源**：
- 每条 finding 的 sources 中，至少要有 1 个 web_fetch 成功读取到正文的来源
- web_fetch 返回空文本（JS 渲染页）、403、超时 → 该 URL 不计数，必须额外搜索替代来源
- 此类失败的 URL 仍可保留在 sources 中（标记 `fetch_status: "failed"`），但需额外补充可读来源

**规则 2 — 官方来源失败时的替代搜索策略**：
如果官方页面 web_fetch 返回空或失败，立即搜索：
```
"{game_name} {事件关键词} TapTap"
"{game_name} {事件关键词} B站 动态"
"{game_name} {事件关键词} 公告 转载"
"{game_name} {事件关键词} 17173"
"{game_name} {事件关键词} 游戏葡萄"
```
这些平台通常服务端渲染，web_fetch 能直接读到正文。

**规则 3 — 来源分为三类**：
| source_type | 含义 | 是否必须可读 |
|-------------|------|:----------:|
| `official` | 原始公告/官方账号发布 | 否（保留 URL 作为溯源，抓取失败也不删除） |
| `media` | 游戏媒体转载报道（17173/游戏葡萄/GameLook/游戏日报） | **是** |
| `community` | 社区讨论（TapTap/B站/NGA/贴吧/微博） | **是** |

每条 finding 的 sources 数组里，**至少要有一个 source_type 为 `media` 或 `community`**（即至少要有一个 web_fetch 成功读取到的可读来源）。

**规则 4 — 搜索缓存利用**：
在开始搜索前，先用 `db_query` 查看 `search_cache` 和 `fetch_cache` 表：
- 了解今天已搜过什么 query（避免完全重复）
- 哪些 URL 已验证可读（`fetch_cache` 中 status_code=200 且 text_length>100）
- 哪些 URL 已知不可达（`fetch_cache` 中 status_code≠200 或 text_length=0，不再 fetch）

#### 5.5.2 搜索流程（V2 更新）

1. **先查缓存**：用 `db_query` 查 `search_cache` + `fetch_cache`，了解已有数据和不可达 URL
2. **查历史趋势**：用 `db_query` 查该游戏的历史排名（`rankings` 表，近 14 天 + 近 30 天均值）
3. **搜事件层和玩家层**（最容易找到直接原因）
4. **对重要结果做 web_fetch**：挑 2-3 篇最重要的点开看全文
5. **如果 web_fetch 失败**：立即搜替代来源（见规则 2），然后再次 web_fetch
6. **搜玩法层和设计层**（给 Design Analyst 用的深度内容，必须带 `design_tags`）
7. **最后搜在研层**（找竞争威胁——招聘、版号、测试、投资信息）
8. 每个维度至少出 1 条 finding，整篇至少 8 条（但宁缺毋滥——如果某维度确实没有可读来源支撑，减少数量）

#### 5.5.3 输出格式（V2 更新）

```json
{
  "game": "鸣潮",
  "bundle_id": "com.kurogame.mingchao",
  "developer": "库洛游戏",
  "rank_context": { "today_rank": 2, "yesterday_rank": 3, "rank_change": 1, "change_type": "up", "date": "2026-06-16" },
  "historical_trend": {
    "rank_history_7d": [{"date": "2026-06-16", "rank": 2}, {"date": "2026-06-15", "rank": 3}],
    "rank_history_30d_avg": 2.5,
    "trend_direction": "up",
    "trend_summary": "近7天排名在2-3之间波动，3.4版本上线后从3升至2，30天均值约2.5位"
  },
  "findings": [
    {
      "dimension": "event",
      "headline": "鸣潮3.4版本「未选择的旅途」6月8日上线，与赛博朋克2077联动",
      "summary": "2026年6月8日，鸣潮3.4版本正式上线，与《赛博朋克2077》展开联动...",
      "sources": [
        {
          "url": "https://mc.kurogames.com/main/news/detail/4772",
          "title": "鸣潮3.4版本更新维护预告",
          "source_type": "official",
          "platform": "官网",
          "published_date": "2026-06-07",
          "fetch_status": "failed"
        },
        {
          "url": "http://www.gamelook.com.cn/2026/06/595074",
          "title": "鸣潮又「赌」赢了：联动2077改写中国二游历史",
          "source_type": "media",
          "platform": "GameLook",
          "published_date": "2026-06-10",
          "fetch_status": "success"
        }
      ],
      "design_tags": [],
      "confidence": "high",
      "relevance_to_change": "direct_cause"
    }
  ],
  "in_development_signals": [
    {
      "company": "鹰角网络",
      "product_name": "明日方舟：终末地",
      "genre": "开放世界RPG/商业科幻",
      "theme": "科幻",
      "status": "已上线",
      "evidence": "2026年1月上线，全球全平台流水突破12亿元",
      "source_url": "https://...",
      "threat_assessment": "high"
    }
  ],
  "search_coverage": {
    "dimensions_covered": ["event", "gameplay", "player", "design", "in_development"],
    "dimensions_missed": [],
    "platforms_used": ["TapTap", "B站", "NGA", "微博", "GameLook", "17173"],
    "total_sources": 12,
    "total_web_searches": 12,
    "total_web_fetches": 6,
    "fetch_success_rate": "4/6"
  }
}
```

**输出自检清单**（Agent 输出前逐条确认）：
1. 每条 finding 的 sources 中，是否至少有 1 个 `fetch_status="success"` 且 `source_type` 为 media 或 community？
2. 如果某条 finding 没有可读来源 → 从 findings 中移除，改为在 `dimensions_missed` 中记录
3. official 类型来源即使 `fetch_status="failed"` 也保留（溯源价值），但不能作为唯一的来源
4. 所有 URL 必须是搜索结果中的真实链接，禁止编造

### 5.6 Verifier（可靠性核验 Agent — Agent B）

职责和设计不变，核验 Researcher 的每一条 finding。详见原设计。

### 5.7 Analyst（分析 Agent — Agent C）

**职责**：综合多日数据 + 调研结果 + 行业背景，产出分析结论。

与旧设计的关键区别：

- **输入加入了 7 天历史趋势**，不只对比昨天
- **加入了 Overview Scanner 的行业背景**，Agent 知道今天行业发生了什么
- 分析维度增加了"趋势持续性判断"——这个变动是今天的偶然波动还是连续第 N 天

**输入**：

```json
{
  "date": "2026-06-16",
  "day_type": "normal",
  "industry_context": { "...Overview Scanner 的输出..." },
  "focus_items": [
    {
      "change": { "...鸣潮的变动..." },
      "research": { "...经核验的调研结果..." },
      "history_7d": [3, 4, 3, 2, 3, 3, 2],  // 近7天排名
      "history_30d_avg_rank": 4.2
    },
    {
      "change": { "...Phigros 的变动..." },
      "research": { "...经核验的调研结果..." },
      "history_7d": [1, 2, 2, 1, 2, 2, 3],
      "history_30d_avg_rank": 2.1
    }
  ],
  "non_focus_changes": [
    {
      "game": "原神",
      "rank_change": 1,
      "change_type": "up",
      "history_7d": [12, 11, 10, 11, 10, 10, 9],
      "note": "连续缓慢回升中，近7天从12→9"
    }
  ]
}
```

**分析维度**：

1. **因果判断**：调研到的事件能否解释变动？（不变）
2. **趋势持续性**：这个变动是独立事件还是连续趋势？过去 N 天是否一直在往同一个方向走？
3. **行业关联**：变动的游戏之间有无竞争关联？（比如鸣潮上升 + 原神底部挣扎 = 二次元赛道内部竞争？）
4. **影响评估**：对自家产品/赛道的影响

**输出**（单条分析示例）：

```json
{
  "game": "鸣潮",
  "developer": "库洛游戏",
  "today_rank": 2,
  "rank_change": "+1 (3→2)",
  "trend_7d": "近7天排名在2-4之间波动，今日突破至第2",
  "trend_direction": "slow_climb",
  "analysis": {
    "causality": "2.2版本更新（6.15）直接驱动排名上升，属于典型的版本更新效应",
    "confidence": 0.82,
    "persistence": "版本更新首周通常维持高位，预计3-5天后排名可能回落至3-4名",
    "competition_note": "鸣潮进入前3的同时Phigros后撤至第3——二次元赛道内部排名正在重新洗牌。原神仍在底部但连续7天缓升，值得并行关注。",
    "impact": "库洛游戏通过高频版本更新维持竞争力的策略正在生效",
    "impact_level": "high",
    "tags": ["版本更新", "前3突破", "重点关注", "二次元赛道"],
    "watch_points": [
      "未来3天排名是否能稳在前3",
      "Phigros是否会反超——注意Phigros并无负向事件，可能是被动让位",
      "原神7天缓慢回升的趋势是否持续"
    ]
  }
}
```

**注意**：商业 Analyst 同时输出一份"整体格局分析"（对所有变动条目的综合判断），供 Briefer 撰写简报时引用。

### 5.8 Design Analyst（玩法设计分析 + 商业决策辅助 — Agent C₂）

**职责**：这是为**决策者**量身定制的 Agent。商业 Analyst 回答"发生了什么、意味着什么"，Design Analyst 回答三个核心问题：
1. **为什么好玩、设计上有什么可学的**
2. **这个方向值不值得做**
3. **如果做，竞争风险在哪里**

**输入**：Researcher 的调研结果中带有 `design_tags` 的 findings + 变动上下文

```json
{
  "game": "鸣潮",
  "rank_context": "iOS游戏榜第2，↑1位，近7天首次突破至第2",
  "design_findings": [
    {
      "event": "新角色「汐」技能机制详解",
      "design_tags": ["地形改造", "元素反应", "配队自由度", "操作上限"],
      "summary": "「汐」定位水属性辅助...",
      "source_type": "community"
    },
    {
      "event": "主题活动「潮汐觅境」设计解读",
      "design_tags": ["分层解锁", "社交共享进度", "付费引导", "在线时长"],
      "summary": "活动采用'探索+收集+兑换'三层结构...",
      "source_type": "industry_media"
    }
  ]
}
```

**分析维度**（从决策者视角，11 个维度）：

| 维度 | 分析内容 | 面向的决策 |
|------|---------|-----------|
| **核心玩法亮点** | 最吸引玩家的机制是什么？为什么 work？ | 玩法参考 |
| **玩家动机设计** | 驱动上线/付费/分享的心理钩子 | 系统设计参考 |
| **付费设计** | 付费点怎么埋的？转化链路怎么设计？ | 商业化参考 |
| **留存机制** | 玩家为什么明天还想来？ | 长线运营参考 |
| **可借鉴点** | 对自家产品的参考价值 + 移植难度 | 立项/迭代决策 |
| **竞争差异** | 与同类产品的差异化打法 | 定位参考 |
| **🆕 赛道可行性** | **这个玩法/题材方向值不值得做？市场天花板在哪？用户需求是否被充分满足？** | **立项决策** |
| **🆕 竞争风险评估** | **在研公司有哪些？各自进度/水平/覆盖率如何？我们进入的窗口期还有多久？** | **立项/资源投入决策** |
| **🆕 题材热度趋势** | **微恐/冰河/火山爆发等目标题材的市场热度是上升还是下降？有没有过度竞争的风险？** | **题材选择决策** |
| **🆕 市场验证信号** | **这个方向有没有已经被市场验证过的成功案例？失败的案例是什么原因？** | **风险控制决策** |
| **🆕 风险反照 (risk_mirror)** | **竞品被玩家集中吐槽的点是什么？我们产品里有没有同样的隐患？如果有，优先级多高？** | **风险控制 + 排期调整** |

**输出**：

```json
{
  "game": "鸣潮",
  "design_analysis": {
    "core_highlight": {
      "title": "「汐」——一个改变战场的辅助",
      "what": "E技能创造水域改变地形，Q技能对水域内敌人造成AOE+控场",
      "why_it_works": "传统辅助是'给队友加buff'，汐是'改变战斗环境让全队受益'。这种设计让辅助玩家不再是'工具人'，而是'战术核心'——操作感和存在感都远超传统辅助",
      "player_response": "NGA社区热议配队方案，玩家自创'汐+雷系'组合打出超预期伤害。配队自由度带来UGC传播——玩家主动生产内容",
      "design_tags": ["角色定位创新", "战术深度", "UGC驱动力"]
    },
    "player_motivation": {
      "hook": "全服共享解锁进度——社交压力转化为参与动力",
      "mechanism": "活动区域解锁不是个人进度而是全服进度。当你看到'全服已解锁至第三区域'而你还在第二区域时，会产生追赶压力。同时解锁时的全服公告给你一种'我是第一批通关者'的炫耀资本",
      "psychological_trigger": "从众效应 + 损失厌恶（限时奖励过期不候）+ 社会比较"
    },
    "monetization_design": {
      "model": "免费体验建立沉没成本 → 轻度付费补足 → 高价值锚点引导中额付费",
      "how": "前70%奖励免费获取，建立'我已经投入了这么多'的沉没成本。最后两档需要付费补足，但金额很小（6-30元），决策门槛极低。完成活动后弹出限时礼包（198元），以'你已经收集了这么多，不差这一步'推动中额付费",
      "elegance": "付费引导不是生硬弹出，而是嵌在活动进度里——'你已经完成80%了，30元拿最后20%'的转化率远超直接卖"
    },
    "retention_mechanism": {
      "daily_loop": "每日解锁新区域 → 探索新内容 → 收集代币 → 看到兑换进度增长 → 期待明天新区域",
      "weekly_loop": "本周累计收集量排名 → 社交炫耀 → 竞争压力",
      "key_insight": "不是'每日签到领奖励'这种粗暴留存，而是'每天都有新东西可以玩'的内容驱动留存"
    },
    "takeaways": [
      {
        "insight": "辅助角色的'操作感'设计——让辅助不再是挂件而是战术核心",
        "applicable_to": "我们的XX系统中可以借鉴'改变环境而非加数值'的设计思路",
        "effort": "medium"
      },
      {
        "insight": "全服共享进度→社交压力的正向利用",
        "applicable_to": "可考虑在公会/团队活动中引入共享进度机制，替代简单的排行榜竞争",
        "effort": "low"
      },
      {
        "insight": "活动内付费引导的渐进式设计——先建立沉没成本再引导付费",
        "applicable_to": "下次活动设计时可参考'免费体验→小额补足→中额礼包'的三段式",
        "effort": "low"
      }
    ],
    "competitive_differentiation": "鸣潮通过'地形改造'和'元素反应'的组合，在开放世界赛道上与原神形成了差异化",
    
    "market_viability": {
      "verdict": "值得关注但不建议直接跟进",
      "reasoning": "移动塔防+微恐题材在市场上已有3款在研产品，其中暗夜防线进度最快（预计2026下半年上线）。但当前市场上微恐塔防的用户需求尚未被充分满足——现有产品的核心玩法偏向传统TD，移动塔防+微恐的组合仍有差异化空间。建议在暗夜防线上线后观察首月数据再决定。",
      "market_window": "6-9个月",
      "risk_level": "medium"
    },
    
    "competitive_landscape": {
      "in_development": [
        {
          "company": "XX游戏工作室",
          "product": "暗夜防线",
          "genre": "移动塔防",
          "theme": "微恐",
          "status": "在研",
          "progress_estimate": "预计2026下半年上线",
          "coverage": "玩法+题材 高度重合",
          "strength": "团队有成功TD产品经验，美术风格成熟",
          "weakness": "暂无版号公开信息，可能存在政策风险",
          "threat_level": "high"
        }
      ],
      "released_competitors": [
        {
          "name": "示例塔防游戏A",
          "market_position": "传统TD塔防头部产品",
          "our_differentiation": "移动塔防的即时操作性是传统TD不具备的差异化空间"
        }
      ],
      "overall_assessment": "赛道尚未饱和，但窗口期有限。关键变量是暗夜防线的上线时间和市场表现。"
    },
    
    "theme_trend_analysis": {
      "theme": "微恐",
      "trend_direction": "上升",
      "evidence": [
        "近3个月5款微恐题材游戏进入榜单前100",
        "TapTap微恐标签搜索量同比增长40%",
        "头部主播开始主动寻找微恐题材内容"
      ],
      "overheat_risk": "低——目前仍是蓝海，但需警惕一旦有爆款出现后的跟风潮"
    },

    "risk_mirror": {
      "competitor_pain_point": "鸣潮2.2版本活动「潮汐觅境」付费引导被部分玩家吐槽'逼氪感明显'——最后两档奖励必须付费解锁，且限时",
      "our_risk": "我们的活动付费引导也是弹窗式+限时，存在同样的'逼氪'隐患",
      "severity": "medium",
      "suggested_fix": "改用渐进式付费引导（先免费体验建立沉没成本→小额补足→中额礼包），参考 Design Analyst 对鸣潮付费设计的拆解"
    },

    "actionable_insight": {
      "what_happened": "暗夜防线利用'视野限制'机制解决了传统TD'全图透视破坏恐怖氛围'的核心矛盾",
      "recommended_action": "在冰河题材demo中实验'暴风雪视野遮蔽'机制——防御塔视野受暴风雪影响周期性缩小",
      "timing": "2周内原型验证",
      "because": "这个机制创新成本低（纯规则逻辑），但差异化价值高——目前市场上没有冰河题材TD做过视野遮蔽。如果验证通过，这可以成为我们的核心差异化卖点。"
    }
  }
}
```

**商业 Analyst vs Design Analyst 的分工**：

| | 商业 Analyst | Design Analyst |
|------|------------|---------------| 
| 回答什么 | 发生了什么、趋势如何 | 为什么好玩、值不值得做、竞争风险在哪 |
| 服务对象 | 运营决策 | **立项/产品/战略决策** |
| 核心增量 | 因果推理 + 趋势判断 | **玩法拆解 + 赛道可行性 + 竞争风险评估** |

### 5.9 Briefer（简报 Agent — Agent E）

**职责**：每天必输出一份简报。融合**商业分析**和**设计分析**两份输入，生成一份完整的竞品日报。根据 `day_type` 自动调整篇幅。

```
day_type == "quiet"   → 轻量简报："今日榜单平稳，前10无显著变动"
day_type == "normal"  → 标准简报：商业分析 + 设计洞察（如有值得关注的更新）
day_type == "volatile"→ 详细简报：完整商业分析 + 完整设计分析
```

**输入**：

- Analyst（商业）的完整输出（整体格局 + 各条目分析）
- Design Analyst 的输出（仅对触发深度调研的游戏有输出）

**处理逻辑**：

1. 生成一句话摘要
2. **商业分析区**：排名变动 + 因果 + 趋势（来自商业 Analyst）
3. **设计洞察区**：核心玩法亮点 + 可借鉴点（来自 Design Analyst）
4. **🆕 竞争风险区**：在研公司追踪表 + 赛道可行性判断 + 题材热度趋势（来自 Design Analyst）
5. 非显著变动一行带过
6. **所有信息附带来源链接和图片**——读者不需要离开飞书去做额外搜索
7. 附带交互入口（追问、深度调研）

**简报设计原则——"看图即知"**：
- 每条分析结论附带**信息来源**（链接 + 缩略图）
- 游戏截图、排名变化图表直接嵌入卡片
- 在研公司信息以**表格**呈现（公司名/产品/进度/覆盖率/威胁等级），不需要外链
- 一句话原则：**读者看完卡片不需要再做任何搜索**

**简报卡片输出示例（塔防日报）**：

```json
{
  "msg_type": "interactive",
  "card": {
    "header": {
      "title": {"tag": "plain_text", "content": "🎮 竞品情报日报 | 2026-06-16"},
      "template": "blue"
    },
    "elements": [
      {
        "tag": "markdown",
        "content": "**📊 今日概况**\n监测塔防品类 15 款，目标题材 8 款。显著变动 3 项，在研动态 1 条。\n"
      },
      {"tag": "hr"},
      {
        "tag": "markdown",
        "content": "### 🔴 重点关注\n\n**「暗夜防线」— TD品类新游进入前50**\n移动塔防+微恐题材，排名 ↑23 (73→50)。XX游戏工作室出品。\n\n![游戏截图](https://img.example.com/anyexianfeng.jpg)\n\n📎 [TapTap页面](https://...) | [官方PV](https://...) | 💬 [追问](action:ask_anye)"
      },
      {"tag": "hr"},
      {
        "tag": "markdown",
        "content": "### 🎮 设计洞察\n\n**微恐+塔防的融合方式**\n暗夜防线不是简单套恐怖皮，而是把'视野限制'融入塔防机制——防御塔有视野范围，怪物在视野外不可攻击。这解决了传统TD'全图透视破坏恐怖氛围'的核心矛盾。\n\n**可借鉴**：视野机制+塔防的融合为'冰河'题材提供了设计参考——可以用'暴风雪视野遮蔽'做类似的机制创新。\n\n📎 来源：[游戏葡萄分析](https://...) | [玩家评测截图](https://...)"
      },
      {"tag": "hr"},
      {
        "tag": "markdown",
        "content": "### ⚠️ 竞争风险\n\n**在研塔防项目追踪**（与冰河/微恐/火山题材相关）：\n\n| 公司 | 产品 | 玩法 | 题材 | 进度 | 覆盖率 | 威胁 |\n|------|------|------|------|------|--------|------|\n| XX工作室 | 暗夜防线 | 移动塔防 | 微恐 | 在研 | 玩法+题材 | 🔴高 |\n| YY互动 | 冰封纪元 | 传统TD | 冰河 | 测试中 | 玩法+题材 | 🔴高 |\n| ZZ网络 | 火山堡垒 | 移动城堡like | 火山 | 立项 | 仅题材 | 🟡中 |\n\n**综合判断**：冰河题材+传统TD赛道已有测试中产品，如果我们要做冰河方向，窗口期预估 6-9 个月。微恐+移动塔防的竞争烈度更高。\n\n📎 信息来源：[YY互动招聘信息](https://...) | [冰封纪元测试招募](https://...)"
      },
      {"tag": "hr"},
      {
        "tag": "markdown",
        "content": "### 🟡 其他值得关注\n\n**XX游戏排名 ↑8** — 冰河题材新游，连续3天上升\n**YY游戏排名 ↓5** — 传统TD，被暗夜防线挤占\n\n### 📋 其余12款变动均为 ±5 位内的正常波动"
      },
      {"tag": "hr"},
      {
        "tag": "note",
        "elements": [
          {"tag": "plain_text", "content": "💡 @我 追问任何产品/在研公司/赛道方向，我会做深度调研"}
        ]
      }
    ]
  }
}
```

### 5.10 Agent 调用策略总结

| day\_type             | Overview Scanner | Researcher | Verifier | Analyst (商业) | Design Analyst | Briefer |
| --------------------- | :--------------: | :--------: | :------: | :----------: | :------------: | :-----: |
| **quiet**（变动 ≤15%）    |        ✅ 跑（选 5 条）  |    ❌ 不跑    |   ❌ 不跑   |   ✅ 轻量（仅格局）  |      ❌ 不跑      |  ✅ 轻量简报 |
| **normal**（变动 15-40%） |        ✅ 跑（选 5-6 条）|  ✅ 仅 ≥5 分  |  ✅ 仅核验调研 |     ✅ 标准     |   ✅ 仅对深度调研的游戏  |  ✅ 标准简报 |
| **volatile**（变动 ≥40%） |        ✅ 跑（选 6-8 条）|   ✅ 所有变动   |  ✅ 所有调研  |     ✅ 详细     |   ✅ 对每个调研的游戏   |  ✅ 详细简报 |

**核心原则**：
- AI 调用量随信息量动态伸缩。安静的日子少烧 token（5 条），热闹的日子多分析（最多 8 条）。
- Overview Scanner 负责从 Story Picker 的 8 条候选中做最终 5-8 条的裁剪——跨榜信号优先考虑。
- Design Analyst 只在有值得深挖的更新内容时才触发——没有更新内容的 ±1 变动不需要玩法分析。
- **TD 品类加成**：塔防品类和目标题材的变动自动获得 attention_score 加成（见 7.3 节 `genre_boost`），确保公司关注的赛道不会被漏掉。
- **图片即信息**：Researcher 搜到的截图、官方宣传图、排名趋势图，全部嵌入简报。读者不需要离开飞书。

***

## 六、飞书集成

### 6.1 飞书应用配置

1. 在[飞书开放平台](https://open.feishu.cn)创建企业自建应用
2. 开通权限：
   - `im:message:send_as_bot` — 发送消息
   - `im:message:read` — 读取消息
   - `im:message:reaction` — （可选）消息卡片按钮回调
3. 事件订阅：`im.message.receive_v1`（接收 @ 机器人消息）
4. 发布上线（仅企业内可见即可）

### 6.2 消息模式

**长连接模式（推荐）**：飞书 SDK 自带 WebSocket 长连接支持，不需要公网 URL 即可接收消息。

```python
from lark_oapi.ws import WSClient
# SDK 内部维持 WebSocket 连接，接收事件回调
```

**推送模式**：调用飞书 API 发送消息（需要 tenant\_access\_token）。

### 6.3 日报消息卡片格式

```json
{
  "msg_type": "interactive",
  "card": {
    "header": {
      "title": {"tag": "plain_text", "content": "🎮 竞品情报日报 | 2026-06-16"},
      "template": "blue"
    },
    "elements": [
      {
        "tag": "markdown",
        "content": "**📊 今日概览**\n监测竞品 12 款，排名显著变动 4 项，重点关注 2 项\n"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "markdown",
        "content": "### 🔴 重点关注\n\n**鸣潮 — iOS游戏榜 ↑1（第3→第2）**\n2.2版本更新叠加KOL集中推广驱动。关注其后劲及对同类产品的分流。\n📎 [官方公告](https://...) | 💬 [追问此产品](action:ask_mingchao)"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "markdown",
        "content": "### 🆕 新上榜\n\n**异环 — iOS游戏榜 第5**\n首次进入榜单前5，Hotta Studio新产品。需持续关注。\n"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "markdown",
        "content": "### 🟡 一般关注\n\n**光·遇 — iOS游戏榜 ↓1（第3→第4）**\n小幅回落，无特殊事件，正常波动。\n"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "note",
        "elements": [
          {"tag": "plain_text", "content": "💡 @我 追问任何产品，我会做深度调研"}
        ]
      }
    ]
  }
}
```

### 6.4 交互模式

**场景一：追问历史**

```
用户: @机器人 原神上周表现怎么样
机器人:
  1. 意图识别 → query_history
  2. Chroma 向量检索 → 找到涉及原神的历史日报
  3. Claude 总结 → 自然语言回复
```

**场景二：实时调研**

```
用户: @机器人 《崩坏：星穹铁道》今天突然冲榜的原因是什么
机器人:
  1. 意图识别 → deep_research
  2. 调用 Researcher → 实时搜索
  3. 调用 Analyst + Design Analyst → 商业分析 + 玩法设计分析
  4. 回复（可分步：先回"正在调研..."，查完后补回结果）
```

**场景三：对比分析**

```
用户: @机器人 对比原神和鸣潮最近一周的表现
机器人:
  1. 意图识别 → compare
  2. 检索双方历史数据 + 分析报告
  3. Claude 生成对比分析
  4. 回复对比卡片
```

**意图路由 Prompt 设计**：

```
用户通过飞书向竞品分析助手提问。判断意图：

{
  "intent": "query_history" | "deep_research" | "compare" | "summary" | "casual_chat",
  "entities": {
    "products": ["产品名或 bundle_id 列表"],
    "time_range": "today" | "this_week" | "last_week" | "this_month",
    "focus": "rank_trend" | "event_cause" | "compare"
  },
  "needs_live_search": true | false,
  "search_queries": ["自动生成的搜索词"]  // 仅在 needs_live_search 时
}

用户问题：{飞书消息文本}
```

***

## 七、数据与存储设计

### 7.1 真实数据格式

从点点数据导出的原始 CSV，每天一份，只包含当天的排名快照。以下是 6.15 和 6.16 两天在 `DESIGN.md` 第五节中使用的真实数据：

```
6.15:
排名 | Bundle ID                | 应用           | iOS/Android | 类别   | 开发者
1    | com.netease.x19          | 我的世界：移动版  | IOS游戏榜    | 热门榜  | 0
2    | com.PigeonGames.Phigros  | Phigros        | IOS游戏榜    | 热门榜  | 鸽游
...

6.16:
排名 | Bundle ID                | 应用           | iOS/Android | 类别   | 开发者
1    | com.netease.x19          | 我的世界：移动版  | IOS游戏榜    | 热门榜  | 0
2    | com.kurogame.mingchao    | 鸣潮            | IOS游戏榜    | 热门榜  | 库洛游戏
...
```

Loader 负责将上述格式标准化后入库，字段映射见 7.2。

**关键限制**：

- 没有 `rank_change` 列——变动需要 Differ 对比计算
- 没有收入/下载量——只有排名一个维度
- CSV 本身不含日期——日期从文件名提取（`YYYY-MM-DD_rankings.csv`）
- **数据质量问题**：部分行的 `Bundle ID` 为 `"0"`（翡翠经营模拟器）、`开发者` 为 `"0"`（我的世界）——Loader 对 `bundle_id == "0"` 的行改用 `应用名` 作为唯一标识的 fallback

### 7.2 Loader 与 Differ 设计

这两个模块是整个系统的数据基座。Agent 能不能得到正确的输入，全看这两个模块。

#### Loader

```python
# loader.py 核心逻辑（伪代码）

def import_csv(file_path: str, date: str) -> int:
    """
    读取原始 CSV，映射为内部标准格式，批量入库。
    
    原始列 → 内部字段：
      排名       → rank (int)
      Bundle ID → bundle_id_raw (str)
      Bundle ID="0" 时 → bundle_id = "fallback:{应用名}"  # 数据质量 fallback
      应用       → game_name (str)
      iOS/Android → platform_raw (str) → platform ("iOS" | "Android")
      类别       → category (str, 如 "游戏榜")
      开发者     → developer (str), "0" 统一转为 NULL
      
    额外补充：
      date      → 从文件名或 API 参数注入
    """
    
    records = []
    for row in csv_rows:
        bundle_id = row["Bundle ID"].strip()
        game_name = row["应用"].strip()
        
        # 数据质量处理
        if bundle_id == "0" or not bundle_id:
            bundle_id = f"fallback:{game_name}"  # 用应用名兜底
        developer = row["开发者"].strip()
        if developer == "0":
            developer = None
        
        records.append({
            "date": date,
            "rank": int(row["排名"]),
            "bundle_id": bundle_id,
            "game_name": game_name,
            "platform": normalize_platform(row["iOS/Android"]),
            "category": row["类别"].strip(),
            "developer": developer,
        })
    
    db.bulk_insert("rankings", records)
    return len(records)
```

#### Differ

```python
# differ.py 核心逻辑（伪代码）

def diff_with_yesterday(date: str) -> list[dict]:
    """
    对比今天和昨天的排名，生成变动清单。
    
    返回的每条变动包含 Agent 所需的全部上下文。
    """
    today = db.query("SELECT * FROM rankings WHERE date = ?", date)
    yesterday = db.query("SELECT * FROM rankings WHERE date = ?", prev_date(date))
    
    today_map = {r.bundle_id: r for r in today}
    yesterday_map = {r.bundle_id: r for r in yesterday}
    
    changes = []
    
    # 1. 遍历今天的榜单——找排名变动和新上榜
    for bundle_id, t in today_map.items():
        y = yesterday_map.get(bundle_id)
        if y is None:
            changes.append({
                "game": t.game_name,
                "bundle_id": bundle_id,
                "platform": t.platform,
                "category": t.category,
                "developer": t.developer,
                "today_rank": t.rank,
                "yesterday_rank": None,
                "rank_change": None,
                "change_type": "new_entry",
                "date": date,
            })
        elif t.rank != y.rank:
            delta = y.rank - t.rank  # 正数=上升, 负数=下降
            changes.append({
                "game": t.game_name,
                "bundle_id": bundle_id,
                "platform": t.platform,
                "category": t.category,
                "developer": t.developer,
                "today_rank": t.rank,
                "yesterday_rank": y.rank,
                "rank_change": delta,
                "change_type": "up" if delta > 0 else "down",
                "date": date,
            })
    
    # 2. 找掉榜的（昨天有、今天没有）
    for bundle_id, y in yesterday_map.items():
        if bundle_id not in today_map:
            changes.append({
                "game": y.game_name,
                "bundle_id": bundle_id,
                "platform": y.platform,
                "category": y.category,
                "developer": y.developer,
                "today_rank": None,
                "yesterday_rank": y.rank,
                "rank_change": None,
                "change_type": "dropped_out",
                "date": date,
            })
    
    # 3. 标记显著程度
    for c in changes:
        c["is_significant"] = _judge_significance(c)
    
    return changes

def _judge_significance(change: dict) -> bool:
    """
    判断是否需要触发 AI 分析。
    
    默认规则：
    - 新上榜 / 掉榜 → 总是触发
    - 排名变动 ≥ 3 位 → 触发
    - 排名变动 < 3 位 → 跳过（小波动不值得调研）
    
    但具体阈值不是写死的——Check-3 的自优化模块会根据
    历史数据不断调整这个阈值。
    """
    if change["change_type"] in ("new_entry", "dropped_out"):
        return True
    if change["rank_change"] is None:
        return False
    return abs(change["rank_change"]) >= 3
```

**Differ 输出示例**：

```json
[
  {
    "game": "鸣潮",
    "bundle_id": "com.kurogame.mingchao",
    "platform": "iOS",
    "category": "游戏榜",
    "developer": "库洛游戏",
    "today_rank": 2,
    "yesterday_rank": 3,
    "rank_change": 1,
    "change_type": "up",
    "is_significant": false,
    "date": "2026-06-16"
  },
  {
    "game": "异环",
    "bundle_id": "com.hottagames.yh.laohu",
    "platform": "iOS",
    "category": "游戏榜",
    "developer": "Hotta Studio",
    "today_rank": 5,
    "yesterday_rank": null,
    "rank_change": null,
    "change_type": "new_entry",
    "is_significant": true,
    "date": "2026-06-16"
  }
]
```

### 7.3 竞品列表配置（YAML）

```yaml
# data/competitor_list.yaml

# === 公司业务方向定义 ===
business_focus:
  genres:
    - "塔防"
    - "移动塔防"
    - "传统TD塔防"
    - "移动城堡like"
  themes:
    - "微恐"        # 轻度恐怖
    - "冰河"        # 冰雪/冰川题材
    - "火山爆发"     # 火山/熔岩题材
  validated_mechanisms:
    - "已被市场验证的塔防玩法"
    - "已验证的商业化模型"

# === 重点关注竞品（在榜单上监控的已上线游戏） ===
monitored_games:
  # ---- 塔防品类 ----
  - bundle_id: "com.xxx.td1"
    name: "示例TD游戏1"
    genre: "移动塔防"
    theme: "微恐"
    priority: "high"
    tags: ["塔防", "微恐", "直接竞品"]

  # ---- 相关题材（即使不是TD也要关注同类题材表现） ----
  - bundle_id: "com.xxx.ice"
    name: "示例冰河题材游戏"
    genre: "模拟经营"   # 非TD，但同题材验证市场需求
    theme: "冰河"
    priority: "medium"
    tags: ["冰河题材", "题材验证"]

  # ... 更多竞品（从榜单中筛选与业务方向相关的游戏）
  # 非塔防、非目标题材的游戏设为 low priority，仅做一般性数据跟踪

# === 在研公司追踪（不在榜单上，但需要主动搜索） ===
in_development_tracking:
  - company: "XX游戏工作室"
    product_name: "暗夜防线"
    genre: "移动塔防"
    theme: "微恐"
    status: "在研"           # 在研 | 测试中 | 即将上线
    known_info: "2025年拿到版号，预计2026下半年上线"
    coverage: "玩法"          # 仅玩法相似 | 玩法+题材都相似 | 玩法+题材+商业模式
    threat_level: "high"     # high | medium | low

  - company: "YY互动"
    product_name: "冰封纪元"
    genre: "传统TD塔防"
    theme: "冰河"
    status: "测试中"
    known_info: "2026年3月开始小规模测试，TapTap预约5万"
    coverage: "玩法+题材"
    threat_level: "high"

  # ... 更多在研公司

# 显著性判定阈值
significance_thresholds:
  rank_change_min: 3
  always_trigger:
    - "new_entry"
    - "dropped_out"
    - "rank <= 5"
  # TD 品类特殊规则：同品类有变动时降低触发门槛
  genre_boost:
    - genre: "塔防"
      boost: 2.0            # TD 品类的 attention_score 自动 +2
    - theme: ["微恐", "冰河", "火山爆发"]
      boost: 1.5            # 目标题材自动 +1.5
```

### 7.4 数据库表设计（SQLite）

```sql
-- 每日榜单原始记录（Loader 导入后落库）
-- 一行 = 一个游戏在某一天在某个榜单上的排名
CREATE TABLE rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,              -- '2026-06-16'
    platform TEXT NOT NULL,          -- 'iOS' | 'Android'
    chart_type TEXT NOT NULL,        -- '免费榜' | '畅销榜' | '热门榜' | '下载榜' | '收入榜'
    category TEXT NOT NULL DEFAULT '', -- app category from source (e.g. '游戏', '应用')
    rank INTEGER NOT NULL,           -- 排名位次
    bundle_id TEXT NOT NULL,         -- 'com.kurogame.mingchao'
    game_name TEXT NOT NULL,         -- '鸣潮'
    developer TEXT,                  -- '库洛游戏'
    source_file TEXT,                -- 来源 CSV 文件名
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, platform, chart_type, bundle_id) -- 同一天同平台同榜单同应用只有一条
);

-- 变动记录（Differ 每日 diff 产生）
-- 一行 = 一个游戏的排名在今天发生了某种变动
CREATE TABLE changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    chart_type TEXT NOT NULL,          -- '免费榜' | '畅销榜' | '热门榜' | ...
    bundle_id TEXT NOT NULL,
    game_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    developer TEXT,
    today_rank INTEGER,              -- 今天排名，NULL 表示掉榜
    yesterday_rank INTEGER,          -- 昨天排名，NULL 表示新上榜
    rank_change INTEGER,             -- 排名变化（正数=上升，负数=下降），NULL 表示上榜/掉榜
    change_type TEXT NOT NULL,       -- 'up' | 'down' | 'new_entry' | 'dropped_out'
    attention_score REAL DEFAULT 0.0,
    is_significant BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, platform, chart_type, bundle_id)  -- 同一天同平台同榜单同应用只有一条
);

-- 跨榜对照信号（cross_chart 模块输出）
-- 一行 = 一个游戏在同一天跨多个榜单的画像 + 检测到的信号
CREATE TABLE IF NOT EXISTS cross_chart_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    game_name TEXT NOT NULL,
    charts_json TEXT NOT NULL,         -- 各榜排名: {"免费榜": 5, "畅销榜": 48, "热门榜": 12}
    signal_pattern TEXT,               -- 'leading' | 'traffic_leak' | 'harvest' | 'word_of_mouth' | 'divergence'
    signal_description TEXT,           -- 人类可读的信号描述
    threat_level TEXT,                 -- 'high' | 'medium' | 'low'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, bundle_id)
);

-- 每日全局扫描结果（Overview Scanner 输出）
-- 每天一份，提供行业背景和波动率上下文
CREATE TABLE daily_overviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    day_type TEXT NOT NULL,           -- 'quiet' | 'normal' | 'volatile'
    volatility REAL,                  -- 波动率 (0~1)
    industry_news_json TEXT,          -- 行业新闻摘要 JSON
    recommended_focus_json TEXT,      -- 推荐调研的变动列表 JSON
    skip_json TEXT,                   -- 建议跳过的变动列表 JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 调研结果
CREATE TABLE research_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id INTEGER REFERENCES changes(id),
    findings_json TEXT,              -- Researcher 输出的完整 JSON
    verified_json TEXT,              -- Verifier 核验后的 JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 分析报告（每天一份，包含所有变动条目的分析结论）
CREATE TABLE analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    research_ids TEXT,               -- 关联的 research_results id 列表 (JSON array)
    report_json TEXT,                -- Analyst 输出的完整 JSON
    brief_card_json TEXT,            -- Briefer 生成的飞书卡片 JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 对话记录（交互层）
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_message TEXT,
    intent TEXT,
    agent_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 渠道有效性记录（Check-2）
CREATE TABLE channel_effectiveness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT,
    search_query TEXT,
    hit_count INTEGER,
    result_quality_score REAL,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 在研公司追踪（Design Analyst 的竞争风险评估输入）
CREATE TABLE in_development_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,            -- 公司名称
    product_name TEXT,                -- 产品名称
    genre TEXT,                       -- 玩法类型
    theme TEXT,                       -- 题材
    status TEXT,                      -- '在研' | '测试中' | '即将上线' | '已上线'
    progress_detail TEXT,             -- 已知进度信息
    coverage TEXT,                    -- '仅玩法' | '仅题材' | '玩法+题材' | '玩法+题材+商业模式'
    threat_level TEXT,                -- 'high' | 'medium' | 'low'
    evidence_json TEXT,               -- 证据（招聘信息/版号/测试截图等）的 JSON
    first_discovered_at TEXT,         -- 首次发现日期
    last_updated_at TEXT,             -- 最后更新日期
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Prompt 版本记录（Check-3）
CREATE TABLE prompt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT,
    version TEXT,
    prompt_content TEXT,
    performance_score REAL,
    is_active BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 7.5 多榜单数据源管理

系统支持多种榜单数据源，每种榜单衡量不同维度。所有抓取器遵循统一输出契约，Loader 只需实现一次兼容。

#### 7.5.1 榜单类型枚举

| chart_type | 核心衡量 | 高位意味着 | 典型波动原因 |
|------------|----------|-----------|-------------|
| `免费榜` | 下载量（近似） | 获客能力强、投放力度大 | 版本更新、买量投放、假期效应 |
| `畅销榜` | 收入（IAP） | 商业化成熟、大R活跃 | 限时活动、新卡池、周年庆 |
| `热门榜` | 社区活跃度 | 口碑好、二创多、玩家自来水 | 攻略爆火、UP主推广、评分飙升 |
| `下载榜` | 纯安装量 | 增长轨迹、新增用户规模 | ASO 优化、渠道推荐、社交裂变 |
| `收入榜` | 总收入 | 综合商业化能力 | 同畅销榜，口径更宽 |

#### 7.5.2 统一 CSV 列规范

所有抓取器必须输出以下列。Loader 自动映射，缺失列用文件名补。

| 列名 | 含义 | 必填 | 示例 |
|------|------|:--:|------|
| `排名` | 榜单排名 | ✅ | `1` |
| `Bundle ID` | 应用商店唯一ID | ⚠️ | `com.netease.x19`；拿不到留空，Loader 补 `fallback:应用名` |
| `应用` | 游戏名称 | ✅ | `鸣潮` |
| `平台` | 操作系统 | ✅ | `iOS` / `Android` |
| `榜单` | 榜单类型 | ✅ | `免费榜` / `畅销榜` / `热门榜` / `下载榜` |
| `品类` | 游戏品类 | 推荐 | `塔防` / `RPG`（Story Picker 聚类用） |
| `开发者` | 开发商/发行商 | 推荐 | `库洛游戏` |

**文件名约定**：`{平台}_{榜单}_{YYYYMMDD}.csv`

```
ios_免费榜_20260616.csv
ios_畅销榜_20260616.csv
tapTap_热门榜_20260616.csv
```

文件名可自动补全缺失的 `平台`、`榜单`、`日期` 字段。

#### 7.5.3 抓取器目录与基类

```
tools/
├── scrapers/
│   ├── __init__.py
│   ├── base.py                   # 基类：统一列映射 + CSV 输出
│   ├── diandian_ios_free.py      # iOS 免费榜
│   ├── diandian_ios_grossing.py  # iOS 畅销榜（后续）
│   ├── diandian_ios_download.py  # iOS 下载榜（后续）
│   └── taptap_android_hot.py     # TapTap 热门榜（后续）
```

基类 `base.py` 职责：
1. 接收抓取器返回的原始行数据 `list[dict]`
2. 按 7.5.2 列规范映射到标准列名
3. `Bundle ID` 为空时自动补 `fallback:{应用}`
4. 按 `{平台}_{榜单}_{YYYYMMDD}.csv` 写出到 `data/raw/`
5. 返回文件路径供 Loader 导入

---

### 7.6 跨榜对照分析

同一个游戏在不同榜单上的位置差异，本身就是最有价值的信号——不需要 AI，纯规则就能检测。

#### 7.6.1 核心认知

```
单榜单分析 = "今天 vs 昨天，排名变了多少"（纵向）
跨榜对照   = "同一个游戏，在不同榜单位置的差异"（横向）

单榜单告诉你发生了什么。
跨榜告诉你这意味着什么。
```

#### 7.6.2 五种跨榜信号模式

##### 模式 1：全面领跑型 `leading`

```
免费榜 ↑  畅销榜 ↑  热门榜 ↑  →  三榜同步上升
```

**解读**：产品全面爆发——获客、变现、口碑都在涨。
**威胁等级**：🔴 **高**。如果是同品类竞品，立即深度分析。
**调研方向**：做对了什么？更新了什么？投放策略是什么？
**对决策者**：这就是你目标赛道要超越的标杆。

##### 模式 2：流量型 `traffic_leak`

```
免费榜 ↑  畅销榜 →  →  拉新强但不变现
```

**解读**：获客有效但商业化跟不上。要么是新手游冲量，要么是免费游戏没付费设计。
**威胁等级**：🟡 中。一旦补上变现就是完整态竞品。
**对决策者**：说明"用户想要但不愿付费"，你做的时候要提前设计付费点。

##### 模式 3：收割型 `harvest`

```
免费榜 →  畅销榜 ↑  →  小圈子高付费
```

**解读**：用户规模不大但付费意愿极强。典型 SLG/二次元核心向。
**威胁等级**：🟡 中。不抢你的用户，但抢你的大R。
**对决策者**：这才是真正的付费竞争对手。学它的商业化设计。

##### 模式 4：口碑型 `word_of_mouth`

```
免费榜 →  热门榜 ↑  →  社区驱动，玩家自来水
```

**解读**：下载没暴涨但社区在发酵。领先指标——今天社区热，明天可能转下载。
**威胁等级**：🟡→🔴。是**预警信号**。
**对决策者**：最需要设计分析师介入的模式。玩法好在哪？能学吗？

##### 模式 5：信号背离 `divergence`

```
免费榜 ↑  畅销榜 ↓  →  买量催出来的虚假繁荣
畅销榜 ↑  免费榜 ↓  →  老游戏靠活动续命，新用户进不来
热门榜 ↑  iOS榜 ↓  →  Android 强 iOS 弱，平台策略有问题
```

**解读**：背离本身就是最值得讲的"故事"。
**威胁等级**：视背离方向而定，但永远值得标记。

#### 7.6.3 跨榜对照示例

```
2026-06-16 三款游戏的多榜数据：

          免费榜  畅销榜  TapTap  信号
游戏 A      #3     #2     #5     leading      "全面领跑，三榜通吃"
游戏 B      #5     #48    #12    traffic_leak "获客强但变不了现"
游戏 C      #82    #8     #90    harvest      "小众高付费，破不了圈"

单看免费榜 → B 比 C 更值得关注
加上畅销榜 → C 才是商业化最强的那个
加上 TapTap  → B 的社区热度远超 C
```

#### 7.6.4 cross_chart 模块设计

```
src/pipeline/cross_chart.py    ← 新增模块
```

**输入**：同一天多个 `(platform, chart_type)` 组合的 rankings 数据
**输出**：每个游戏的跨榜画像 + 信号列表，写入 `cross_chart_signals` 表

核心逻辑：

```
1. 加载当天所有榜单数据
2. 按 bundle_id 合并：同一个游戏在不同榜单的位置聚成一行
3. 对每个出现在 ≥2 个榜单中的游戏：
   a. 计算各榜排名差异
   b. 匹配信号模式（全面领跑 / 流量型 / 收割型 / 口碑型 / 背离）
   c. 生成信号描述 + 威胁等级
4. 排序：背离 > 全面 > 其他 → 写入 DB
```

#### 7.6.5 与 Story Picker 集成

Story Picker 新增第 6 类故事——**📐 跨榜信号**：

```json
{
  "story_type": "cross_chart_signal",
  "story_headline": "「游戏 B」免费榜 #5 畅销榜 #48 — 获客强但变现能力严重滞后",
  "game_name": "游戏 B",
  "pattern": "traffic_leak",
  "charts": { "免费榜": 5, "畅销榜": 48, "热门榜": 12 },
  "story_angle": "高下载低付费的漏斗问题——是商业化设计缺陷还是产品阶段所致？",
  "threat_level": "medium"
}
```

跨榜信号与单榜信号（大幅跃升/黑马突围/断崖下跌等）并行进入 Story Picker 候选池，统一排序，最多 8 条候选。Overview Scanner Agent 结合行业上下文做最终 5-8 条的选择，跨榜信号因信息量更高而优先考虑。

#### 7.6.6 对 AI Agent 的增强

商业 Analyst 接收跨榜上下文作为额外输入：

```json
{
  "cross_chart_context": {
    "multi_chart_coverage": "今日 15 款游戏出现在 ≥2 个榜单中",
    "leading_games": ["游戏 A"],
    "divergence_cases": ["游戏 D（免费榜↑畅销榜↓）"],
    "new_cross_signals": ["游戏 B 首次出现流量型信号"]
  }
}
```

Design Analyst 利用跨榜信号做决策支持：
- 流量型信号 → "这个品类用户想玩但不愿付费，商业化怎么设计？"
- 收割型信号 → "小众高付费群体的需求特征是什么？你的产品能满足吗？"

---

### 7.7 向量存储（Chroma）

用于交互层的 RAG 检索（用户追问历史时使用）：

```
Collection: "analysis_reports"
  - 每份分析报告的摘要 + 关键结论做 embedding
  - metadata: {date, game_names, tags, impact_level}

Collection: "findings"
  - 每条经核验的调研 findings 做 embedding
  - metadata: {date, game_name, event_type, source_type}
```

***

## 八、自优化设计（Check-3）

### 8.1 Prompt 自优化流程

```
┌────────────────────────────────────────────┐
│  Trigger: 每周 / 累积 N 次用户追问          │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  Step 1: 收集失败案例                        │
│  - 用户追问的问题（原简报未覆盖）              │
│  - 核验被拒绝的 findings（调研方向不对）       │
│  - 分析被用户纠正的 case                     │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  Step 2: AI 诊断                             │
│  输入: 失败案例 + 当前 prompt                 │
│  输出: prompt 改进建议 + 新 prompt 草稿        │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  Step 3: A/B 验证                           │
│  - 用历史 case 分别跑新旧 prompt               │
│  - AI 评价输出质量                            │
│  - 质量提升 → 自动替换，记录版本              │
│  - 无提升 → 存档建议，人工决定                │
└────────────────────────────────────────────┘
```

### 8.1.1 用户行为反馈加权

Prompt 自优化解决的是"写得好不好"——改进 prompt 文本质量。本节解决的是"看什么"——根据用户行为调整系统应该关注什么。

**核心认知**：推荐系统精准是因为用户行为数据多（刷了 300 小时抖音，每次划走/看完/点赞都是标签）。竞品情报系统做不到这个量级的行为数据，但有一份更高质量的信号——**决策者主动追问什么**。追问不是"我点了个赞"，追问是"我需要更多信息来做决策"。

**三类行为信号**：

| 行为 | 信号强度 | 含义 | 系统反应 |
|------|:---:|------|------|
| 用户追问某产品/方向 | 🔴 最强 | "这对我决策有价值，日报没给够" | 该产品/方向调研优先级永久 +2 |
| 用户连续 N 天未追问某产品 | 🟡 中等 | "我不关心这个" | 该产品调研优先级逐步 -1，最低降至仅数据跟踪 |
| 用户纠正 Agent 的分析结论 | 🔴 最强 | "你搞错了原因/方向/影响" | 纠错内容进入 RAG 检索池，同类 case 分析时作为上下文注入 |

**实现方案**：

```
┌────────────────────────────────────────────┐
│  Trigger: 每次用户交互后异步更新               │
│  - 追问 → update_game_priority(+2)          │
│  - 纠正 → insert_correction(case + fix)     │
│  - 沉默 → decay_inactive_games(30天阈值)     │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  game_priority 表（新增）                     │
│                                              │
│  CREATE TABLE game_priority (                │
│    bundle_id TEXT PRIMARY KEY,               │
│    base_priority REAL DEFAULT 0.0,  -- 初始分 │
│    user_boost REAL DEFAULT 0.0,     -- 追问加成│
│    last_interaction_date TEXT,               │
│    interaction_count INTEGER DEFAULT 0,      │
│    total_questions INTEGER DEFAULT 0,        │
│    decay_rate REAL DEFAULT 0.0,              │
│    effective_priority AS (base_priority       │
│      + user_boost - decay_rate)  -- 计算列   │
│  );                                           │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  corrections 表（新增）                       │
│                                              │
│  CREATE TABLE corrections (                  │
│    id INTEGER PRIMARY KEY,                   │
│    context_game TEXT,      -- 关联产品        │
│    context_topic TEXT,     -- 关联方向/题材   │
│    wrong_conclusion TEXT,  -- AI 原结论       │
│    user_correction TEXT,   -- 用户纠正        │
│    created_at TIMESTAMP,                     │
│    used_count INTEGER DEFAULT 0              │
│  );                                           │
│                                              │
│  Agent 分析同类产品时，via RAG 检索相关纠错， │
│  作为 "已知的容易出错的点" 注入 context。     │
└──────────────────┬─────────────────────────┘
                   ▼
┌────────────────────────────────────────────┐
│  效果                                       │
│                                              │
│  Week 1: 所有变动平等调研，8 条/天            │
│  Week 4: 用户追问过的产品优先，3-5 条/天      │
│  Week 8: 系统知道你对什么感兴趣/不感兴趣，     │
│          安静日自动跳过低优先级变动，          │
│          只在真正有信号的产品上深度调研        │
│                                              │
│  每天跑的 AI token 从第一周的 ~5000/天         │
│  降到第八周的 ~2000/天，同时日报更精准。       │
└────────────────────────────────────────────┘
```

**与 §8.1 Prompt 自优化的区别**：

| | §8.1 Prompt 自优化 | §8.1.1 行为反馈加权 |
|---|---|---|
| 优化目标 | Prompt 文本质量 | 注意力分配（看什么、不看什么） |
| 输入 | 失败案例（AI 产出差） | 用户行为（追问/纠正/沉默） |
| 输出 | 新 prompt 草稿 | 调整后的优先级分数 |
| 生效方式 | 替换 prompt 文件 | 修改 DB 中的权重 |
| 类比 | 教练改进了运动员的动作 | 教练告诉你这个对手不值得打、那个值得全力打 |

**实施优先级**：🟡 P1 — 依赖飞书交互层跑通（Week 8），但数据模型可以在 Week 5-6 提前建表。

### 8.2 规则参数自优化

```python
# optimizer 检查逻辑（伪代码）
for metric, threshold in significance_thresholds.items():
    # 回顾最近 30 天的变动标记
    marked = db.query("SELECT * FROM changes WHERE metric=? AND is_significant=1", metric)
    feedback = db.query("用户反馈/追问中涉及但未被标记的变动")
    
    # AI 评估：现有阈值是否合适？
    analysis = llm.analyze(f"""
    当前阈值: {metric} 变动超过 {threshold} 视为显著
    最近显著变动: {marked}
    漏报案例: {feedback}
    
    建议新的阈值及理由。
    """)
    
    if analysis.suggested_threshold and analysis.confidence > 0.7:
        # 生成 PR（参数变更请求）
        save_suggestion(analysis)
```

***

## 九、API 设计

### 9.1 REST API

| 方法     | 路径                                    | 说明                      |
| ------ | ------------------------------------- | ----------------------- |
| `POST` | `/api/data/import`                    | 上传 CSV 文件，触发数据导入 + diff |
| `POST` | `/api/report/generate`                | 手动触发一次完整日报流程            |
| `GET`  | `/api/report/latest`                  | 获取最新日报                  |
| `GET`  | `/api/report/history?date=2026-06-16` | 获取指定日期日报                |
| `GET`  | `/api/game/{name}/history?days=30`    | 获取某产品历史数据               |
| `POST` | `/api/feishu/callback`                | 飞书事件回调（Webhook 模式）      |
| `GET`  | `/api/health`                         | 健康检查                    |

### 9.2 内部调用流

#### 数据导入流程

```
POST /api/data/import
  │
  ├── Loader.import_csv(file, date)
  │     ├── 解析原始 CSV 列 → 内部标准字段
  │     ├── 平台字段标准化（"IOS游戏榜" → "iOS"）
  │     └── Bulk INSERT INTO rankings
  │
  ├── Differ.diff_with_yesterday(date)
  │     ├── SELECT today's rankings (100 条)
  │     ├── SELECT yesterday's rankings
  │     ├── 逐 bundle_id 对比排名
  │     ├── 标记 change_type + attention_score
  │     └── INSERT INTO changes
  │
  ├── StoryPicker.pick_stories(changes, history)      ← 新增，纯脚本
  │     ├── 从 20~40 条变动中匹配 6 类故事（含跨榜信号）
  │     ├── 去重、排序、截断（最多 8 条候选）
  │     └── 返回 stories 列表（供后续 AI 层消费，最终 5-8 条由 Overview Scanner 决定）
  │
  └── 返回：
        { "imported": 100, "changes_found": 28, "stories_selected": 4 }
        
        如果昨天没有数据（Day 1）：
        { "imported": 50, "changes_found": 0, "significant": 0,
          "hint": "首日数据已入库，明日导入后可进行对比分析" }
```

#### 日报生成流程

```
POST /api/report/generate?date=2026-06-16
  │
  ├── 检查 rankings 表：该日期是否有数据
  │     └── 没有 → 返回 "请先导入 {date} 的数据"
  │
  ├── 检查 changes 表：该日期是否已有 diff 结果
  │     ├── 没有 → 先跑 Diff.diff_with_yesterday(date)
  │     └── 有 → 直接用
  │
  ├── Overview Scanner（每天必跑，接收 Story Picker 的最多 8 条候选故事）
  │     ├── 搜索今日游戏行业新闻
  │     ├── 对比历史波动率
  │     ├── 对每条候选故事补充行业背景
  │     ├── 结合行业上下文，从候选中决定最终的 5-8 条进入深度调研
  │     ├── 输出 recommended_focus（5-8 条）+ skip_deep_research_for
  │     └── 存储 → daily_overviews
  │
  ├── Researcher（按需，仅 recommended_focus + attention_score ≥ 5.0）
  │     ├── for each focus_item:
  │     │     ├── web_search + web_fetch
  │     │     └── 存储 → research_results
  │     └── 无 focus → 跳过
  │
  ├── Verifier（按需，仅对 Researcher 的输出做核验）
  │
  ├── Analyst（商业）
  │     ├── 输入：变动清单 + 7天历史 + 行业背景 + 核验后的调研
  │     ├── 输出整体格局分析
  │     ├── 对每个 focus_item 输出条目分析
  │     └── 存储 → analysis_reports
  │
  ├── Design Analyst（玩法，仅对 Researcher 产出了 design_findings 的条目）
  │     ├── 输入：Researcher 中带 design_tags 的 findings + 排名上下文
  │     ├── 输出：核心玩法亮点 + 玩家动机 + 付费设计 + 可借鉴点
  │     └── 存储 → analysis_reports（design_analysis 字段）
  │
  ├── Briefer.generate(analysis)          → card_json
  │     ├── day_type=quiet  → 轻量简报
  │     ├── day_type=normal → 标准简报
  │     └── day_type=volatile → 详细简报
  │
  ├── Pusher.send(card_json)              → 飞书推送
  └── Storage.embed_report(report)        → Chroma 向量化
```

**关键变化**：不再有"无显著变动就不生成报告"的逻辑。每天必然产出简报，篇幅随信息量自动调整。

***

## 十、10 周实现计划

### 总览

```
Week 1-2   ████████░░░░░░░░░░  数据管道（纯代码，无 AI）
Week 3-5   ░░░░░░░░████████░░  AI Agent 逐个实现
Week 6-7   ░░░░░░░░░░░░░░███░  飞书集成 + 简报
Week 8     ░░░░░░░░░░░░░░░░█░  交互层
Week 9     ░░░░░░░░░░░░░░░░░█  自优化 + 跑通全链路
Week 10    ░░░░░░░░░░░░░░░░░█  打磨 + 论文
```

### 详细周计划

#### Week 1（6/16 - 6/22）项目骨架 + 数据管道
**主题：让数据先跑起来**

| 优先级 | 任务 | 产出 | 状态 |
|--------|------|------|------|
| 🔴 必做 | 创建项目目录结构、`requirements.txt`、`.env.example` | 项目能 `pip install` | ✅ 已完成 |
| 🔴 必做 | `src/config.py` — 环境变量读取、路径配置 | 配置集中管理 | ✅ 已完成 |
| 🔴 必做 | `src/storage/sqlite.py` — 建表（rankings/changes）+ 基础 CRUD | 数据库就绪 | ✅ 已完成 |
| 🔴 必做 | `src/pipeline/loader.py` — 解析真实 CSV，处理 bundle_id="0" | 能导入你的两天数据 | |
| 🔴 必做 | `src/pipeline/differ.py` — 对比昨日排名，attention_score 计算 | 能算出变动清单 | |
| 🟡 建议 | `src/pipeline/story_picker.py` — 五类故事检测规则 | 能从变动中筛故事 | |
| 🆕 新增 | `tools/diandian_auth.py` + `tools/diandian_scroll.py` — iOS 游戏免费榜自动抓取 | 数据源自动化 | ✅ 已完成 |
| 🆕 新增 | `tools/scrapers/base.py` — 统一 CSV 列规范 + 基类 | 多榜单兼容的基础 | |
| 🆕 新增 | `src/storage/sqlite.py` — schema 加 `chart_type`、`cross_chart_signals` 表 | 多榜单数据基座 | |
| ⚪ 可选 | `src/pipeline/cross_chart.py` — 跨榜对照分析（依赖 ≥2 种榜单数据） | 5 种信号模式自动检测 | |

**本周验证**：导入 6.15 和 6.16 的真实数据 → Differ 正确算出 4 条变动 → Story Picker 给鸣潮打了最高分 → 跨榜信号机制就绪（待多榜单数据导入后触发）

**依赖**：准备好至少 2-3 天的真实 CSV 数据

---

#### Week 2（6/23 - 6/29）Agent 基类 + Overview Scanner
**主题：第一次让 AI 参与进来**

| 优先级 | 任务 | 产出 |
|--------|------|------|
| 🔴 必做 | `src/agents/base.py` — Claude API 调用封装、tool use 框架、结构化输出 | Agent 基础设施 |
| 🔴 必做 | `src/tools/web_search.py` — 搜索工具（Bing/SerpAPI/自建） | 第一个 tool |
| 🔴 必做 | `src/tools/web_fetch.py` — 网页抓取工具 | 第二个 tool |
| 🔴 必做 | `src/agents/overview_scanner.py` + `prompts/overview_scanner.yaml` | 第一个 Agent 跑通 |
| 🟡 建议 | `src/tools/db_query.py` — 历史数据查询 tool | Agent 能查数据库 |
| 🟡 建议 | Story Picker 没做完的收尾 | 数据管道完全就绪 |

**本周验证**：手动触发 Overview Scanner → 它搜到了当天游戏行业新闻 → 返回了 JSON 格式的结果

**风险**：Claude API 账号/支付就绪了吗？如果不行，本周内要搞定或切换到国内模型

---

#### Week 3（6/30 - 7/6）Researcher + Verifier
**主题：调研和核验链路跑通**

| 优先级 | 任务 | 产出 |
|--------|------|------|
| 🔴 必做 | `src/agents/researcher.py` + `prompts/researcher.yaml` | 五维搜索（含在研层） |
| 🔴 必做 | `src/tools/image_fetch.py` — 截图抓取 | 简报有图的基础 |
| 🔴 必做 | `src/agents/verifier.py` + `prompts/verifier.yaml` | 信息核验链路 |
| 🟡 建议 | Researcher → Verifier 的串联逻辑（在 runner 里） | Do-Check 半链路跑通 |

**本周验证**：给 Researcher 一个变动（如"XX塔防游戏新上榜"）→ 它搜到了事件+玩法细节+截图 → Verifier 打了可靠性分

**里程碑**：Do-Check 的核心逻辑跑通了 🎯

---

#### Week 4（7/7 - 7/13）Analyst（商业）
**主题：分析能力上线**

| 优先级 | 任务 | 产出 |
|--------|------|------|
| 🔴 必做 | `src/agents/analyst.py` + `prompts/analyst.yaml` | 因果推理 + 趋势判断 |
| 🔴 必做 | 7 天历史趋势数据查询逻辑 | Analyst 能看到多日趋势 |
| 🟡 建议 | Overview Scanner → Researcher → Verifier → Analyst 全链路串联 | Do-Check 全链路跑通 |

**本周验证**：喂入两天数据 → Analyst 输出了含因果判断、趋势预判、7 天趋势的分析报告

**里程碑**：日报的"商业分析"部分可以独立产出了 🎯

---

#### Week 5（7/14 - 7/20）Design Analyst
**主题：决策者视角的分析**

| 优先级 | 任务 | 产出 |
|--------|------|------|
| 🔴 必做 | `src/agents/design_analyst.py` + `prompts/design_analyst.yaml` | 10 维分析输出 |
| 🔴 必做 | 在研公司搜索逻辑（Researcher 的在研层结果 → Design Analyst 的结构化输出） | 竞争风险表 |
| 🔴 必做 | `src/storage/sqlite.py` — `in_development_tracking` 表 CRUD | 在研信息持久化 |
| 🟡 建议 | 两个 Analyst 并行调用（商业 + 设计同时跑） | 效率优化 |

**本周验证**：Design Analyst 输出了含"值不值得做"判断、在研公司表、题材热度趋势的完整分析

**里程碑**：所有 AI Agent 开发完成 🎯

---

#### Week 6（7/21 - 7/27）Briefer + 飞书卡片
**主题：让分析变成可读的日报**

| 优先级 | 任务 | 产出 |
|--------|------|------|
| 🔴 必做 | `src/agents/briefer.py` + `prompts/briefer.yaml` | 商业分析 + 设计分析 → 飞书卡片 JSON |
| 🔴 必做 | `src/feishu/card_builder.py` — 飞书卡片拼装（含 img、表格、链接） | 图文并茂的卡片 |
| 🔴 必做 | `src/feishu/pusher.py` — 调用飞书 API 发送消息 | 能推到飞书群 |
| 🟡 建议 | 三级篇幅逻辑（quiet/normal/volatile） | 简报长度自适应 |

**前置条件**：飞书应用已创建、权限已开通

**本周验证**：手动触发一次完整日报流程 → 飞书群收到一张带截图+竞争风险表的日报卡片

**里程碑**：端到端跑通了！从 CSV 到飞书卡片 🎯🎯

---

#### Week 7（7/28 - 8/3）定时任务 + 联调
**主题：让它每天自己跑**

| 优先级 | 任务 | 产出 |
|--------|------|------|
| 🔴 必做 | `src/pipeline/runner.py` — 编排完整流程（Loader→Differ→Story Picker→6 Agent→Push） | 一键触发 |
| 🔴 必做 | 定时调度（APScheduler / Windows 任务计划 / cron） | 每天 9:00 自动跑 |
| 🔴 必做 | 用 3-5 天真实数据跑通，检查输出质量 | 验证系统稳定 |
| 🟡 建议 | 简单的 Web 管理页（`frontend/index.html`）：上传 CSV、手动触发、查看历史 | 操作更方便 |

**本周验证**：放 5 天的 CSV 在 `data/raw/`，系统每天 9:00 自动跑 → 飞书群收到 5 份日报

**里程碑**：系统进入"无人值守"状态 🎯

---

#### Week 8（8/4 - 8/10）交互层
**主题：从推送到对话**

| 优先级 | 任务 | 产出 |
|--------|------|------|
| 🔴 必做 | `src/feishu/bot.py` — WebSocket 长连接，接收 @消息 | 机器人能收到消息 |
| 🔴 必做 | 意图路由（query_history / deep_research / compare / summary） | 识别用户想干什么 |
| 🔴 必做 | RAG 检索链路（`src/storage/vector_store.py`） | 追问历史能检索到 |
| 🟡 建议 | 交互式调研（用户问 → 实时触发 Researcher → 回复） | 追问→回答闭环 |

**本周验证**：在飞书群 @机器人 "原神最近一周表现怎么样" → 机器人检索历史日报 → 回复自然语言总结

**里程碑**：双向交互能力上线 🎯

---

#### Week 9（8/11 - 8/17）自优化 + 全链路打磨
**主题：让系统越用越好**

| 优先级 | 任务 | 产出 |
|--------|------|------|
| 🔴 必做 | `src/optimize/prompt_optimizer.py` — 收集失败案例 → AI 诊断 → A/B 验证 → 自动替换 prompt | Prompt 自优化闭环 |
| 🔴 必做 | `src/optimize/rule_optimizer.py` — attention_score 阈值自动调整 | 规则参数自优化 |
| 🟡 建议 | 渠道拓展建议（Check-2）：分析"调研无果"的 case → 推荐新渠道 | 信息源自动扩展 |
| 🟡 建议 | 写 10-20 个测试用例 | 回归测试基础 |

**本周验证**：模拟几次"用户追问后补全"的场景 → 自优化模块生成改进后的 prompt → A/B 对比验证 → 自动替换

**里程碑**：系统具备自我进化能力 🎯

---

#### Week 10（8/18 - 8/24）打磨 + 论文
**主题：把一切收拾干净**

| 优先级 | 任务 | 产出 |
|--------|------|------|
| 🔴 必做 | 完整的 `README.md` + 部署文档 | 别人能照着跑起来 |
| 🔴 必做 | `.env.example` + 飞书应用配置说明 | 一键部署 |
| 🔴 必做 | Dockerfile + docker-compose.yml | Docker 部署支持 |
| 🔴 必做 | 论文写作（如果这是毕设） | 论文初稿 |
| 🟡 建议 | 录制 3 分钟 Demo 视频 | 答辩/面试展示 |
| ⚪ 可选 | GitHub 开源（去掉敏感配置） | 简历链接 |

**最终验证**：另一台电脑，git clone → 配置 .env → docker-compose up → 导入数据 → 收到飞书日报 ✅

**里程碑**：**项目交付 🎯🎯🎯**

---

### 里程碑总览

| 周 | 里程碑 | 验证方式 |
|----|--------|---------|
| W1 | 数据管道就绪 | CSV 导入 → 数据库有数据 → Diff 正确 |
| W3 | Do-Check 链路跑通 | Researcher + Verifier 产出经核验的调研结果 |
| W5 | 全部 Agent 开发完成 | 6 个 Agent 都能独立调用并返回正确 JSON |
| W6 | 🎯 端到端跑通 | CSV → 飞书卡片，全自动 |
| W7 | 定时无人值守 | 每天 9:00 自动推送日报 |
| W8 | 双向交互 | @机器人 → 检索 → 回复 |
| W9 | 自优化闭环 | 系统能自动改进自己的 Prompt |
| W10 | 🎯🎯🎯 项目交付 | Docker 一键部署 + 论文完成 |

### 关键依赖和风险

| 依赖/风险 | 需要什么时候搞定 | 备选方案 |
|----------|----------------|---------|
| Claude API 账号和支付 | Week 1 之前 | 国内代理 / DeepSeek API / 通义千问 |
| 飞书应用创建和权限审批 | Week 5 之前 | 先用飞书 Webhook 机器人（免审批）过渡 |
| 网络搜索 API（Bing/SerpAPI） | Week 2 之前 | DuckDuckGo 免费搜索 / 自建爬虫 |
| 至少 5-7 天真实 CSV 数据 | Week 1-2 积累 | 手动从点点下载 |
| 内网穿透（ngrok）如用 Webhook 模式 | Week 6 之前 | 用飞书长连接模式（免公网 URL） |

***

## 十一、关键设计原则

### 11.1 每个 Agent 可独立运行和测试

```bash
# 单独测试 Researcher
python -m src.agents.researcher --change '{"game":"鸣潮","rank_change":1,"change_type":"up","date":"2026-06-16"}'

# 单独测试 Verifier
python -m src.agents.verifier --input research_output.json

# 单独测试 Differ（不触发 Agent）
python -m src.pipeline.differ --date 2026-06-16 --dry-run

# 测试完整日报流程
curl -X POST http://localhost:8000/api/report/generate?date=2026-06-16
```

### 11.2 中间结果全部落库

Agent 每一步的输出都存入 SQLite。好处：

- 出错时快速定位是哪个 Agent 的问题
- 交互问答时可直接引用历史记录
- 自优化时有训练数据来源

### 11.3 Prompt 外置到 YAML

```yaml
# prompts/overview_scanner.yaml
system: |
  你是游戏行业全局扫描助手。你的任务是搜索当天游戏行业的整体动态，
  并判断哪些排名变动值得深入调研、哪些只是正常噪声。
  
  要求：
  - 搜索至少 3 个不同角度的行业新闻 query
  - 判断今日波动率是否在正常范围
  - 对有调研价值的变动给出推荐理由，对噪声给出跳过理由
  - 不要浪费资源调研 ±1 位且非前 5 名的微调
  
工具：
  - web_search: 搜索网络
  - db_query: 查询历史波动率数据

user_template: |
  日期：{date}
  平台：{platform}
  今日概况：{overview_json}
  变动清单：{changes_json}
  
  请搜索行业动态，判断哪些变动值得深度调研。

# prompts/researcher.yaml
system: |
  你是游戏行业竞品调研助手。给定一个数据变动，你需要搜索相关信息，
  找出可能导致该变动的具体事件。
  
  要求：
  - 至少搜索 3 个不同角度的 query
  - 每个事件需注明来源和发布时间
  - 优先采用官方公告和正规媒体报道
  
工具：
  - web_search: 搜索网络
  - web_fetch: 抓取网页内容

输出格式：JSON（严格遵循 schema）

user_template: |
  请调研以下数据变动：
  产品：{game_name}（{developer}）
  Bundle ID：{bundle_id}
  平台：{platform}
  排名：第 {yesterday_rank} → 第 {today_rank}（{change_type}）
  日期：{date}
  
  请搜索并返回相关事件信息。
```

### 11.4 先跑通再优化

第一版 Agent 可以很简单——一个 prompt + 一个搜索结果。跑通完整链路后，再逐步加：

- Verifier 核验
- 多轮搜索
- Prompt 自优化

***

## 十二、环境变量配置

```bash
# .env
# === Claude API ===
ANTHROPIC_API_KEY=sk-ant-xxx
ANTHROPIC_MODEL=claude-sonnet-4-6  # 性价比之选

# === 飞书 ===
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_VERIFICATION_TOKEN=xxx

# === 搜索工具（选一个） ===
# 方案 A：Bing Search API
BING_SEARCH_API_KEY=xxx
# 方案 B：SerpAPI
SERPAPI_KEY=xxx
# 方案 C：自建（用 requests + BeautifulSoup）

# === 数据库 ===
SQLITE_PATH=./data/intel.db
CHROMA_PATH=./data/chroma

# === 服务 ===
HOST=127.0.0.1
PORT=8000

# === 内网穿透（飞书回调用，长连接模式可省略） ===
NGROK_AUTH_TOKEN=xxx
```

***

## 十三、LLM 备选方案

| 方案                 | 优点               | 缺点            |
| ------------------ | ---------------- | ------------- |
| **Claude API**（首选） | 质量最好，tool use 原生 | 需要海外支付        |
| 国内代理 Claude API    | 支付方便             | 需找可靠渠道        |
| 通义千问 API           | 国内直接可用，便宜        | tool use 能力较弱 |
| DeepSeek API       | 性价比极高，中文好        | 稳定性一般         |
| Ollama 本地模型        | 完全离线             | 需要 GPU，推理质量受限 |

建议支持多 LLM 可切换：`config.py` 中配置 provider，Agent 基类做适配。

***

## 十四、部署方式

### 开发模式

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API keys

# 启动
python run.py
```

### 生产模式（个人电脑长期运行）

**方案 A：直接跑**

```bash
# Windows 用任务计划程序，每天 9:00 触发
python run.py --task daily-report
```

**方案 B：Docker（推荐，环境隔离）**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "run.py"]
```

```bash
docker build -t intel-agent .
docker run -d --name intel-agent \
  --env-file .env \
  -v ./data:/app/data \
  -p 8000:8000 \
  intel-agent
```

**方案 C：Windows 服务**（nssm 注册为系统服务，开机自启）

***

## 十五、依赖清单

```
# requirements.txt
# === Web 框架 ===
fastapi==0.115.*
uvicorn[standard]==0.34.*

# === 数据处理 ===
pandas>=2.2
openpyxl>=3.1        # Excel 支持

# === LLM ===
anthropic>=0.49
# openai>=1.0        # 备用

# === 飞书 ===
lark-oapi>=1.4

# === 数据库 ===
chromadb>=0.5

# === 搜索抓取 ===
httpx>=0.28
beautifulsoup4>=4.12
# duckduckgo-search>=7.0  # 免 API key 的搜索备选

# === 工具 ===
pyyaml>=6.0
python-dotenv>=1.0
pydantic>=2.0
pydantic-settings>=2.0

# === 定时任务 ===
# APScheduler>=3.10        # 内置定时调度
    
# === 内网穿透（可选） ===
# pyngrok>=7.0
```

***

## 十六、Agent 性能优化（待实施）

以下优化方案已评估可行性和收益，暂不实施，待系统跑通后再按优先级逐项落地。

### 16.1 优化前提：计时器

不加计时器就无法定位瓶颈。每个 Agent 运行时，对以下步骤打时间戳：

```
总耗时 45s
  ├── LLM 调用 × 3 轮    ~15s  (每轮 3-8s)
  ├── web_search × 3     ~15s  (DuckDuckGo 慢，单次 2-8s)
  ├── web_fetch × 2      ~10s  (目标网站响应不可控)
  └── JSON 解析/校验      ~1s
```

**实现方式**：在 `Agent.run()` 里用 `time.monotonic()` 记录每个阶段耗时，输出到日志或返回 dict 的 `_timing` 字段。

**收益**：开发期精确知道时间花在哪，生产期设定超时阈值和告警。

---

### 16.2 并行 Tool Call（高收益，低改动）

**问题**：当前 Agent 的多个 tool call 是串行执行的——即使 LLM 在同一轮返回了 3 个 `web_search` 调用，`_append_tool_results` 里也是 for 循环逐个执行。3 个搜索 query 白白等了 10 秒。

**方案**：

```
现状:  search(q1) → 5s → search(q2) → 5s → search(q3) → 5s = 15s
优化:  search(q1) ┐
       search(q2) ├─ 并发 ─ 5s
       search(q3) ┘
```

`_append_tool_results` 里把同一轮的 tool call 用 `concurrent.futures.ThreadPoolExecutor` 并发执行（搜索/抓取都是 I/O 密集型，线程池即可）。

**改动量**：`src/agents/base.py` 的 `_append_tool_results` 方法，约 15 行。

**风险**：无。tool call 之间没有依赖关系，并发安全。

---

### 16.3 缓存层（中收益，中改动）

**问题**：开发阶段反复调同一个 Agent、跑同一个 query，每次都要重新走网络搜索，浪费时间 + 搜索 API 配额。调一次 Overview Scanner 的 prompt 可能要跑 3-5 次才能调好，每次 45 秒。

**方案**：文件缓存，对 web_search 和 web_fetch 的结果做持久化。

```
data/cache/
  search_{md5(query)}_{date}.json   ← TTL 1 小时
  fetch_{md5(url)}.json             ← TTL 24 小时
```

缓存逻辑放在 tool 函数内部（`web_search` / `web_fetch`），对 Agent 和 LLM 完全透明。

**改动量**：`src/tools/web_search.py` 和 `src/tools/web_fetch.py`，各加 ~20 行。

**收益**：开发阶段同样的 query 第二次跑直接读缓存，网络等待降为 0。第一次真实跑完后，后续 prompt 迭代可以秒出结果。

---

### 16.4 干跑 / Mock 模式（高收益，低改动）

**问题**：调 prompt 的时候，你关心的是 Agent 输出的 JSON 结构对不对、逻辑通不通——而不是搜索结果是否真实。每次调 prompt 都要等 30 秒网络搜索，80% 的时间在等网络。

**方案**：`Agent.run()` 加一个 `_mock: bool = False` 参数。当 `_mock=True` 时，工具调用不触发真实网络请求，直接返回预录的假结果。

```
agent.run(date="2026-06-16", _mock=True)
  → web_search 被拦截，返回 mock_search_results.json 的内容
  → web_fetch 被拦截，返回 mock_fetch_result.json 的内容
  → LLM 照常调用（这是需要测试的核心）
```

Mock 数据可以来自：
- 第一次真实跑时自动录下来（`_record=True` 模式）
- 手动编辑 JSON 构造边界 case

**改动量**：`src/agents/base.py` 的 `_execute_tool` 方法，约 10 行。

**与缓存层的关系**：互补。缓存层省的是"同一个 query 跑第二次"，mock 模式省的是"任何 query 都跳过网络"。开发 prompt 时用 mock，验证完整性时用缓存。

---

### 16.5 确定性计算前置（低收益，但免费）

**问题**：Overview Scanner 输出里的 `volatility_context`（今天波动率 vs 本周均值对比），本质上是纯 SQL 查询 + 数学计算。让 LLM 做这个事浪费 token 和时间，Differ 模块可以直接算好。

**方案**：在 `src/pipeline/differ.py` 里加一个 `compute_volatility_context(date)` 函数，用 SQL 算出近 7 天波动率均值，和今天做对比，生成 `volatility_context` 的纯字符串。这部分数据不从 LLM 产出，而是拼接到 Agent 的输入或输出里。

**改动量**：`src/pipeline/differ.py` 加一个函数，Overview Scanner 的 `scan()` 里调一下。

**收益**：每次调用省 50-100 token（不大，但免费）。

---

### 16.6 不建议做的优化

| 不建议 | 原因 |
|--------|------|
| 流式输出 (streaming) | DeepSeek 开启 `response_format=json_object` 后不支持 streaming，结构完整性优先于感知速度 |
| 换更小的模型 | 竞品分析的推理质量 > 速度。省 2 秒换来错误结论，不划算 |
| 减少搜索轮次 | Overview Scanner 至少搜 3 个角度是设计文档定的，砍了等于降数据质量 |
| Checkpoint / resume | 过度工程化。Agent 总共 3-5 轮 tool call，重跑成本不高，加 checkpoint 反而增加复杂度 |
| Agent 调用并行化 | Researcher → Analyst 之间有因果依赖（Researcher 的结果是 Analyst 的输入），不能无脑并行。Week 7 的 runner 里会根据 day_type 做动态编排，不需要现在做 |

---

### 16.7 实施优先级

| 优先级 | 优化项 | 投入 | 收益 | 建议时机 |
|:--:|--------|:--:|:--:|------|
| 🔴 P0 | 计时器 | 1h | 定位瓶颈的前提 | 下次跑 Agent 之前 |
| 🔴 P1 | 并行 Tool Call | 0.5h | 搜索阶段快 3 倍 | Week 3 开始调 Researcher 时 |
| 🟡 P2 | 缓存层 | 1h | 开发迭代快 5 倍 | 开始频繁调 prompt 之前 |
| 🟡 P2 | Mock 模式 | 0.5h | 调 prompt 不用等网络 | 与缓存层一起做 |
| 🟢 P3 | 确定性计算前置 | 0.5h | 省 token（不多但免费） | Week 4-5 优化 Analyst 时 |

**总投入**：约 3.5 小时。建议分两批做——P0+P1 先上（1.5h），P2 等开始频繁调 prompt 时再上。

