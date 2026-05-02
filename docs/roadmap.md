# 路线图

四阶段路线图，按依赖关系和风险递进排列——每一阶段都是下一阶段的前提。

---

## Phase 1: 工具底座 + 数据可靠性 + 交易铁律 (P0) ✅ 已完成

**目标**：Claude Code 拥有完整的分析工具箱、可靠的数据源、统一的交易纪律。

### 1.A 工具层 — 已完成

将 `stock.py` 的单体"一把抓"拆解为离散的 CLI 工具。每个工具单一职责：JSON 进、JSON 出。Claude Code 通过 Bash 按需调用，自主决定查询什么数据、如何组合分析结果。

| ID | Tool | Command | Description |
|----|------|---------|-------------|
| 1.1 | `quote` | `python tools/quote.py 600519` | 实时价格/涨跌幅/换手率/量比。`market` 查看大盘概况 |
| 1.2 | `technical` | `python tools/technical.py 600519 --all` | MA (5/10/20/60)、MACD、RSI、KDJ、布林带、量价分析 (pandas-ta) |
| 1.3 | `fundamental` | `python tools/fundamental.py 600519` | PE / PB / ROE / 营收增速 / 行业分位排名 |
| 1.4 | `flow` | `python tools/flow.py 600519` | 北向资金、主力净流入、大单方向 |
| 1.5 | `news` | `python tools/news.py 600519` | 近期公告、研报评级、市场情绪聚合 |
| 1.6 | `screen` | `python tools/screen.py -s breakout` | 多因子筛选，策略内联为 Python 常量 |
| 1.7 | `backtest` | `python tools/backtest.py 600519 -s ma_cross` | 轻量历史回测。"该形态出现 N 次，胜率 X%，平均收益 Y%" |
| 1.8 | `portfolio` | `python tools/portfolio.py` | 自选股管理：增删查改，持仓盈亏概览 |
| 1.9 | `trend` | `python tools/trend.py 600519 --check all` | MA排列/交叉/乖离/趋势状态 |
| 1.10 | `signal_detector` | `python tools/signal_detector.py 600519 -s all` | 5 种入场信号检测（金叉/放量突破/缩量回踩/底部放量/一阳三阴） |
| 1.11 | `pivot` | `python tools/pivot.py 600519 --mode all` | 枢轴点/支撑阻力聚类/箱体区间/缠论中枢 |
| 1.12 | `fibonacci` | `python tools/fibonacci.py 600519` | 斐波那契回撤位/扩展目标位/波浪规则验证 |
| 1.13 | `sentiment` | `python tools/sentiment.py 600519` | 换手热度/量能趋势/ATR波动/均线粘合/情绪综合评分 |

> 13 个工具全部实现。腾讯 `qt.gtimg.cn` API（88 字段含 PE/PB/换手率/量比）作为行情主源，200 只/批次全市场拉取 ~2s。为什么用 CLI 而不是 MCP Server：每个调用无状态、即时返回，Claude Code 内置 Bash 工具原生支持，零基础设施开销。

### 1.B 多源数据 Fallback

实地测试对比后确定的数据源优先级：

| 优先级 | 数据源 | 速度 | 字段 | 用途 |
|--------|--------|------|------|------|
| 1 (主) | 腾讯 `qt.gtimg.cn` | ~0.07s | 88（含PE/PB/换手率/量比/市值） | 实时行情、全市场选股 |
| 2 | 新浪 `hq.sinajs.cn` | ~0.1s | 34（基础OHLCV） | 行情 fallback、K线历史 |
| 3 | akshare Sina 后端 | ~25s | 14（价量） | 代码列表缓存、最后手段 |

- **腾讯 API**：不依赖 eastmoney push2，不受 IP 封锁影响，速度最快、字段最全，价格与 Sina 一致（5只样本误差 < 0.02）
- **K线历史**：新浪 K线 API（`money.finance.sina.com.cn`）提供日线 OHLCV，最大 ~2000 天
- **财务/资金/新闻**：akshare 东方财富接口（非 push2 端点，可用）
- **efinance / pytdx**：实测不可用 — efinance 依赖 eastmoney push2（同 IP 被封），pytdx 通达信 TCP 端口不通

每个工具内部封装 fallback 逻辑，优先腾讯 → 失败自动切换新浪 → akshare 最后手段。`_fallback.py` 集中管理 fallback 链。

