# OA 竞品日报 — 最终 Agent 架构

> 2026-06-22

---

## 总览：8 Agent + 4 Scraper + 1 规则引擎

```
                              ┌─────────────────────────────────┐
                              │        Phase 0A: 抓取层          │
                              │         4 Scraper 并行           │
                              │         0 token                  │
                              └──────────────┬──────────────────┘
                                             │
              ┌──────────────┬───────────────┼───────────────┬──────────────┐
              ▼              ▼               ▼               ▼              ▼
         rankings.csv   taptap_*.csv    steam_*.csv     news_*.csv    diandian_*.csv
              │              │               │               │
              └──────────────┼───────────────┼───────────────┘
                             │               │
                    ┌────────┴───────────────┴────────┐
                    │   Phase 0B: Loader (0 token)     │
                    │   Phase 0C: Track Filter (0 token)│
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │   Phase 1: 规则管道 (0 token)     │
                    │   Differ → StoryPicker →          │
                    │   CrossChart                      │
                    └────────────────┬────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         │                           │                           │
         ▼                           ▼                           ▼
┌─────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   Phase 2B      │    │   Phase 3A           │    │   Phase 3B           │
│   调研链 (有token)│    │   新闻策展 (有token)  │    │   图片管道 (有token)  │
│                 │    │                     │    │                     │
│ OverviewScanner │    │   NewsCurator       │    │   ImageCurator      │
│   ↓             │    │                     │    │                     │
│ Researcher      │    │   market_news        │    │   Researcher 的      │
│   ↓             │    │   → 主题聚类         │    │   image_fetch 结果   │
│ Verifier        │    │   → 今日必读         │    │   → 选 1-2 张        │
│                 │    │   → 跨日追踪         │    │   → 上传飞书         │
└────────┬────────┘    └──────────┬──────────┘    └──────────┬──────────┘
         │                        │                          │
         └────────────────────────┼──────────────────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │   Phase 3C: 设计分析     │
                    │   DesignAnalyst (有token) │
                    │   1 款游戏深度拆解        │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   Phase 4: 排版 (有token) │
                    │   Briefer                │
                    │   融合全部 → 飞书卡片     │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   Phase 5: 推送 (0 token) │
                    │   飞书卡片 + 图片          │
                    └─────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    Phase 6: 质量审计 (每周, 有token)              │
│                    QualityAuditor                                │
│                    回溯 → 红绿灯报告                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 一、Agent 矩阵

### 调研链（每天跑，顺序依赖）

| # | Agent | 角色 | 一句话 | 工具 | 轮次 | Token |
|:--:|-------|------|--------|------|:--:|:-----:|
| 1 | **OverviewScanner** | 雷达 | 今天该看谁？ | web_search, web_fetch | 3 | ~2K |
| 2 | **Researcher** | 记者 | 发生了什么？为什么？ | web_search, web_fetch, db_query, image_fetch | 12 | ~8K |
| 3 | **Verifier** | 编辑 | 信息可信吗？ | web_search, web_fetch | 6 | ~3K |

### 策展链（每天跑，并行）

| # | Agent | 角色 | 一句话 | 工具 | 轮次 | Token |
|:--:|-------|------|--------|------|:--:|:-----:|
| 4 | **NewsCurator** | 📋 新 | 今天什么新闻值得读？ | 无（纯推理） | 1 | ~500 |
| 5 | **ImageCurator** | 📋 新 | Researcher 抓的图哪张能用？ | 无（纯推理） | 1 | ~200 |

### 深度分析（每天跑）

| # | Agent | 角色 | 一句话 | 工具 | 轮次 | Token |
|:--:|-------|------|--------|------|:--:|:-----:|
| 6 | **DesignAnalyst** | 军师 | 值不值得做？威胁多大？ | web_search | 6 | ~5K |

### 排版与推送

| # | Agent | 角色 | 一句话 | 工具 | 轮次 | Token |
|:--:|-------|------|--------|------|:--:|:-----:|
| 7 | **Briefer** | 排版 | 一切 → 6 段 Markdown | 无（纯推理） | 1 | ~1.5K |

### 质量闭环（每周跑）

| # | Agent | 角色 | 一句话 | 工具 | 轮次 | Token |
|:--:|-------|------|--------|------|:--:|:-----:|
| 8 | **QualityAuditor** | 📋 新 | 这周谁做错了什么？ | db_query | 2 | ~3K |

---

## 二、数据流：谁消费什么、产出什么

```
market_news ──→ NewsCurator ──→ NewsBrief ──→ Briefer ──→ 飞书卡片 📰 板块
                                                  ↑
