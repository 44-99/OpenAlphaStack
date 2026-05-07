# 路线图

四阶段递进：**有工具可用 → 能赚钱 → 不亏钱 → 更好看**。

每项只进路线图的条件：直接提升盈利能力 / 防止亏损 / 消除实盘安全隐患。锦上添花归 Phase 4。

---

## Phase 1: 工具底座 + 数据可靠性 + 交易铁律 ✅ 已完成

**目标**：Claude Code 拥有完整的分析工具箱、可靠的数据源、统一的交易纪律。

### 1.A 工具层 — 已完成

| ID | Tool | Command | Description |
|----|------|---------|-------------|
| 1.1 | `quote` | `python tools/quote.py 600519` | 实时价格/涨跌幅/换手率/量比 |
| 1.2 | `technical` | `python tools/technical.py 600519 --all` | MA/MACD/RSI/KDJ/布林带/量价分析 |
| 1.3 | `fundamental` | `python tools/fundamental.py 600519` | PE/PB/ROE/营收增速/行业分位 |
| 1.4 | `flow` | `python tools/flow.py 600519` | 北向资金/主力净流入/大单方向 |
| 1.5 | `news` | `python tools/news.py 600519` | 近期公告/研报评级/市场情绪 |
| 1.6 | `screen` | `python tools/screen.py -s breakout` | 多因子筛选 |
| 1.7 | `backtest` | `python tools/backtest.py 600519 -s ma_cross` | 轻量历史回测（单股单策略） |
| 1.8 | `portfolio` | `python tools/portfolio.py` | 自选股管理 + 持仓盈亏概览 |
| 1.9 | `trend` | `python tools/trend.py 600519 --check all` | MA排列/交叉/乖离/趋势状态 |
| 1.10 | `signal_detector` | `python tools/signal_detector.py 600519 -s all` | 5 种入场信号检测 |
| 1.11 | `pivot` | `python tools/pivot.py 600519 --mode all` | 枢轴点/支撑阻力/箱体/缠论中枢 |
| 1.12 | `fibonacci` | `python tools/fibonacci.py 600519` | 斐波那契回撤/扩展/波浪验证 |
| 1.13 | `sentiment` | `python tools/sentiment.py 600519` | 换手热度/量能趋势/ATR/情绪评分 |

> 13 个工具全部实现。腾讯 qt.gtimg.cn API 为主源，新浪 fallback，akshare 最后手段。

### 1.B 多源数据 Fallback — 已完成

| 优先级 | 数据源 | 速度 | 字段 | 用途 |
|--------|--------|------|------|------|
| 1 (主) | 腾讯 qt.gtimg.cn | ~0.07s | 88 | 实时行情、全市场选股 |
| 2 | 新浪 hq.sinajs.cn | ~0.1s | 34 | 行情 fallback、K线历史 |
| 3 | akshare Sina 后端 | ~25s | 14 | 代码列表缓存、最后手段 |

### 1.C 交易铁律 — 已完成

7 条铁律编码入 CLAUDE.md，优先级高于任何策略技能：

1. 严进策略（乖离率 > 5% 不买）
2. 趋势交易（MA5 > MA10 > MA20 必须条件）
3. 效率优先（筹码结构）
4. 买点偏好（缩量回踩支撑）
5. 风险排查（利空一票否决）
6. 估值关注（PE 偏高必须提示）
7. 强势趋势股放宽（龙头可放宽至 7%，必须设止损）

---

## Phase 2: 策略引擎 — 把钱赚到

**目标**：让 AI 做出能赚钱的交易决策。这个 Phase 每一项都回答"它能让我多赚钱或少亏钱吗？"

### 架构总览 (v3 — Sub-Agent + API Tool Use + 1s Tick)

