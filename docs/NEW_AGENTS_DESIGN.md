# 三个新 Agent 设计文档

> 最后更新：2026-06-22
> 状态：设计阶段，待实现

---

## 一、概览

三个 Agent 填补当前流水线中"数据有了但没人分析"的缺口：

| Agent | 消费的数据 | 产出 | 运行频率 | Token 消耗 |
|-------|-----------|------|---------|-----------|
| **NewsCurator** | `market_news` 表 | 今日必读 3-5 条 + 主题聚类 + 跨日追踪 | 每天 | ~500/次 |
| **ImageCurator** | Researcher 的 image_fetch 结果 | 1-2 张精选图片的 `image_key` | 每天 | ~200/次 |
| **QualityAuditor** | 一周的 Verifier 拒绝 + Scanner 推荐 + 卡片反馈 | 质量红绿灯报告 | 每周 | ~3000/次 |

三者都在现有基础设施上构建——不需要新的数据源、不需要新的 API Key、不需要改数据库 Schema。

---

## 二、NewsCurator — 新闻策展 Agent

### 2.1 为什么需要

当前 `market_news` 每天爬 游侠/17173 头条 → Briefer 原文照搬到卡片。20 条新闻里可能 18 条跟赛道无关，用户需要自己筛。

NewsCurator 做的事：**把"信息流"变成"信息摘要"**。

### 2.2 输入

```python
# 从 DB 直读，不经任何预处理
market_news = db.get_market_news_by_date(date)  # 列表，每条含 title/url/source/summary
track_config = competitor_list["track_config"]    # 赛道关键词 + 可无视关键词
```

### 2.3 输出

```python
class NewsBrief(BaseModel):
    top_stories: list[TopStory]          # 3-5 条今日必读
    topic_clusters: list[TopicCluster]   # 主题聚类
    escalating_stories: list[Escalating] # 跨日追踪：连续出现的话题

class TopStory(BaseModel):
    title: str
    url: str
    source: str                         # 游侠 / 17173
    why_matters: str                    # 一句话，为什么赛道相关
    urgency: str                        # "立即关注" | "本周关注" | "了解即可"
    track_tags: list[str]               # 命中了哪些赛道关键词

class TopicCluster(BaseModel):
    topic: str                          # "版号" / "大厂动态" / "出海" / "新品" / "政策" / ...
    count: int                          # 今天这个主题有几条
    highlight: str                      # 一句话概括今天这个主题最重要的信息
    stories: list[str]                  # 属于这个主题的新闻标题

class Escalating(BaseModel):
    keyword: str                        # 反复出现的关键词（公司名/游戏名/政策名）
    days_seen: int                      # 连续出现天数
    latest_headline: str
    escalation_note: str                # "连续3天出现 → 可能是大事"
```

### 2.4 Agent 设计

```
max_tool_rounds: 1   ← 纯推理，不需要搜索
max_tokens: 2048
tool: 无              ← 数据全在输入里，不需要额外搜索
```

**Prompt 要点**：
- 用 `track_config` 判断每条新闻的赛道相关度
- 相同主题的新闻归到一个 cluster，不要每条都单独列
- 跨日追踪需要查前几天的 `market_news`（由调用方传入 `recent_news`）
- 优先级：赛道直接相关 > 行业大趋势 > 纯资讯

### 2.5 集成点

```
Phase 0A: news_feeds 抓取 → market_news 表
    ↓
Phase 4 (新增): NewsCurator 读取 market_news → 产出 NewsBrief
    ↓
Phase 4: Briefer 读取 NewsBrief（代替原文照搬）→ 卡片 📰 板块
```

**Briefer 侧的改动**：`brief()` 多接收一个 `news_brief: NewsBrief | None` 参数。卡片 📰 板块改为：
```
📰 市场变动
   
🔥 今日必读
· 《2026年6月版号下发》 — 3款塔防游戏获批 [立即关注]
· 《XX大厂宣布进军塔防赛道》 — 竞品动态 [本周关注]

📂 主题分布
版号(3) | 大厂动态(5) | 出海(2) | 新品(8)
```

