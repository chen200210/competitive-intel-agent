# Agent 系统质量指南

> 最后更新：2026-06-22
> 合并自 AGENT_PRECISION_GUIDE.md + AGENT_QUALITY_DIMENSIONS.md，按当前设计更新。

---

## 一、当前系统架构

### Agent 清单（5 个）

| Agent | 职责 | 触发条件 |
|-------|------|---------|
| OverviewScanner | 赛道变动筛选，判断哪些值得深度调研 | 每次跑 |
| Researcher | 五维深挖（事件/玩法/玩家/设计/在研） | Scanner 推荐的每条 |
| Verifier | 信息可信度核验 | Researcher 产出后 |
| DesignAnalyst | 三维护设计分析（核心玩法/留存移植/竞争风险） | 有 design_tags 的调研 |
| Briefer | 融合全量数据 → 六板块飞书卡片 | 每次跑 |

### 已砍掉的 Agent

| Agent | 原因 |
|-------|------|
| Analyst | 日报不需要商业因果推理 |
| NewGameWatcher | Scraper 数据直读 DB，不经 AI 中转 |
| MarketNewsScanner | 同上，新闻标题+URL 直接展示 |

### 核心设计原则

> **Scraper 抓到的结构化数据，直接展示，不让 AI 经手。AI 只用在两件事上：理解和判断。**

---

## 二、三维度：稳定性 / 准确性 / 成本

```
追求准确性 → 多搜几个 query → 多调几次 API → 成本 ↑
追求成本低 → 少搜、少调 LLM → 信息不够全 → 准确性 ↓
追求稳定性 → 加 retry、加 fallback、加冗余 → 成本 ↑
追求稳定性 → 强制兜底输出 → 内容可能不完整 → 准确性 ↓
```

不存在"三个维度都做到极致"的方案。只有"在约束条件下做了正确取舍"的方案。

---

## 三、当前已做的努力

### 3.1 稳定性

| 做了什么 | 在哪里 | 效果 |
|---------|--------|------|
| 三级搜索降级 | `web_search.py` — 360 → 搜狗 → Bing | 一个挂了自动换下一个 |
| JSON 五层 fallback | `base.py` — 直接解析 → 修复 → 去 fence → 正则提取 → 兜底 | LLM 输出什么都能兜住 |
| 结果截断保护 | `base.py` — 单条 tool result 上限 12000 字符 | 不会撑爆 LLM context window |
| max_tool_rounds 强制终止 | `base.py` — 5 轮搜不完也强制输出 JSON | 不会无限循环烧 token |
| 审计日志 fire-and-forget | `base.py` — 日志写失败不阻塞主流程 | 可观测性不能成为故障点 |
| 并行 Agent 异常隔离 | `runner.py` — 一个 Researcher/Verifier 挂了不影响其他 | 部分失败不扩散 |
| 数据入库幂等 | `sqlite.py` — UNIQUE 约束 | 同一天重复跑不脏数据 |
| force 模式先删后跑 | `runner.py` — 删除已有 results + changes 再重跑 | 强制重跑不会产生重复 |
| Day 1 不触发 AI | `differ.py` — 首日数据只积累不分析 | 新系统不会因无历史数据而乱报 |

### 3.2 准确性

| 做了什么 | 在哪里 | 效果 |
|---------|--------|------|
| 六类故事检测 | `story_picker.py` — 跃升/黑马/暴跌/爬升/异动/跨榜 | 从海量变动中挑最有信息量的 |
| 五维搜索策略 | `researcher.yaml` — 事件/玩法/玩家/设计/在研 | 不遗漏关键信息维度 |
| 来源可达性规则 | `researcher.yaml` — 每条 finding 必须有可读来源 | 不会出现"找不到出处"的结论 |
| 三维可靠性核验 | `verifier.yaml` — 来源权威性 + 可交叉验证性 + 因果逻辑 | 低可信度信息被标记 |
| 位置感知关注度评分 | `differ.py` — 排名区间权重 + 变动幅度 + Breakout + 赛道加成(+1.5) | 不是一刀切的"≥3 位就重要" |
| 跨榜信号检测 | `cross_chart.py` — 5 种信号模式 | 单榜看不出的问题跨榜暴露 |
| 赛道规则引擎 | `track_filter.py` — 纯规则打标，赛道 > 可无视 | 不消耗 token |
| 搜索缓存提示 | `researcher.py` — 告诉 Agent 已知不可达 URL | 避免在坏链上浪费 fetch |