```
┌─ 盘后 (15:30) ─ 每天 1 次 ─────────────────────────────────────┐
│  Phase 0: Python 并行启动 3 个 Claude Code 子任务 (sub-agent)    │
│  ┌─ A: 宏观政策研究 → 500 字摘要                                │
│  ├─ B: 板块轮动分析 → 推荐 3 板块 (~500 字)                     │
│  └─ C: 决策复盘 + 持仓评估 → 经验注入 (~500 字)                 │
│                                                                  │
│  Phase 1-3: API + Tool Use（结构化 JSON，API 层面强制）           │
│  定方向(set_direction) → 选标的(add_candidate) → 调仓(adjust)      │
│  输入: 3 子智能体摘要 + 行情 + screen 20只 + 账户状态              │
│  输出: market_bias + candidates + adjustments (JSON schema 强制)   │
│                                                                  │
│  Phase 2: 纯 Python 风控                                          │
│  risk.py + signal.py 硬校验 → plan.json                           │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼ plan.json
┌─ 盘中 (9:25-15:00) ─ Python 机械执行 ──────────────────────────┐
│  · 9:25  执行候选买入 (限价单, 不追高)                            │
│  · 每 1s: 止盈止损检查 (ThreadPool 并行行情+并行扫描, 124ms)     │
│  · 每 1s: 规则信号扫描 (action=buy/sell/alert, 去重)             │
│  · 紧急: 大盘-3% / 个票-5% / 账户-10% → Claude Code 紧急会话     │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼
┌─ 全局风控 (硬编码, 不可跳过) ───────────────────────────────────┐
│  · 单笔: 卫星 -5% / 核心 -8% 硬止损                               │
│  · 账户: -20% → 熔断, 仅允许平仓                                  │
│  · 仓位: 核心 ≤50% + 卫星 ≤30% = 总 ≤80% (回测/模拟盘可全仓)     │
│  · 单票: 核心 ≤20% / 卫星 ≤7.5%                                  │
│  · 信号: action=buy/sell/alert 三态, 同 code+rule 24h 不重复     │
└──────────────────────────────────────────────────────────────────┘
```

**仓位模型：核心+卫星 (50/30/20)**

| 层级 | 占比 | 来源 | 持有周期 | 单票上限 | 止损 |
|------|------|------|----------|----------|------|
| 核心 | 50% | Claude Code 政策/事件选股 | 2-8 周 | 20% | -8% |
| 卫星 | 30% | screen.py + signal_rules.py | 1-5 天 | 7.5% | -5% |
| 现金 | 20% | T+1 缓冲 + 极端机会 | — | — | — |

**空仓条件（双重确认）**：上证 MA5<MA20 + 连跌 3 日 + Claude Code bearish 同时满足 → 空仓至 20%。

**三种模式统一：**

| 维度 | Backtest | Paper | Live |
|------|----------|-------|------|
| 数据源 | 历史 K 线回放 | 实时行情 | 实时行情 |
| Claude Code | 盘后 3 次/日（3 sub-agent 并行） | 盘后 3 次/日 | 盘后 3 次/日 |
| API+Tool Use | 盘后 3 次/日 | 盘后 3 次/日 | 盘后 3 次/日 |
| 盘中 Claude Code | 无（紧急同样仿真） | 仅紧急触发 | 仅紧急触发 |
| Python 执行 | 完全相同 | 完全相同 | 完全相同 |

### 2.1 策略技能库 ✅ 已完成

3 个场景化技能管线，description-based 激活：

| 技能管线 | 场景 | 组成 |
|----------|------|------|
| `stock-analyzer` | 个股深度分析 | 6 阶段管线（数据→趋势→信号→位置→风险→输出） |
| `market-analyzer` | 市场研判 | 情绪周期 → 龙头识别 → 板块轮动 |
| `stock-screener` | 多因子选股 | 短线/中线/热钱 3 策略 |

### 2.2 交易信号校验层 ✅ 已完成

`tools/signal.py` — Claude Code 产出信号强制通过硬校验：

- 止损价 < 买入价、风险回报比 ≥ 1.5:1、乖离率 ≤ 5%（龙头 ≤ 7%）、置信度 0-100
- 校验不通过 → 拒绝原因，不进入执行管线
- 校验通过 → `data/signals.jsonl` → 进入执行队列

### 2.3 风控计算器 ✅ 已完成

`tools/risk.py` — 纯数学层，零 LLM：

| 函数 | 逻辑 |
|------|------|
| `calc_volatility_metrics()` | 日波动率 → 年化 → 波动率分位 |
| `calc_volatility_adjusted_limit()` | 低波动 → 25% / 高波动 → ≤10% |
| `calc_correlation_multiplier()` | 高相关 → 0.7x / 低相关 → 1.1x |
| `calc_position_size()` | 综合波动率 + 相关性 → 建议股数 |
| `max_drawdown_check()` | 当前回撤 vs 历史最大回撤 |