taptap_new_games ─────────────────────────────────┤
steam_port_games ─────────────────────────────────┤
unreleased_games ─────────────────────────────────┤
                                                  │
rankings → Differ → changes → StoryPicker ────────┤
                        ↓                         │
                   CrossChart ────────────────────┤
                        ↓                         │
                   OverviewScanner ──→ focus ─────┤
                        ↓                         │
                   Researcher ──→ findings ───────┤
                        │    ↘                    │
                        │     image_fetch URLs ───┼──→ ImageCurator ──→ image_keys ──┘
                        ↓                         │
                   Verifier ──→ verified ─────────┤
                        ↓                         │
                   DesignAnalyst ──→ design ──────┘

一周后:
  Verifier 拒绝记录 ──→ QualityAuditor ──→ 红绿灯报告 (文件)
  Scanner 推荐记录 ──→
  Researcher 数据 ──→
  Push 状态 ──→
```

---

## 三、Steam 移植检测（3 路径判定）

`steam_ports.py` — 判定一个手游是不是 Steam PC 移植：

```
_check_steam_port(game_name, taptap_url)
    │
    ├── 路径 A: Steam 集成标记 (铁证)
    │   页面 JSON 中搜: steam_review_with_comment,
    │   steam_lowest_price, steam_bar,
    │   steam_rank_with_comment
    │   → TapTap 后台关联了 Steam App ID
    │   → 不受多端排除规则影响
    │   命中: 怪物火车2
    │
    ├── 路径 B: 文本关键词 (较强)
    │   搜: steam移植, pc移植, 端游移植,
    │   已在steam发售, steam原版
    │   命中: 霓虹深渊2
    │
    ├── 路径 C: 组合信号 (较弱)
    │   端游 + 移植 同时出现, 或 PC + 移植
    │   命中: 选技大乱斗
    │
    └── 防止误判: 如果页面有"双端上线/同步发售/全平台"
        等同步信号 → 跟移植信号做数量对比
        → 同步信号更多 → 排除 (但路径 A 不受此限制)
        命中排除: 绝区零, 鸣潮
```

---

## 四、Agent 间关系

```
        ┌─────────────────────────────────────────────┐
        │               每天跑的 7 个                   │
        │                                             │
        │  OverviewScanner                             │
        │      │                                      │
        │      ▼                                      │
        │  Researcher ────────┐                       │
        │      │              │ image URLs             │
        │      ▼              ▼                       │
        │  Verifier      ImageCurator                 │
        │      │              │                       │
        │      ├──────────────┤                       │
        │      │              │                       │
        │      ▼              ▼                       │
        │  DesignAnalyst  NewsCurator                 │
        │      │              │                       │
        │      └──────┬───────┘                       │
        │             ▼                               │
        │         Briefer                             │
        │             │                               │
        │             ▼                               │
        │         Push → 飞书                         │
        └─────────────────────────────────────────────┘

        ┌─────────────────────────────────────────────┐
        │              每周跑的 1 个                    │
        │                                             │
        │  QualityAuditor                             │
        │  读: Verifier 拒绝 + Scanner 推荐            │
        │      + Researcher 数据 + Push 状态           │
        │  写: 红绿灯报告 (文件)                        │
        └─────────────────────────────────────────────┘
