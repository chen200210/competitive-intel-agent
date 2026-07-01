# Deployment Guide

本文件是当前项目的生产部署手册，目标是让技术同事从零完成部署、托管、联调、验收和日常维护。

推荐部署形态:

- 单机 Linux 主机或云主机
- Python venv + systemd + cron
- SQLite 本地持久化
- Feishu Bot 走 WebSocket 长连接常驻
- FastAPI 仅作为内网健康检查接口，可选开启

不推荐一上来就全量 Docker 化的原因:

- 项目依赖 Playwright + Chromium
- `data/.bilibili_chrome_profile/` 和 `data/.diandian_chrome_profile/` 需要持久化
- 首次人工登录在宿主机上更直接

仓库内已补充的部署样例:

- [Dockerfile](/E:/DOSH/OA/Dockerfile)
- [docker-compose.yml](/E:/DOSH/OA/docker-compose.yml)
- [deploy/cron/oa-crontab.example](/E:/DOSH/OA/deploy/cron/oa-crontab.example)
- [deploy/systemd/oa-feishu-bot.service.example](/E:/DOSH/OA/deploy/systemd/oa-feishu-bot.service.example)
- [deploy/systemd/oa-health-api.service.example](/E:/DOSH/OA/deploy/systemd/oa-health-api.service.example)
- [deploy/supervisor/oa.conf.example](/E:/DOSH/OA/deploy/supervisor/oa.conf.example)
- [docs/OPS_ONE_PAGE.md](/E:/DOSH/OA/docs/OPS_ONE_PAGE.md)

## 1. 生产形态建议

建议按下面方式理解进程拆分:

- 常驻进程 1: `python -m src.feishu.bot`
  作用: 接收飞书卡片按钮回调、记录反馈、自动触发 Deep Research
- 常驻进程 2: `uvicorn src.main:app --host 127.0.0.1 --port 8000`
  作用: 暴露 `/api/health` 健康检查接口
- 定时任务 A: 上午全量日报
  命令: `python -m src.pipeline.runner --scrape --force --push <CHAT_ID>`
- 定时任务 B: 下午热点速报
  命令: `python -m src.pipeline.runner --hot-only --push <CHAT_ID>`
- 定时任务 C: 校准器
  命令: `python -m src.pipeline.runner --calibrate --calibrate-days 14 -v`

推荐优先级:

1. 先上线 bot 常驻 + 上午全量日报（暂时不应该上线热点追踪板块，可以选择拔掉）
2. 再上线下午 `--hot-only`
3. 校准器最后接入，等反馈量稳定后再开

## 2. 环境准备

生产环境建议:

- OS: Ubuntu 22.04/24.04 LTS 或同类 Linux
- Python: 3.12
- 时区: `Asia/Shanghai`
- CPU/内存: 2 vCPU / 4 GB 起步
- 磁盘: 20 GB 起步

系统依赖:

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3-pip sqlite3 curl unzip
```

如果宿主机直接跑 Playwright，额外建议安装:

```bash
sudo apt-get install -y \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 libgtk-3-0
```

## 3. 首次初始化顺序

首次初始化推荐严格按这个顺序执行:

1. 拉代码到目标目录，例如 `/opt/oa`
2. 建 venv
3. 安装 Python 依赖
4. 安装 Playwright Chromium
5. 复制并填写 `.env`
6. 准备 `data/competitor_list.yaml` 和 `data/bilibili_creators.yaml`
7. 完成需要登录态的浏览器 profile 初始化
8. 跑 smoke test
9. 再接 systemd / supervisor / cron

命令示例:

```bash
cd /opt/oa
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

如果 Playwright 缺系统依赖，可追加:

```bash
playwright install-deps chromium
```

## 4. .env 配置

从 `.env.example` 复制:

```bash
cp .env.example .env
```

当前代码实际读取的环境变量如下:

| 变量 | 必填 | 用途 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | 主 LLM | 日报摘要、打分、热点筛选、Deep Research、Calibrator 都依赖它 |
| `DEEPSEEK_BASE_URL` | 否 | LLM Base URL | 默认 `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | 否 | LLM 模型 | 默认 `deepseek-chat` |
| `FEISHU_APP_ID` | 是 | 飞书应用鉴权 | 推送和 bot 都依赖 |
| `FEISHU_APP_SECRET` | 是 | 飞书应用鉴权 | 推送和 bot 都依赖 |
| `FEISHU_VERIFICATION_TOKEN` | 是 | 飞书事件校验 | WebSocket bot 仍会使用 |
| `TAVILY_API_KEY` | 强烈建议 | 搜索增强 | 当前代码实际读取的搜索 API key |
| `SQLITE_PATH` | 否 | SQLite 主库路径 | 默认 `./data/intel.db` |
| `CHROMA_PATH` | 否 | 预留路径 | 当前主链路基本未依赖 |
| `HOST` | 否 | 健康 API 地址 | 仅 `uvicorn src.main:app` 用 |
| `PORT` | 否 | 健康 API 端口 | 默认 `8000` |
| `NGROK_AUTH_TOKEN` | 否 | 未来 webhook 模式预留 | 当前推荐生产方案不需要 |

当前 `.env.example` 里保留但主代码未实际读取的键:

- `BING_SEARCH_API_KEY`
- `SERPAPI_KEY`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_MODEL`

这些可以保留，但不应被当成当前上线阻塞项。

### Chat ID 说明

`chat_id` 不是 `.env` 变量，当前是通过命令行 `--push <CHAT_ID>` 传入。

建议在公司调度平台里把群 `chat_id` 配成任务参数或平台变量，例如:

```bash
python -m src.pipeline.runner --scrape --force --push oc_xxx
```

## 5. 密钥/权限矩阵

建议按下面方式分工:

| 项目 | 谁提供 | 权限范围 | 最低要求 |
|---|---|---|---|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飞书应用管理员 | 机器人推送、消息事件、联系人查询 | 应用已发布到目标租户 |
| `FEISHU_VERIFICATION_TOKEN` | 飞书应用管理员 | 事件校验 | 与飞书后台一致 |
| `chat_id` | 业务群管理员或运维 | 目标群接收日报 | bot 已加入该群 |
| `DEEPSEEK_API_KEY` | AI 平台负责人 | LLM 调用 | 有稳定额度 |
| `TAVILY_API_KEY` | 搜索服务负责人 | 搜索 API | 建议单独项目 key |
| `NGROK_AUTH_TOKEN` | 可不提供 | 仅 webhook/公网回调调试 | 当前生产不需要 |

飞书侧至少确认:

- Bot 已被加入目标群
- 机器人有发消息权限
- 事件订阅已允许卡片交互回调
- 如果需要用户名显示，联系人读取权限要可用

## 6. 数据持久化说明

以下路径和处理方式需要明确区分。

必须持久化:

- `data/intel.db`
  说明: 主数据库，包含日报、新闻、反馈、校准参数、去重状态
- `data/competitor_list.yaml`
  说明: 赛道规则、热点开关、部分评分配置来源
- `data/bilibili_creators.yaml`
  说明: B 站监控名单
- `data/.bilibili_chrome_profile/`
  说明: B 站登录态，丢失后需要重新人工登录
- `data/.diandian_chrome_profile/`
  说明: 点点数据登录态，也被部分 Playwright 流程复用，丢失后需要重新人工登录

建议持久化:

- `data/intel.db-wal`
- `data/intel.db-shm`

说明:

- 这两个是 SQLite WAL 运行期文件
- 在线备份时最好和 `intel.db` 一起考虑
- 如果服务完全停止后再备份，通常只需要 `intel.db`

可重建:

- `data/raw/`
  说明: 抓取中间文件，主来源已经会入库，可删除或定期清理
- `data/processed/`
  说明: 派生目录，可重建
- `data/chroma/`
  说明: 当前主链路不是强依赖，可按实际使用决定是否保留

## 7. Playwright / Chromium 部署说明

项目依赖 Playwright 和 Chromium。至少这些路径会用到浏览器:

- `tools/scrapers/diandian_batch.py`
- `tools/diandian_auth.py`
- `tools/scrapers/bilibili_creators.py`
- `src/tools/taptap_resolver.py`

### 必装项

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 哪些抓取需要人工登录

必须先人工登录:

- 点点数据: `data/.diandian_chrome_profile/`
- B 站: `data/.bilibili_chrome_profile/`

通常不需要单独人工登录，但会复用浏览器环境:

- `src/tools/taptap_resolver.py`

### 点点数据初始化

```bash
source .venv/bin/activate
python tools/diandian_auth.py
```

预期:

- Chromium 窗口打开
- 手工登录点点数据
- 回终端按 Enter
- 登录态写入 `data/.diandian_chrome_profile/`

### B 站初始化

```bash
source .venv/bin/activate
python -m tools.scrapers.bilibili_creators
```

预期:

- 首次运行会打开 Chromium
- 手工完成 B 站登录
- 关闭浏览器或等待流程结束
- 后续可改用 `--headless`

### 生产建议

- 浏览器 profile 必须放在持久化目录
- profile 不要放在 `/tmp`
- 升级 Playwright 后，先手工跑一次登录相关流程再恢复定时任务

## 8. Hot Tracker 开关

热点模块开关不在 `.env`，而在 `data/competitor_list.yaml` 的 `hot_tracker` 配置中。

默认建议:

```yaml
hot_tracker:
  enabled: false
  required: false
```

含义:

- `enabled: false`: 不执行热点关键词收集、热点搜索、热点卡片渲染
- `required: false`: 即使热点失败，也不让主日报失败

部署时务必一并阅读:

- [docs/HOT_TRACKER_COLD_PLUG.md](/E:/DOSH/OA/docs/HOT_TRACKER_COLD_PLUG.md)

生产推荐顺序:

1. 第一阶段先关掉热点模块，保证主日报先稳定
2. 第二阶段再开启 `enabled: true`
3. 除非团队明确要求，否则不要一开始就设 `required: true`

## 9. 飞书 bot 启动与回调联调

当前项目的 bot 是 WebSocket 长连接模式，不依赖公网 webhook，也不要求 ngrok 才能生产可用。

启动命令:

```bash
source .venv/bin/activate
python -m src.feishu.bot
```

预期日志:

- 输出 `Starting Feishu bot (WebSocket)...`
- 输出 `Bot is running. Press Ctrl+C to stop.`

### 回调联调步骤

1. 启动 bot
2. 先推一张测试卡片到目标群
3. 在飞书群里点击 `👍` / `👎` / `感兴趣`
4. 观察 bot 终端是否打印 `CARD_ACTION`
5. 确认数据库 `user_feedback` 有新增记录

测试推送命令:

```bash
python -m src.feishu.pusher test-chat oc_xxx
```

如果需要查 bot 当前可见的群:

```bash
python -m src.feishu.pusher list-chats
```

## 10. 日报和定时任务说明

### 上午全量日报

推荐定时:

- 每天 09:05 Asia/Shanghai

命令:

```bash
cd /opt/oa && source .venv/bin/activate && python -m src.pipeline.runner --scrape --force --push oc_xxx
```

作用:

- 跑全量抓取
- 导入 DB
- 生成日报
- 推送到飞书

### 下午 hot-only

推荐定时:

- 每天 15:00 Asia/Shanghai

命令:

```bash
cd /opt/oa && source .venv/bin/activate && python -m src.pipeline.runner --hot-only --push oc_xxx
```

作用:

- 只重跑热点
- 不重跑全链路抓取
- 推送单独热点速报卡片

### calibrate 是否定时

推荐结论:

- 不是 P0 必开项
- 反馈样本少于 30 条时，不建议定时跑
- 样本稳定后，建议每天 21:30 或每 2-3 天跑一次

示例:

```bash
cd /opt/oa && source .venv/bin/activate && python -m src.pipeline.runner --calibrate --calibrate-days 14 -v
```

### bot 是否常驻

是，必须常驻。

原因:

- 卡片反馈靠它接收
- 热点 `感兴趣` 的自动 Deep Research 触发也靠它

## 11. cron 和公司调度平台示例

### cron 示例

参考文件:

- [deploy/cron/oa-crontab.example](/E:/DOSH/OA/deploy/cron/oa-crontab.example)

核心示例:

```cron
CRON_TZ=Asia/Shanghai
5 9 * * * cd /opt/oa && . .venv/bin/activate && python -m src.pipeline.runner --scrape --force --push oc_xxx >> /var/log/oa/daily.log 2>&1
0 15 * * * cd /opt/oa && . .venv/bin/activate && python -m src.pipeline.runner --hot-only --push oc_xxx >> /var/log/oa/hot-only.log 2>&1
30 21 * * * cd /opt/oa && . .venv/bin/activate && python -m src.pipeline.runner --calibrate --calibrate-days 14 -v >> /var/log/oa/calibrate.log 2>&1
```

### 公司调度平台示例

可以拆成 3 个任务:

1. `oa-daily-report`
   命令: `cd /opt/oa && . .venv/bin/activate && python -m src.pipeline.runner --scrape --force --push ${OA_CHAT_ID}`
2. `oa-hot-only`
   命令: `cd /opt/oa && . .venv/bin/activate && python -m src.pipeline.runner --hot-only --push ${OA_CHAT_ID}`
3. `oa-calibrate`
   命令: `cd /opt/oa && . .venv/bin/activate && python -m src.pipeline.runner --calibrate --calibrate-days 14 -v`

平台变量建议:

- `OA_CHAT_ID`
- `TZ=Asia/Shanghai`

## 12. 常驻进程托管方案

推荐方案:

1. `systemd`
2. `supervisor`
3. `docker compose`

### systemd

样例文件:

- [deploy/systemd/oa-feishu-bot.service.example](/E:/DOSH/OA/deploy/systemd/oa-feishu-bot.service.example)
- [deploy/systemd/oa-health-api.service.example](/E:/DOSH/OA/deploy/systemd/oa-health-api.service.example)

适用:

- Linux 服务器
- 标准运维体系
- 最稳妥

### supervisor

样例文件:

- [deploy/supervisor/oa.conf.example](/E:/DOSH/OA/deploy/supervisor/oa.conf.example)

适用:

- 已有 supervisor 运维体系
- 想同时托管 bot 和 health-api

### Docker / docker-compose

样例文件:

- [Dockerfile](/E:/DOSH/OA/Dockerfile)
- [docker-compose.yml](/E:/DOSH/OA/docker-compose.yml)

适用:

- 团队已有容器平台
- 已经解决浏览器 profile 的挂载和初始化

注意:

- Docker 更适合“已经有 profile”的稳定运行
- 首次登录态初始化仍建议在宿主机进行

## 13. 健康检查标准

定义“今天成功”的最低标准:

1. 今天的日报成功生成
   标准: `analysis_reports` 存在当天记录
2. 今天的日报成功推送
   标准: 调度日志中出现 `[OK] Pushed to`
3. bot 可接收卡片回调
   标准: 点击按钮后 bot 日志出现 `CARD_ACTION`
4. 数据库可写
   标准: 反馈点击能写入 `user_feedback`
5. 热点模块关闭时不报错
   标准: `hot_tracker.enabled=false` 时，全量日报仍成功，`--hot-only` 返回 skipped 而不是异常

### 健康检查命令

API 健康检查:

```bash
curl http://127.0.0.1:8000/api/health
```

预期:

- `status` 为 `ok`
- 返回数据库路径
- 返回 `latest_date`

## 14. 最小验收清单

部署后控制在 5 条命令内，建议这样验收:

1. 校验飞书连接

```bash
python -m src.feishu.pusher list-chats
```

预期:

- 能看到目标群 `chat_id`

2. 启动 bot

```bash
python -m src.feishu.bot
```

预期:

- 输出 `Bot is running`
- 保持常驻不退出

3. 启动健康 API

```bash
uvicorn src.main:app --host 127.0.0.1 --port 8000
```

预期:

- 服务启动成功
- `curl http://127.0.0.1:8000/api/health` 返回 `status: ok`

4. 跑一遍全量日报

```bash
python -m src.pipeline.runner --date 2026-06-30 --scrape --force --skip diandian_batch --push oc_xxx
```

预期:

- pipeline 完成
- 飞书收到日报
- DB 中生成当天 `analysis_reports`

5. 跑一遍 hot-only

```bash
python -m src.pipeline.runner --date 2026-06-30 --hot-only --push oc_xxx
```

预期:

- 如果热点开启，飞书收到热点速报
- 如果热点关闭，流程明确返回 skipped，不应抛异常

补充人工验收:

- 在飞书点一次 `👍` / `👎` / `感兴趣`
- bot 日志看到 `CARD_ACTION`
- 数据库 `user_feedback` 有新增

## 15. 失败排障手册

### 飞书发不出去

优先检查:

- `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 是否正确
- bot 是否已被加到目标群
- `chat_id` 是否写错
- `python -m src.feishu.pusher list-chats` 是否能列到目标群
- 日志里是否有 `Feishu API error`

### bot 没收到回调

优先检查:

- `python -m src.feishu.bot` 是否常驻
- `FEISHU_VERIFICATION_TOKEN` 是否和飞书后台一致
- 飞书事件订阅里是否已开启卡片回调
- 点击按钮时终端是否出现 `CARD_ACTION`

### Playwright 起不来

优先检查:

- 是否执行过 `python -m playwright install chromium`
- Linux 系统库是否缺失
- 首次登录流程是否在带界面的环境完成
- profile 目录是否有权限写入

### 点点/B 站登录态失效

处理方式:

- 删除对应 profile 目录前先备份
- 重新跑初始化登录流程
- 再手工执行一次相关 scraper 验证

### SQLite 锁冲突

优先检查:

- 是否多个全量日报任务同时执行
- 是否同一时刻同时跑多个写入型脚本
- 是否把 bot、日报、校准器都堆在同一分钟启动

缓解建议:

- 避免并发跑多个 `runner`
- 把 `calibrate` 排到晚间
- 保持单机单库，不要把 `intel.db` 放网络盘

## 16. 备份与恢复策略

### 建议备份内容

必须备份:

- `data/intel.db`
- `data/competitor_list.yaml`
- `data/bilibili_creators.yaml`
- `data/.bilibili_chrome_profile/`
- `data/.diandian_chrome_profile/`
- `.env`

可选备份:

- `data/raw/`

### 备份频率建议

- `intel.db`: 每日 1 次
- 浏览器 profile: 每周 1 次，或每次手工重新登录后立即备份
- `.env`: 变更后立即备份到密钥管理系统

### 备份示例

停服务后直接复制:

```bash
cp data/intel.db /backup/oa/intel-$(date +%F).db
tar -czf /backup/oa/bilibili-profile-$(date +%F).tar.gz data/.bilibili_chrome_profile
tar -czf /backup/oa/diandian-profile-$(date +%F).tar.gz data/.diandian_chrome_profile
```

### 恢复原则

1. 先停 bot 和定时任务
2. 恢复 `intel.db`
3. 恢复两个浏览器 profile
4. 再启动 bot 和定时任务

## 17. Docker 说明

当前仓库已补 Docker 样例，但推荐结论是:

- P0 生产优先用 `venv + systemd + cron`
- Docker 作为 P1 可选方案

原因:

- SQLite 和浏览器 profile 都是典型本地持久化资产
- 首次人工登录在容器里不方便

如果团队必须容器化:

- 挂载 `./data:/app/data`
- 用宿主机保存 `.env`
- 首次登录完成后再让容器常驻

## 18. 参考文档

- [README.md](/E:/DOSH/OA/README.md)
- [docs/HANDOFF_INDEX.md](/E:/DOSH/OA/docs/HANDOFF_INDEX.md)
- [docs/HOT_TRACKER_COLD_PLUG.md](/E:/DOSH/OA/docs/HOT_TRACKER_COLD_PLUG.md)
