# 代码审查报告 — 2026-06-25 (面试评审发现)

> 审查来源：[REVIEW-2026-06-25-INTERVIEW.md](REVIEW-2026-06-25-INTERVIEW.md) — 秋招面试官视角全项目评审
> 审查方式：全项目源码扫描 + 架构分析
> 本文档仅收录**可修复的 Bug 和工程缺陷**，不含设计讨论

---

## 🔴 确认 Bug（需修复）

### BUG #E1: `except Exception: pass` 系统性静默吞异常（CRITICAL） ✅ **已修复 2026-06-26**

**范围**: 全项目 ~55 处 `except Exception:` 裸块 | **严重度**: CRITICAL | **状态**: 已修复

**修复内容**:
- 全项目 22 个 Python 文件共 ~63 处 `except Exception:` 改为 `except Exception as e:` + `print(f"  [WARN] ...: {e}", file=sys.stderr)`
- 覆盖关键路径：DB 写入（sqlite.py）、标签持久化（briefer.py:134）、去重读写（dedup.py 8 处）、AI 打分兜底（scorer.py）、管道监控记录（runner.py:224）
- 所有 scraper 文件（bilibili_creators、diandian_batch、news_feeds、steam_ports、taptap_new_games）同步修复
- 当前全项目 `src/` + `tools/` 目录 0 处裸 `except Exception:`，124 处带日志的 `except Exception as e:`

---

### BUG #E2: `taptap_resolver.py` Playwright 资源泄漏风险（HIGH） ✅ **已修复 2026-06-26**

**文件**: `src/tools/taptap_resolver.py` | **严重度**: HIGH | **状态**: ✅ 已修复

**修复内容**:
- `context.close()` 移入 `try/finally` 块，确保 `page.goto()` 或 click 操作抛异常时浏览器 context 仍然被关闭
- `sync_playwright()` 上下文管理器作为第二层安全网（driver 进程级别清理），但 `finally` 确保 context 级别优雅关闭
- 详见 `CLAUDE.md` #13

```python
# 修复后
context = p.chromium.launch_persistent_context(...)
try:
    page = context.new_page()
    page.goto(...)
    # ... click/extract logic ...
finally:
    context.close()  # 异常安全
```

---

### BUG #E3: `_keyword_in_text()` 无单词边界检查导致误分类（MEDIUM） ✅ **已修复 2026-06-26**

**文件**: `src/pipeline/track_filter.py` | **严重度**: MEDIUM | **状态**: ✅ 已修复

**原始问题**: `_keyword_in_text()` 纯子串匹配导致 "TD" 误匹配 "WTD"、"GTD" 等。

**修复内容**:
- 纯 ASCII 关键词（`kw.isascii() and kw.isalpha()`）使用 `\b` 单词边界 + `re.ASCII` flag
- **关键细节**: 必须使用 `re.ASCII`——Python 默认 Unicode 模式将 CJK 字符视为 `\w`，导致 `\bTD\b` 无法匹配 "塔防TD手游"（防(\w)→T(\w) 无边界）。`calibrator.py:_match_topic()` 已正确处理此问题（`flags=re.ASCII` + 注释说明）
- 混合关键词（如 "幸存者like"）回退到子串匹配，避免 `\b` 在 CJK/ASCII 边界失效
- 详见 `CLAUDE.md` #13

---

### BUG #E4: `_shown_games_cache` 模块级全局可变状态 — 竞态条件（MEDIUM） ✅ 已修复 (2026-06-26)

**文件**: `src/agents/briefer.py` | **严重度**: MEDIUM | **状态**: ✅ 已修复

`_shown_games_cache` 模块级可变字典已在 briefer 拆分重构中移除，缓存逻辑整合到 `Database` 调用中，不再使用模块级全局状态。

---

### BUG #E5: 延迟导入打破循环依赖 — 架构坏味道（MEDIUM） ✅ **已修复 2026-06-26**

**文件**: 全项目 `src/` | **严重度**: MEDIUM | **状态**: ✅ 已修复

