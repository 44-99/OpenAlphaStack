# 路线图

四阶段路线图，按依赖关系递进——每一阶段是下一阶段的前提。

---

## Phase 1: 工具底座 + 数据可靠性 + 交易铁律 (P0) ✅ 已完成

**目标**：Claude Code 拥有完整的分析工具箱、可靠的数据源、统一的交易纪律。

### 1.A 工具层 — 已完成

将 `stock.py` 的单体拆解为离散的 CLI 工具。每个工具单一职责：JSON 进、JSON 出。Claude Code 通过 Bash 按需调用。

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

> 13 个工具全部实现。腾讯 qt.gtimg.cn API 为主源，新浪 fallback，akshare 最后手段。CLI 而非 MCP Server：无状态、即时返回、零基础设施。

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

## Phase 2: 统一 Agent 引擎 + 策略闭环 (P0)

**核心目标**：构建一个在回测、模拟盘、实盘三种模式下运行完全相同代码路径的统一引擎。架构为 **盘后 Claude Code 批量分析 + 次日 Python 机械执行**，利用 A 股 T+1 制度和政策驱动特性获取 Alpha。

### 架构总览 (v3 — Sub-Agent Research + 1s Tick + Global Risk)

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
| 卫星 | 30% | screen.py + signal_rules.py (仅 action=buy) | 1-5 天 | 7.5% | -5% |
| 现金 | 20% | T+1 缓冲 + 极端机会 + 空仓储备 | — | — | — |

**空仓条件（双重确认）**：技术面恶化（上证 MA5<MA20 + 连跌 3 日）+ Claude Code bearish 同时满足 → 空仓至 20%。

**三种模式统一：**

| 维度 | Backtest | Paper | Live |
|------|----------|-------|------|
| 数据源 | 历史 K 线回放 | 实时行情 | 实时行情 |
| Claude Code | 盘后 3 次/日（3 sub-agent 并行, 历史重放） | 盘后 3 次/日 | 盘后 3 次/日 |
| API+Tool Use | 盘后 3 次/日（direction+candidates+adjustments） | 盘后 3 次/日 | 盘后 3 次/日 |
| 盘中 Claude Code | 无（紧急同样仿真） | 仅紧急触发 | 仅紧急触发 |
| Python 执行 | 完全相同 | 完全相同 | 完全相同 |
| 仓位上限 | 全仓（暴露策略真实表现） | 全仓 | ≤80% |

**文件结构（三种模式统一）：**

```
data/output/
  {mode}_{start_iso}/
    ledger.jsonl    # 决策账本 — 每天追加，跨会话连续
    state.json      # 完整状态 {cash, holdings, nav_curve, data_time}
    plan.json       # v2: market_bias + buy_candidates + holding_adjustments + risk_report
```

`data_time`：backtest 模式 = 回放到的历史日期时间；paper/live 模式 = 真实世界时间。

### 2.1 策略技能库 — 3 个场景化技能管线 ✅ 已完成

| 技能管线 | 场景 | 组成 |
|----------|------|------|
| `stock-analyzer` | 个股深度分析 | 6 阶段管线（数据→趋势→信号→位置→风险→输出） |
| `market-analyzer` | 市场研判 | 情绪周期 → 龙头识别 → 板块轮动 |
| `stock-screener` | 多因子选股 | 短线/中线/热钱 3 策略 |

策略组织：3 个管线 SKILL.md + 策略作为 references 按需加载。激活方式为 description-based（Agent 自主选择技能）。

### 2.2 交易信号验证层 ✅ 已完成

`tools/signal.py` — Claude Code 产出信号强制通过硬校验：

- 止损价 < 买入价（买入方向）
- 风险回报比 ≥ 1.5:1
- 乖离率 ≤ 5%（龙头 ≤ 7%）
- 置信度 0-100 范围
- 校验不通过 → 返回拒绝原因，不进入任何执行管线
- 校验通过 → 写入 `data/signals.jsonl` → 进入执行队列

### 2.3 确定性风险计算器 ✅ 已完成

`tools/risk.py` — 纯数学层，零 LLM：

