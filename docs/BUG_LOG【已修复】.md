# Bug 日志

> 记录项目中的已知 bug，按严重度排序。最后更新：2026-06-25 (P0+P1 Calibrator + β-Fusion + code review)

---

## 🔴 未修复

（本轮全部清零）

---

## 🟢 已修复

### BUG #C1: Calibrator system prompt 占位符从未被替换（CRITICAL）

**发现**: 2026-06-25 code review Angle A | **修复日期**: 2026-06-25

`prompts/calibrator.yaml` 的 system prompt 包含 `{feedback_days}` / `{feedback_table}` / `{current_params}` 三个 Python format 占位符。但 `Agent.run()` (`base.py:153`) 只对 `user_template` 调用 `.format(**kwargs)`，system prompt 原样发送给 LLM。校准器收到的 system prompt 是字面文本 `"近 {feedback_days} 天 user_feedback 聚合数据"` — 从未见过真实反馈数据。所有校准输出基于零信号，topic_boosts / dim_weights 均为 LLM 幻觉。

**根因**: `Agent.run()` 设计上 system prompt 是静态模板，user_template 是动态模板。calibrator.yaml 把动态数据错误放在了 system 段。

**修复**: 动态数据 (`# Input` 段 + 反馈表格 + 当前参数) 从 system 移到 user_template。system 段仅保留静态指令（Role / CoT Steps / Requirements / Matched Pool / Output Format）。

### BUG #C2: 分布重试消息阈值与代码不一致（HIGH）

**发现**: 2026-06-25 code review Angle A | **修复日期**: 2026-06-25

`scorer.py` 分布重试消息告诉 AI "高于60分的最多 ≤{max_above} 条"，其中 `max_above = int(len(candidates) * 0.20)`。但 `_check_distribution()` 实际检查 `int(total * 0.30)`。AI 被误导按 20% 压分，实际检查是 30%。

**修复**: retry 消息阈值 `0.20` → `0.30`，加上注释 `# must match _check_distribution`。

### BUG #C3: topic_boosts 在分布检查之后执行，可绕过 cap（HIGH）

**发现**: 2026-06-25 code review Angle G | **修复日期**: 2026-06-25

评分流程: AI → β-fusion → 分布检查+fallback → topic_boosts。fallback 把低分条目封顶到 39，但 topic_boosts 随后 +5~+20 可把分数拉回 40+，通过质量门禁。分布强制成了可绕过建议。

**修复**: 重排为 AI → β-fusion → topic_boosts → 分布检查+fallback。分布 fallback 是最终防线。

### BUG #C4: CalibratorOutput 无值域校验（HIGH）

**发现**: 2026-06-25 code review Angle G | **修复日期**: 2026-06-25

LLM 输出的 topic_boosts / dim_weights 直接入库，无代码层校验。一个幻觉 `{"Steam移植": 500}` 或 `{"track": 200}` 可永久污染后续所有日报打分。

**修复**: `CalibratorOutput` 添加 `@model_validator`：未知 topic key 丢弃+warn，值 clamp [-20,20]，dim_weights sum≠100 重置为默认值。`ALLOWED_TOPIC_KEYS` 和 `DEFAULT_DIM_WEIGHTS` 提升为模块常量。

### BUG #C5: calibration_params.version 无 UNIQUE + 非原子插入（MEDIUM）

**发现**: 2026-06-25 code review Angle B | **修复日期**: 2026-06-25

`get_next_calibration_version()` 和 `insert_calibration_params()` 分属两个事务。并发校准可产生重复 version，破坏版本单调性。

**修复**: DDL 加 `UNIQUE` 约束。`insert_calibration_params` 改用子查询 `(SELECT COALESCE(MAX(version),0)+1 FROM calibration_params)` 在单事务内原子分配版本号。

### BUG #C6: user_feedback 表无 date 索引（MEDIUM）

**发现**: 2026-06-25 code review Angle F | **修复日期**: 2026-06-25

`user_feedback` 表按 date / target_date 过滤的查询走全表扫描。表无界增长（每次按钮点击一行），随反馈积累查询持续劣化。

**修复**: 添加 `idx_user_feedback_date` 和 `idx_user_feedback_target_date` 两个索引。

### BUG #C7: --calibrate 与其他 flag 冲突时静默忽略（LOW）

**发现**: 2026-06-25 code review Angle B | **修复日期**: 2026-06-25

`runner.py --calibrate --scrape --push oc_xxx` 只执行校准，scrape 和 push 被跳过且无任何提示。

**修复**: 当 --calibrate 与 --scrape / --push / --skip 同时使用时，打印 `[WARN]`。

### BUG #C8: AI 标签产出后从未持久化 → Calibrator 无法做标签×反馈分析（HIGH）

**发现**: 2026-06-25 code review Angles C+D | **修复日期**: 2026-06-25

