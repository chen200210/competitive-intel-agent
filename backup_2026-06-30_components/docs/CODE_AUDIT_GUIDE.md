# 代码审计指南

> 基于 2026-06-23 架构升级中发现的系统性问题的审计框架。每次大改后跑一轮，避免同类问题复发。

---

## 1. 死代码清理

### 为什么重要

架构迭代过程中函数被弃用但未删除，导致：
- 后续开发者误以为某个函数还在用，改了却不见效果
- 函数体内的 bug（如这次 `_compact_cross` return 后残留 `_load_reported_taptap` 函数体）无人发现

### 审计方法

```bash
# 列出 briefer.py 中所有顶级函数
grep -n "^def " src/agents/briefer.py

# 逐个 grep 调用方，确认每个函数至少被调用一次
grep -rn "_compact_taptap" src/
grep -rn "_compact_steam" src/
grep -rn "_compact_changes" src/
grep -rn "_compact_cross" src/
```

### 当前已知死代码 (2026-06-25 已清理)

| 文件 | 函数 | 原因 |
|------|------|------|
| `briefer.py` | `_compact_taptap()` | 卡片 JSON 不再由 AI 拼装 |
| `briefer.py` | `_compact_steam()` | 同上 |
| `briefer.py` | `_compact_changes()` | 同上 |
| `briefer.py` | `_compact_cross()` | 同上 |
| `sqlite.py` | `get_new_game_names_by_date()` | 被 `_yesterday_shown_games()` 替代 (2026-06-25 已删除) |

### 额外检查

```bash
# 检查已删除 Agent 的 prompt 文件是否残留
ls prompts/analyst.yaml prompts/researcher.yaml prompts/verifier.yaml \
   prompts/design_analyst.yaml prompts/overview_scanner.yaml 2>/dev/null

# 检查已删除 Agent 的 .py 文件是否残留
ls src/agents/analyst.py src/agents/researcher.py src/agents/verifier.py \
   src/agents/design_analyst.py src/agents/overview_scanner.py 2>/dev/null
```

git status 已确认上述文件标记为 D (deleted)，清理完成。

---

## 2. 异常处理审计

### 核心原则

> 只有两种异常可以吞：**重试也没用的** 和 **失败不影响核心输出的**。
> 其余必须至少打日志。

### 当前需审查的 `except: pass` 模式

```bash
# 搜索所有吞异常的位置
grep -rn "except Exception:" --include="*.py" src/ tools/ | grep -v "# "
grep -rn "except:" --include="*.py" src/ tools/
```

### 按风险分级

**高风险**（数据丢失 / 静默降级）:
- `briefer.py` ~line 169 — DB `upsert_analysis_report` 失败被吞 → 卡片推送的是旧数据
- `steam_ports.py:_sync_to_db` ~line 370 — DB 写入失败被吞 → Steam 端口数据丢失
- `news_feeds.py:_sync_to_db` ~line 701 — 同上

建议：至少 `print(f"[ERROR] DB sync failed: {e}", file=sys.stderr)` 并返回错误计数。

**中风险**（辅助功能失效不影响主流程）:
- `briefer.py:_get_taptap_urls` — 获取链接失败时静默降级为无链接，可接受但建议打 warn 日志
- `briefer.py:_load_reported_*` — 去重查 DB 失败静默降级为不去重，可接受但建议打 warn 日志
- `render.py:_match_new_game` — 2026-06-25 已修复: `except Exception` → `except ImportError` + `print([WARN], stderr)`。与 `_get_taptap_urls` 的错误处理模式对齐。

**低风险**（审计日志 / 缓存）:
- `base.py` ~line 470 — audit logging 失败 → 审计链断裂，但 Agent 主流程不受影响。建议改为 `logging.warning`

### 已知修复记录

| 日期 | 位置 | 问题 | 修复 |
|------|------|------|------|
| 2026-06-25 | `render.py:_match_new_game` | 裸 `except Exception` 静默吞所有错误 | 收敛为 `except ImportError` + stderr warn |
| 2026-06-25 | `sqlite.py` | `get_new_game_names_by_date` 死代码(0调用者) | 删除 |
| 2026-06-23 | `briefer.py` | `_compact_*` 系列函数残留 | 删除 |

---

## 3. 常量一致性

### 问题

同一概念在不同文件里各自定义关键词列表，改一处漏一处。

### 当前重复定义

