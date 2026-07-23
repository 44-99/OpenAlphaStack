---
name: market-analyzer
description: 分析 A 股大盘、市场情绪、板块轮动和龙头线索。用户询问今日市场、指数强弱、情绪周期、强势板块、北向或资金方向、龙头股时使用。通过 OpenAlphaStack MCP 获取带时间戳的数据，不用于发布交易计划。
---

# 市场研判

先获取事实，再进行解释；缺失的数据必须明确标记。

## 工作流

1. 调用 `market_overview` 获取指数、涨跌分布、成交等市场概况。
2. 调用 `market_news` 获取近期市场新闻，保留来源和时间。
3. 需要判断情绪周期时，读取 `references/sentiment-cycle.md`。
4. 需要判断板块持续性时，读取 `references/sector-rotation.md`。MCP 未返回板块资金数据时，不得用新闻标题替代资金流证据。
5. 需要识别龙头时，读取 `references/dragon-head.md`；对少量候选调用 `stock_quote`、`stock_technical` 和 `stock_news` 复核。
6. 输出市场环境、情绪阶段、板块线索、龙头证据、数据限制和适用的风险暴露区间。

不要把市场研判直接当作买卖指令，也不要承诺收益。