### 2.4 统一 Agent 引擎 ✅ 已完成

`tools/paper_engine.py` — Phase 2 核心交付物。Sub-agent 并行研究 + API Tool Use 结构化决策 + 1s tick 高速执行。

**LLM 调用分工：**

| 阶段 | 方式 | 原因 |
|------|------|------|
| Phase 0 子智能体 × 3 | Claude Code CLI | 需要完整工具/记忆/技能上下文做研究 |
| Phase 1-3 结构化决策 | API + Tool Use | JSON Schema 强制，零解析 |
| 紧急响应 | API + Tool Use | 延迟敏感 + 输出必须精确结构化 |
| 飞书对话 | Claude Code CLI | 理解用户意图、查数据、个性化回复 |

**内部模块：**

| 模块 | 职责 |
|------|------|
| `SubAgentRunner` | Phase 0：并行启动 3 个 Claude Code 子任务（政策/板块/复盘） |
| `OvernightPipeline` | Phase 1-3：API+Tool Use 结构化决策 + Python 风控 |
| `llm_client.py` | 4 个 Tool Schema + `call_with_tool()` 封装 Anthropic SDK |
| `PlanV2` | plan.json v2 读写 |
| `FastLane` | 盘中 Python 执行：1s tick 并行行情+扫描，去重，熔断 |
| `EmergencyTrigger` | 大盘-3% / 个票-5% / 账户-10% → Claude Code 紧急会话 |
| `BacktestRunner` | 历史重放回测，Claude Code 看到仿真日期的历史新闻 |
| `Ledger` / `State` / `Clock` | 决策账本 / 资金持仓状态 / 仿真时钟 |

**回测**：250 交易日，约 2-3 分钟/日（3 sub-agent 并行 ~20s + API Tool Use 3 次 ~80s + 风控 <1s）。

### 2.5 CI/CD ✅ 已完成

| 工作流 | 触发 | 内容 |
|--------|------|------|
| `ci.yml` | 每次 push | 工具 schema 校验、规则层基线对比、ruff lint |
| `agent-backtest.yml` | 手动触发 | 完整 Agent 回测，产出胜率/夏普/回撤报告 |
| `deploy.yml` | 手动触发 | Docker build + push → SSH VPS → docker compose up -d |

### 2.6 可靠性加固 ✅ 已完成

- Docker Compose + `restart: unless-stopped`
- `logging_config.py` — JSON 结构化日志 + 自动轮转
- `daily_report.py` — 日交易报表（P&L/胜率/回撤，飞书推送）

### 2.7 飞书通知推送 ✅ 已完成

| 事件 | 优先级 | 触发时机 |
|------|--------|----------|
| 🚀 引擎启动 | P1 | 回测/模拟盘/实盘启动，报告模式和参数 |
| 📊 盘后流水线完成 | P1 | 三阶段完成，报告候选/通过数 |
| 💰 交易执行 | P1 | 每笔买入/卖出/止损成交，含价格/数量/盈亏 |
| 🚨 异常告警 | P0 | 选股超时、API 报错、紧急触发 |
| 📈 每日简报 | P1 | 收盘后：当日 P&L、胜率、持仓变动、净值 |
| ⏸️ 引擎停止 | P2 | 正常停止或异常退出 |
| 🔄 进度播报 | P2 | 回测每 20 交易日播报一次 |

实现：`tools/notifier.py` 封装飞书推送 + `config.py` `ENGINE_CHAT_IDS` 指定通知目标。

---

### 🆕 2.8 Shadow Account — 从自己的交易中学习 ✅ 已完成 → 🔄 增强中

> 来源：[Vibe-Trading] 的 Shadow Account 功能 + [TradingAgents] 的 TradingMemoryLog 延迟反思模式。适应我们的场景：已有 `ledger.jsonl` 记录每笔决策和结果，让 Claude Code 从中找出赔钱模式并闭环改进。

**做什么**：定期复盘已执行交易，自动提取"哪里做错了"，并在下次决策时注入改进建议。