| 概念 | 定义位置 | 内容 |
|------|---------|------|
| 赛道关键词 | `track_filter.py:DEFAULT_TRACK_KEYWORDS` | 塔防/肉鸽/割草 + 英文 |
| 赛道关键词 | `briefer.py:_score_news_item > track_kw` | 同上（硬编码） |
| 赛道关键词 | `briefer.py:_filter_track_changes > track_keywords` | 同上（硬编码） |
| 新闻屏蔽词 | `briefer.py:_compact_news > news_block_keywords` | AirPods/iPhone/NBA/… |
| 新闻屏蔽词 | `audit.py:NON_GAME_KEYWORDS` | 相似但有差异 |
| B站屏蔽词 | `briefer.py:_compact_news > bilibili_block_keywords` | 折扣/电竞/CS2/… |
| 游戏媒体白名单 | `briefer.py:_compact_news > game_media` | 游侠/17173/3DM/… |
| 游戏媒体白名单 | `news_feeds.py` — 四组 scraping URLs | 各自硬编码 |

### 修复方案

```
competitor_list.yaml
├── track_config
│   ├── genres: [塔防, 肉鸽, 割草, ...]
│   └── ignored_categories: [女性向, 二次元, ...]
├── news_config          ← 新增
│   ├── block_keywords: [AirPods, iPhone, ...]
│   ├── bilibili_block_keywords: [史低, 电竞, ...]
│   └── source_weights: {游戏陀螺: 0.5, 3DM: 0.3, ...}
└── game_media: [游侠, 17173, ...]  ← 新增
```

所有模块从 YAML 单一来源读取。

### 审计命令

```bash
# 搜索硬编码关键词列表
grep -rn "\[.*塔防.*\]" --include="*.py" src/
grep -rn "AirPods\|iPhone\|NBA\|世界杯" --include="*.py" src/
```

---

## 4. 类型安全

### 问题

`dict[str, Any]` 贯穿全链路 — scraper 产出 → DB 存储 → briefer 读取。字段名拼写错误不会在编译期暴露，运行时默默跳过或返回空值。

### 关键数据流及字段

```
rankings:
  date, platform, chart_type, rank, bundle_id, game_name, developer

changes:
  date, platform, chart_type, bundle_id, game_name, today_rank,
  yesterday_rank, rank_change, change_type, attention_score

steam_port_games:
  date, game_name, steam_url, has_mobile_version, track_relevant

taptap_new_games:
  date, game_name, downloads, rating, tags, taptap_url, track_relevant

market_news:
  date, headline, source, url, track_relevant, publish_date
```

### 建议

至少给这 5 个核心表行结构定义 Pydantic model：

```python
# src/schemas.py (新建)
from pydantic import BaseModel

class RankingRow(BaseModel):
    date: str
    platform: str
    chart_type: str
    rank: int
    bundle_id: str
    game_name: str
    developer: str | None = None

class ChangeRow(BaseModel):
    date: str
    platform: str
    chart_type: str
    bundle_id: str
    game_name: str
    today_rank: int | None = None
    yesterday_rank: int | None = None
    rank_change: int | None = None
    change_type: str
    attention_score: float = 0.0
```

不强制全链路使用（改造成本高），但 **briefer 读取 DB 后立即 validate**，让字段缺失在最早节点暴露。

### 审计命令

```bash
# 搜索 dict[str, Any] 使用
grep -rn "dict\[str, Any\]" --include="*.py" src/
# 搜索裸 dict get 调用（可能拼错字段名）
grep -rn "\.get(" --include="*.py" src/agents/
```

---

## 5. 测试覆盖

### 现状

只有 `track_filter.py` 有 `--test` 自测（18 case，全部通过）。

### 优先补测清单

**`_build_new_games_md()` — 新游板块生成** (briefer.py):

| Case | 输入 | 期望 |
|------|------|------|
| 纯 Steam | steam=[A,B], tap=[], unr=[] | A [Steam 移植], B [Steam 移植] |
| 纯 TapTap | steam=[], tap=[C,D], unr=[] | C — tags, D — tags |
| 重叠 | steam=[A], tap=[A,B] | A [Steam] — tags, B — tags |
| 重叠去重 | steam=[A], tap=[A] | A 只出现一次 |
| 全部为空 | steam=[], tap=[], unr=[] | 三条"无新增"提示 |
| Steam 带 URL | steam=[A有url], tap=[] | → [Steam 主页](url) |
| Steam 无 URL | steam=[A无url], tap=[] | 只有游戏名，无链接 |