### 2.6 CLI

```bash
python -m src.agents.news_curator --date 2026-06-22
python -m src.agents.news_curator --date 2026-06-22 --verbose  # 打印完整 NewsBrief JSON
```

---

## 三、ImageCurator — 图片管道 Agent

### 3.1 为什么需要

Researcher 用 `image_fetch` 抓了游戏截图 → 存到 fetch_cache 表 → 然后**没人用**。Briefer 不调 `upload_images_for_card()`，飞书卡片纯文字。

ImageCurator 做的事：**在 Researcher → Briefer 之间插一个轻量选择，挑最有信息量的图，上传到飞书，把 `image_key` 传给 Briefer**。

### 3.2 输入

```python
# 从 Researcher 的 output 中提取
findings = research_result["findings"]  # 每个 finding 可能有 image_urls
# 从 fetch_cache 表查 Researcher 这轮抓到的所有图片
image_urls = db.get_fetch_cache_by_run(run_id, content_type="image")
```

### 3.3 输出

```python
class CuratedImages(BaseModel):
    selected: list[SelectedImage]  # 1-2 张

class SelectedImage(BaseModel):
    image_key: str                 # 飞书上传后的 key
    url: str                       # 原始 URL
    game_name: str
    selection_reason: str          # "玩法截图，展示了核心战斗界面" | "宣传图，展示了新角色"
    category: str                  # "gameplay" | "promotional" | "icon"
```

### 3.4 Agent 设计

```
max_tool_rounds: 1   ← 纯推理选图
max_tokens: 1024
tool: 无              ← 不搜索，只选图
```

**选择逻辑**（在 prompt 里描述）：
1. 玩法截图 > 宣传图 > icon
2. 多款游戏时，每款选 1 张，优先选赛道游戏
3. 图片 URL 如果来自已知不可达域名 → 跳过
4. 如果没有任何可用图片 → 返回空，Briefer 降级为纯文字

**上传逻辑**（在 Agent 外，调用现有 `upload_images_for_card()`）：
```python
def curate_and_upload(date: str) -> CuratedImages | None:
    # 1. 查 Researcher 抓到的图片
    image_urls = _get_researcher_images(date)
    if not image_urls:
        return None
    
    # 2. Agent 选图
    curated = image_curator.run(image_urls=image_urls)
    
    # 3. 上传到飞书
    for img in curated.selected:
        img.image_key = pusher.upload_image(img.url)
    
    return curated
```

### 3.5 集成点

```
Phase 2B: Researcher (image_fetch 抓图)
    ↓
Phase 4 (新增): ImageCurator 选图 → 上传飞书 → 产出 image_keys
    ↓
Phase 4: Briefer 接收 image_keys → 嵌入卡片
```

**Briefer 侧的改动**：`brief()` 多接收一个 `images: CuratedImages | None` 参数。卡片中游戏分析板块嵌入对应的图片。

### 3.6 CLI

```bash
python -m src.agents.image_curator --date 2026-06-22
```

---

## 四、QualityAuditor — 质量审计 Agent

### 4.1 为什么需要

Verifier 天天拒绝发现，Scanner 推荐了不重要的游戏，Briefer 卡片里可能有格式错误——但**没人告诉它们"你这里做错了"**。系统没有反馈闭环。

QualityAuditor 做的事：**每周回溯一次，找出系统性质量问题，输出人可读的改进建议**。

### 4.2 输入

