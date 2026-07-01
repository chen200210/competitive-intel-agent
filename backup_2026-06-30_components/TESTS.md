# 测试计划

> **当前状态**：P0 + P1 已实现（13 个测试文件）。P2 待实现。

## P0 — 数据正确性（必须最先写）

### 1. `test_loader.py` — Loader 数据质量

| 用例 | 输入 | 预期 |
|------|------|------|
| CSV bundle_id=0 | `bundle_id: "0"`, `应用: "翡翠经营模拟器"` | bundle_id → `fallback:翡翠经营模拟器` |
| CSV developer=0 | `开发者: "0"` | developer → NULL |
| 平台标准化 | `iOS/Android: "IOS游戏榜"` | platform → `iOS` |
| 平台标准化 | `iOS/Android: "安卓游戏榜"` | platform → `Android` |
| 日期提取 | 文件名 `ios_热门榜_20260616.csv` | date → `2026-06-16` |
| 日期提取 | 文件名 `2026-06-16_rankings.csv` | date → `2026-06-16` |
| 批量导入 | 含 100 条有效记录 + 5 条脏数据 | 100 条入库，5 条正确处理 |
| 重复导入 | 同一天同一文件导入两次 | 第二次 INSERT OR REPLACE，不报错 |
| chart_type 缺失 | CSV 无 "榜单" 列 | Loader 从文件名推断或设默认值 |
| 空开发者 | `开发者: ""` | developer → NULL |

### 2. `test_differ.py` — Differ 正确性

| 用例 | 输入 | 预期 |
|------|------|------|
| 排名上升 | 昨天 #3，今天 #2 | change_type=up, rank_change=+1 |
| 排名下降 | 昨天 #2，今天 #3 | change_type=down, rank_change=-1 |
| 新上榜 | 昨天无，今天 #50 | change_type=new_entry, yesterday_rank=NULL |
| 掉榜 | 昨天 #30，今天无 | change_type=dropped_out, today_rank=NULL |
| 排名不变 | 昨天 #5，今天 #5 | 不产生 change 记录 |
| attention_score 头部 | 新上榜直接 #7 | score≥7.0 |
| attention_score 尾部 | ↑3 (78→75) | score≤1.0 |
| attention_score breakout | ↑15 (45→30) | breakout bonus +2.0 |
| attention_score 掉榜 | 从 #5 掉榜 | score≥7.0 |
| day_type quiet | 80 款中 4 款变动，0 进出 | quiet |
| day_type normal | 80 款中 20 款变动，3 进出 | normal |
| day_type volatile | 80 款中 35 款变动，或 8+ 进出 | volatile |
| 首日数据 | 第一天导入，无昨日 | hint: "首日数据已入库" |
| 多平台混入 | iOS + Android 同一日期 | 按 platform 分组 diff |

### 3. `test_story_picker.py` — Story Picker 规则

| 用例 | 输入 | 预期 |
|------|------|------|
| 大幅跃升 | ↑15 (50→35) | story_type=big_jump |
| 大幅跃升 20+ | ↑25 (80→55) | story_type=big_jump |
| 小幅上升不触发 | ↑5 (50→45) | 不被选中 |
| 黑马突围 | new_entry 直接 #30 | story_type=black_horse |
| 黑马不触发 | new_entry #60 | 不被选中（超过 50） |
| 断崖下跌 | ↓20 (15→35) | story_type=cliff_drop |
| 掉榜断崖 | 从 #20 掉榜 | story_type=cliff_drop |
| 小跌不触发 | ↓3 (10→13) | 不被选中 |
| 持续爬升 | 连续 5 天每天 +1 | story_type=steady_climber |
| 持续 3 天不触发 | 连续 3 天上升 | 不被选中（需 ≥5 天） |
| 品类异动 | 3 款同品类同向变动 | story_type=cluster_move |
| 品类 2 款不触发 | 2 款同品类同向变动 | 不被选中（需 ≥3） |
| 去重 | 同一游戏同时命中 big_jump 和 steady_climber | 只保留优先级高的 |
| 截断 | 9 条候选 | 只输出 8 条 |
| 跨榜信号合并 | cross_chart 产出了 1 条信号 | 与单榜故事合并排序 |

