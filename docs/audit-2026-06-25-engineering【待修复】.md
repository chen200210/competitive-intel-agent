# 代码审查报告 — 2026-06-25 (面试评审发现)

> 审查来源：[REVIEW-2026-06-25-INTERVIEW.md](REVIEW-2026-06-25-INTERVIEW.md) — 秋招面试官视角全项目评审
> 审查方式：全项目源码扫描 + 架构分析
> 本文档仅收录**可修复的 Bug 和工程缺陷**，不含设计讨论

---

## 🔴 确认 Bug（需修复）

### BUG #E1: `except Exception: pass` 系统性静默吞异常（CRITICAL）

**范围**: 全项目 ~35% 的 `except` 块为空 `pass` | **严重度**: CRITICAL

以下关键路径的异常被静默吞掉，故障完全不可观测：

| 文件:行号 | 场景 | 后果 |
|-----------|------|------|
| `runner.py:224` | `insert_pipeline_run()` 写入失败 | 整条运行记录丢失，监控系统看不到任何异常 |
| `runner.py:175` | 获取 B 站视频列表失败 | audit 拿不到完整 context，卡片质量检查不完整 |
| `briefer.py:134` | 标签持久化失败 | pos_label/neg_label 未写入 DB，Calibrator 无信号可分析 |
| `taptap_resolver.py:49` | 数据库缓存查询失败 | 每次都重新走 Playwright 浏览器解析，无缓存加速 |
| `taptap_resolver.py:106` | Playwright 页面操作失败 | 调用方拿到空结果但不知道是"没找到"还是"出错了" |
| `dedup.py` 5 处 | 去重记录写入/查询失败 | 重复内容可能被推送多次，用户看到重复日报 |
| `scorer.py:57` | `load_scoring_config()` YAML 加载失败 | 默默用硬编码默认值，配置变更不生效且无告警 |
| `hot_tracker.py` 4 处 | 热点关键词收集/搜索失败 | 日报缺热点板块，用户看不到但系统不自知 |

**修复方向**:
1. 区分三类异常处理策略：`fatal`（抛出任其传播）、`degraded`（`logging.warning` + 继续）、`best-effort`（仅限注释明确说明的场景）
2. 关键路径（`insert_pipeline_run`、标签持久化、去重写入）禁止静默——至少 `logging.error`
3. `best-effort` 场景必须在注释中说明"为什么失败不影响核心输出"

---

### BUG #E2: `taptap_resolver.py` Playwright 资源泄漏风险（HIGH）

**文件**: `src/tools/taptap_resolver.py` | **严重度**: HIGH

`sync_playwright()` 上下文管理器未包裹在 `try/finally` 中。如果 `page.goto()` 或 `page.content()` 抛异常，浏览器进程不会被清理，每次泄漏一个 Chromium 实例。

```python
# 当前代码（简化）
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(url)          # 如果这里抛异常...
    html = page.content()   # 或者这里...
    # browser.close() 永远不会被调用
```

**修复方向**: 内层加 `try/finally` 确保 `browser.close()` 必定执行，或使用 Playwright 的 `browser = await p.chromium.launch()` 后 `async with browser:` 模式。

---

### BUG #E3: `_keyword_in_text()` 无单词边界检查导致误分类（MEDIUM）

**文件**: `src/pipeline/track_filter.py` | **严重度**: MEDIUM

`_keyword_in_text()` 做的是纯子串匹配。游戏名或描述中包含 "TD" 作为子串（如 "WTD"、"TDK"、"STD"）会被误分类为塔防赛道。

```python
# 当前逻辑
if "TD" in text.upper():  # 匹配 "WTD Studios" 中的 TD
    return True
```

**修复方向**: 对缩写类关键词（TD、TDs）加单词边界检查（`\bTD\b`），或至少检查前后字符是否为空格/标点/字符串边界。

---

### BUG #E4: `_shown_games_cache` 模块级全局可变状态 — 竞态条件（MEDIUM）

**文件**: `src/agents/briefer.py` | **严重度**: MEDIUM

```python
_shown_games_cache: dict[str, set[str]] = {}
```

模块级可变字典在多线程环境下（runner 使用 `ThreadPoolExecutor`）存在竞态条件。虽然当前 `brief_from_db` 在单线程中调用，但一旦未来并行化 briefer 调用，缓存会被并发读写破坏。

**修复方向**: 将缓存移到 `Database` 类中，或用 `threading.local()` 做线程隔离，或直接用 `lru_cache` 装饰器替代手动缓存。