```python
# 从 DB 读本周所有数据
week_dates = [today - i for i in range(7)]

# Verifier 数据
verifier_stats = {
    date: {
        "total_findings": N,
        "passed": N,
        "rejected": N,
        "avg_total_score": 3.2,
        "rejection_reasons": [...],      # 从 verification_notes 提取
        "low_authority_count": N,        # source_authority < 3
        "weak_causality_count": N,       # causal_logic < 3
    }
    for date in week_dates
}

# Scanner 数据
scanner_stats = {
    date: {
        "recommended_count": N,
        "cross_chart_signals_in_focus": N,
        "track_games_in_skip": [...],    # 赛道游戏被错误跳过的
        "non_track_games_in_focus": [...], # 非赛道游戏被错误推荐的
    }
    for date in week_dates
}

# Researcher 数据
researcher_stats = {
    date: {
        "total_findings": N,
        "dimension_coverage": {...},     # event/gameplay/player/design/in_dev 各维度的 finding 数
        "fetch_failure_rate": 0.15,     # web_fetch 失败比例
        "avg_tool_rounds_used": 8.3,
    }
}

# 卡片推送状态
push_stats = {
    date: {"success": True/False, "error": "..."}
}
```

### 4.3 输出

```python
class AuditReport(BaseModel):
    week_range: str                      # "2026-06-16 ~ 2026-06-22"
    overall_grade: str                   # "🟢 健康" | "🟡 需关注" | "🔴 有问题"
    
    # 四个维度的分报告
    verifier_health: VerifierHealth
    scanner_accuracy: ScannerAccuracy
    researcher_health: ResearcherHealth
    pipeline_reliability: PipelineReliability
    
    # 改进建议
    recommendations: list[Recommendation]

class VerifierHealth(BaseModel):
    status: str                          # green / yellow / red
    avg_pass_rate: float                 # 本周平均通过率
    top_rejection_reasons: list[str]     # 拒绝原因 Top 3
    systemic_issues: list[str]           # "来源权威性不足是主要拒绝原因，占 60%"
    suggestion: str                      # "建议 Researcher 优先搜官方渠道，减少论坛/自媒体来源"

class ScannerAccuracy(BaseModel):
    status: str
    precision: float                     # 推荐的游戏中，被 Analyst 认为有洞察的比例
    track_recall: float                  # 赛道游戏中，被正确推荐的比例
    missed_signals: list[str]            # 被漏掉的重要游戏
    false_positives: list[str]           # 被推荐但不重要的游戏
    suggestion: str

class ResearcherHealth(BaseModel):
    status: str
    dimension_gaps: list[str]            # 哪些维度经常空
    fetch_reliability: float             # web_fetch 成功率
    avg_rounds_used: float
    source_quality_distribution: dict    # 官方/媒体/自媒体/论坛/匿名的比例
    suggestion: str

class PipelineReliability(BaseModel):
    status: str
    scraper_failures: list[str]          # 哪天哪个 scraper 挂了
    push_failures: list[str]             # 哪天推送失败
    data_gaps: list[str]                 # 哪天缺数据
    suggestion: str

class Recommendation(BaseModel):
    priority: str                        # "P0" | "P1" | "P2"
    target: str                          # "Researcher" | "Scanner" | "Verifier" | "Briefer" | "Pipeline"
    problem: str                         # 一句话描述问题
    action: str                          # 一句话建议行动
    expected_impact: str                 # 一句话预期效果
```

### 4.4 Agent 设计

```
max_tool_rounds: 2   ← 第一轮分析，第二轮自检
max_tokens: 4096
tool:
  - db_query          ← 查 audit_logs 表分析工具调用失败模式
  - web_search        ← 不做（纯数据分析）
```

**两轮设计**：
1. **轮 1**：读入所有统计数据 → 产出 AuditReport 草稿
2. **轮 2**：自检草稿——"这个结论有没有数据支撑？有没有遗漏的维度？" → 修正后输出

### 4.5 集成点

```
每周一自动触发（或手动 /audit）
    ↓
QualityAuditor 读 DB → 产出 AuditReport
    ↓
人看报告 → 决定要不要改 prompt / 调参数 / 修 bug
```

**不自动改代码**——QualityAuditor 只诊断，不下药。后续可以做第二个 Agent 根据 AuditReport 自动调 prompt，但当前阶段保持人在回路中。