### 1.C 交易铁律前置技能

将以下 7 条交易纪律编码为 `skills/trading-principles.md`，作为始终加载的前置技能注入 CLAUDE.md 系统提示词：

1. **严进策略（不追高）**: 乖离率 > 5% 坚决不买入。乖离率 < 2% 最佳买点，2-5% 可小仓，> 5% 直接判定观望
2. **趋势交易（顺势而为）**: MA5 > MA10 > MA20 多头排列是必须条件，空头排列坚决不碰
3. **效率优先（筹码结构）**: 关注筹码集中度，现价高于平均成本 5-15% 为健康区间
4. **买点偏好（回踩支撑）**: 缩量回踩 MA5/MA10 获得支撑是最佳买点，跌破 MA20 观望
5. **风险排查**: 减持公告、业绩预亏、监管处罚、行业政策利空、大额解禁 — 一票否决
6. **估值关注**: PE 明显偏高时必须在风险提示中说明
7. **强势趋势股放宽**: 龙头/强势股可适当放宽乖离率要求，但必须设止损

---

## Phase 2: 策略技能体系 + 模拟盘验证 — 可信度闭环 (P0)

**这一阶段是整个系统的信任基础。** 核心逻辑：多策略技能分析 → 产生交易信号 → 模拟盘执行 → 实盘数据跟踪 → 胜率/P&L/夏普公开可查 → 数据证明策略可靠 → 才有资格进入实盘。

### 2.1 策略技能库 — 3 个场景化技能管线 ✅ 已重构

将 11 套经典交易策略从独立关键词触发技能重组为 3 个场景化管线（基于 SeeleAI 的 router + references 模式）：

| 技能管线 | 场景 | 组成 |
|----------|------|------|
| `stock-analyzer` | 个股深度分析 | 6 阶段管线（数据→趋势→信号→位置→风险→输出），references: 入场信号/位置管理/高级框架/风险 |
| `market-analyzer` | 市场研判 | 情绪周期 → 龙头识别 → 板块轮动，references: 情绪周期/龙头/板块轮动 |
| `stock-screener` | 多因子选股 | 短线/中线/热钱 3 策略，references: 短线/中线/热钱参数 |

**架构变更**：
- 激活方式：关键词触发 → **description-based**（Agent 理解用户意图自主选择技能）
- 计算层：`skills/*/scripts/` → **`tools/` 统一 CLI 工具**（13 个）
- 策略组织：11 个独立 SKILL.md → **3 个管线 SKILL.md + 策略作为 references 按需加载**
- 新增 5 个工具：`trend.py`（趋势研判）、`signal_detector.py`（5 种入场信号）、`pivot.py`（箱体/中枢）、`fibonacci.py`（斐波那契）、`sentiment.py`（情绪评分）

> **为什么是 Skills 而不是 JSON 配置**：策略不只是筛选条件，更是分析框架。Claude Code 需要的是自然语言的策略方法论（什么时候用、怎么判断、注意事项），而不是一套硬编码的数值阈值。Skills 的渐进式展开设计让策略深度可调——日常分析只加载 SKILL.md，深度研究时按需展开 references/。

### 2.2 交易信号验证层

**借鉴 ai-hedge-fund 的受约束决策模型**：ai-hedge-fund 的 Risk Manager + Portfolio Manager 两层设计——Risk Manager 做纯数学计算（波动率仓位上限、相关性惩罚），Portfolio Manager 只在预计算好的约束范围内选动作——确保 LLM 不会做出越界决策。

适配 AlphaClaude 架构的方式：

- Claude Code 产出的交易信号强制通过 Python `validate_signal()` 校验：
  - 买入价是否在合理范围（乖离率 ≤ 5%，龙头 ≤ 7%）
  - 止损价是否低于买入价
  - 建议仓位是否超单只上限
  - 是否触发了风险排查的一票否决项
- 校验不通过 → 信号被拦截，返回拒绝原因，Claude Code 修正后重新提交
- 校验通过 → 信号进入模拟盘（2.4）或实盘（Phase 3）执行队列

> **为什么放在模拟盘之前**：没有验证的信号不能进入任何执行管线——即使是模拟盘也不能执行越界信号。这是交易安全的第一道闸门。

### 2.3 `tools/risk.py` — 确定性风险计算器