**两阶段架构 (TradingAgents 启发)**：
- **Phase A（已实现）**：ledger.jsonl → FIFO 配对 → 行为诊断 → 文本报告。零 LLM 成本。
- **Phase B（增强中）**：下次 pipeline 运行时 → 拉取上次决策的实际市场收益 → LLM 生成 2-4 句反思 → 注入 Sub-Agent C 的 prompt，形成"决策→结果→反思→改进"闭环。

**输入**：`data/output/*/ledger.jsonl` ＋ 飞书对话中的分析记录

**输出**：
- 行为诊断：处置效应（亏了不肯卖）/ 过度交易（频率过高）/ 追涨（买在乖离率峰值）/ 锚定（守着成本价不肯动）
- 模式提取："每次止损设在 -5% 但实际平均亏损 -8%，因为滑点 + 犹豫"
- 改进建议："AI 总是在板块轮动结束后还在推旧板块标的"→ 调整 sub-agent B 的 prompt
- **Phase B 新增**：延迟反思注入——"上次决策的 NVDA 买入评级在 20 天后实际收益 -3.2%（vs SPY +1.5%），原因是忽略了美联储利率决议前的波动风险"

**为何放在 Phase 2**：这是最直接的"提升赚钱能力"的功能——不是靠猜测应该怎么优化，而是靠真实交易数据告诉你哪里真的在亏钱。Phase B 反思闭环让系统具备自我进化能力。

**完工标准**：Phase A: 回测中 Shadow Account 能识别出 ≥2 种重复犯的错误模式（✅ 已完成）。Phase B: 反思闭环注入后下一轮回测指标有可测量的改善。

---

### 🆕 2.9 Bull/Bear 双视角辩论 — 选股更审慎 ✅ 已完成

> 来源：[Vibe-Trading] 的 investment_committee 和 [AI-Trader] 的多 Agent 角色协作。

**做什么**：Stage 2（选标的）从"一个 AI 单向评分"改为"两个 AI 辩论后裁决"。

**当前**：sub-agent C 收到候选池 → 单一 bullish 评分 → 输出 buy_candidates

**改进**：
```
候选池
  ├─ Bull Agent: "这只票为什么该买？"（找做多理由）
  ├─ Bear Agent: "这只票为什么不买？"（找做空理由/风险点）
  └─ Risk Agent: 综合双方论点 → 裁决 → 最终 buy_candidates
```

**为什么有效**：单一 AI 分析容易产生确认偏误（看到了利好就忽略利空）。强制 Bull/Bear 双方辩论后再裁决，天然对冲这个偏误。

**实现**：复用现有 `SubAgentRunner`，改 prompt 编排——Bull 和 Bear 各给一份角色 prompt，Risk Agent 拿双方的输出做裁决。Phase 2 内在 SubAgentRunner 中加 `run_debate()` 方法即可。

**完工标准**：Bull/Bear 辩论选出的标的在回测中胜率高于单一评分的基线。

---

### 🆕 2.10 策略实验管线 — 每天自动选最优策略 ✅ 已完成

> 来源：[QuantDinger] 的策略实验管线概念。

**做什么**：盘后不只跑一套参数做决策，并行跑 2-3 个策略变体，自动选回测表现最好的执行。

**变体示例**：
- 变体 A：核心 50% + 卫星 30%（当前默认）
- 变体 B：偏保守——核心 35% + 卫星 20%（高波动市场自动触发）
- 变体 C：偏进攻——核心 60% + 卫星 30%（多头排列+低波动市场自动触发）

**流程**：盘后并行跑 N 个变体 → 每个变体产出 plan.json → 按该变体的历史回测 Sharpe/MaxDD 排名 → 自动选最优 → 次日执行。

**为什么不是过度工程**：不是搞几十个变体网格搜索。只在市场状态发生显著变化时（如波动率突破阈值）触发变体切换。大部分时间跑默认策略。核心价值是**避免在错误的市场环境里用错误的仓位参数**。

**完工标准**：存在 ≥2 个有意义的策略变体，在市场状态切换时能自动选择更优参数。

---

### 🆕 2.11 API 可靠性 + 双模型分层 — 降本增效 🔄 进行中

> 来源：[TradingAgents] 的双模型策略 + 结构化输出降级模式。