### 3.3 成本

| 做了什么 | 在哪里 | 效果 |
|---------|--------|------|
| Story Picker 纯规则 | `story_picker.py` — 零 token 筛选 | AI 只处理精选后的数据 |
| Track Filter 纯规则 | `track_filter.py` — 零 token 打标 | OverviewScanner 只收赛道变动 |
| 并行 Tool Call | `base.py` — ThreadPoolExecutor 并发执行 | 搜索阶段快 ~3 倍 |
| 搜索缓存 | `web_search.py` — 同 query 同日期 24h 缓存 | 开发迭代不重复付费 |
| 确定性计算前置 | `differ.py` — 排名对比用 SQL 算 | 省 token |
| 跨榜信号纯规则 | `cross_chart.py` — 算法匹配 | 不调 AI |
| Scraper 数据直读 | `briefer.py` — 从 DB 读，不经 Agent | 省掉 2 个 Agent 的全部 token |
| Agent 从 8 减到 5 | 砍 Analyst/NewGameWatcher/MarketNewsScanner | 每次少 3 次 LLM 调用 |

---

## 四、当前缺口

### 4.1 稳定性缺口

| 问题 | 现状 | 目标 |
|------|------|------|
| LLM API 限流/超时 | 直接失败 | 指数退避 retry（1s → 2s → 4s），最多 3 次 |
| 所有 Researcher 全挂 | Briefer 收到空数据 | 空数据时降级为"今日无深度调研" |
| DeepSeek 单 provider | 挂了全挂 | 可切换多 provider（base.py 已支持 model 参数） |

### 4.2 准确性缺口

| 问题 | 现状 | 目标 |
|------|------|------|
| 零评测体系 | 不知道准确率 | 人工标注 20-30 条正确答案，建 eval set |
| 阈值拍脑袋 | STRONG_RANK=15, SIGNIFICANT_DELTA=25 | 用 30 天数据回测调优（跨榜统计基线） |
| LLM 验证 LLM | Verifier 判断另一个 LLM 输出 | 加交叉验证：多个独立来源印证才给高分 |
| Researcher 幻觉 | 编造来源频率未知 | 每 100 条 finding 抽查 10 条验证来源真实性 |
| Prompt 改动无法评估效果 | 改了不知道变好变坏 | 每次改 prompt 跑 eval 看变化 |

### 4.3 成本缺口

| 问题 | 现状 | 目标 |
|------|------|------|
| 没有 token 计数 | 计时器有了，token 没统计 | Agent 调用后记录 prompt_tokens + completion_tokens |
| 没有成本报告 | 不知道单次日报花多少钱 | 日报末尾附 cost |
| 缓存命中率未知 | 缓存层有，没统计命中率 | 可视化缓存命中率 |

---

## 五、精确度提升（五件事）

### 1. Few-shot 示例（ROI 最高）

LLM 是模仿机器。一份带示例的 prompt 比三页规则描述管用。在主策划聊完后，用他认可的简报当 few-shot 示例塞进 prompt。

**投入**：每个 prompt 加 1 个示例，15 分钟/个。

### 2. 自我批判再输出（Self-Refine）

Briefer 现在是单轮调用（`max_tool_rounds=1`）。改成两轮：

```
轮 1：LLM 输出简报草稿
轮 2：从主策划视角批判——
  - 哪段分析说了等于没说？
  - 新游关注里 Steam 移植排最前了吗？TapTap 只展示了赛道相关的吗？
  - 市场变动只放了游侠/17173 吗？最多 5 条了吗？
  - 设计洞察有没有市场可行性判断和行动建议（应该没有）？
  修正后输出最终版。
```

**改动量**：`briefer.py` 的 `max_tool_rounds` 1→2，加二轮 prompt。约 15 行。

### 3. 主策划偏好注入

每次聊完后，把反馈写成偏好文件，Agent 运行时注入 prompt。

```
主策划偏好（示例）：
- 设计洞察里不要写"建议压力测试"。只写塔防品类特有的风险。
- 关心的是"这个玩法我们能抄吗？抄了成本多少？"
- 新游只关注赛道相关的
- 新闻只放游戏媒体来源，不要百科/知乎
```

**投入**：初次 10 分钟，之后每次聊完 2 分钟更新。

