---
name: stock-analyzer
description: 对具体 A 股进行技术面、基本面、新闻、位置和风险分析。用户提供股票代码或名称，询问能否买入、卖出、持有，或需要入场价、止损、止盈与仓位建议时使用。通过 OpenAlphaStack MCP 获取数据，仅提供研究结论，不直接修改模拟盘状态。
---

# 个股分析

所有结论必须能追溯到 MCP 返回的数据和时间。

## 工作流

1. 规范化为六位股票代码；无法确认代码时先要求用户补充。
2. 并行调用 `stock_quote`、`stock_technical` 和 `stock_news`。
3. 涉及估值、盈利质量或行业比较时调用 `stock_fundamentals`。
4. 根据均线、MACD、量价、乖离率和波动判断趋势：`BEAR`、`WEAK_BEAR`、`SIDEWAYS`、`BULL` 或 `STRONG_BULL`。
5. 需要入场信号时读取 `references/entry-signals.md`；需要支撑、阻力和持仓处理时读取 `references/position-management.md`。
6. 读取 `references/risk-checklist.md`，检查公告、业绩、监管、解禁、估值和数据质量风险。
7. 若给出仓位建议，调用 `calculate_position_size`；只有取得所需收盘价序列时才调用 `calculate_volatility`。
8. 输出趋势、信号、位置、风险、研究建议、数据时间和缺失信息。

价格区间、止损和止盈必须说明依据。不得绕过 MCP 直接写 `plan.json`，不得提交真实订单，也不得把一次分析表述为收益保证。

## MCP 响应契约

- 每次调用先检查 `schema_version` 和 `ok`，分析字段只从 `data` 读取。
- 报告数据来源时引用 `meta.source`、`meta.as_of` 和 `meta.freshness.status`；过期或时间未知的数据必须进入风险部分。
- `ok=false` 时依据 `error.code` 决定重试或停止，不得把供应商异常当作个股事实。
- 离线演示可读取 `stock_quote`、`stock_technical`、`stock_fundamentals` 和 `stock_news` Demo 数据集；必须注明是合成数据，不能形成真实股票建议。
