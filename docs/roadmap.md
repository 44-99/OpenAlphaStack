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

**核心目标**：构建一个在回测、模拟盘、实盘三种模式下运行完全相同代码路径的统一引擎。回测用历史数据加速回放，模拟盘和实盘用实时行情——除此之外没有任何区别。这是整个系统的信任基础。

### 架构总览

```
┌─ Python Engine (常驻进程) ───────────────────────────────────┐
│                                                               │
│  根据 MODE 切换数据源:                                         │
│    backtest → 历史 K 线逐日回放                                │
│    paper    → 腾讯/新浪实时行情                                 │
│    live     → 腾讯/新浪 + 券商 API                             │
│                                                               │
│  快车道 (1-5s, backtest 模式下加速):                           │
│    · 价格监控 → 止盈止损触发 → 立即撮合                         │
│    · 规则信号 (金叉/放量) → signal.py → 自动执行               │
│    · 异动检测 → 写入 event_queue.jsonl                         │
│    · 按 plan.json 执行当前持仓计划                              │
│                                                               │
│  会话管理 (单实例锁):                                           │
│    · 9:15 启动 Claude Code session                             │
│    · 维持单会话至 15:30 收盘                                    │
│    · 盘前竞价 9:15-9:25 独立处理                                │
│    · 全局互斥锁 — 同一时间只有 1 个 Claude Code                  │
│                                                               │
└───────────────────────────────────────────────────────────────┘

┌─ Claude Code (每天一个会话) ──────────────────────────────────┐
│                                                               │
│  启动 → 读 state.json + plan.json + ledger.jsonl + 当日数据    │
│  盘中 → Python 推送事件 → tools 分析 → skills 策略 → 决策      │
│  结束 → 写 ledger.jsonl + state.json + plan.json               │
│                                                               │
│  回测时钟模型:                                                  │
│    · 异动触发 → 仿真时钟暂停                                    │
│    · Claude Code 分析 ~60s（现实时间）                          │
│    · 决策产出 → 仿真时钟 +60s → 以此时历史价成交                 │
│    · 仿真时钟继续 ▶                                             │
│    · 无信号的时段快速扫描（毫秒级跳过）                          │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

**三层统一，完全相同的代码路径：**

| 维度 | Backtest | Paper | Live |
|------|----------|-------|------|
| 数据源 | 历史 K 线回放 | 实时行情 | 实时行情 |
| 资金账户 | 虚拟 | 虚拟 | 券商真实 |
| 时钟 | 加速（Claude Code 时暂停） | 真实 | 真实 |
| Claude Code | 每天一个会话 | 每天一个会话 | 每天一个会话 |
| 信号校验 | signal.py | signal.py | signal.py |
| 文件结构 | backtest_YYYY-MM-DD_HHMMSS/ | paper_YYYY-MM-DD_HHMMSS/ | live_YYYY-MM-DD_HHMMSS/ |

**文件结构（三种模式统一）：**

```
data/output/
  {mode}_{start_iso}/
    ledger.jsonl    # 决策账本 — 每天追加，跨会话连续
    state.json      # 完整状态 {cash, holdings, nav_curve, data_time}
    plan.json       # 当前持仓计划（止盈止损位、持仓预期）
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

### 2.4 统一 Agent 引擎 — `tools/paper_engine.py`

Phase 2 的核心交付物。一个引擎，三种模式。

**双速架构：**

| 车道 | 执行者 | 速度 | 职责 |
|------|--------|------|------|
| 快车道 | Python | 1-5s | 止盈止损、规则信号、异动检测、执行 plan.json |
| 慢车道 | Claude Code | 按需 | 策略判断、多因子综合、计划更新、写 ledger |

**策略两层模型：**

| 层级 | 执行者 | 回测能力 | 模拟盘 | 实盘 |
|------|--------|----------|--------|------|
| 规则层 | Python `signal_rules.py` | ✅ 纯 Python 回测 | ✅ 自动执行 | ✅ 自动执行 |
| 判断层 | Claude Code + skills | ✅ Agent 回测含 LLM | ✅ 交易时段 | ✅ 交易时段 |

- 规则层：金叉/死叉、放量突破、乖离率阈值、均线排列——确定性条件，Python 直接触发
- 判断层：真突破还是诱多、多因子综合、板块轮动判断——需要 Claude Code 推理

**会话锁 + 事件队列：**

```
Python 检测事件 → event_queue.jsonl
触发条件满足 → acquire_lock()（全局互斥）
→ 启动 Claude Code（单实例）
→ Claude Code 读累积事件 → 分析 → 决策
→ 写 ledger + plan + state
→ release_lock()
会话期间新事件继续写入队列，等下次触发
```

