# Calibrator Agent 设计

> **状态**: ✅ 已实现 (2026-06-25)
> **实现文件**: `src/agents/calibrator.py` + `prompts/calibrator.yaml`
> **RecGPT 应用指南**: `docs/RECGPT_APPLICATION.md`
> **已知 Bug**: `docs/BUG_LOG【已修复】.md` #C1-#C7

独立校准 agent，每周跑一次，消费 `user_feedback` 数据反哺日报打分。

## 已实现与设计差异

| 设计项 | 原设计 | 实际实现 |
|--------|--------|---------|
| 输入 | user_feedback + market_news 聚合 | ✅ 同设计。`_aggregate_feedback()` SQL 聚合 |
| 输出参数 | source_weights + topic_boosts + dim_weights | topic_boosts + dim_weights。source_weights 由 source_constants.py 管理，不纳入校准 |
| dim_weights | 四维 (track/density/insight/timeliness) | 三维 (track/density/insight)，timeliness 已由代码层 freshness 信号覆盖 |
| Prompt 结构 | 自由格式 | RecGPT 五段式: Role→Input→CoT Steps→Requirements→Matched Pool→Output |
| 输出校验 | 无 | Pydantic model_validator: 值域 clamp + 未知 key 丢弃 + sum=100 强制 |
| 版本化 | version + created_at | ✅ 同设计。额外: applied flag + UNIQUE 约束 + 原子插入 |
| 消费方 | scorer.py 直接读取 | ✅ `load_calibration_for_scorer()` + `apply_topic_boosts()` |
| 评分融合 | 无（P1 新增） | β-Fusion: `0.3×signal_score + 0.7×ai_score`，校准 topic_boosts 在分布检查之前执行 |

## 使用方式

```bash
# 当反馈 ≥30 条后
python -m src.pipeline.runner --calibrate --calibrate-days 14 -v
python -m src.agents.calibrator --days 14 --force -v
```

## 原始设计（以下为 2026-06-24 初稿）

## 输入
- `user_feedback` 近 7-14 天数据（按来源/话题聚合 👍/👎 比例）
- `market_news.useful_count` / `useless_count`（按 URL 汇总）
- 当前 `calibration_params` 表的上一版参数

## 输出 → `calibration_params` 表（版本化，带 `version`/`created_at`/`summary`）
- `source_weights` — 来源权威分: `{"游戏陀螺": 0.5, "3DM": 0.3, ...}`
- `topic_boosts` — 话题偏好: `{"独立游戏": +5, "二次元抽卡": -3, ...}`
- `dim_weights` — 四维打分权重: `{"track": 40, "density": 30, "insight": 20, "timeliness": 10}`
- `summary` — AI 写的校准理由（可审计）

## 消费方
- `source_weights` 替换硬编码来源权重（当前在 `source_constants.py` 中）
- `topic_boosts` 在 AI 打分后做加成（`scorer.ai_summarize_and_judge()`）
- Summarizer prompt 注入 `dim_weights` 参考值 + 反馈统计摘要

## LLM 策略
Calibrator agent 用 LLM 分析反馈模式、识别相关性、输出参数 JSON。
纯规则算 👍/👎 比例只能做 Level 1，要让 AI 发现
"独立游戏深度报道总是被赞，不管来源"这种跨维度的洞察，才能上 Level 2-4。

## 前置条件
依赖用户反馈数据积累 ≥ 30 条后启动。当前等待数据收集中。