### 4.6 触发方式

```bash
# 审计本周
python -m src.agents.quality_auditor

# 审计指定周
python -m src.agents.quality_auditor --week 2026-06-16

# 输出到文件
python -m src.agents.quality_auditor --output audit_2026W25.md
```

也可以通过 Skill 触发（在 `/audit` skill 中集成）。

---

## 五、流水线变更

### 5.1 新 Phase 顺序

```
Phase 0A: Scrape（并行，0 token）          ← 不变
Phase 0B: Loader（0 token）                 ← 不变
Phase 0C: Track Filter（0 token）           ← 不变
Phase 1:  Differ → StoryPicker → CrossChart ← 不变
Phase 2B: OverviewScanner → Researcher ‖ Verifier ← 不变

Phase 3A: NewsCurator（有 token）           ← 🆕
Phase 3B: ImageCurator（有 token）          ← 🆕
Phase 3C: DesignAnalyst（有 token）         ← 不变，重编号

Phase 4:  Briefer（有 token）               ← 接收 NewsBrief + CuratedImages
Phase 5:  Push → 飞书卡片                   ← 不变

Phase 6:  QualityAuditor（每周，有 token）  ← 🆕
```

### 5.2 `runner.py` 改动

```python
# Phase 3A: News Curation
news_brief = None
if market_news:
    news_brief = run_news_curator(date, verbose=verbose)

# Phase 3B: Image Curation
curated_images = None
if research_results:
    curated_images = curate_and_upload(date)

# Phase 3C: Design Analyst (unchanged)
design_analysis = run_design_analyst_if_needed(date, verbose=verbose)

# Phase 4: Briefer (now receives news_brief + curated_images)
card = briefer.brief(
    ...,
    news_brief=news_brief,
    curated_images=curated_images,
)
```

### 5.3 Briefer 改动

`brief()` 新增两个可选参数，兼容旧调用：

```python
def brief(
    ...,
    news_brief: NewsBrief | None = None,       # 🆕
    curated_images: CuratedImages | None = None, # 🆕
) -> dict:
```

- 有 `news_brief` → 📰 板块用结构化摘要
- 无 `news_brief` → 降级为原文照搬（旧行为）
- 有 `curated_images` → 卡片嵌入图片
- 无 `curated_images` → 纯文字卡片（旧行为）

---

## 六、数据库变更

**不需要新表**。三个 Agent 都只读现有数据：

| Agent | 读哪些表 |
|-------|---------|
| NewsCurator | `market_news` |
| ImageCurator | `fetch_cache`（图片 URL） |
| QualityAuditor | `research_results`、`daily_overviews`、`analysis_reports`、`audit_logs` |

QualityAuditor 的报告可以存文件（`data/audit_reports/YYYY-MM-DD.md`），不进数据库。

---

## 七、提示词文件

新增 3 个 YAML：

```
prompts/
├── news_curator.yaml       # 🆕
├── image_curator.yaml      # 🆕
├── quality_auditor.yaml    # 🆕
├── overview_scanner.yaml   # 不变
├── researcher.yaml         # 不变
├── verifier.yaml           # 不变
├── design_analyst.yaml     # 不变
└── briefer.yaml            # 修改：接收 news_brief + curated_images
```

---

## 八、实施顺序

| 顺序 | Agent | 理由 |
|:---:|--------|------|
| 1 | **ImageCurator** | 改动最小（只在 Researcher→Briefer 间插一层），效果最直观（卡片从纯文字变图文） |
| 2 | **NewsCurator** | 改动中等（新增 Agent + Briefer 改 📰 板块），日活用户能直接感知质量提升 |
| 3 | **QualityAuditor** | 改动最大（需要设计统计逻辑），但不需要每天跑，可以慢慢迭代 |

ImageCurator 先做——它只需要改 `runner.py` 里加一个步骤 + Briefer prompt 里加图片相关的指令，零数据库变更。
