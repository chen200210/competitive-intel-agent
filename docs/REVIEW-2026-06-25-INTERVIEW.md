# 项目技术评审 — 2026-06-25 (秋招面试官视角)

> 评审视角：秋招面试官，考察工程能力、架构设计、代码质量、测试、错误处理等维度。
> 评审范围：全项目源码 (~14,700 行 Python) + 架构决策 + 工程实践。
> 候选人背景推测：应届/1-2年经验，独立完成的全栈项目。

## 总体评价：中上 / 70-75 分（百分制）

核心判断：**这是一个"能用"的真实项目**，比大多数应届生的 toy project 强很多。架构分层清晰，对 AI 的局限性有清醒认识（零 token 规则层与 AI 层分离），Prompt Engineering 有深度。但工程习惯（错误处理、类型设计、测试基础设施）还停留在"脚本思维"，离真正的软件工程有一段距离。

---

## Part 1: 做得好的 (Keep Doing)

### 1. 架构分层清晰 ★★★★☆

`Scraper → Loader → Differ → StoryPicker → Briefer → Audit → Push` 流水线阶段划分合理。每个阶段职责明确，特别是**"零 token 规则层"和"AI 层"的分离**——`render`、`differ`、`track_filter`、`audit` 完全不调 LLM，只有 `scorer` 调。说明你理解了 AI 的不可靠性和成本问题，不是那种"什么都丢给 LLM"的初学者。

**Keep**: 保持这个边界。未来加新板块时，**永远先问自己"这段内容能不能用代码生成"**，而不是直接把数据扔给 LLM。

### 2. Prompt Engineering 有深度 ★★★★☆

`prompts/summarizer.yaml` 写得相当好：
- **CoT 推理步骤**（5 步：信息提取→摘要生成→打分定位→标签选择→输出）
- **强制分布约束**（≥25% 低于 40 分 + ≤30% 高于 60 分）防止 LLM 退回到"安全中间"分数
- **Matched Verdict Pool** 标签体系（6 个正面 + 6 个负面标签，严格受控词汇）
- **Retry 机制**（最多 3 次，全失败后代码层强制修正）

Calibrator 的反馈闭环设计（用户 👍👎 → 分析 → 调整 topic_boosts → 影响后续打分）也是有思考深度的。

**Keep**: 评分体系的复杂度已经足够。加新维度时优先扩展标签池而非新增评分维度。

### 3. P0 算法有测试覆盖 ★★★☆☆

`test_track_filter.py`、`test_differ.py`、`test_story_picker.py`、`test_loader.py` 覆盖了核心计算逻辑的边界条件。你选择先测试"最容易出错的计算逻辑"而不是"最容易写测试的 CRUD"，这个优先级判断是对的。

**Keep**: 每次改算法逻辑时同步更新对应的 P0 测试。

### 4. 有审计和监控意识 ★★★☆☆

`audit.py` 做飞书卡片推送前的零 token 质量门禁（三板块：新游/市场/排名），`pipeline_runs` 表记录每次运行的阶段耗时和状态——说明你在想"出问题了怎么发现"而不仅仅是"怎么跑通"。

**Keep**: 加新内容板块时，**必须在 audit.py 中同步添加对应的检查规则**。

### 5. Briefer 拆分执行得好 ★★★☆☆

从 1263 行单文件拆为 6 个模块（`briefer.py` + `market_pipeline.py` + `scorer.py` + `render.py` + `enrichment.py` + `dedup.py`），每个模块 200-500 行，职责清晰。`CLAUDE.md` 中记录了模块间依赖关系图，便于后续维护。

### 6. Scraper 基类的 Template Method 模式 ★★★☆☆

`ChartScraper.run() → scrape()[子类实现] → _clean()[基类] → _write_csv()[基类]`。新增数据源只需实现 `scrape()`，字段映射和 CSV 落盘统一处理。

---

## Part 2: 需要改进的 (To Fix)

### 🔴 P0 — 错误处理形同虚设（最严重的问题）

全项目有 **~35% 的 `except` 块是空的 `pass`**。这不是小问题——这是系统性的工程缺陷。具体实例：