---

### BUG #E5: 延迟导入打破循环依赖 — 架构坏味道（MEDIUM）

**文件**: `src/agents/briefer.py` + `src/agents/render.py` | **严重度**: MEDIUM

```python
# briefer.py 中
def brief_from_db(...):
    from src.agents.render import build_market_elements  # 延迟导入

# render.py 中
def _match_new_game(...):
    from src.agents.scorer import ...  # 延迟导入
```

函数内部的 `import` 是为了打破模块间的循环依赖。这表明模块边界划分有问题——`render` 不应该依赖 `scorer`，`briefer` 和 `render` 之间应该有更清晰的单向依赖。

**修复方向**:
- `_match_new_game` 依赖的 `fuzzy_match_game_name` 已经提取到 `src/tools/taptap_resolver.py`，检查是否还有残留依赖
- 考虑引入 `src/agents/shared.py` 存放共享常量和轻量工具函数，打破循环

---

## 🟡 低严重度

### BUG #E6: `MONITORED_HIGH_PRIORITY` 空集合 — 死功能（LOW）

**文件**: `src/pipeline/differ.py` | **严重度**: LOW

```python
MONITORED_HIGH_PRIORITY: set[str] = set()
```

在模块级别声明为空集合，从未被填充。`compute_attention_score()` 中有监控列表奖金逻辑（`if game_name in MONITORED_HIGH_PRIORITY: bonus += 1.5`），但永远不会触发。

**修复方向**: 要么从 YAML 配置加载监控列表，要么删除相关代码。不要留一个永远不走的分支。

---

### BUG #E7: 全项目用 `print` 代替 `logging` 模块（LOW）

**文件**: 全项目 | **严重度**: LOW

所有日志输出使用 `print(f"[WARN] ...", file=sys.stderr)` 或 `print(f"[label] ...", file=sys.stderr)`。没有使用 Python 标准库 `logging` 模块。

**后果**:
- 无日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- 无法按级别过滤输出
- 无法重定向到文件
- 无法与外部日志收集系统集成

**修复方向**: 在 `src/config.py` 中配置 `logging.basicConfig()`，全局替换 `print(..., file=sys.stderr)` → `logging.warning()` / `logging.error()` / `logging.info()`。

---

### BUG #E8: `sys.path.insert` 路径 hack（LOW）

**文件**: `src/tools/taptap_resolver.py` + 所有 `tests/*.py` | **严重度**: LOW

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

手动修改 `sys.path` 是脆弱的做法——依赖文件在文件系统中的相对位置，换个目录运行就会挂。

**修复方向**: 项目根目录添加 `pyproject.toml`，用 `pip install -e .` 以可编辑模式安装，之后 `from src.xxx` 即可正常工作。

---

### BUG #E9: 硬编码魔法数字散落各处（LOW）

**文件**: 全项目 | **严重度**: LOW

| 位置 | 魔法数字 | 含义 |
|------|----------|------|
| `runner.py:30` | `max_workers=8` | 并行 scraper 上限 |
| `briefer.py` | `[:12]` | 排名表格截断条数 |
| `scorer.py` | `top_n=7` | 市场新闻精选条数 |
| `market_pipeline.py` | `7` 天 | 新鲜度门禁 |
| `dedup.py` | `30` 天 | 去重 TTL |

这些值分散在代码中，没有统一的配置入口。调整参数需要翻多个文件。

**修复方向**: 集中到 `src/config.py` 的 pydantic-settings 中，或在各模块顶部定义为命名常量（`MAX_RANKING_ROWS = 12`）。

---

## 统计

| 严重度 | 数量 | Bug 编号 |
|--------|------|----------|
| CRITICAL | 1 | E1 |
| HIGH | 1 | E2 |
| MEDIUM | 3 | E3, E4, E5 |
| LOW | 4 | E6, E7, E8, E9 |
| **合计** | **9** | |

---

## 与非 Bug 改进的界限

以下从评审中识别的问题**不属于 Bug**，不在本审计文档修复范围：
- **`sqlite.py` 上帝类拆分** → 架构重构，需单独规划
- **`dict[str, Any]` → TypedDict 迁移** → 渐进式类型改进，非缺陷
- **pytest 迁移 + CI 搭建** → 基础设施改进，非代码缺陷
- **Git 历史整理** → 流程改进，非代码缺陷
- **集成测试补全** → 测试覆盖率改进，非缺陷