```

---

## 五、数据库

| 表 | 谁写 | 谁读 |
|----|------|------|
| `rankings` | Loader | Differ, Researcher (db_query) |
| `changes` | Differ | StoryPicker, CrossChart, Scanner, Researcher |
| `daily_overviews` | OverviewScanner | Briefer, QualityAuditor |
| `research_results` | Researcher | Verifier, DesignAnalyst, QualityAuditor |
| `analysis_reports` | DesignAnalyst, Briefer | Briefer, QualityAuditor |
| `taptap_new_games` | taptap_new_games scraper | Briefer, Steam Ports, NewGameScorer (未来) |
| `steam_port_games` | steam_ports scraper | Briefer, Track Filter |
| `market_news` | news_feeds scraper | **NewsCurator**, Briefer |
| `unreleased_games` | (手动/未来 Agent) | Briefer |
| `diandian_search_cache` | diandian_search | bot.py |
| `audit_logs` | base.py (每次工具调用) | QualityAuditor |
| `fetch_cache` | web_fetch, image_fetch | Researcher (cache_hint), **ImageCurator** |
| `search_cache` | web_search | Researcher (cache_hint) |
| `in_development_tracking` | (手动/未来 PipelineTracker) | Briefer |

---

## 六、飞书卡片 6 板块

```
┌─────────────────────────────────┐
│ 📊 今日概况                      │
│ OverviewScanner 推荐 + 跨榜信号   │  ← 有 AI
├─────────────────────────────────┤
│ 🆕 新游关注                      │
│ TapTap 新游 + Steam 移植         │  ← 纯数据 (0 token)
├─────────────────────────────────┤
│ 📰 市场变动                      │
│ NewsCurator 策展摘要             │  ← 🆕 有 AI (原: 原文照搬)
├─────────────────────────────────┤
│ 📊 排名变动                      │
│ Pipeline 纯规则产出               │  ← 纯数据 (0 token)
├─────────────────────────────────┤
│ 🎮 设计洞察                      │
│ DesignAnalyst 深度分析            │  ← 有 AI
│ + ImageCurator 配图              │  ← 🆕 卡片有图了
├─────────────────────────────────┤
│ 🔴 竞争风险                      │
│ 在研公司表                       │  ← 纯数据 (0 token)
└─────────────────────────────────┘
```

---

## 七、Briefer 模板化设计

### 7.1 为什么改

Briefer 现在是每次生成完整飞书卡片 JSON——包括元素类型、布局参数、按钮配置。这些结构从来不变，变的是里面的文字。让 LLM 每次重新生成结构：

- 浪费 token（一半消耗在 JSON 结构上）
- 容易格式错误（飞书拒收）
- 改卡片样式要调 prompt（而不是改代码）

### 7.2 改法：模板 + 填空

```
改前:  Briefer → 完整飞书卡片 JSON (结构 + 内容一把梭)
改后:  Briefer → 6 段 Markdown 内容
      card_builder.py → 填进模板 → 完整飞书卡片 JSON