| 位置 | 代码 | 后果 |
|------|------|------|
| `runner.py:175` | `except Exception: pass` | 获取 B 站视频失败 → audit 拿不到完整 context |
| `runner.py:224` | `except Exception: pass` | `insert_pipeline_run()` 写入失败 → 整条运行记录丢失，监控盲区 |
| `briefer.py:134` | `except Exception: pass` | 标签持久化失败 → Calibrator 拿不到信号 |
| `taptap_resolver.py:49,106` | `except Exception: pass` | DB 查询/Playwright 失败 → 调用方不知道解析失败 |
| `dedup.py` 多处 | `except Exception: pass` | 去重记录写入失败 → 重复内容可能推送多次 |
| `hot_tracker.py` 多处 | `except Exception: pass` | 热点收集失败静默 → 日报缺热点板块 |
| `scorer.py:57` | `except Exception: pass` | YAML 加载失败 → 默默用默认配置，与预期行为不一致 |

**为什么严重**：在生产环境中这意味着**故障不可观测**。数据库挂了、API 超时了、文件损坏了——你的系统不会有任何告警，只会默默产出质量下降的日报，你最后一个知道。

**改进方向**：
- 区分 `fatal`（必须抛）、`degraded`（记录日志继续）、`best-effort`（允许静默但需注释说明原因）
- 引入结构化日志（`logging` 模块而不是 `print`），至少区分 WARNING 和 ERROR 级别
- 关键路径（如 `pipeline_runs` 写入失败）绝不应该静默

---

### 🟠 P1 — 类型系统"有但没用到位"

你用了 `from __future__ import annotations` 和完整的函数签名类型注解（253 个函数），这很好。但内部数据流几乎全是 `dict[str, Any]`：

```python
def brief_from_db(date: str, ...) -> dict[str, Any]:  # 返回什么？调用方不知道
    top_news: list[dict[str, Any]]  # 这个 dict 有哪些 key？没人知道
```

游戏、新闻、排名变动是不同的数据实体，但在你的代码里它们都是 `dict[str, Any]`——编译器帮不了你，IDE 补全不了，重构时 key 改名全靠全局搜索。

`scorer.py` 里用了 Pydantic 做 LLM 输出的边界校验——这是对的，但管道内部的数据传递也应该有结构定义。

**改进方向**：
- 用 `TypedDict` 定义核心数据实体（`GameRecord`、`NewsItem`、`RankChange` 等）
- 或者更进一步，用 Pydantic/dataclass 做内部 DTO
- 这不需要改架构，纯增量改进

---

### 🟠 P1 — `sqlite.py` 是上帝类（God Class）

1325 行，12+ 张表的全部 CRUD 塞在一个类里。这是项目里最典型的"单体内聚不足"问题：

```python
class Database:
    def insert_ranking(...)     # rankings 表
    def get_market_news(...)    # market_news 表
    def insert_hot_keywords(...)# hot_keywords 表
    def record_feedback(...)    # user_feedback 表
    # ... 还有 50+ 个方法
```

你用了注释分隔线来组织代码（`# ── Rankings CRUD ──`），这其实是"这个类该拆了"的信号。

**改进方向**：
- 按领域拆分为 `RankingRepo`、`NewsRepo`、`FeedbackRepo` 等，每个 100-200 行
- 共用连接管理抽到 `Database` 基类或依赖注入

---

### 🟡 P2 — 测试体系不够成熟

9 个测试文件覆盖了核心算法（这点好），但：
- **自建测试框架**而不是 pytest——没有 fixture、参数化、覆盖率报告、并行执行
- **没有 CI 配置**——没有 GitHub Actions / pre-commit hook / mypy 类型检查
- **0 个集成测试**——整个 pipeline 跑通了吗？`runner.py` 没有任何测试
- **scraper 完全没测**——这些是最容易出问题的（外部网络、HTML 结构变化）

**改进方向**：
- 迁移到 pytest（语法兼容，改 `assert` 即可，成本低）
- 加一个 GitHub Actions workflow：`pytest + mypy --strict` 作为最低门禁
- 至少给 `runner.py` 加一个"端到端跑通不崩溃"的 smoke test

