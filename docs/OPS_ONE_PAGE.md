# 运维交付单页

给运维同事的最短执行版，按这个顺序做即可。

## 1. 必备信息

上线前准备好:

- 代码目录，例如 `/opt/oa`
- `.env`
- 飞书群 `chat_id`
- `data/competitor_list.yaml`
- `data/bilibili_creators.yaml`
- 点点登录态目录 `data/.diandian_chrome_profile/`
- B 站登录态目录 `data/.bilibili_chrome_profile/`

必填密钥:

- `DEEPSEEK_API_KEY`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_VERIFICATION_TOKEN`
- `TAVILY_API_KEY`

## 2. 首次部署

```bash
cd /opt/oa
python3.12 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

如果是首次部署的机器，还要完成人工登录:

- 点点: `python tools/diandian_auth.py`
- B 站: `python -m tools.scrapers.bilibili_creators`

## 3. 常驻进程

必须常驻:

- `python -m src.feishu.bot`

建议常驻:

- `uvicorn src.main:app --host 127.0.0.1 --port 8000`

推荐托管:

- Linux: `systemd`
- 已有进程托管体系: `supervisor`

样例文件:

- [../deploy/systemd/oa-feishu-bot.service.example](/E:/DOSH/OA/deploy/systemd/oa-feishu-bot.service.example)
- [../deploy/systemd/oa-health-api.service.example](/E:/DOSH/OA/deploy/systemd/oa-health-api.service.example)
- [../deploy/supervisor/oa.conf.example](/E:/DOSH/OA/deploy/supervisor/oa.conf.example)

## 4. 定时任务

上午全量日报:

```bash
cd /opt/oa && . .venv/bin/activate && python -m src.pipeline.runner --scrape --force --push oc_xxx
```

下午热点速报:

```bash
cd /opt/oa && . .venv/bin/activate && python -m src.pipeline.runner --hot-only --push oc_xxx
```

夜间校准器，可选:

```bash
cd /opt/oa && . .venv/bin/activate && python -m src.pipeline.runner --calibrate --calibrate-days 14 -v
```

推荐时间:

- `09:05` 全量日报
- `15:00` `--hot-only`
- `21:30` `--calibrate`

## 5. 今日成功标准

今天算成功，至少满足:

1. 日报生成成功
2. 飞书推送成功
3. bot 收到按钮回调
4. 数据库可写
5. 热点关闭时主链路不报错

对应检查:

- `analysis_reports` 有当天记录
- 调度日志有 `[OK] Pushed to`
- bot 日志出现 `CARD_ACTION`
- `user_feedback` 有新增
- `hot_tracker.enabled=false` 时，全量日报仍可成功

## 6. 最小验收

只跑 5 条命令:

1. `python -m src.feishu.pusher list-chats`
   预期: 能看到目标群
2. `python -m src.feishu.bot`
   预期: 输出 `Bot is running`
3. `uvicorn src.main:app --host 127.0.0.1 --port 8000`
   预期: `curl /api/health` 返回 `status=ok`
4. `python -m src.pipeline.runner --date 2026-06-30 --scrape --force --skip diandian_batch --push oc_xxx`
   预期: 日报成功推送
5. `python -m src.pipeline.runner --date 2026-06-30 --hot-only --push oc_xxx`
   预期: 热点开启则推送，关闭则明确 skipped

补充人工操作:

- 点一次 `👍` / `👎` / `感兴趣`
- 确认 bot 日志出现 `CARD_ACTION`

## 7. 出问题先看哪里

飞书发不出去:

- 查 `.env`
- 查 `chat_id`
- 跑 `python -m src.feishu.pusher list-chats`

bot 没回调:

- 查 `python -m src.feishu.bot` 是否常驻
- 查飞书事件订阅和 `FEISHU_VERIFICATION_TOKEN`

Playwright 起不来:

- 查 `python -m playwright install chromium`
- 查系统依赖
- 查浏览器 profile 是否存在

SQLite 锁冲突:

- 不要并发跑多个 `runner`
- 把 `calibrate` 放到晚间

## 8. 备份

必须备份:

- `data/intel.db`
- `.env`
- `data/competitor_list.yaml`
- `data/bilibili_creators.yaml`
- `data/.bilibili_chrome_profile/`
- `data/.diandian_chrome_profile/`

详细部署手册:

- [../README_DEPLOY.md](/E:/DOSH/OA/README_DEPLOY.md)
