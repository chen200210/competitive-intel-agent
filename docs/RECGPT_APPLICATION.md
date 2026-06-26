# RecGPT：阿里 LLM 推荐系统落地方案 & 应用指南

> 来源：王喆《RecGPT：阿里大模型推荐系统落地方案》(arxiv:2507.22879)
> 日期：2026-06-25 | 状态：五个模式全部落地

---

## 目录

1. [RecGPT 论文全解](#一recgpt-论文全解)
2. [五个工业设计模式](#二五个工业设计模式)
3. [模式在日报系统的落地](#三模式在日报系统的落地)
4. [系统日常运行](#四系统日常运行)
5. [映射总表](#五映射总表)

---

## 一、RecGPT 论文全解

### 1.1 背景与问题

淘宝"猜你喜欢"推荐系统面临的核心困境：**协同过滤只看 ID，不懂语义**。

传统双塔模型知道"用户 A 和用户 B 行为相似"，但不知道为什么相似。用户今天搜索了"户外登山靴"，明天浏览了"防晒霜"——协同过滤只看到两个不相关的商品 ID，看不出背后的语义联系："这人在准备户外旅行"。

RecGPT 的解法：**把 LLM 的语义理解能力嵌入推荐系统的召回层**。不是让 LLM 替代推荐算法，而是让 LLM 补足协同过滤的语义盲区。

在线效果（全量部署在淘宝首页"猜你喜欢"）：

| 指标 | 提升 |
|------|------|
| CTR（点击率） | +6.33% |
| IPV（页面浏览量） | +9.47% |
| DCAU（日活点击用户） | +3.72% |
| CICD（内容兴趣点击多样性） | +6.96% |
| ATC（加购） | +3.91% |

同时有效**缓解了马太效应**——长尾商品获得更公平的曝光。

### 1.1.5 输入压缩：行为序列三层过滤

用户行为序列是 LLM 的输入，但原始数据量巨大。RecGPT 在输入 LLM 前做三层压缩，使 **98% 用户序列适配 128K 上下文**：

```
第一层 — 信号强度过滤：
  只保留强兴趣信号（购买、加购、收藏、搜索），丢弃弱信号（点击、评论浏览）

第二层 — 字段精简：
  商品信息只保留关键字段（名称、类别、品牌），丢弃详情描述、图片 URL 等冗余字段

第三层 — 时间维度聚合：
  同一时间段内的重复行为合并为一次；经常同时出现的商品组合为一个"会话单元"
```

这三层压缩和我们 pipeline 的新闻压缩思路完全一致：body 截断 500 字（字段精简）、关键词过滤弱信号新闻（信号强度）、7 天新鲜度门禁（时间聚合）。RecGPT 的压缩并非特例——**任何把非结构化数据喂给 LLM 的系统都需要类似的压缩策略**。

### 1.2 核心架构：三塔召回

RecGPT 把传统双塔（用户塔 + 物品塔）扩展为三塔：

```
用户行为序列（压缩后）
       │
       ├──→ LLM_UI（用户兴趣挖掘）──→ 兴趣标签集合
       │                                      │
       ├──→ LLM_IT（商品标签预测）──→ Tag 塔 ─┤
       │                                      │
       └──→ 原始行为序列 ──→ User 塔 ────────┼──→ 融合分数 ŷ
                                              │
                        Item 属性 ──→ Item 塔 ┘
```

**三个塔**：

| 塔 | 输入 | 处理 | 输出 |
|----|------|------|------|
| User 塔 | 用户 ID + 多行为序列 | ID embedding + 行为均值池化 + DNN | hᵤ |
| Item 塔 | 物品特征（ID/类目/品牌/价格） | 稀疏+dense 特征拼接 + DNN | hᵥ |
| Tag 塔 | LLM_IT 生成的商品标签文本 | 分词 + token embedding 均值池化 + DNN | hₜ |

**分数融合**：

```
ŷ_col   = hᵤᵀ · hᵥ          (协同分数：用户-物品行为相关性)
ŷ_sem   = hₜᵀ · hᵥ          (语义分数：标签-物品语义相关性)
ŷ_final = β·ŷ_col + (1-β)·ŷ_sem    (β=0.5 时为平衡点)
```

**β 的含义**：β→1 偏向推荐"相似人群喜欢什么"（安全但同质化），β→0 偏向 LLM 推断"你可能想要什么"（新颖但可能不准）。β=0.5 在探索与利用之间取得平衡。

**在线推理**：预计算融合向量 `h_fuse = β·hᵤ + (1-β)·hₜ`，直接做 ANN 最近邻检索——不需要实时调用 LLM。

**损失函数**：三塔联合训练，总损失为三项加权和：

```
L_TAR = L_col + α·L_tag + (1-α)·L_cate

L_col  — 协同优化：拉近用户与其点击物品的距离（负采样对比学习）
L_tag  — 语义优化：拉近 LLM 标签与对应物品的距离
L_cate — 类别对比：细粒度区分同类别下不同商品（如"登山靴" vs "雪地靴"）
```

这个三层损失设计体现了 RecGPT 的核心哲学：**LLM 标签不是替代协同过滤，而是补充它**。L_col 保留传统推荐的全部能力，L_tag 叠加 LLM 的语义理解，L_cate 防止 LLM 标签过度泛化。

### 1.3 三个 LLM 任务及其 Prompt 结构

RecGPT 包含三个 LLM 调用点，每个都遵循同一个**五段式 prompt 模板**：

```
┌─ Role ──────────────────────────────────┐
│ 一句话限定角色：你是XXX                   │
├─ Input ─────────────────────────────────┤
│ 压缩后的行为数据/用户画像/上下文           │
├─ Requirements ──────────────────────────┤
│ 硬约束：数量门槛、排除条件、格式要求       │
├─ Matched Interest Pool ─────────────────┤
│ 预设合法输出空间，LLM 只能从中选择          │
├─ Output ────────────────────────────────┤
│ 结构化 JSON format                       │
└─────────────────────────────────────────┘
```

#### LLM_UI — 用户兴趣挖掘

**定位**：从原始行为序列中显式提取用户兴趣。是整个链路的起点。

```
Role:    "你是电商平台购物顾问"
Input:   用户画像(年龄/性别/所在地) + 压缩行为序列(购买/加购/搜索/收藏)
         上下文(季节/节日)
Req:     区分长期兴趣 vs 短期场景；交叉参考性别年龄排除不符兴趣
         至少生成 10 个兴趣；意愿性(排除被动消费)；合理性(有行为证据)
Pool:    3000+ 预设兴趣标签（对接平台商品分类体系）
Output:  [{ID, Interest, Stage(长期/短期), Reason}...]
```

**两阶段设计理由**：先提炼大方向（"对户外运动感兴趣"），再细化为标签（"户外防水登山靴"）——比直接让 LLM 预测标签稳定得多。这是 CoT 的工业应用：不是让 LLM "一步步想"，而是你主动拆成两步，每步独立约束。

#### LLM_IT — 商品标签预测

**定位**：把 LLM_UI 的抽象兴趣翻译为可检索的商品标签。粒度从"家居装饰"细化到"北欧简约台灯"。

```
Role:    "你是淘宝专业商品推荐专员"
Input:   用户属性 + LLM_UI 输出 + 原始行为 + 当前季节
Req:     五大约束——
         ① 兴趣一致性（标签与 LLM_UI 输出对齐）
         ② 多样性（至少 50 个标签）
         ③ 语义精确（格式 = "修饰词 + 核心词"，禁止模糊描述）
         ④ 时效新鲜度（排除近 1 个月交互过的商品）
         ⑤ 季节相关性
Pool:    平台标准化商品标签体系
Output:  [{Item Tag, Interest, Reason}...]  三元组
```

**质量控制**：四维拒绝采样——相关性（与兴趣对齐否）、一致性（参考画像行为否）、特异性（避免泛术语）、有效性（标签对应商品真实存在）。

**增量学习**：每两周更新一次 LLM_IT，三步流程：

```
数据净化：QwQ-32B 过滤用户行为中的噪声交互（误点、误触）
兴趣补全：推断每次交互背后的兴趣和理由（填补用户未显式表达的意图）
数据平衡：用户内最多 80 标签 + 类别级二次采样（防止头部用户/类别主导训练）
```

效果：微调后的 TBStars-SFT 标签通过率 **88.80%**，超过未微调的 DeepSeek-R1（80.00%）。但关键结论是 **DeepSeek-R1 不微调也能用**——通过率差距仅 8.8 个百分点，对中小团队而言，20% 成本完成 80% 效果。

#### LLM_RE — 推荐解释生成

**定位**：给用户看的推荐理由，如"夏日穿搭清爽有型"。CoT 显式拆为两步：

```
Role:    基于用户画像和推荐商品生成个性化推荐解释
Input:   用户兴趣 + 当前日期 + 商品信息(标签/标题)
CoT:     Step 1 上下文理解：提取商品核心特征
         Step 2 解释生成：合成有创意、有感染力的推荐理由
Req:     长度 6-10 字；自然流畅可幽默；禁止编造功能/万能词汇/口号套话
Output:  {Explanation: "夏日穿搭清爽有型"}
```

**工程关键**：所有推荐解释**离线预计算**——"兴趣 × 类别 → 解释" 映射表，在线直接查表，零实时 LLM 推理延迟。

### 1.4 训练与数据策略

**行为序列压缩**（输入 LLM 前的三次过滤）：

```
第一层：只保留强兴趣信号（购买/加购/收藏/搜索），丢弃弱信号（点击/浏览）
第二层：商品信息只保留关键字段（名称/类别/品牌），丢弃详情/图片
第三层：时间维度聚合（同时间段重复行为合并，经常同时出现的 item 聚合）
```

效果：98% 用户序列适配 128K 上下文窗口。

**Fine-tuning 三步走**：

```
Step 1 — 多任务微调：16 个微调训练集、16.3K 样本
Step 2 — 推理增强预对齐：DeepSeek-R1 生成高质量训练集 → 人工精选 → 预对齐
Step 3 — 自训练进化：模型自生成样本 → Human-LLM 协同评估 → 持续学习
```

**基座模型选型**：微调效果取决于基座模型的推理能力。RecGPT 对比了三个基座：

| 模型 | 兴趣生成通过率 | 标签预测通过率 | 备注 |
|------|:---------:|:---------:|------|
| DeepSeek-R1（未微调） | 高 | 80.00% | **不微调也能用** |
| Qwen3-SFT（微调后） | 略高 | 略高 | 微调成本高，收益递减 |
| TBStars-3.5B（微调后） | 接近 | **88.80%** | 微调后最优，但仅领先 8.8pp |

**关键结论**：DeepSeek-R1 **不微调也能用**——标签通过率 80%，微调后最优模型仅领先 8.8 个百分点。中小团队 20% 成本完成 80% 效果。这也是我们选择 DeepSeek 作为 Summarizer 和 Calibrator 基座的原因——prompt engineering + 反馈数据已经足够，不需要微调。

### 1.5 LLM as a Judge 评估链

RecGPT 的质量保障不是靠人工标注，而是靠**评估链**：

```
人类标注少量测试集（种子）
       ↓
微调 LLM Judge（评估模型）
       ↓
LLM Judge 评估任务模型（LLM_UI / LLM_IT / LLM_RE 的输出）
       ↓
分类结果反馈 → 定期人工抽检 Judge 误判 → 修正 Judge → 循环
       ↑                                               ↓
       └──────────── 修正后的 Judge 重新评估 ────────────┘
```

三个任务的评判维度：

| 任务 | 评判维度 |
|------|---------|
| 用户兴趣挖掘 | Willingness（意愿性）, Reasonableness（合理性） |
| 标签预测 | Relevance, Consistency, Specificity, Validity |
| 推荐归因 | Relevance, Factuality, Clarity, Safety |

**核心逻辑**：人监督 Judge → Judge 监督 SFT-LLM。人不直接评估每个 LLM 输出（不可扩展），而是只抽检 Judge 的误判（可扩展）。

---

## 二、五个工业设计模式

从 RecGPT 论文中可以提炼出五个**跨场景通用的 LLM 工业落地模式**：

### 模式 1：Matched Interest Pool（输出空间约束）

**问题**：LLM 自由发挥会输出不在你体系内的分类/标签/参数，下游无法消费。

**解法**：在 prompt 里放一个预设的合法输出空间，LLM 只能在这个池子里选。RecGPT 用 3000+ 标签池约束 LLM_UI 的输出，对接下游商品分类体系。

**本质**：传统 prompt engineering 靠"需求描述"引导 LLM 行为。Matched Pool 更进一步——靠"合法集合"硬约束 LLM 输出。你告诉 LLM 的不是"请给出好的输出"，而是"你的输出必须从以下选项中选"。

### 模式 2：CoT 显式拆分（任务分解）

**问题**：一步到位的 prompt 输出质量不稳定——摘要和打分混在一起，LLM 倾向"先打分再写配合分数的摘要"。

**解法**：不让 LLM "自己一步步想"，而是**你主动把任务拆成多个阶段**，每个阶段独立 prompt 和约束。RecGPT 把用户理解拆成 LLM_UI（大方向）→ LLM_IT（细粒度标签）两步；推荐归因拆成"上下文理解 → 解释生成"两步。

**本质**：CoT 的工业形态不是 "let's think step by step"，而是 **"我已经把任务拆成了 N 步，你按顺序执行"**。每一步的输入输出都是结构化的，不会因为上一步的自由文本污染下一步。

### 模式 3：β-Fusion（代码信号 + AI 信号融合）

**问题**：LLM 打分可能离谱（给空洞通稿打 75 分，给深度报道打 25 分）。纯规则打分又太死板（不知道"独立游戏深度报道"比"大厂公关稿"更有价值）。

**解法**：代码层计算一个 **signal_score**（正则/规则，零 token），和 AI 的 **ai_score** 做加权融合：`fused = β × signal + (1-β) × ai`。RecGPT 在三塔召回中做 `β × 协同分数 + (1-β) × 语义分数`。

**本质**：LLM 提供语义增量，**补充而非替代**规则系统。β 控制两者的权重——β=0 全靠 AI（可能不靠谱），β=1 全靠规则（太死板），β=0.3~0.5 在两者间平衡。代码信号还起到"锚"的作用，防止 LLM 打分集体塌缩到中间段。

### 模式 4：LLM as a Judge（评估链）

**问题**：没有足够人工标注来评估 LLM 输出质量。AI 打分到底准不准？用户反馈是终极信号，但太稀疏。

**解法**：人类标注少量测试集 → 微调 LLM Judge → LLM Judge 评估任务模型 → 定期人工抽检 Judge。人不直接评估每个 LLM 输出（不可扩展），只抽检 Judge 的误判（可扩展）。

**本质**：Judge 本身也是一个 LLM——你用 LLM 的推理能力来评估另一个 LLM 的输出质量。适合"正确性有模糊地带"的任务：评分、标签、推荐理由。

### 模式 5：离线预计算（LLM 离线干活，在线零延迟）

**问题**：实时 LLM 推理延迟不可接受（推荐系统要求 <100ms）。

**解法**：LLM 离线计算 → 结果持久化（DB/文件/lookup table）→ 在线直接读取。RecGPT 的推荐解释是预计算的"兴趣 × 类别 → 解释"映射表，在线查表返回，不调 LLM。

**本质**：LLM 适合做**分析型**任务（理解、判断、归因），不适合做**响应型**任务（必须在 100ms 内返回）。把你的 LLM 调用分成两类：离线分析的延迟容忍、在线查询的延迟敏感。让 LLM 只干前者，在线永远走缓存。

---

## 三、模式在日报系统的落地

### 3.1 系统当前架构

三个 LLM 触达点缩减为**一个**：

```
Scraper 抓取 (0 token)
   │
   ├─→ 排名/新游/移植/新闻 → DB (0 token)
   │
   ├─→ 规则管线 (0 token)
   │   filter_news → apply_fatigue → deep_fetch → _extract_body_signals
   │
   ├─→ Summarizer AI (1 次 LLM 调用) ← 唯一的 token 消耗点
   │   包含四个模式的全部效果：
   │   · CoT 5步推理 → 更稳定的打分
   │   · Matched Verdict Pool → 结构化标签
   │   · β-Fusion → 代码信号融合
   │   · topic_boosts → Calibrator 调参生效
   │
   ├─→ 代码拼装 markdown (0 token)
   │   sorted_news → md_blocks → 飞书卡片 JSON
   │
   └─→ 飞书推送 (0 token)
```

### 3.2 模式落地详情

#### 模式 1: Matched Interest Pool → Matched Verdict Pool

**RecGPT 做法**：3000+ 兴趣标签池约束 LLM_UI 输出。

**我们的做法**：6 正面 + 6 负面标签池约束 Summarizer 的判词输出。AI 必须从池中选标签，不能自创。

**文件**：`prompts/summarizer.yaml`（标签池定义）+ `src/agents/scorer.py`（`_VALID_POS_LABELS` / `_VALID_NEG_LABELS` 代码层校验）

**数据流**：
```
summarizer.yaml     → AI 选择 pos_label/neg_label
scorer.py           → 提取 + 校验（非法值丢弃）
briefer.py          → 持久化到 market_news 表
calibrator.py       → 读回标签用于统计分析
```

**为什么是 6+6 而非更多**：标签太多 LLM 选择困难（选择瘫痪），太少区分度不够。6 个是 RecGPT 的经验——每个池的标签数不超过人一眼能扫完的数量。

#### 模式 2: CoT 显式拆分 → 5 步推理链

**RecGPT 做法**：LLM_UI（粗）→ LLM_IT（细）两步拆开，独立 prompt 和约束。

**我们的做法**：不拆 LLM 调用（太贵），在 **prompt 内部** 强制 5 步推理：

```
Step 1 — 信息提取：核心事件 + 涉及公司 + 数据点数量（心中完成）
Step 2 — 摘要生成：基于提取结果写 3-5 句
Step 3 — 打分定位：对照四档锚定判断落点
Step 4 — 标签选择：从 Matched Verdict Pool 匹配
Step 5 — 输出 JSON
```

**文件**：`prompts/summarizer.yaml`（CoT 步骤在 system prompt 顶部）

**为什么不拆成两次调用**：每天 40 条候选，拆两次 → 80 次 LLM 调用。prompt 内部 CoT 零额外成本，效果接近（LLM 在输出前按步推理）。

#### 模式 3: β-Fusion → `0.3 × signal + 0.7 × AI`

**RecGPT 做法**：`ŷ = β·ŷ_col + (1-β)·ŷ_sem`，β=0.5 在推荐场景平衡协同与语义。

**映射关系**（这是理解的关键）：

RecGPT 的公式里有两个分数来源——传统的协同过滤（ŷ_col）和新的 LLM 语义（ŷ_sem）。我们的系统里也有两个分数来源——传统的代码规则（signal_score）和新的 LLM 打分（ai_score）：

| RecGPT 符号 | RecGPT 含义 | 我们的对应 | 我们的含义 |
|:---------:|----------|--------|------|
| ŷ_col | 协同过滤分数（传统方法，靠用户行为 ID 相似度） | signal_score | 代码规则分数（传统方法，靠正则提取正文长度/数据点/新鲜度） |
| β | 传统方法的权重 = 0.5 | 0.3 | 传统方法的权重 = 0.3 |
| ŷ_sem | LLM 语义分数（新方法，靠标签理解商品内容） | ai_score | LLM 打分（新方法，靠 AI 理解新闻内容价值） |
| 1-β | LLM 的权重 = 0.5 | 0.7 | LLM 的权重 = 0.7 |

**为什么我们的 β 比 RecGPT 小**：

RecGPT 的 ŷ_col 是协同过滤——淘宝打磨了十年的推荐算法，非常可靠，所以给它 50% 权重。我们的 signal_score 是代码规则——看正文长度够不够、有没有数据点、是不是昨天发的。这些规则只能做**粗筛**，不知道"独立游戏深度报道"比"大厂公关稿"更有价值。所以代码只占 30%，AI 占 70%。

但 0.3 不是摆设——它做**兜底**。举个实际例子：

```
空洞通稿：body_len=0, fact_count=0, freshness="未标注", is_digest=false
  → signal_score = 40 - 15 - 10 - 0 + 0 = 15 分
  → AI 误打了 75 分
  → fused = 0.3 × 15 + 0.7 × 75 = 4.5 + 52.5 = 57 分 ✅ 被拉回合理区间

深度报道：body_len=500+, fact_count=8, freshness="今日", is_digest=false
  → signal_score = 40 + 5 + 10 + 0 + 10 = 65 分
  → AI 打了 72 分
  → fused = 0.3 × 65 + 0.7 × 72 = 19.5 + 50.4 = 70 分 ✅ 两个信号一致，微调
```

第一个例子是 β-Fusion 的核心价值：**代码信号做锚，防止 LLM 离谱打分**。第二个例子是正常情况——两个信号指向同一方向，融合结果接近原始 AI 分。

**硬约束**：当 `signal_score < 20` 且 `ai_score > 60` 时，融合结果强制封顶 59。这是代码的"否决权"——"正文为空+没有数据点"的新闻，无论 AI 多喜欢，分数不得超过 59。

**文件**：`src/agents/scorer.py`（`_compute_signal_score` + `_BETA=0.3` + `_SIGNAL_FLOOR=20` / `_AI_SOFT_CAP=60`）

**评分流程**：
```
AI raw score (LLM)
  → β-Fusion (0.3×signal + 0.7×AI)     ← 代码信号做锚，拉回离谱分
  → topic_boosts (Calibrator 用户偏好)   ← 反馈数据调参
  → 分布检查 + fallback (最终防线)      ← 不可绕过
  → 去重 + 质量门禁 + 来源多样性 → top 7
```

#### 模式 4: LLM as a Judge → Calibrator

**RecGPT 做法**：人标注种子 → 微调 Judge → Judge 评估 SFT-LLM → 人抽检 Judge。

**我们的做法**：用户 👍/👎 替代人工标注，LLM（Calibrator）替代微调的 Judge。

```
user_feedback (👍/👎)
    → calibrator._aggregate_feedback()     SQL 聚合（零 token）
    → LLM 分析（Calibrator agent, 1 次调用）  ← 类比 Judge
    → calibration_params 表（版本化，v1/v2/...）
    → scorer.load_calibration_for_scorer()  每天自动读取
    → apply_topic_boosts()                 打分加成
    → 日报质量提高 → 更多反馈 → 循环
```

**文件**：`src/agents/calibrator.py` + `prompts/calibrator.yaml` + `src/storage/sqlite.py`（`calibration_params` 表）

**为什么不需要微调 Judge**：推荐系统的标签预测有 3000+ 类别，需要专门微调。我们的任务简单得多——只需要分析"用户喜欢/不喜欢哪类话题"，prompt + 反馈统计就够了。这也印证了 RecGPT 的另一个结论：DeepSeek-R1 不微调也能用。

**触发方式**：
```bash
# 首次运行（等待 ≥30 条反馈）
python -m src.pipeline.runner --calibrate --calibrate-days 14 -v

# 之后建议每周跑一次
```

#### 模式 5: 离线预计算 → 把 LLM 调出热路径

**RecGPT 做法**：推荐解释离线算好存 lookup table，在线查表零延迟。

**这个模式的核心思想**：区分两类工作——"需要 LLM 但不需要实时"和"根本不需要 LLM"。两类都离线化，让在线路径零 AI 调用。

RecGPT 的推荐解释是**第一类**（需要 LLM 但不实时）——"为什么推荐这个商品"的判断需要 LLM 理解用户兴趣和商品属性，但不需要在 100ms 内算出来。于是离线预计算所有"兴趣 × 类别 → 解释"组合，存成 lookup table，在线直接查表。

我们的系统有三处应用，覆盖两类：

**第一类："需要 LLM 但不实时"** — Calibrator 参数缓存：

```
Calibrator（每周跑一次，LLM 分析反馈）
    → calibration_params 表（版本化，存 DB）
    → 每天日报：load_calibration_for_scorer() 直接读 DB
    → apply_topic_boosts() 纯数学运算
    → 零 LLM 调用
```

这和 RecGPT 的 lookup table **完全同构**：LLM 离线干活（分析反馈 → 调参），结果持久化（DB 表），在线零成本读取。Calibrator 是离线 LLM，每天跑日报时只读它的输出，不调它。

**第二类："根本不需要 LLM"** — 两类机械操作砍掉：

| 砍掉 | 理由 | 现在怎么做 |
|------|------|----------|
| Briefer AI 排版 | 把 7 段已写好的摘要拼成 markdown，纯字符串拼接 | `brief()` 中代码直拼：`sorted_news → md_blocks` |
| 代码信号提取 | 数字数、找公司名、看日期——正则就能做 | `_extract_body_signals()` 零 token，结果注入 prompt |

这两处砍掉的逻辑是：**LLM 不是免费的**。RecGPT 把 LLM 调出推荐系统的在线路径以节省延迟。我们把 LLM 调出机械性工作以节省 token。原理一样——LLM 只用在它不可替代的地方（语义理解和判断），其他地方用代码。

**结果**：整个日报系统现在只有 **1 个 LLM 调用点**（Summarizer 打分+摘要+标签），其他全部是代码或缓存读取。

**文件**：`src/agents/calibrator.py`（离线 LLM → DB）、`src/agents/scorer.py`（`load_calibration_for_scorer` 在线读 + `_extract_body_signals` 零 token）、`src/agents/briefer.py`（代码直拼 markdown）

---

## 四、系统日常运行

### 每日日报

```bash
python -m src.pipeline.runner --scrape --force --push oc_xxx -v
```

Verbose 输出中可以观察到四个模式的效果：

```
── AI 打分明细 ──
   1. 🏷️  78分  sig: 55  [track_direct][]         塔防+肉鸽双赛道   《明日方舟》...
   2.     65分  sig: 48  [playable_reference][]    玩法可借鉴        某独立游戏...
   3.     42分  sig: 30  [][no_gameplay]          仅下载量公告      某大厂PR...

   [dist] post-adjustment distribution needs correction: ...
   [calib] topic_boosts applied to 2 items (calibration v3)
   [labels] persisted 7 label annotations to market_news
```

| 输出列 | 对应模式 | 说明 |
|--------|---------|------|
| `sig:55` | β-Fusion | 代码信号分数，0.3 权重已融入 78 分 |
| `[track_direct][]` | Matched Interest Pool | 正面/负面标签，持久化到 `market_news` |
| `[calib] topic_boosts` | LLM as a Judge | Calibrator 参数已生效 |
| `[labels] persisted` | Matched Interest Pool | 标签写入 DB 供后续分析 |

### 每周校准

```bash
# 检查反馈量
python -c "from src.storage.sqlite import get_db; db=get_db(); print(db._connect().execute('SELECT COUNT(*) FROM user_feedback').fetchone()[0])"

# 够了就跑（≥30条）
python -m src.pipeline.runner --calibrate --calibrate-days 14 -v
```

成功输出示例：
```json
{
  "version": 3,
  "topic_boosts": {"独立游戏": 5, "二次元": -10},
  "dim_weights": {"track": 45, "density": 35, "insight": 20},
  "findings": [
    {
      "pattern": "独立游戏深度报道总是被赞，不管来源",
      "evidence_count": 5,
      "action": "topic_boosts.独立游戏 +5",
      "confidence": "high"
    }
  ],
  "summary": "近14天共35条反馈。独立游戏报道5/5👍 → +5; 二次元泛行业分析3/5👎 → -10"
}
```

### 反馈闭环

```
用户点 👍/👎 按钮
  → user_feedback 表（每次点击一行，含 news_url + feedback_type）
  → 积累 ≥30 条
  → Calibrator 分析（LLM 发现偏好模式，输出 topic_boosts + dim_weights）
  → calibration_params 表（版本化，v1 → v2 → v3...）
  → 次日日报自动读取最新版本
  → Summarizer 打分时 apply_topic_boosts()
  → 用户偏好体现在新闻筛选里
  → 日报更符合用户口味 → 更多反馈 → 循环
```

---

## 五、映射总表

| RecGPT 组件 | 日报系统对应 | 状态 |
|------------|-------------|------|
| 用户行为序列压缩 | Scraper → 结构化数据 + body 截断 500 字 + 弱信号过滤 | ✅ |
| LLM_UI 兴趣挖掘 | Summarizer → 信息提取 + 摘要生成（CoT Step 1-2） | ✅ |
| LLM_IT 标签预测 | Summarizer → 打分定位 + 标签选择（CoT Step 3-4） | ✅ |
| 三塔召回 β 融合 | β-Fusion: `0.3×signal_score + 0.7×ai_score` + 硬约束 | ✅ |
| LLM_RE 推荐解释 | Briefer → 代码直拼 markdown（LLM 调用已移除） | ✅ |
| LLM as a Judge | Calibrator → 消费 user_feedback → 调参 → 版本化 | ✅ |
| 离线预计算 | `load_scoring_config()` / 代码直拼 / `_extract_body_signals()` | ✅ |
| Matched Interest Pool | Matched Verdict Pool（6+6 标签池 + 代码校验 + DB 持久化） | ✅ |
| CoT 显式拆分 | Prompt 内部 5 步推理链 | ✅ |
| 行为序列三层压缩 | market_pipeline: 关键词→去重→新鲜度→疲劳 | ✅ |

**五个模式全部落地。当前状态**：Calibrator 代码就位，等待 `user_feedback` 积累 ≥30 条后首次运行。其余四个模式每日自动生效。