---

### 🟡 P2 — Git 历史不可读

3 个 commit 涵盖 14,700 行代码的完整演进过程——这不可能。合理推测是你用了大仓（monorepo），在别处开发然后 squash 过来，或者为了"干净"做了 rebase。但面试官看到 3 个 commit 的项目，无法判断你的开发习惯：是分步骤迭代还是全部写完再提交？commit message 是否规范？遇到问题是否会通过 git bisect 定位？

**改进方向**：日常开发中保持小步提交（每个功能点 1 个 commit），即使最终 squash merge 到主分支，你的开发分支历史也能在面试中展示。

---

### 🟡 P2 — 其他工程问题

1. **全局可变状态**：`briefer.py` 的 `_shown_games_cache` 模块级变量——并发场景下是竞态条件
2. **`sys.path.insert` hack**：`taptap_resolver.py` 和所有测试文件都手动改了 path——应该用 `pip install -e .` 或正确的包结构
3. **延迟导入打破循环依赖**：`briefer.py` 和 `render.py` 在函数内部 `from X import Y`——这是设计层面的坏味道，说明模块边界没画对
4. **硬编码魔法数字**：`max_workers=8`、`[:12]` 切片、`top_n=7`——分散在代码各处，没有统一的配置入口
5. **`print` 做日志**：全项目用 `print(f"[WARN] ...", file=sys.stderr)`，没有用 `logging` 模块——没有日志级别、没有格式化、无法重定向到文件
6. **`MONITORED_HIGH_PRIORITY` 空集合**：`differ.py` 中声明但从未填充——死功能
7. **`_keyword_in_text()` 无单词边界检查**：`track_filter.py` 中子串匹配——游戏名包含 "TD" 作为子串（如 "WTD"）会被误分类
8. **`taptap_resolver.py` 无 `try/finally`**：`sync_playwright()` 上下文未包裹——异常时可能泄漏浏览器资源

---

## Part 3: 面试追问清单

如果这是面试，我会追问以下问题：

> **Q1**: "你的 Differ 算法里有一个 `MONITORED_HIGH_PRIORITY` 集合，目前是空的。它应该从哪里读取数据？为什么还没实现？"
> → 考察你是否意识到 feature flag / 未完成功能的管理

> **Q2**: "如果 LLM API（DeepSeek）挂了，你的日报会怎样？有没有降级策略？"
> → 考察容灾思考

> **Q3**: "你的去重用的是 SQLite `INSERT OR IGNORE`，如果两个 scraper 同时写入同一条数据会怎样？"
> → 考察并发理解（WAL 模式下只有写锁等待，不会丢数据，但你是否知道这一点？）

> **Q4**: "你如何验证重构（比如把 briefer 拆成 6 个模块）没有引入 bug？"
> → 考察你是否依赖测试还是靠"跑一遍看看"

> **Q5**: "这个项目跑在 Windows 上，但你的 shell 命令用 Bash。为什么选择 Windows？有没有考虑过 Docker 化？"
> → 考察部署和环境管理的思考

---

## 总结评分卡

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐ | 分层合理，AI/非AI分离好，但模块间有循环依赖 |
| 代码质量 | ⭐⭐⭐ | 类型注解有但不到位，上帝类未拆分，魔法数字散落 |
| 错误处理 | ⭐⭐ | **最大短板** — 35%异常静默吞掉，生产事故隐患 |
| 测试 | ⭐⭐⭐ | 核心算法覆盖好，但缺少集成测试和 CI |
| 文档 | ⭐⭐⭐⭐ | CLAUDE.md 写得很好（但更像是 AI 助手的速查表而非给人看的架构文档） |
| 工程成熟度 | ⭐⭐ | 无 CI/CD、无 linter、git 历史不可读、print 代替 logging |
| 业务深度 | ⭐⭐⭐⭐ | Prompt 设计、反馈闭环、审计门禁体现了对问题域的深入思考 |

**优先改进路线**：消除空 `except: pass` → 引入 `logging` 替代 `print` → 给核心数据结构加 `TypedDict` → 搭建 pytest + GitHub Actions。这四件事做完，项目会提升一个档次。