**做什么**：
- **结构化输出降级**：`llm_client.call_with_tool()` 当前 Tool Use 失败直接报错 → 加 try/except，失败时自动 fallback 到 `call_text()` + 自由文本解析，保证管线不中断。
- **双模型分层**：Phase 0 子智能体（研究/辩论）用便宜模型（`QUICK_THINK_MODEL`），Phase 1-3 结构化决策（定方向/选标的/风控）用贵模型（`ANTHROPIC_MODEL`）。TradingAgents 的实践：10 个浅层 Agent 用便宜模型，2 个决策 Agent 用贵模型。

**为什么放在 Phase 2**：结构化降级直接提升管线可靠性（防止单次 API 失败中断整晚分析）。双模型分层在不降低决策质量的前提下降低 API 成本。

**完工标准**：结构化降级在 Tool Use 失败时成功 fallback 并返回同结构数据。双模型配置可通过 .env 切换。

---

## Phase 3: 实盘交易 — 别把钱亏掉

**目标**：用真钱时不出事故。每一项回答"它能防止我亏掉不该亏的钱吗？"

**准入条件**：Agent 回测 + 模拟盘 ≥ 1 个月同时达标（≥30 笔交易，胜率 >55%，夏普 >1.0，最大回撤 <25%）。

实盘与模拟盘跑**完全相同的代码**。唯一差异：

| 维度 | Paper | Live |
|------|-------|------|
| 资金账户 | data/paper_*/state.json 虚拟现金 | 券商账户真实资金 |
| 启动方式 | 自动 | 用户飞书说「开启实盘」或 `/trade live on` |
| 安全闸门 | 无 | 双层闸门（.env + 运行时命令双重确认） |

### 3.1 券商接入

| ID | Feature | Description |
|----|---------|-------------|
| 3.1 | **券商接入** | `tools/trade.py` 封装券商 API。首选东方财富 OpenAPI（RESTful、散户友好） |
| 3.2 | **交易确认流** | 每笔订单触发飞书交互卡片确认。**绝不静默自动交易** |
| 3.3 | **双层安全闸门** | `.env` `PAPER_ONLY=true`（默认）+ 运行时 `--mode live` 显式确认 |
| 3.4 | **订单幂等性** | 每笔信号生成唯一 `trade_id`（`{date}_{symbol}_{seq}`），下单前检查去重 |
| 3.5 | **自主等级** | L0 完全手动 / L1 半自动（预设参数内自主执行）/ L2 全托管（人类收盘 review）。默认 L1 |

### 🆕 3.6 多持仓相关性风控

> 来源：原 Phase 4.2，提前到实盘前必须上线。

持仓 ≥2 只时计算相关性矩阵。任意两只相关性 ≥0.6 → 自动将两只的仓位上限各自下调 30%。防止"分散了 but actually 同涨同跌"的假分散。

### 🆕 3.7 Session 自动轮转

> 来源：[cc-connect] 的 session auto-rotation。

实盘场景下 Claude Code 会话可能持续数小时。上下文膨胀导致响应质量下降 → 盘中紧急判断出错。设 `idle_timeout`（默认 50 轮对话或 30 分钟），超时自动提示 `/new`。用户可选择"整理记忆后重置"或"暂不重置"。

### 🆕 3.8 流式消息回复

> 来源：[cc-connect] 的 streaming output。

实盘中每秒钟都可能是钱。当前等 Claude Code 完整返回后才一次性发飞书——30 秒空白期用户不知道 AI 在想什么。改为 Claude Code `stream-json` 模式逐 token 推送到飞书消息。用户看到 AI 在"想"，不是"卡住了"。

### 🆕 3.9 回复冗长度控制

> 来源：[cc-connect] 的 progress compact mode。

不同场景需要不同信息密度：

| 模式 | 触发 | 输出内容 |
|------|------|----------|
| `/mode full` | 复盘/深度分析 | 完整思考过程 + 数据 + 结论（当前行为） |
| `/mode compact` | 盘中盯盘 | 只显示结论 + 关键价位 + 一句话理由 |
| `/mode quiet` | 快速查询 | 只输出"买/卖/观望" |

默认 compact，复盘时切 full。

---

### 🆕 3.10 大额交易前风控辩论 — 双层校验的最后一道防线

> 来源：[TradingAgents] 的 Aggressive/Conservative/Neutral 三人风控辩论模式。

