# 热点追踪模块 — 代码审查报告

**审查日期**: 2026-06-25  
**审查范围**: 热点追踪模块全部 9 个文件（新建 2 + 修改 7）  
**审查方法**: 7 角度交叉审查（逐行扫描 / 移除行为审计 / 跨文件追踪 / 复用审计 / 简化审计 / 效率审计 / 架构层级审计）  
**状态**: ✅ 全部修复

---

## 发现清单（按严重程度排序）

### #1 🔴 CRITICAL — `CURATED_KEYWORDS` 全局常量被原地修改 ✅

- **文件**: `src/pipeline/hot_tracker.py:140`
- **问题**: `collect_hot_keywords()` 中 `kw["weight"] = adjusted` 直接写入模块级常量 `CURATED_KEYWORDS` 的 dict 对象
- **根因**: `all_keywords.extend(CURATED_KEYWORDS)` 传递的是引用而非拷贝
- **影响**: 每次调用后全局常量被污染，长期运行进程（bot）累积残留状态
- **修复**: 合并前浅拷贝 dict — `all_keywords.extend(dict(kw) for kw in CURATED_KEYWORDS)`

### #2 🔴 CRITICAL — `user_feedback` 唯一索引阻止跨类型反馈 ✅

- **文件**: `src/storage/sqlite.py:456` (旧) → `:497` (新)
- **问题**: `idx_user_feedback_dedup` 索引作用于 `(news_url, open_id)`，不含 `feedback_type`
- **影响**: 同一用户对同一 URL 先点 👍 再点 👀 感兴趣 → 第二次 `INSERT OR IGNORE` 静默丢失
- **修复**: 新增 `_migrate_v9()` → DROP 旧索引 → CREATE 新索引 `(news_url, open_id, feedback_type)`

### #3 🔴 CRITICAL — `mark_hot_topic_selected` 不清理旧选择 ✅

- **文件**: `src/storage/sqlite.py:1055`
- **问题**: `--force` 重跑时旧条目 `selected=1` 不清零，新标记叠加
- **影响**: `get_hot_topic_news_by_date(selected=True, limit=7)` 按 id 排序返回旧数据
- **修复**: 标记前先 `UPDATE hot_topic_news SET selected = 0 WHERE date = ?`

### #4 🟠 HIGH — `record_hot_topic_click` 缺少 `chat_id` ✅

- **文件**: `src/storage/sqlite.py:1068` + `src/feishu/bot.py`
- **问题**: INSERT 语句未包含 `chat_id` 列，bot 调用方也未传递
- **影响**: 热点点击反馈无法按群聊聚合分析
- **修复**: DB 方法签名新增 `chat_id` 参数并写入 INSERT；bot.py 调用处传入 `chat_id`

### #5 🟠 HIGH — `insert_hot_keywords` 权重更新被 `INSERT OR IGNORE` 静默丢弃 ✅

- **文件**: `src/storage/sqlite.py:995`
- **问题**: 同日期重跑时，新计算的权重被忽略，保留首次写入的值
- **影响**: 反馈闭环的权重调整只在首次写入生效，重跑时反馈数据被浪费
- **修复**: `INSERT OR IGNORE` → `INSERT OR REPLACE`

### #6 🟡 MEDIUM — 空缓存结果导致关键词被永久跳过 ✅

- **文件**: `src/pipeline/hot_tracker.py:333`
- **问题**: `cached is not None` 将空列表 `[]` 视为有效缓存命中
- **影响**: 瞬时网络故障导致的空结果被缓存 24h，关键词持续静默
- **修复**: `if cached is not None` → `if cached is not None and len(cached) > 0`

### #7 🟡 MEDIUM — `_extract_domain()` 在两个文件中重复定义 ✅

- **文件**: `src/agents/render.py:378` / `src/pipeline/hot_tracker.py:458` (旧)
- **问题**: 完全相同的函数（逐字节一致）定义在两处
- **影响**: 修改一处另一处遗漏 → 展示域名与 DB 存储域名不一致
- **修复**: 新建 `src/tools/url_utils.py` 提取共享函数，两处改为导入调用。删除两个本地副本。
- **⚠ 修复引入的回归**: 初次修复时将 `extract_domain` 导入放在了 `build_hot_topic_elements()` 内部，但实际调用发生在 `build_hot_topics_md()` 中，导致 `NameError`。已在二次审查中发现并修复（导入移至 `build_hot_topics_md()` 内部）。