---

## P1 — 输出结构正确性

### 4. `test_agent_base.py` — Agent 基类

| 用例 | 输入 | 预期 |
|------|------|------|
| JSON_ENFORCEMENT 注入 | Agent("overview_scanner") | system_prompt 末尾含 JSON_ENFORCEMENT |
| 正常 JSON 解析 | `{"key": "value"}` | 直接返回 dict |
| 带前言的 JSON | `Now I have...\n{"key": "value"}` | Layer 0 截掉前言，解析成功 |
| 缺逗号修复 | `{"a": 1\n"b": 2}` | Layer 2 插入逗号，解析成功 |
| 中文裸引号修复 | `"title": "疑"代号Nami"有新进展"` | 内部 `"` 转为 `"`/`"` |
| 合法引号不被误杀 | `"鸣潮",\n"bundle_id"` | 换行后的合法 `"` 保持原样 |
| Markdown fence | `\`\`\`json\n{"a":1}\n\`\`\`` | 剥离 fence 后解析成功 |
| 正则兜底 | `text text {"a": 1} more text` | 提取 {}-block 解析成功 |
| 完全无法解析 | `not json at all` | 返回 raw_output + parse_error |
| 无工具 Agent | Agent("analyst", tools=None) | 单轮直接输出，max_tool_rounds=1 |
| 有工具 Agent | Agent("researcher", tools=[...]) | 多轮循环直到无 tool_calls |
| _timing 完整性 | 任意 Agent.run() | 含 total_ms, llm_total_ms, tool_total_ms, rounds, tool_summary |
| _timing 最终轮 | 无工具 Agent | rounds 含 "final answer" 条目 |

### 5. `test_researcher_output.py` — Researcher 输出 schema

| 用例 | 输入 | 预期 |
|------|------|------|
| 五维度覆盖 | mock 数据（含 5 维度各 2 条） | dimensions_covered 含全部 5 个 |
| 每条 finding 含来源 | 任意 finding | sources 数组非空，含 url |
| fetch_status 字段 | 任意 source | 含 fetch_status (success/failed) |
| design_tags 字段 | gameplay/design finding | design_tags 非空 |
| in_development_signals | 含在研信息 | threat_assessment 为 high/medium/low |
| search_coverage | 任意输出 | 含 dimensions_covered, dimensions_missed, platforms_used |
| 来源可达性自检 | finding 无 fetch_status=success | 该 finding 应被移除或在 dimensions_missed 中记录 |

### 6. `test_verifier_output.py` — Verifier 输出 schema

| 用例 | 输入 | 预期 |
|------|------|------|
| 三维度评分 | 模拟 10 条 findings | 每条含 source_authority, cross_validation, causal_logic |
| 分值范围 | 任意评分 | 1-5 之间 |
| total_score 计算 | 4, 3, 4 | 3.7 (保留 1 位小数) |
| pass 判定 | total_score ≥ 3.0 | verdict = pass |
| reject 判定 | total_score < 3.0 | verdict = reject |
| cross_references | 验证搜索到佐证 | 含 url, title, relation |
| summary 汇总 | 10 条中 8 pass 2 reject | passed=8, rejected=2, average_score 正确 |

### 7. `test_briefer_card.py` — Briefer 卡片格式

| 用例 | 输入 | 预期 |
|------|------|------|
| 基础结构 | mock business + design analysis | 含 msg_type: "interactive" 和 card 对象 |
| card 含 header | 任意输出 | header.title.content 含日期 |
| card 含 elements | 任意输出 | elements 数组非空，每项有 tag |
| 无"详见调研报告" | 任意输出 | 全文搜索不出现该字样 |
| 链接真实性 | 任意输出 | 所有 URL 以 http:// 或 https:// 开头 |
| 无 ### 标题 | 任意输出 | markdown content 不含 `###` |
| 表格 ≤6 列 | volatile 日输出 | 竞争风险表列数 ≤6 |
| quiet 日省略 | day_type=quiet | 无设计洞察、竞争风险区 |
| volatile 日完整 | day_type=volatile | 所有区块都存在 |
| 链接来自输入 | 输入只有 gamelook.com.cn 的 URL | 输出只有该域名链接，无编造 |