**原始问题**: 全项目 ~114 处函数内 `from src.xxx import yyy`，审计文档原以为是"打破循环依赖"。实际分析发现 briefer 拆分后**已无跨模块循环依赖**——这些延迟导入是拆分时从原 1263 行单文件带过来的惯性写法，不是必要技术手段。

**修复内容**:
- `briefer.py` — 10 处 render/market_pipeline/scorer/dedup/differ/track_filter/taptap_resolver 提至顶层
- `render.py` — 4 处 enrichment/card_builder/pusher/url_utils 提至顶层
- `scorer.py` — 2 处 token_utils/source_constants 提至顶层
- `market_pipeline.py` — 2 处 dedup/enrichment 提至顶层
- `dedup.py` — 1 处 token_utils 提至顶层
- `enrichment.py` — 1 处 image_fetch 提至顶层
- `hot_tracker.py` — 2 处 web_search/base 提至顶层
- **合计 ~22 处延迟导入提至模块顶层**

**保留内联的场景**（仅 3 种合法理由）:
1. `get_db()` / `settings` — 惰性加载避免 import 时触发 DB 连接/.env 读取
2. `try/except ImportError` / `try/except Exception` — 优雅降级（calibrator、classify_game、taptap_resolver 等）
3. `if __name__ == "__main__"` 块 — CLI 入口专用导入

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
| CRITICAL | 0 (E1 已修复) | — |
| HIGH | 0 (E2 已修复) | — |
| MEDIUM | 0 (E3, E5 已修复) | — |
| LOW | 4 | E6, E7, E8, E9 |
| **待修复** | **4** | |
| **已修复** | **5** | E1, E2, E3, E4, E5 (all 2026-06-26) |

---

## 与非 Bug 改进的界限

以下从评审中识别的问题**不属于 Bug**，不在本审计文档修复范围：

- **`sqlite.py` 上帝类拆分** → 架构重构，需单独规划。**→ [决策记录：暂不拆分](#决策记录-sqlitepy-上帝类暂不拆分)**
- **`dict[str, Any]` → TypedDict 迁移** → ✅ Layer 1 已完成 (2026-06-26): `src/types.py` 11 个 TypedDict + 10 个文件函数签名更新。Layer 2（dataclass DTO）择机推进。
- **pytest 迁移 + CI 搭建** → 基础设施改进，非代码缺陷
- **Git 历史整理** → 流程改进，非代码缺陷
- **集成测试补全** → 测试覆盖率改进，非缺陷

---

## 决策记录: `sqlite.py` 上帝类暂不拆分

> **决策日期**: 2026-06-26
> **决策**: 降级为 P3（nice-to-have），不纳入当前迭代
> **触发条件**: 文件膨胀到 2000+ 行、或需要单独测试某个 repo、或新增数据表超过 20 张

### 评估

| 维度 | 分析 |
|------|------|
| **当前规模** | 1343 行，对于 DAO 层不算大。拆成 6 个 repo 后每个 ~220 行，净减代码量有限 |
| **代码性质** | 方法基本都是 5-15 行的 thin wrapper，彼此不交叉调用，不存在"类太大导致的理解困难" |
| **炸半径** | 50+ 调用点全得改 import。当前 `get_db().some_method()` 模式足够清晰，从未因此产生过 bug |
| **对比 Briefer 拆分** | Briefer 拆分有实效——消除了循环依赖、分离了 AI 层和规则层。DAO 层不存在这类问题，所有方法都是独立数据访问 |
| **机会成本** | 有更值得做的事：热点追踪增强 → 全自动 cron → Docker 化。拆分 DAO 不阻塞任何功能，也不解决现存的正确性或性能问题 |

### 将来方案（如果触发条件满足）

采用 **Facade 模式**，零破坏迁移：

```python
class Database:
    def __init__(self):
        self._conn = ...
        self.rankings = _RankingRepo(self._conn)   # 具体 CRUD 搬进去
        self.news = _NewsRepo(self._conn)          # 共享连接
        self.feedback = _FeedbackRepo(self._conn)

# 调用方渐进迁移：db.insert_rankings() → db.rankings.insert()
# 整个过程旧接口保留，不强制一次性迁移
```