P2 Matched Verdict Pool 让 AI 输出 `pos_label`/`neg_label`，但标签只在内存中流转（scorer → briefer → 函数返回即丢弃），从未写入任何 DB 表。Calibrator 无法做 "用户是否偏好 `track_direct` 标签的新闻" 这种分析——整个 label taxonomy 的目的被架空了。

**修复**: 三步——(1) `market_news` 表加 `pos_label`/`neg_label` 列 (`_migrate_v11`)；(2) `brief()` 中调用 `update_market_news_labels()` 持久化标签；(3) `calibrator._aggregate_feedback()` SQL 读回标签并在反馈表格中展示。

### BUG #C9: Fallback 路径遗漏 pos_label/neg_label key（MEDIUM）

**发现**: 2026-06-25 code review Angle A | **修复日期**: 2026-06-25

AI 全部重试失败时的 fallback 代码对 item 调 `setdefault("ai_summary")`/`setdefault("ai_score")`/`setdefault("ai_verdict")`，但遗漏了 `pos_label` 和 `neg_label`。fallback item 的 key 集合与正常 item 不一致。

**修复**: fallback 路径补上 `item.setdefault("pos_label", "")` 和 `item.setdefault("neg_label", "")`。

### BUG #C10: LLM 输出的 label 值无代码层校验（MEDIUM）

**发现**: 2026-06-25 code review Angle A | **修复日期**: 2026-06-25

AI 输出的 `pos_label`/`neg_label` 直接入库，无任何校验。一个幻觉标签（如 `"interesting"`）可通过整个管线持久化到 `market_news`，污染 Calibrator 的统计分析。

**修复**: `scorer.py` 定义 `_VALID_POS_LABELS` 和 `_VALID_NEG_LABELS` 常量（与 `summarizer.yaml` Matched Verdict Pool 同步）。提取标签后即时校验，未知值丢弃为空字符串。

### BUG #C11: summarizer.yaml "0-1个" 与 "必须生成所有字段" 矛盾（LOW）

**发现**: 2026-06-25 code review Angle A | **修复日期**: 2026-06-25

Prompt 先告诉 AI "选择 0-1 个标签"（暗示可选），20行后又"必须生成 summary、score、pos_label、neg_label、verdict"（暗示必填）。AI 可能理解为"不匹配时省略 JSON key"而非"填空字符串"。

**修复**: 统一为 "选择 1 个 → 填入 pos_label（无匹配时填空字符串 ""）"。新增 label-score 一致性要求：`track_direct` 应对应高分，`off_track`/`no_gameplay` 应对应低分。

### BUG #C12: briefer.py 死代码残留（LOW）

**发现**: 2026-06-25 code review Angle D | **修复日期**: 2026-06-25

Briefer 的 LLM 调用早已替换为代码直拼 markdown，但 `build_agent()`、`MarketOutput`、`from pydantic import BaseModel`、`from src.agents.base import Agent` 四个死代码残留未清理。`test_briefer_card.py` 仍导入并测试 `build_agent`。

**修复**: 移除四个死代码 + 测试中的相关断言。`briefer.py` 净减 20 行。

### BUG #C13: calibrator.py 参数加载函数 60% 重复（LOW）

**发现**: 2026-06-25 code review Angle D | **修复日期**: 2026-06-25

`_load_current_params` 和 `load_calibration_for_scorer` 各自实现"导入 get_db → 调用 get_latest_calibration_params() → 提取字段 → 兜底默认值"。唯一差异是前者多返回 `summary`，后者有 `mark_calibration_applied` 副作用。

**修复**: 提取共享函数 `_load_params_base()`（纯读，无副作用）。两个函数各自在 base 之上叠加自己的语义。

### BUG #C14: 反馈查询 2-5 个关联子查询 → 1 个 LEFT JOIN（LOW）

**发现**: 2026-06-25 code review Angles C+F | **修复日期**: 2026-06-25

`_aggregate_feedback()`(5 个子查询) 和 `build_feedback_summary()`(2 个子查询) 对 `market_news` 的每个字段做独立关联子查询，每行反馈触发多次冗余 index seek。

**修复**: 改为 `LEFT JOIN (SELECT ... FROM market_news GROUP BY url)` 单次 JOIN。`market_news` 有 `UNIQUE(date, url)`，子查询 `GROUP BY url` 取任意一行即可。

---

## 🟢 历史已修复

### BUG #F1: 用户反馈计数器被每日清理重置

**修复日期**: 2026-06-24 | `_build_feedback_summary()` 改为从 `user_feedback` 聚合，计数器更新降级为 best-effort

### BUG #F2: PG.biz 新闻被 game_media 白名单过滤

**修复日期**: 2026-06-24 | `game_media` 列表加 `"pocketgamer"`

### BUG #F3: PG.biz scraper 的 mark_reported 阻止同次 briefer 读取