**集合竞价 9:15-9:25：**

```
9:15  引擎启动，监控竞价价格
9:20  检测开盘价偏离昨收 > 3% → 标记异动
9:25  撮合出开盘价 → 触发 Claude Code 盘前 session
9:30  连续竞价开始，按 Claude Code 产出计划运行
```

### 2.5 规则信号引擎 — `tools/signal_rules.py`

纯 Python，零 LLM。在快车道中运行，产出确定性信号：

- MA 金叉/死叉检测
- 放量突破（量比 > 1.5 且涨幅 > 2%）
- 乖离率偏离报警
- 均线多头/空头排列变化
- 筹码集中度变化

所有规则信号同样经过 `signal.py` 校验通道。与 Claude Code 信号的区别：规则信号是确定性触发，不需要推理。

### 2.6 策略决策账本 — `ledger.jsonl`

解决 Claude Code 跨 session 上下文丢失导致策略反复横跳。

```
每行一条决策:
{"seq": 1, "time": "09:35", "decision": "今日偏多，消费主线",
 "confidence": 75, "evidence": ["降准落地", "北向净流入"]}
{"seq": 2, "time": "09:42", "decision": "600519 开仓 100 股",
 "reason": "放量突破 + MA5>MA10>MA20", "from_seq": 1}
```

规则：
- Claude Code 每次启动**必须读完整 ledger**
- 要改之前的决策 → 必须给出明确的反向证据
- 无反向证据 → 默认维持前判，只做微调
- 不依赖上下文窗口 — 依赖文件系统

### 2.7 Agent 回测体系 — `tools/backtest_runner.py`

**回测 = 模拟盘 + 历史数据。** 代码路径完全相同。

**时钟模型：**
- Claude Code 运行时 → 仿真时钟暂停
- 决策产出后 → 仿真时钟 +60s → 以此时历史价成交
- 无信号时段 → 快速扫描（毫秒级跳过）
- 延迟模型与模拟盘/实盘一致（都是 ~60s 分析延迟）

**使用方式：**
```
# 启动完整 Agent 回测
python tools/paper_engine.py --mode backtest \
  --start 2023-01-01 --end 2024-12-31 \
  --universe screen_breakout --capital 100000

# 断点续跑
python tools/paper_engine.py --mode backtest --resume day_042
```

**回测耗时估算：** 250 个交易日 × 日均 1-2 次 Claude Code 调用 × 60s ≈ 4-8 小时。夜间跑一次绰绰有余。

**准入标准（进入实盘的前提）：**
- ≥ 30 笔交易
- 胜率 > 55%
- 夏普 > 1.0
- 最大回撤 < 25%

### 2.8 CI/CD

三种模式，按成本和 LLM 参与度分层：

| 工作流 | 触发 | 内容 | LLM |
|--------|------|------|-----|
| `ci.yml` | 每次 push | Python 工具 schema 校验、规则层基线对比、代码 lint | 零 |
| `agent-backtest.yml` | 手动 / 每周 | 完整 Agent 回测（6 个月 / 50-200 只），产出胜率/夏普/回撤报告 | Claude Code 全程参与 |
| `deploy.yml` | 手动触发 | Docker build + push → SSH VPS → docker compose up -d | 零 |

```
ci.yml (push, 2min):
  ├─ Python 工具冒烟测试 (13 个工具 schema 校验)
  ├─ 规则层策略基线对比 (纯 Python, 对比 data/baselines/)
  └─ 代码 lint

agent-backtest.yml (manual / weekly cron):
  ├─ 启动 paper_engine.py --mode backtest
  ├─ 产出报告 → 保存 data/backtest_reports/
  └─ 基线更新 (可选)

deploy.yml (manual):
  ├─ docker build + push
  └─ ssh VPS → docker compose up -d
```

### 2.9 可靠性加固 ✅ 已完成

- Docker Compose + `restart: unless-stopped`
- `logging_config.py` — JSON 结构化日志 + 自动轮转（3 handler：stdout / JSON debug / JSON error）
- `.github/workflows/` — CI/CD 管线

### 2.10 收盘日报

每日 15:30 Claude Code 会话结束前产出：

- 当日 P&L、胜率、持仓变动
- 净值曲线更新
- 飞书卡片推送（涨绿跌红）
- 同时写入 `data/{mode}_{start_iso}/daily_reports/`

---

## Phase 3: 实盘交易管线 (P1)

**准入条件：** Agent 回测 + 模拟盘同时达标（≥30 笔交易，胜率 > 55%，夏普 > 1.0）。

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
| 4.5 | **飞书应用商店上架** | 稳定后考虑公开发布 |
