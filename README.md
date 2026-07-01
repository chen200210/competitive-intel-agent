# 竞品情报智能体

面向游戏行业的日报系统，负责自动汇总榜单变化、市场新闻、Steam 移植、TapTap 新游和热点话题，并生成可直接推送到飞书的日报卡片。

部署、托管、验收请优先看:

- [README_DEPLOY.md](/E:/DOSH/OA/README_DEPLOY.md)
- [docs/OPS_ONE_PAGE.md](/E:/DOSH/OA/docs/OPS_ONE_PAGE.md)

## 项目在做什么

系统每天会处理这些信息源:

- 排行榜 CSV 导入与每日排名对比
- TapTap 新游日历
- Steam 移植检测
- 游戏行业资讯头条
- B 站创作者动态
- 热点关键词与热点追踪

最终输出 4 个主要板块:

- 新游关注
- 市场变动
- 排名变动
- 热点追踪

其中前 3 个板块是主链路，热点追踪可按配置冷插拔。

## 核心链路

```text
Scrapers -> Loader -> Differ -> Story Picker -> Briefer -> Card Audit -> Feishu
                                  |              |
                                  |              +-> Market News Pipeline
                                  +-> Track Filter
                                  +-> Hot Tracker (optional)
```

关键目录:

- `src/agents/`
  作用: briefer、scorer、render、deep_researcher、calibrator 等 Agent 模块
- `src/pipeline/`
  作用: differ、story_picker、track_filter、runner、audit 等规则流水线
- `src/feishu/`
  作用: 卡片推送、交互按钮、bot 回调
- `src/storage/`
  作用: SQLite schema 与 CRUD
- `tools/scrapers/`
  作用: 点点、TapTap、Steam、资讯、B 站等抓取器
- `data/`
  作用: SQLite 主库、配置 YAML、raw 中间数据、浏览器 profile

## 常用命令

全量日报:

```bash
python -m src.pipeline.runner --scrape --force
```

推送飞书:

```bash
python -m src.pipeline.runner --scrape --force --push oc_xxx
```

下午热点速报:

```bash
python -m src.pipeline.runner --hot-only --push oc_xxx
```

启动飞书 bot:

```bash
python -m src.feishu.bot
```

启动健康检查 API:

```bash
uvicorn src.main:app --host 127.0.0.1 --port 8000
```

## 配置与数据

环境变量模板:

- [.env.example](/E:/DOSH/OA/.env.example)

关键持久化资产:

- `data/intel.db`
- `data/competitor_list.yaml`
- `data/bilibili_creators.yaml`
- `data/.bilibili_chrome_profile/`
- `data/.diandian_chrome_profile/`

热点模块开关说明:

- [docs/HOT_TRACKER_COLD_PLUG.md](/E:/DOSH/OA/docs/HOT_TRACKER_COLD_PLUG.md)

## 交接阅读顺序

1. [README_DEPLOY.md](/E:/DOSH/OA/README_DEPLOY.md)
2. [docs/OPS_ONE_PAGE.md](/E:/DOSH/OA/docs/OPS_ONE_PAGE.md)
3. [docs/HANDOFF_INDEX.md](/E:/DOSH/OA/docs/HANDOFF_INDEX.md)
4. [docs/NEW_REPORT_ARCHITECTURE.md](/E:/DOSH/OA/docs/NEW_REPORT_ARCHITECTURE.md)