**修复日期**: 2026-06-24 | 删除 scraper `_sync_to_db` 中的 `mark_reported` 调用

### BUG #F4: 中文新闻大面积缺 publish_date 被新鲜度门禁过滤

**修复日期**: 2026-06-24 | 无 `publish_date` 的条目用 `target_date` 兜底

### BUG #F5: `_handle_deep_research` 引用不存在的 researcher 模块，用户触发即崩溃

**修复日期**: 2026-06-24 | 删除 `_handle_deep_research`，从 INTENT_PROMPT + handlers + 欢迎消息中移除 `deep_research` intent

### BUG #F6: `_filter_track_changes` 三处各自实现（runner / briefer / taptap_resolver）

**修复日期**: 2026-06-24 | 统一到 `track_filter.filter_track_changes()`

### BUG #F7: `_classify_day` 两处重复 + `total=len(changes)` 导致 day_type 恒为 "volatile"

**修复日期**: 2026-06-24 | briefer 改用 differ 的 `classify_day`，`total` 从 rankings 表 COUNT 而非 `len(changes)`。5 个 sum 推导式合并为单次遍历。

### BUG #F8: `_load_track_config()` 无缓存，每次 pipeline ~135 次 YAML 读盘

**修复日期**: 2026-06-24 | 加 `@functools.lru_cache(maxsize=1)`

### BUG #F9: 6 张废弃 DB 表 + 对应 CRUD 方法 + `_migrate_v3` 死代码

**修复日期**: 2026-06-24 | 删除 DDL + CRUD + 清理迁移和 db_query 示例

### BUG #F10: `analysis_reports` 3 个废弃列 + `upsert_analysis_report` 8 参数简化

**修复日期**: 2026-06-24 | DDL 删 `research_ids/report_json/design_analysis_json`，签名从 8 → 5 参数

### BUG #F11: `increment_news_feedback` 竞态条件

**修复日期**: 2026-06-24 (第三轮) | 去重检查（SELECT）和插入（INSERT）合并到同一事务。用 `INSERT OR IGNORE` + `cursor.rowcount` 原子化判断是否写入成功。删除了 `insert_feedback` 的间接调用（该方法变为死代码）。

### BUG #F12: B站来源判断使用裸字符串包含

**修复日期**: 2026-06-24 (第三轮) | `"bilibili" in src` → `src == "bilibili"` 精确匹配。`_is_overseas` 同步改为精确匹配 `== "pocketgamer.biz"`。

### BUG #F13: 反馈回复依赖 ephemeral 数据

**修复日期**: 2026-06-24 (第二轮) | `bot.py:165-169` 已有 `if headline else` fallback — 查不到 headline 时省略「」，改为 "感谢 xxx 的反馈，接下来将会推荐更多类似新闻 🙏"。

### BUG #F14: scraper 层去重与 briefer 层去重不协调

**修复日期**: 2026-06-24 (第三轮) | `insert_market_news_deduped()` 从全表永久去重 (`SELECT DISTINCT url FROM market_news`) 改为同日去重 (`WHERE date = ?`)。跨日去重统一由 `reported_items` 的 TTL 机制处理。

### BUG #F15: 6 处静默异常吞没

**修复日期**: 2026-06-24 (第三轮) | 审计 #7 的 6 处 `except Exception: pass` 全部加上 `print(f"[WARN] ...", file=sys.stderr)`：DB upsert / TapTap URL 解析 / 游戏详情补充 / track_filter 分类 / fatigue 历史读取 / mark_reported 写入。

### BUG #F16: Windows emoji 导致中文新闻入库崩溃

**修复日期**: 2026-06-24 (第三轮) | `sqlite.py`、`base.py`、`news_feeds.py`、`steam_ports.py`、`taptap_new_games.py`、`pocketgamer_biz.py` 中 `print()` 的 emoji（📊⚠️✅🔴ℹ️）全部替换为 ASCII 标签。根因: Windows GBK 终端 `UnicodeEncodeError` → `insert_market_news_deduped` 抛异常 → 中文 120 条新闻静默不入库。

### BUG #F17: `increment_news_feedback` 引用了不存在的变量 `news_url`

**修复日期**: 2026-06-24 (第三轮) | `news_url` → `url`。回退检查: 重构时误将参数名 `url` 写成了 `news_url`，所有用户反馈点击静默失败（NameError → caught → return "error"）。

### BUG #F18: 审计 10 项 (audit-2026-06-24-r3)

**修复日期**: 2026-06-24 (第三轮) | 10 项全部修复 — score_news 批量 ID 错误/null 防御/死 import/正则重编译/串行查询；base.py CSV 日期/Bundle ID float zero/EXTRA_COLUMNS 稀疏/date 参数未使用；token_utils 共享提取消除重复逻辑。