| 函数 | 逻辑 |
|------|------|
| `calc_volatility_metrics()` | 日波动率 → 年化 → 波动率分位 |
| `calc_volatility_adjusted_limit()` | 低波动 → 25% 仓位 / 高波动 → ≤10% |
| `calc_correlation_multiplier()` | 高相关 → 0.7x / 低相关 → 1.1x |
| `calc_position_size()` | 综合波动率 + 相关性 → 建议股数 |
| `max_drawdown_check()` | 当前回撤 vs 历史最大回撤 |

### 2.4 统一 Agent 引擎 v3 — `tools/paper_engine.py` ✅ 已完成

Phase 2 核心交付物。v3 在 v2 基础上增加：sub-agent 并行研究层、1s tick 高速执行、规则信号 action 三态、账户级熔断。

**LLM 调用分工（关键架构决策）：**

| 阶段 | 方式 | 原因 |
|------|------|------|
| Phase 0 子智能体 × 3 | Claude Code CLI | 需要完整工具/记忆/技能上下文做研究 |
| Phase 1-3 结构化决策 | API + Tool Use | 输出给机器消费，API 层面 JSON Schema 强制，零解析 |
| 紧急响应 | API + Tool Use | 延迟敏感 + 输出必须精确结构化 |
| 飞书对话 | Claude Code CLI | 理解用户意图、查数据、个性化回复 |

**内部模块：**

| 模块 | 职责 |
|------|------|
| `SubAgentRunner` | Phase 0：并行启动 3 个 Claude Code 子任务（政策/板块/复盘），完整工具访问 |
| `OvernightPipeline` | Phase 1-3：API+Tool Use 结构化决策 + Python 风控，零 prompt 解析 |
| `llm_client.py` | 4 个 Tool Schema + `call_with_tool()` 封装 Anthropic SDK，懒加载规避 signal 冲突 |
| `PlanV2` | plan.json v2 读写（market_bias / buy_candidates / holding_adjustments / emergency_triggers / risk_report） |
| `FastLane` | 盘中 Python 执行：1s tick 并行行情+扫描，action=buy/sell/alert 分支，去重，熔断 |
| `EmergencyTrigger` | 大盘-3% / 个票-5% / 账户-10% → 暂停自动交易 → Claude Code 紧急会话 |
| `BacktestRunner` | 历史重放回测，Claude Code 看到仿真日期的历史新闻 |
| `Ledger` / `State` / `Clock` | 决策账本 / 资金持仓状态 / 仿真时钟 |

**已复用组件（需小幅修改）：** `signal_rules.py`（加 action 字段 buy/sell/alert）、`signal.py`（校验层）、`risk.py`（风险计算器 + 熔断）

**回测：** 250 个交易日，每交易日约 2-3 分钟（3 sub-agent 并行约 20s + API Tool Use 3 次约 80s + 风控 <1s）。

**准入标准（进入实盘的前提）：**
- ≥ 30 笔交易
- 胜率 > 55%
- 夏普 > 1.0
- 最大回撤 < 25%
- **新增：模拟盘 ≥ 1 个月验证**

### 2.5 CI/CD ✅ 已完成

| 工作流 | 触发 | 内容 | LLM |
|--------|------|------|-----|
| `ci.yml` | 每次 push | 19 个工具 schema 校验、规则层基线对比、ruff lint | 零 |
| `agent-backtest.yml` | 手动触发 | 完整 Agent 回测，产出胜率/夏普/回撤报告 | Claude Code 全程参与 |
| `deploy.yml` | 手动触发 | Docker build + push → SSH VPS → docker compose up -d | 零 |

### 2.6 可靠性加固 ✅ 已完成

- Docker Compose + `restart: unless-stopped`
- `logging_config.py` — JSON 结构化日志 + 自动轮转（3 handler）
- `daily_report.py` — 日交易报表（P&L/胜率/回撤，飞书推送）

### 2.7 飞书监控推送 ✅ 已完成

**目标**：引擎（回测/模拟盘/实盘）所有关键事件通过飞书实时推送，无需手动翻 JSON 文件。

**分工原则**：

| 通道 | 职责 | 适合场景 |
|------|------|----------|
| **飞书** | 推送 + 指令控制 | 异常告警、每日简报、交易通知、`/status` `/positions` 等快捷指令 |
| **Web 页面** | 可视化监控 + 复盘 | 净值曲线、持仓仪表盘、多 run 收益对比（Phase 4 交付） |

