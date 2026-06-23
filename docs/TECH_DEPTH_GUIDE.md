# 技术深度提升指南

## 当前状态

所有模块已跑通（Loader → Differ → Cross-Chart → Story Picker → Scanner → Researcher → Verifier → Analyst → Design Analyst → Briefer → Pusher），但每个模块内部是"调一次 LLM → 返回 JSON → 存库"的单层模型。技术上能跑，深度上跟 demo 项目拉不开差距。

以下是四个不靠堆代码行数、真正有技术含量的方向。

---

## 1. 自评估框架（Eval Framework）

### 现状

改完 prompt → 跑一次 Scanner → 肉眼判断"嗯这次 focus 选得不错"。主观判断，无法量化，无法复现。两次 prompt 改动之间不知道该保留哪个。

### 做法

做一套离线评估数据集——准备 5-10 份"标准答案"，每次改 prompt 后自动跑 eval 对比：

```
eval_set/
  case_0618/
    input.json        # Scanner 的输入（changes + overview + cross_chart_signals）
    expected.json     # 人工标注：应该选哪些游戏进 focus，哪些该 skip
  case_0619/
    ...
```

```python
# tests/test_eval_scanner.py

def test_scanner_recall():
    """Scanner 选出的 focus 覆盖了 expected 的百分之多少？
       80% 以上 = 没漏掉重要游戏"""
    for case in eval_cases:
        result = scan(case.input)
        recall = len(set(result.focus) & set(case.expected)) / len(case.expected)
        assert recall >= 0.8, f"{case.name}: recall={recall:.0%}"

def test_scanner_precision():
    """Scanner 选出的 focus 有多少是 expected 里的？
       低于 60% = 选太多噪音，浪费后续 Researcher token"""
    for case in eval_cases:
        result = scan(case.input)
        precision = len(set(result.focus) & set(case.expected)) / len(result.focus)
        assert precision >= 0.6, f"{case.name}: precision={precision:.0%}"
```

同样的框架可以套到 Researcher、Briefer 上——每个 Agent 都有一组 eval case。

### 实操步骤

核心逻辑就一件事：把人工判断变成可重复的自动对比。

**用到什么**：

| 需要 | 是什么 | 有吗 |
|------|--------|:---:|
| 标准答案数据集 | 5 个日期，每个日期标注"应该选哪些游戏进 focus" | ❌ 需要手动标 |
| JSON 对比逻辑 | 比较 Scanner 输出和标准答案的重叠 | ❌ 需要写 |
| pytest | 已会 | ✅ |

不需要新框架、新数据库、新 API。纯 Python 脚本 + pytest。

**第一步：人工标注（你来做，不是 AI）**

挑过去已经跑过的 5 个日期。对每个日期，打开 `daily_overviews` 里的 `recommended_focus_json`，对着 `changes` 表，人工判断：哪些游戏**应该**被选中？

```
eval_set/
  case_20260618.json
  case_20260616.json
  ...
```

每个文件就是一个 JSON：

```json
{
  "date": "2026-06-18",
  "expected_focus": [
    "王国保卫战5",
    "向僵尸开炮-尸潮来袭",
    "地牢猎手6",
    "和平精英",
    "金铲铲之战"
  ],
  "expected_skip": [
    "开心消消乐",
    "蛋仔派对"
  ]
}
```

标注原则：你当自己是主策划——哪些游戏不调研会后悔？哪些调研了纯属浪费钱？

**关键注意**：标注时**不要看当天的 Scanner 输出**。先自己判断应该选哪些，再看 Scanner 原来的选择。先看 Scanner 输出会被带偏。

这一步是整个框架最难的部分，因为你在编码你的判断标准。但也是最值钱的部分——标注完你就有了可以被验证、被讨论、被优化的标准。标准本身是迭代的：每次跟主策划聊完，把反馈更新到 expected 里。

**第二步：写对比逻辑（一个 Python 文件）**

`tests/test_eval_scanner.py`：