**借鉴 ai-hedge-fund 的 Risk Manager 纯数学层**：ai-hedge-fund 的 Risk Manager 是唯一不使用 LLM 的组件——波动率计算、相关性矩阵、仓位上限全部是 numpy/pandas 确定性计算。直接移植到 AlphaClaude 的工具层：

| 函数 | 来源 | 逻辑 |
|------|------|------|
| `calculate_volatility_metrics()` | ai-hedge-fund `risk_manager.py:222` | 日波动率 → 年化波动率 → 波动率分位（30日滚动窗口） |
| `calculate_volatility_adjusted_limit()` | ai-hedge-fund `risk_manager.py:270` | 低波动(<15%)→25%仓位 / 中波动(15-30%)→15-20% / 高波动(>50%)→≤10% |
| `calculate_correlation_multiplier()` | ai-hedge-fund `risk_manager.py:301` | 高相关性(≥0.8)→0.7x / 中等(0.4-0.6)→1.0x / 低相关(<0.2)→1.1x |
| `position_size()` | 新增 | 综合波动率 + 相关性 → 输出建议股数 |
| `max_drawdown_check()` | 新增 | 当前回撤 vs 历史最大回撤，超阈值报警 |

调用方式：`python tools/risk.py 600519 --capital 100000 --positions 600519,000858` — JSON 输出仓位建议。Claude Code 在产出交易信号前调用此工具获取仓位约束，信号中的"建议仓位"字段直接引用 risk.py 的输出。

> **为什么是 CLI 工具而不是独立服务**：和现有 13 个工具完全一致的模式。纯数学计算、无状态、即时返回。零架构冲突。

### 2.4 模拟盘引擎 + 审计日志 — Phase 3 实盘的前置关卡

虚拟账户 + 初始资金。每次 skill 策略产生交易信号并通过验证层（2.2）后自动在模拟盘中以实时市价成交。每日核算持仓盈亏、累计收益率、胜率、最大回撤、夏普比率。

**借鉴 QuantDinger 的 Agent 审计设计**：QuantDinger 的 `qd_agent_audit` 表记录每笔 Agent 调用的完整链路——谁、什么时间、什么操作、结果如何。适配为模拟盘审计：

```
data/paper/audit.jsonl  — 每笔模拟交易一行 JSON：
{
  "trade_id": "paper_20260503_001",
  "timestamp": "2026-05-03T10:30:00",
  "skill": "stock-analyzer",
  "signal": {"symbol": "600519", "action": "buy", "price": 1850.00, ...},
  "validation": {"passed": true, "checks": {...}},
  "execution": {"fill_price": 1851.50, "slippage": 0.08},
  "pnl": null  // 平仓时回填
}
```

达到预设标准（如 30 笔交易 + 胜率 >55% + 夏普 >1.0）后，该策略获得实盘准入资格。

**区别于回测**：回测是历史拟合，模拟盘是实时市场下的前向检验——暴露的是完整的策略+执行链路在真实环境中的表现，而非仅验证公式本身。

### 2.5 交易跟踪

`/track <代码> <买入价> <止损> <止盈>` 记录每笔推荐。每日收盘 cron 对比目标价 vs 实际价。`/track status` 输出累计胜率、P&L、夏普比率。按策略维度拆分统计，识别哪些策略真正有效、哪些需要调优。

### 2.6 富文本战报 + Jinja2 模板

用 Jinja2 结构化模板替代手拼 Markdown：

- 每日 15:30 收盘总结用飞书 `send_post` 卡片格式：涨的绿色、跌的红色、胜率汇总、模拟盘净值曲线
- 模板支持多格式输出：飞书卡片 / 纯文本 / Markdown
- 卡片适合截图分享→其他群→自然增长

### 2.7 自选股异动监控

`/watch 600519` `/unwatch` `/portfolio`。`tools/portfolio.py` 已完成（增删查改 + 持仓盈亏概览）。待完成：自选股触发异动条件（用户设定价格阈值）时主动推送飞书通知。

### 2.8 一键部署 + CI/CD

- Docker Compose + 预配置 `.env` 模板。非程序员 5 分钟内从零到跑起来
- GitHub Actions 定时触发分析任务（零服务器成本方案）
- 稳定后考虑飞书应用商店上架

### 2.9 可靠性加固

进程守护 (systemd / Docker restart)、结构化日志、崩溃飞书告警。Session 排队替代"正忙，请稍后"。