**`_compact_news()` — 新闻过滤+打分+选拔** (briefer.py):

| Case | 输入 | 期望 |
|------|------|------|
| 全 track_relevant | 10条 track=True | 返回 ≤7，track 优先 |
| 全非 track | 10条 track=False | 返回 ≤7，按来源权威排 |
| B站超 2 条 | 5条 bilibili track=True | 最多 2 条 B站 |
| 无 publish_date | 非B站新闻无日期 | 被过滤掉 |
| 过期新闻 | publish_date=8天前 | 被过滤掉 |
| 来源多样性 | 4来源各3条 | 每个来源至少 1 条 |
| 内容去重 | 同一游戏出现 3 次 | 最多 2 次 |
| 不足 7 条 | 只有 3 条合格新闻 | 返回 3 条 |

**`_select_diverse()` — 多样性选拔** (briefer.py):

| Case | 输入 | 期望 |
|------|------|------|
| 来源轮询 | 3来源各3条，选5 | 3条来源保底+2条按分填充 |
| B站上限 | B站3条+其他10条 | 最多2条B站 |

### 测试框架建议

不引入 pytest 依赖，沿用 `track_filter.py` 的 `--test` CLI 自测模式：

```python
# src/agents/briefer.py
if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(_run_tests())
```

---

## 6. 审计检查清单

每次 PR / 大改后逐项过：

- [ ] `grep -rn "except.*:" src/ tools/` → 每个 `pass` 都有理由
- [ ] `grep -rn "def _" src/agents/` → 每个私有函数都有调用方
- [ ] 关键字列表只定义一次（`competitor_list.yaml` 或 `config.py`）
- [ ] 新增 DB 列有对应 migration（`_migrate_vN`）
- [ ] `--force` 清理范围覆盖所有受影响表
- [ ] `audit.py` 的常量和 `briefer.py` 一致
- [ ] `reported_items` 的 `item_type` 覆盖所有需要去重的数据源
- [ ] 新 scraper 的 CSV 不会被 Loader 误导入 `rankings`（文件名不含排行榜关键词）
- [ ] `_build_new_games_md` 和 `_build_ranking_md` 的 markdown 在飞书实际渲染过
- [ ] `track_filter --test` 18 case 全过
- [ ] `fuzzy_match_game_name` 与 `resolve_taptap_urls` 匹配逻辑一致（共用同一函数，不重复实现）
- [ ] `_yesterday_shown_games` 的筛选逻辑与 `brief_from_db` 新游选择一致（track-relevant → top-5 fallback）
- [ ] 昨日新游 badge 的强制纳入使用 prepend 而非 append（确保不被 `[:12]` 丢弃）
- [ ] `except Exception` 只用于重试无意义或非核心路径，其余有 warn 日志

---

## 7. 快速审计命令

```bash
# 1. 死代码
grep -rn "^def " src/agents/briefer.py | cut -d' ' -f2 | cut -d'(' -f1 | while read fn; do
  count=$(grep -rn "$fn" src/ --include="*.py" | grep -v "def $fn" | wc -l)
  [ "$count" -eq 0 ] && echo "DEAD: $fn"
done

# 2. 吞异常
echo "=== bare except: ==="
grep -rn "except:" src/ tools/ --include="*.py"
echo "=== except Exception: pass ==="
grep -rn "except Exception:" src/ tools/ --include="*.py" | grep -v "raise\|log\|print\|warn"

# 3. 重复常量
echo "=== track keywords ==="
grep -rn "塔防\|肉鸽\|割草" src/ --include="*.py" | grep -v test | grep -v "#"

# 4. 类型安全
echo "=== dict[str, Any] ==="
grep -rn "dict\[str, Any\]" src/ --include="*.py" | wc -l

# 5. 测试
python -m src.pipeline.track_filter --test

# 6. 昨日新游badge — 确认fuzzy_match_game_name是匹配逻辑的唯一实现
grep -rn "re.split.*\[-—–（(］" src/ --include="*.py" | grep -v "fuzzy_match_game_name\|test"

# 7. 强制纳入 — 确认使用prepend而非append
grep -rn "sector_changes.*append\|extras.*sector_changes" src/agents/briefer.py
```