```
测试流程：
1. 读取 eval_set/ 下所有 case 文件
2. 对每个 case：
   a. 从数据库取该日期的 changes + cross_chart_signals
   b. 调用 scan()，传入相同的 input
   c. 拿到 Scanner 输出的 recommended_focus
   d. 算 recall：Scanner 选中的 / expected 期望的 = 多少%
   e. 算 precision：Scanner 选中的有多少在 expected 里 = 多少%
3. 汇总所有 case，输出平均分
```

核心计算就两行：

```python
recall = len(set(actual_focus) & set(expected_focus)) / len(expected_focus)
precision = len(set(actual_focus) & set(expected_focus)) / len(actual_focus)
```

- **回忆率（recall）**：该选的全选了吗？漏了就是 recall 低
- **精确率（precision）**：选的都对吗？选了不该选的就是 precision 低

**第三步：挂到 pytest**

跟 P0/P1 测试一样跑：

```bash
pytest tests/test_eval_scanner.py -v
```

改 prompt 之后跑一次，自动告诉你 recall 和 precision 跟上次比是涨了还是跌了。

**第一次跑预期**：分数可能很难看。这是好事——说明 eval 框架在起作用，捕捉到了 prompt 和人工判断之间的真实差距。

### 为什么有技术含量

标注标准答案这件事本身就是对你的领域知识的编码——你在回答"什么是好的竞品情报判断"。标注了 10 天后，你不仅有一个 eval 框架，还有一份可以被任何人复用、被任何团队审计的数据集。这个数据集比你任何一段代码都值钱。

**投入**：2 小时（标注 5 个 case 各 15 分钟 + 写 eval runner 45 分钟）

---

## 2. Prompt 自动优化闭环（Check-3 工程化）

### 现状

DESIGN.md §8.1 画了流程图但没实现。每次改 prompt 都是手动：改 YAML → 跑一次 → 肉眼判断 → 再改。

### 做法

不是简单的"改 prompt"，而是一个完整的实验管理系统。四个步骤：

```python
# src/optimize/prompt_optimizer.py

class PromptOptimizer:
    """Prompt 自优化引擎 —— DESIGN.md §8.1 的实现"""

    def collect_failures(self, since_date: str) -> list[Failure]:
        """
        从 conversations 表收集失败案例：
        - 用户追问了原简报没覆盖的问题 → 覆盖不足
        - 用户纠正了分析结论 → 分析错误
        - Verifier 核验拒绝 → 信息不可靠
        """
        pass

    def diagnose(self, failures: list[Failure],
                 current_prompt: str) -> Diagnosis:
        """
        AI 结构化诊断 —— 不是随便问一句"怎么改 prompt"：

        诊断维度：
        - 失败集中在哪个维度？（事件层漏了？设计层太浅？在研层没搜到？）
        - 是搜索 query 不够好，还是搜索结果的利用不够好？
        - 是信息量不足（搜少了），还是信息筛选不对（搜到了但没采用）？
        - 是否存在系统性的某类游戏总是被漏掉（如独立游戏、海外游戏）？
        """
        pass

    def propose_fix(self, diagnosis: Diagnosis) -> str:
        """基于诊断生成新 prompt 草稿"""
        pass

    def ab_test(self, old_prompt: str, new_prompt: str,
                eval_cases: list[EvalCase]) -> ABResult:
        """
        用自评估框架（第 1 项）对比新旧 prompt：
        - 召回率变化
        - 精确率变化
        - 输出长度变化
        - 来源质量变化（fetch_success_rate）

        质量提升 → 自动替换
        无提升 → 存档建议，人工决定
        """
        pass

    def apply(self, new_prompt: str, version: str) -> None:
        """记录版本 → 替换 YAML → 存 prompt_versions 表 → 标记 active"""
        pass
```

整个流程：