> **模拟盘通过标准是进入 Phase 3 的硬性条件**——没有经过前向检验的策略不能接触真实资金。

---

## Phase 3: 实盘交易管线 (P1) — 准入条件：Phase 2 模拟盘验证通过

| ID | Feature | Description |
|----|---------|-------------|
| 3.1 | **券商接入** | `tools/trade.py` 封装券商 API。首选东方财富 OpenAPI（低门槛、RESTful、散户友好）。后期可选 QMT/PTrade 等专业通道。JSON 订单格式：`{symbol, action, quantity, price, order_type}`。 |
| 3.2 | **交易确认流** | 每笔订单触发飞书交互卡片："*即将买入 贵州茅台 100 股 @ ¥1850，确认？*" 用户点击"确认"→ 订单执行 → 成交回报推送。**绝不静默自动交易。** |
| 3.3 | **策略→信号→交易 全链路** | 端到端：skill 框架触发工具链 → Claude Code 产生结构化交易信号 → 飞书确认卡片 → `trade.py` 执行 → P&L 跟踪。人始终在环路中。 |
| 3.4 | **条件单** | 止损/止盈订单持久化到 `data/orders.json`。独立监控进程每 30s 检查价格，触发条件即执行。重启后自动恢复。 |
| 3.5 | **双层安全闸门** | **借鉴 QuantDinger 的 Agent Gateway 安全模型**：QuantDinger 的 `paper_only` 是双层闸门——token 级别 `paper_only=true`（默认）+ 服务端 `AGENT_LIVE_TRADING_ENABLED=true`（需显式开启）。适配为：Phase 3 启动时默认 `PAPER_ONLY=true`，实盘需同时在 `.env` 配置和运行时命令两处显式确认。任一闸门未开启则拒绝执行实盘订单。**这不是可选项——是实盘准入的硬性安全要求。** |
| 3.6 | **订单幂等性** | **借鉴 QuantDinger 的 `Idempotency-Key` 模式**：每笔交易信号生成唯一 `trade_id`（`{date}_{symbol}_{seq}`），下单前检查 `data/orders.json` 是否已存在相同 ID。防止飞书消息重复/网络重试导致重复下单。QuantDinger 在 `qd_agent_jobs` 表中做了相同设计——同一 `(token_id, kind, idempotency_key)` 组合只执行一次。 |

---

## Phase 4: 增强层 (P2) — 锦上添花

前三阶段已构成完整的「分析→推荐→跟踪→交易」闭环。Phase 4 是在此之上的体验和深度增强，不阻塞核心业务。

| ID | Feature | Description |
|----|---------|-------------|
| 4.1 | **持仓跟踪系统** | FIFO/平均成本法、分红/拆股处理、实时估值、风险分析。`/portfolio` 查看完整持仓和损益。 |
| 4.2 | **深度回测报告** | 不仅做统计回测（胜率/夏普/最大回撤），还加入 AI 回测验证：评估历史分析建议的准确性和概率校准。参数网格搜索优化、蒙特卡洛模拟。 |
| 4.3 | **盘中异动引擎** | 独立进程每 30-60s 轮询。检测：价格突破、成交量异动、涨跌停逼近、指数拐点。飞书卡片推送订阅用户。 |
| 4.4 | **新闻情绪管线** | 定时抓取财经新闻 → 轻量情感分类 → 按股票关键词索引的 JSON 缓存 → 用户查询时注入上下文。不上重向量数据库。 |
| 4.5 | **Kronos 类辅助模型** | 引入预训练的金融 K 线基础模型（如 Kronos，22k+ stars）作为辅助判断信号源。`tools/ml_opinion.py` 调用模型 API，返回结构化的技术面评分。Claude Code 将其视为 14 个数据源之一纳入分析，不替代主决策逻辑。位置在 Phase 4 而非 Phase 2 的理由：它是锦上添花的多维度参考，不是策略可信度的核心前提。 |
| 4.6 | **多持仓相关性风险** | **借鉴 ai-hedge-fund 的 Risk Manager 相关性矩阵**：当模拟盘/实盘持仓 ≥2 只时，`tools/risk.py` 输出持仓间相关性矩阵。高相关性（≥0.6）→ 自动降仓位上限，防止"分散了但没真正分散"的集中风险。ai-hedge-fund 的 `calculate_correlation_multiplier()` 直接从 `risk_manager.py:301` 移植。 |