### 4. 给 Agent 看历史（增量调研）

Researcher 先查上次调研记录，在已有基础上查缺补漏——版本更新搜"上次到现在"，玩法机制沿用，社区评价重新搜。

**投入**：改 `researcher.yaml` + `researcher.py`，约 30 分钟。

### 5. 输出自检从 prompt 移到代码层

Prompt 里的自检 = 建议。代码里的自检 = 强制。

```python
# researcher.py — research() 末尾
for f in findings:
    has_readable = any(
        s.get("fetch_status") == "success"
        for s in f.get("sources", [])
    )
    if not has_readable:
        result["dimensions_missed"].append(f"'{f['headline']}' 无可读来源")
```

**投入**：`researcher.py` 加 30 行。

### 6. Briefer 卡片审计层（新增）

**问题**：Briefer 是 LLM——它会编造 URL、漏掉板块、把非赛道游戏放进新游、把 AirPods 广告当新闻。Prompt 指令是建议，LLM 可以忽略。

**方案**：在 Briefer 产出卡片后、推送前，加一层**纯代码审计**——不耗 token，只做硬性检查。能自动修的自动修，修不了的标红报警。

```
Phase 4: Briefer → 卡片 JSON
              ↓
         Audit 审计层（0 token）
              ↓
         Pass? ──→ Phase 5: Push
              ↓
         Fail? ──→ 自动修 → 重试 → 还不行 → 标红报警
```

**检查项**：

| # | 检查 | 红灯条件 | 自动修？ |
|---|------|---------|---------|
| 1 | URL 真实性 | 卡片里的 URL 不在输入数据的 URL 集合中 | ❌ 删除该链接 |
| 2 | 新闻来源 | 新闻来源不是 游侠/17173 | ✅ 移除该条 |
| 3 | 新闻内容 | 标题命中非游戏关键词（AirPods/世界杯/耳机…） | ✅ 移除该条 |
| 4 | 新闻数量 | > 5 条 | ✅ 截断到 5 条 |
| 5 | 新游赛道 | TapTap 新游的 track_relevant=false 出现在卡片 | ✅ 移除该条 |
| 6 | Steam 排序 | Steam 移植没排在新游板块最前面 | ✅ 重排 |
| 7 | 设计洞察署名 | 洞察段落不以 `**游戏名**：` 开头 | ❌ 标黄 |
| 8 | 板块完整性 | 缺少某个必选板块 | ❌ 标红 |
| 9 | 卡片大小 | JSON 超过飞书 30KB 限制 | ✅ 截断最长的板块 |
| 10 | 残留关键词 | 卡片中出现 微恐/冰河/火山/风险反照 等已废弃词汇 | ❌ 标红 |

**接口**：

```python
# src/pipeline/audit.py — 新文件，~100 行

def audit_card(card: dict, context: AuditContext) -> AuditResult:
    """审核卡片，能修就修，不能修就报"""

class AuditResult:
    passed: bool          # 是否通过
    score: int            # 0-100
    fixes_applied: list   # 自动修复的项
    warnings: list        # 标黄的项
    failures: list        # 标红的项
    fixed_card: dict      # 修复后的卡片
```

**接入 runner**：

```python
# Phase 4.5
audit_result = audit_card(card, context)
if not audit_result.passed:
    print(f"  ⚠️ Audit: {len(audit_result.failures)} failures")
card = audit_result.fixed_card
```

**投入**：新文件 `src/pipeline/audit.py`，~100 行纯代码，零 token。

---

## 六、实施优先级

| 顺序 | 做什么 | 投入 | 依赖 |
|:---:|--------|:---:|------|
| 🔴 1 | 卡片审计层（audit.py） | 30min | 无 |
| 🔴 2 | Briefer Self-Refine | 15min | 无 |
| 🔴 3 | 代码层自检（Researcher 强制字段检查） | 30min | 无 |
| 🟡 4 | 跨榜统计基线 | 1h | 需要 30 天历史数据 |
| 🟡 5 | Researcher 历史增量 | 30min | 无 |
| 🟢 6 | Few-shot 示例 | 15min/个 | 需主策划认可的简报 |
| 🟢 7 | 主策划偏好文件 | 10min | 需跟主策划聊完 |

1-3 纯工程活，现在就能做。审计层优先级最高——不耗 token，直接堵 LLM 的漏。
