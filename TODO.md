# 项目计划

> 最后更新：2026-06-23
> 
> **设计决策**：不做商业化调查。赛道 = 塔防/肉鸽。新闻只从四大游戏站（游侠/17173/3DM/游戏陀螺）抓取。Scraper 数据直读 DB 不经 AI。

---

## 已完成

- [x] 全链路并行优化：从 27min 降至 ~3min
- [x] 跨榜信号优先规则 + 赛道过滤 + 图片搜索优化
- [x] NEW_REPORT_ARCHITECTURE 第一轮：track_config + 5新表DDL + track_filter + taptap_new_games
- [x] NEW_REPORT_ARCHITECTURE 第二轮：steam_ports + news_feeds
- [x] **第三轮：简报新格式**（全部完成）
  - [x] design_analyst 删 risk_mirror + monetization + market_viability + actionable_insight（砍到 3 维）
  - [x] overview_scanner 只盯赛道 → 纯推理 Agent（砍掉 web_search/web_fetch，零工具）
  - [x] briefer 重写：六板块 → 五板块（砍竞争风险），直读 DB Scraper 数据
  - [x] runner 串联：砍 Analyst/NewGameWatcher/MarketNewsScanner，Agent 8→5
  - [x] 飞书交互：card_builder 新文件 + bot 点点回调 + pusher 注入按钮
- [x] **质量兜底**
  - [x] 卡片审计层 `audit.py`：11 项硬检查 + 自动修复（零 token）
  - [x] 全局清扫旧赛道关键词（微恐/冰河/火山）
- [x] **搜索方案**：360(主) → 搜狗 → Bing，三级降级
- [x] **新闻来源扩展**：游侠 + 17173 + 3DM + 游戏陀螺，去除非游戏关键词 + 砍掉赛道新闻搜索
- [x] **关注度加赛道权重**：`differ.py` 赛道游戏 +1.5 分

---

## 待办 — 不需要等，现在就能做

- [ ] **Briefer Self-Refine** — `max_tool_rounds` 1→2
- [ ] **Researcher 强制字段检查** — 代码层自检，防漏字段
- [ ] **跨榜统计基线** — 用历史数据算均值+标准差，阈值数据驱动
- [ ] 定时调度（每天 9:00 自动跑）
- [ ] Docker 部署

---

## 待办 — 需要主策划输入

- [ ] 跟主策划聊简报内容
- [ ] 写主策划偏好文件
- [ ] 飞书卡片排版优化
- [ ] 给 Briefer prompt 加 few-shot 示例
- [ ] 自评估框架

## 用户反馈驱动的新闻推荐优化

- [ ] **飞书回调解析** — `bot.py` 加消息路由，识别 @机器人 + "好/噪音/不错/有用" 等关键词，提取被引用消息中的新闻 URL
- [ ] **DB 表** — 新建 `user_feedback` 表: date, url, headline, source, rating(1/-1), reason, user_id
- [ ] **反馈记录** — bot 解析后写入 `user_feedback`，回复用户确认
- [ ] **偏好总结 Agent** — 定期（每周/累计20条新反馈）调用 LLM 读 feedback，输出偏好摘要（偏好类型、噪音类型、来源倾向）
- [ ] **注入打分** — 偏好摘要注入 summarizer prompt 或 `_score_news_item()`，影响最终入选
- [ ] **效果回测** — 对比反馈前后的入选新闻变化，确认偏好生效