```
用户追问/纠正 (conversations 表)
    │
    ▼
collect_failures() → 提取失败案例
    │
    ▼
diagnose() → AI 结构化诊断（哪个维度？根因是什么？）
    │
    ▼
propose_fix() → 生成新 prompt
    │
    ▼
ab_test() → 用 eval 框架对比新旧版本
    │
    ├── 质量提升 → apply() 自动替换
    └── 无提升 → 存档，等人工决定
```

### 为什么有技术含量

这是把 PDCA 里的 Check 环节真正工程化了。不是"跑通了 Check 的逻辑"，而是"Check 本身是一个可运行的子系统"。答辩/面试时，这是你跟其他竞品分析项目的核心差异——**你的系统会自我进化**。

**投入**：3 小时。`collect_failures` 依赖 conversations 表有数据（需要飞书交互先跑起来）。可以先做 `diagnose + propose_fix + ab_test`，手动喂失败案例。

---

## 3. 跨榜信号的统计基线（阈值 → 数据）

### 现状

Cross-Chart 用硬编码阈值（`STRONG_RANK=15`, `SIGNIFICANT_DELTA=25`）。所有游戏用同一把尺子。

**问题**：一个游戏长期免费榜 #5、畅销榜 #48，差距 43 看起来很大——但如果过去 30 天都是这样，那今天没有异常。阈值判断把常态当信号。

### 做法

给每个游戏算自己的"正常波动范围"——统计基线：

```python
# src/pipeline/cross_chart.py 新增

def compute_baseline(bundle_id: str, days: int = 30) -> dict:
    """
    返回该游戏各榜单的历史均值 + 标准差。
    比如鸣潮过去30天：免费榜均值 3.2±0.8, 畅销榜均值 28.5±4.1
    """
    history = db.get_game_history(bundle_id, days)
    by_chart = defaultdict(list)
    for r in history:
        by_chart[r["chart_type"]].append(r["rank"])

    baseline = {}
    for chart, ranks in by_chart.items():
        mean = statistics.mean(ranks)
        std = statistics.stdev(ranks) if len(ranks) > 1 else 0
        baseline[chart] = {"mean": mean, "std": std}
    return baseline


def is_anomaly(today: dict, baseline: dict,
               z_threshold: float = 2.0) -> bool:
    """
    今天某个榜单的排名偏离历史均值超过 z_threshold 个标准差 → 真异常。

    例子：
      鸣潮今天免费榜 #2，历史均值 3.2±0.8 → z=1.5 → 不异常
      鸣潮今天免费榜 #15，历史均值 3.2±0.8 → z=14.75 → 强烈异常
    """
    for chart, rank in today.items():
        bl = baseline.get(chart)
        if bl and bl["std"] > 0:
            z = abs(rank - bl["mean"]) / bl["std"]
            if z >= z_threshold:
                return True
    return False
```

跨榜信号从"差距大 = 信号"变成"偏离正常 = 信号"：

```
之前：
  游戏A 免费#5 畅销#48 → 差距43 → SIGNIFICANT_DELTA=25 → 有信号

之后：
  游戏A 过去30天免费均值#8±3 畅销均值#45±5
  今天免费#5（z=1.0, 正常） 畅销#48（z=0.6, 正常）
  → 差距43对别的游戏是信号，对这游戏是常态 → 不是信号
```

反过来也一样：一个游戏免费榜从 #30 跳到 #8、但畅销榜纹丝不动——差距绝对值只有 22（< SIGNIFICANT_DELTA），但对这个游戏来说非常异常。

### 为什么有技术含量

从阈值驱动变成数据驱动。阈值是你拍脑袋的，基线是数据算出来的。随着数据积累（每天 100 款 × 3 个榜单），基线从第 1 天的不准逐渐收敛。这是一个**随时间自动变好的系统**。

**投入**：2 小时。改 `cross_chart.py` 的 `detect_signal` + 新增 `compute_baseline` + `is_anomaly`。

---

## 4. 两路分析师互驳（Multi-Agent Verification）

### 现状