```

### 7.3 模板（写死在 `card_builder.py` 里）

```python
CARD_TEMPLATE = {
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {"tag": "plain_text", "content": "竞品日报 {{DATE}}"},
            "template": "blue",
        },
        "elements": [
            # ── 板块 1 ──
            {"tag": "div", "text": {"tag": "lark_md", "content": "**📊 今日概况**"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": "{{OVERVIEW_SECTION}}"}},
            {"tag": "hr"},

            # ── 板块 2 ──
            {"tag": "div", "text": {"tag": "lark_md", "content": "**🆕 新游关注**"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": "{{NEW_GAMES_SECTION}}"}},
            {"tag": "hr"},

            # ── 板块 3 ──
            {"tag": "div", "text": {"tag": "lark_md", "content": "**📰 市场变动**"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": "{{NEWS_SECTION}}"}},
            {"tag": "hr"},

            # ── 板块 4 ──
            {"tag": "div", "text": {"tag": "lark_md", "content": "**📊 排名变动**"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": "{{RANKING_SECTION}}"}},
            {"tag": "hr"},

            # ── 板块 5 ──
            {"tag": "div", "text": {"tag": "lark_md", "content": "**🎮 设计洞察**"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": "{{DESIGN_SECTION}}"}},
            {"tag": "hr"},

            # ── 板块 6 ──
            {"tag": "div", "text": {"tag": "lark_md", "content": "**🔴 竞争风险**"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": "{{RISK_SECTION}}"}},
        ],
    },
}
```

### 7.4 Briefer 的新输出

```python
class BriefContent(BaseModel):
    """Briefer 现在只生成 6 段 Markdown，不碰 JSON 结构"""
    overview_section: str       # 今日概况
    new_games_section: str      # 新游关注
    news_section: str           # 市场变动
    ranking_section: str        # 排名变动
    design_section: str         # 设计洞察
    risk_section: str           # 竞争风险
```

### 7.5 拼装流程

```python
def build_card(brief: BriefContent, date: str, images: CuratedImages | None) -> dict:
    """把 Briefer 的 6 段 Markdown 填进模板，输出飞书卡片 JSON。"""
    
    # 1. 纯文本替换——把 {{xxx}} 换成 Briefer 产出的 Markdown
    card = json.loads(json.dumps(CARD_TEMPLATE))  # deep copy
    elements_str = json.dumps(card["card"]["elements"])
    elements_str = elements_str.replace("{{DATE}}", date)
    elements_str = elements_str.replace("{{OVERVIEW_SECTION}}", brief.overview_section)
    elements_str = elements_str.replace("{{NEW_GAMES_SECTION}}", brief.new_games_section)
    elements_str = elements_str.replace("{{NEWS_SECTION}}", brief.news_section)
    elements_str = elements_str.replace("{{RANKING_SECTION}}", brief.ranking_section)
    elements_str = elements_str.replace("{{DESIGN_SECTION}}", brief.design_section)
    elements_str = elements_str.replace("{{RISK_SECTION}}", brief.risk_section)
    card["card"]["elements"] = json.loads(elements_str)
    
    # 2. 有图就嵌入图片元素
    if images:
        for img in images.selected:
            card["card"]["elements"].insert(-2, {
                "tag": "img",
                "img_key": img.image_key,
                "alt": {"tag": "plain_text", "content": img.game_name},
            })
    
    return card
```

### 7.6 改完之后的数据流

```
OverviewScanner ──→ focus ─────────────────────┐
Researcher ──→ findings ───────────────────────┤
Verifier ──→ verified ─────────────────────────┤
NewsCurator ──→ NewsBrief ─────────────────────┤
ImageCurator ──→ image_keys ───────────────────┤
DesignAnalyst ──→ design ──────────────────────┤
taptap/steam/news/unreleased ── 直读 DB ────────┤
changes/cross_signals ── Pipeline 产出 ────────┘
                                    │
                                    ▼
                            ┌──────────────┐
                            │   Briefer     │  ← 只生成 6 段 Markdown
                            │   纯推理       │    不碰 JSON 结构
                            │   ~1.5K token │
                            └──────┬───────┘
                                   │ BriefContent (6 个 str)
                                   ▼
                            ┌──────────────┐
                            │ card_builder  │  ← 纯代码，0 token
                            │ 模板填空       │    填 {{OVERVIEW_SECTION}}
                            │ 嵌入图片       │    塞 image_keys
                            └──────┬───────┘
                                   │ 完整飞书卡片 JSON
                                   ▼
                            ┌──────────────┐
                            │   Push        │
                            │   飞书卡片     │
                            └──────────────┘
```

### 7.7 好处

| 维度 | 改前 | 改后 |
|------|------|------|
| Briefer token | ~3K | **~1.5K**（省一半） |
| 卡片格式正确率 | 偶尔飞书拒收 | **永不出错**（结构写死在代码里） |
| 改卡片样式 | 调 prompt → 测 → 再调 | **改模板**，不改 prompt |
| 加按钮 / 改布局 | Briefer 要重学飞书规则 | **模板里加**，Briefer 无感知 |
| 图片嵌入 | Briefer 不管 | **card_builder 自动塞** |

---

## 八、Token 预算

| 环节 | Agent | 每天消耗 |
|------|-------|---------|
| 调研 | OverviewScanner | ~2K |
| 调研 | Researcher (×N 并行) | ~8K × N |
| 调研 | Verifier (×N 并行) | ~3K × N |
| 策展 | NewsCurator | ~500 |
| 策展 | ImageCurator | ~200 |
| 分析 | DesignAnalyst | ~5K |
| 排版 | Briefer（模板化后） | ~1.5K |
| **每天合计** | (按 N=3 估算) | **~48K token** |
| **每周审计** | QualityAuditor | ~3K (一次性) |

---

## 九、已砍掉的

| Agent | 原因 |
|-------|------|
| Analyst | 商业因果推理被 DesignAnalyst 的竞争风险维度 + Scanner 的跨榜信号覆盖 |
| NewGameWatcher | TapTap 数据 Briefer 直读 DB 展示，不需要 Agent 中转 |
| MarketNewsScanner | NewsCurator 替代——做策展而不是扫描 |