**飞书通知事件清单**：

| 事件 | 优先级 | 触发时机 |
|------|--------|----------|
| 🚀 引擎启动 | P1 | 回测/模拟盘/实盘启动时，报告模式和参数 |
| 📊 盘后流水线完成 | P1 | 三阶段完成（子智能体 + API Tool Use + 风控），报告候选/通过数 |
| 💰 交易执行 | P1 | 每笔买入/卖出/止损成交，含价格/数量/盈亏 |
| 🚨 异常告警 | P0 | 选股超时、API 报错、紧急触发（大盘-3%/个票-5%/账户-10%） |
| 📈 每日简报 | P1 | 收盘后：当日 P&L、胜率、持仓变动、净值 |
| ⏸️ 引擎停止 | P2 | 正常停止或异常退出 |
| 🔄 进度播报 | P2 | 回测每 20 个交易日播报一次进度和中间统计 |

**实现**：
- `tools/notifier.py` — 封装飞书推送，引擎直接调用，不关心 chat_id/message 格式
- `config.py` — `ENGINE_CHAT_IDS` 环境变量指定通知目标（可复用 `ALERT_CHAT_IDS`）
- `paper_engine.py` — 关键节点调用 `notifier.xxx()`

### 2.8 Web 监控面板 (Phase 4 交付)

飞书解决"知道发生什么"，Web 解决"看清全貌"：
- 实时净值曲线图（Plotly/ECharts）
- 持仓盈亏仪表盘
- 多 run 对比（回测收益曲线叠加）
- 交易记录可交互表格

技术选型待定（Streamlit 快速原型 vs FastAPI + 静态 HTML）。

---

## Phase 3: 实盘交易管线 (P1)

**准入条件：** Agent 回测 + 模拟盘 ≥ 1 个月同时达标（≥30 笔交易，胜率 > 55%，夏普 > 1.0，最大回撤 < 25%）。

实盘与模拟盘跑**完全相同的代码**。唯一差异：

| 维度 | Paper | Live |
|------|-------|------|
| 资金账户 | data/paper_*/state.json 虚拟现金 | 券商账户真实资金 |
| 启动方式 | 自动 | 用户飞书说「开启实盘」或 `/trade live on` |
| 安全闸门 | 无 | 双层闸门（.env + 运行时命令双重确认） |

| ID | Feature | Description |
|----|---------|-------------|
| 3.1 | **券商接入** | `tools/trade.py` 封装券商 API。首选东方财富 OpenAPI（RESTful、散户友好）。JSON 订单格式 |
| 3.2 | **交易确认流** | 每笔订单触发飞书交互卡片确认。**绝不静默自动交易** |
| 3.3 | **双层安全闸门** | `.env` `PAPER_ONLY=true`（默认）+ 运行时 `--mode live` 显式确认。任一闸门未开启 → 拒绝实盘订单 |
| 3.4 | **订单幂等性** | 每笔信号生成唯一 `trade_id`（`{date}_{symbol}_{seq}`），下单前检查 `data/orders.json` 去重 |
| 3.5 | **自主等级** | L0 完全手动 / L1 半自动（预设参数内自主执行）/ L2 全托管（人类收盘 review）。默认 L1 |

---

## Phase 4: 增强层 (P2)

| ID | Feature | Description |
|----|---------|-------------|
| 4.1 | **深度回测报告** | AI 回测验证（历史分析建议的概率校准）、参数网格搜索、蒙特卡洛模拟 |
| 4.2 | **多持仓相关性风险** | 持仓 ≥2 只时输出相关性矩阵，高相关（≥0.6）自动降仓位上限 |
| 4.3 | **新闻情绪管线** | 定时抓取财经新闻 → 轻量情感分类 → 按股票索引 JSON 缓存 |
| 4.4 | **Kronos 类辅助模型** | 预训练 K 线模型作为辅助信号源，不替代主决策逻辑 |
| 4.5 | **Web 监控面板** | 实时净值曲线、持仓仪表盘、多 run 收益对比、交易记录表格（见 2.8 设计） |
| 4.6 | **飞书应用商店上架** | 稳定后考虑公开发布 |
