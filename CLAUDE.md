# CLAUDE.md

## 项目概述
竞品情报多智能体系统。完整设计文档见 DESIGN.md，3 分钟概览见 README.md。

**业务聚焦**：塔防品类 + 微恐/冰河/火山爆发题材 + 已被市场验证的玩法。

**核心价值**：回答三个问题——
1. 这个方向值不值得做？
2. 市场上谁在做、做成什么样了？
3. 如果做，竞争风险在哪里？

## 技术栈
Python 3.12 + FastAPI + SQLite + Chroma + Claude API + 飞书 SDK

## 关键文件
- `DESIGN.md` — 完整设计文档（Agent 定义、Schema、算法、数据库）
- `README.md` — 概要
- `AI_CODING_GUIDE.md` — AI 编码实操指南
- `data/raw/` — 手动下载的 CSV 放这里
- `data/competitor_list.yaml` — 竞品列表 + 业务方向配置

## 当前进度
项目设计阶段完成，尚未开始写代码。按 DESIGN.md 第十章的计划执行。

## 编码规范
- 类型注解必须写，用 Python 3.12 语法
- 函数返回值用 Pydantic BaseModel，不用 dict
- Agent prompt 从 YAML 文件加载，不硬编码
- 每个模块可独立测试：`python -m src.xxx --test`
- 中间结果全部落 SQLite
- 真实榜单有 100 款游戏，数据只有排名没有收入/下载量
- 原始 CSV 的 bundle_id 和 developer 字段可能为 "0"，Loader 需要处理

## 开工方式
每次告诉我你要做 DESIGN.md 里的哪个模块，我会自己去读对应章节，不需要你复制粘贴。