### 8. `test_feishu_card.py` — 飞书卡片兼容性

| 用例 | 输入 | 预期 |
|------|------|------|
| header template | 任意卡片 | template 为 "blue" 或其他有效值 |
| markdown tag | elements 中的内容块 | tag 为 "markdown" |
| hr tag | 分隔线 | tag 为 "hr" |
| note tag | 底部提示 | tag 为 "note"，elements 含 plain_text |
| emoji 位置 | 任意卡片 | 🔴🟡⚪ 只出现在标题/加粗文字中，不在链接前 |
| 链接格式 | 含 URL 的行 | 格式为 `→ [文字](URL)`，无其他字符干扰 |

---

## P2 — 集成与边界（⚠️ 计划中，尚未实现）

### 9. `test_pipeline_integration.py` — Pipeline mock 全链路

| 用例 | 输入 | 预期 |
|------|------|------|
| 全链路执行顺序 | 两天各 100 条数据 | Differ→StoryPicker→CrossChart→OV→Researcher→Verifier→Analyst→Design→Briefer |
| Skip 逻辑 | 第一次跑完后，同日期再跑 | 所有步骤标记 skipped |
| Force 重跑 | `--force` | 所有步骤重新执行 |
| 首日数据 | 只有一天数据 | Differ 返回提示，Researcher 不触发 |
| Error 容错 | 某个 Agent 抛异常 | Runner 记录 error 状态，继续执行后续步骤 |
| 空 changes | 今天和昨天榜单完全一样 | Story Picker 返回 0 stories |

### 10. `test_edge_cases.py` — 边界情况

| 用例 | 输入 | 预期 |
|------|------|------|
| 空 changes | `[]` | Story Picker 返回空，Researcher 不触发 |
| 单条 change | 只有 1 条变动 | 流程正常完成 |
| 超长游戏名 | 游戏名 120 字符 | 不截断不报错 |
| 特殊字符 bundle_id | `com.test.game-v2.0_beta` | 正确处理 |
| day_type 边界 10% | volatility 恰好 0.100 | quiet |
| day_type 边界 30% | volatility 恰好 0.300 | volatile |
| 无 industry_news | Overview Scanner 搜不到新闻 | industry_news_today 为空数组 |
| 无设计 findings | Researcher 没产出 design_tags | Design Analyst 跳过 |
| 全是 reject | Verifier 全部打回 | Analyst 收到空 verified findings |

### 11. `test_token_budget.py` — API 消耗预估（不调 API）

| 用例 | 输入 | 预期 |
|------|------|------|
| 安静日消耗 | day_type=quiet, 5 focus items | 预估 token 数 ≤ 20K |
| 普通日消耗 | day_type=normal, 6 focus items | 预估 token 数 ≤ 60K |
| 剧烈波动日消耗 | day_type=volatile, 8 focus items | 预估 token 数 ≤ 120K |
| 耗时分项 | 任意配置 | 输出 LLM/tool/parse 三项占比 |

### 12. `run_all_tests.py` — 一键全跑

```cmd
python run_all_tests.py
```

- 依次运行 P0 → P1 → P2 所有不依赖 API 的测试
- 每阶段结束打印通过/失败数
- 所有阶段结束后打印总览
- 任一 P0 失败 → 立即停止
- 支持 `--phase p0` 只跑某个阶段

---

## 优先级标识

| 标识 | 含义 | 数量 | 状态 |
|:--:|------|:--:|:--:|
| 🔴 P0 | 数据正确性 | 3 个文件，28 条用例 | ✅ 已实现 |
| 🟡 P1 | 输出结构正确性 | 5 个文件，42 条用例 | ✅ 已实现 |
| 🟢 P2 | 集成与边界 | 4 个文件，17 条用例 | ⚠️ 计划中 |
| | **合计** | **13 个文件（已实现），87 条用例** |