**做什么**：当前风控是纯 Python 数学层（`risk.py`：止损/仓位/波动率），缺少情境推理能力。在单笔仓位 > 15% 或总仓位 > 50% 的大额交易执行前，启动 3 人风控辩论：

```
Trader 提案（买入价/止损/仓位）
  ├─ Aggressive Debater: "这批仓位可以放大，市场情绪好"
  ├─ Conservative Debater: "仓位太大了，黑天鹅概率被低估"
  └─ Neutral Debater: 综合双方 → 最终仓位建议（维持/缩减/否决）
```

**为什么有效**：`risk.py` 做的是数学题（波动率×仓位上限），但实盘中很多风险是情境性的——"虽然数学上仓位合理，但下周有美联储议息"。Python 层永远算不出这个，需要 LLM 做情境推理。

**实现**：复用 `OvernightPipeline._run_bull_bear_debate()` 的编排模式，仅在满足触发条件时调用（避免每笔小交易都跑）。输出为仓位调整建议（maintain/reduce/reject），由 `risk.py` 最终裁决。

**完工标准**：大额交易触发风控辩论且输出有意义的仓位调整建议，辩论不增加 > 3 秒延迟。

---

## Phase 4: 增强与体验 — 锦上添花

**目标**：更好看、更深入、更开放。不影响当下的赚钱能力。

### 4.1 深度回测增强

| ID | Feature | Description |
|----|---------|-------------|
| 4.1a | **回测校准** | AI 回测验证——概率校准、参数网格搜索、蒙特卡洛模拟 |
| 4.1b | **多 run 对比** | 不同策略变体的收益曲线叠加对比 |

### 4.2 新闻情绪管线

定时抓取财经新闻 → 轻量情感分类 → 按股票代码索引 JSON 缓存 → sub-agent A 可引用。

### 4.3 Kronos 辅助模型

预训练 K 线模型作为辅助信号源，不替代主决策逻辑。输入 Claude Code 作为参考信号。

### 4.4 Web 监控面板

飞书解决"知道发生什么"，Web 解决"看清全貌"：
- 实时净值曲线图（Plotly/ECharts）
- 持仓盈亏仪表盘
- 多 run 收益曲线对比
- 交易记录可交互表格

技术选型待定（Streamlit 快速原型 vs FastAPI + 静态 HTML）。

### 4.5 飞书应用商店上架

稳定后考虑公开发布。

---

## 四 Phase 工作量对比

| Phase | 项数 | 核心主题 | 每项都在回答 |
|-------|------|----------|-------------|
| P1 基础 | 3 模块 | 有工具可用 | "我能分析股票了吗？" |
| P2 引擎 | 11 项（+4 新增） | **直接提升盈利能力** | "它能让我多赚钱或少亏钱吗？" |
| P3 实盘 | 10 项（+5 新增） | **用真钱不犯致命错误** | "它能防止我亏掉不该亏的钱吗？" |
| P4 增强 | 5 项（精简后） | 更好看更强更开放 | "锦上添花" |

**P2 vs 原版变化**：砍掉 Web 面板（→P4），新增 Shadow Account + Bull/Bear 辩论 + 策略实验管线 + API 可靠性/双模型分层。四项都是零新基础设施、直接提升策略质量或降低成本。

**P3 vs 原版变化**：新增多持仓相关性（从 P4 前移）+ Session 轮转 + 流式消息 + 冗长度控制 + 风控辩论。全是实盘场景下"不犯致命错误"的安全刚需。

**未纳入的项目对比文档启发及原因**：

| 启发 | 不纳入原因 |
|------|-----------|
| MCP Server 包装 | 19 个 CLI 工具已经是 `python tools/x.py` 直接调用，包装成 MCP 多此一举 |
| 统一工具注册表 | CLI 工具天然可发现（`ls tools/`），加 metadata.json 是过度工程 |
| 工具输出压缩 | Claude Code 自带上下文管理，且我们的工具输出已经是紧凑 JSON |
| 飞书富卡片 | 纯体验项，不提升策略质量，放 P4 以后再说 |
| Hook 系统 | notifier.py 已覆盖所有关键事件，泛化 Hook 无实际场景 |
| A/B 实验框架 | 策略实验管线（2.10）已用更轻量的方式解决同一问题 |
| 工具预计算快照 | CLI 工具已有内部缓存（行情 300s / 基本面 3600s），预计算收益有限 |