单线流程：Researcher 搜 → Verifier 核验 → Analyst 分析。任何人或模型在单线上都可能产生盲点——Researcher 搜漏了一个角度，Analyst 就不可能知道。

### 做法

让两次独立的分析互相挑战，而不是线性传递：

```
Researcher A (中文平台) → Analyst A (独立分析)
        │                        │
        │   独立搜、独立分析       │
        │                        │
Researcher B (行业媒体+海外) → Analyst B (独立分析)
        │                        │
        └────────┬───────────────┘
                 ▼
          Synthesizer:
          "A 说鸣潮是因为版本更新冲榜。
           B 说鸣潮是因为联动 2077 活动。
           两者的证据分别是...
           判断：两者都对——版本更新和联动是同一事件的两个面。
           合成结论：鸣潮 3.4 版本联动 2077 是主要驱动力，
           版本更新是载体，联动内容是引爆点。
           置信度：高（两份独立分析指向同一方向，无矛盾）。"
```

两个 Researcher 用不同的搜索策略：

| | Researcher A | Researcher B |
|---|------------|------------|
| 搜索平台 | TapTap / B站 / 微博 / NGA | 17173 / GameLook / 游戏葡萄 / Reddit |
| 搜索角度 | 玩家视角：口碑、评测、社区反应 | 行业视角：商业影响、市场规模、竞争格局 |
| 语言 | 中文优先 | 中英文 |
| 搜索轮次 | 正常 12 轮 | 正常 12 轮 |

Synthesizer 的职责不是简单的汇总——是对比和交叉验证：

```
输出结构：

{
  "consensus": [
    "双方一致的结论"  // 高置信度，直接采纳
  ],
  "disagreement": [
    {
      "A_says": "...",
      "B_says": "...",
      "resolution": "Synthesizer 的判断",
      "verdict": "采纳 A / 采纳 B / 两者互补 / 需人工判断"
    }
  ],
  "A_only": [
    "只有 A 发现的洞察（玩家社区角度独有）"
  ],
  "B_only": [
    "只有 B 发现的洞察（行业媒体角度独有）"
  ],
  "confidence_boost": "A 和 B 的一致结论置信度从独立分析的 0.7 提升到 0.92"
}
```

### 为什么有技术含量

这比"做更多 Agent"高一个层次——不是数量的堆叠，而是把单线的确定性流程变成**多路可证伪的分析**：

- 两份分析一致 → 高置信度，结论更可信
- 两份分析矛盾 → 发现了信息缺口，需要人工深入
- 只有一路发现 → 另一路的搜索策略需要改进

分歧本身是最有价值的信号——它告诉你不应该完全信任任何一个单线分析。

### 成本

每次调研多一倍 LLM 调用（两个 Researcher + 两个 Analyst + 一个 Synthesizer）。可以通过以下方式控制：

- 只对高威胁跨榜信号触发双路
- 安静日只跑单路
- Synthesizer 用便宜模型（DeepSeek 而非 Claude）

**投入**：3 小时。改 Runner 的 Researcher/Analyst 并行逻辑 + 写 Synthesizer prompt + Runner 增加 day_type 判断。

---

## 实施路线

| 顺序 | 做什么 | 投入 | 依赖 | 为什么先做 |
|:---:|--------|:---:|------|------|
| 🔴 1 | 自评估框架 | 2h | 无 | 基础设施。没有它，2/3/4 的改动都无法量化验证 |
| 🔴 2 | Prompt 优化闭环 | 3h | 1（eval 框架） | 让 3/4 的 prompt 改动自动化 |
| 🟡 3 | 跨榜统计基线 | 2h | 1（eval 验证） | 阈值 → 数据，随数据积累自动变好 |
| 🟡 4 | 两路互驳 | 3h | 1（eval 验证） | 单线 → 多路可证伪 |

**核心原则**：1 是地基。没有 1，你永远不知道 2 的 prompt 改动、3 的基线算法、4 的双路分析是不是真的更好了——你只能肉眼看，而肉眼是不可复现的。