### #8 🟡 MEDIUM — `_search_with_fallback()` 重复了 `web_search()` 的引擎回退循环 ✅

- **文件**: `src/pipeline/hot_tracker.py:412-441`
- **问题**: 与 `src/tools/web_search.py` 的 `web_search()` 逻辑相同，仅引擎优先级不同
- **影响**: 新增搜索引擎需改两处；`ENGINE_CHAIN` 模块级常量定义了但从未使用
- **修复**: 删除死代码 `ENGINE_CHAIN` 常量（`web_search()` 参数化重构留待后续）

### #9 🟢 LOW — `_check_ddg_reachable()` 在缓存命中时白费 HTTP 请求 ✅

- **文件**: `src/pipeline/hot_tracker.py:320` (旧) → `:345` (新)
- **问题**: HTTP 探测在所有缓存查找之前执行，重跑时全部命中缓存仍付出 3-8s 延迟
- **影响**: 每日重跑 2-3 次 → 累计浪费 10-24s
- **修复**: 改为懒加载 — 仅首次缓存未命中时探测。全部命中缓存时 `vpn_ok` 返回 `True` 不触发误报警告。

### #10 🟢 LOW — 死代码：`if results else "unknown"` + `today` 兜底 ✅

- **文件**: `src/pipeline/hot_tracker.py:362` (旧) / `:326` (旧)
- **问题**: `if results else "unknown"` 位于已由 `if results:` 守卫的块内；`date or _date.today().isoformat()` 因 `date` 是必传参数永远走不到 `or` 分支
- **修复**: 两处死代码均已删除

---

## 二次审查发现的回归问题

### #11 🔴 CRITICAL — `extract_domain` 导入作用域错误 ✅

- **文件**: `src/agents/render.py:362` (旧) → `:295` (新)
- **问题**: 首次修复 #7 时，将 `from src.tools.url_utils import extract_domain` 放在 `build_hot_topic_elements()` 内部，但实际调用在 `build_hot_topics_md():325`。两函数是独立作用域。
- **影响**: `brief_from_db()` → `build_hot_topics_md()` → 第 325 行 `extract_domain(url)` → `NameError`，卡片生成崩溃
- **修复**: 将导入移至 `build_hot_topics_md()` 函数内部，从 `build_hot_topic_elements()` 中移除

---

## 设计改进：多样性保障

**问题**: 反馈闭环只有"降温"（低点击→低权重），没有"纳新"。若 trending 词高度集中，curated 关键词可能被永久挤出 top 10，关键词池收敛到死水。

**方案**: 新增 `CURATED_MIN_SLOTS = 3` 保底机制 + 每日 scraper 自然纳新。

**实现** (`src/pipeline/hot_tracker.py:144-172`):
- 非 curated 词按权重取前 7 席
- curated 词保底 3 席（权重再低也不被挤出）
- 总共上限 10 席
- 百度/知乎热搜每天重新抓取 → 新热点词自然流入
- 无"永久剔除"机制 → 低权重词只是当天不进，第二天 scraper 重新抓到又是新词新机会

**验证**: 模拟 8 个高权重 baidu 词(1.3-2.0) + 5 个低权重 curated 词(0.1-0.5) → 结果: 7 baidu + 3 curated ✅

---

## 修复优先级与状态

| 优先级 | 发现 | 状态 |
|--------|------|------|
| P0 | #1 CURATED_KEYWORDS 变异 | ✅ 已修复 |
| P0 | #2 唯一索引冲突 | ✅ 已修复 (migration v9) |
| P0 | #3 selected 累积 | ✅ 已修复 |
| P1 | #4 缺少 chat_id | ✅ 已修复 |
| P1 | #5 权重被忽略 | ✅ 已修复 |
| P1 | #6 空缓存跳过 | ✅ 已修复 |
| P2 | #7 _extract_domain 重复 | ✅ 已修复 (引入回归→已修复) |
| P2 | #8 引擎回退重复 | ✅ 死代码已删 (重构留待后续) |
| P2 | #9 VPN 探测浪费 | ✅ 已修复 (懒加载) |
| P2 | #10 死代码 | ✅ 已修复 |
| — | #11 extract_domain 作用域回归 | ✅ 已修复 |
| — | 多样性保障 | ✅ 已实现 |
