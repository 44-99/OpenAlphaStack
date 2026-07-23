---
name: stock-screener
description: 使用确定性规则筛选 A 股候选标的。用户要求选股、推荐标的、寻找短线或中线机会、热点和游资方向时使用。通过 OpenAlphaStack MCP 筛选并复核候选，最多输出五只，不直接发布交易计划。
---

# 选股筛选

筛选负责缩小研究范围，不负责证明未来收益。

## 工作流

1. 根据用户目标选择策略：
   - 短线或未指定：读取 `references/short-term.md`，使用 `breakout`。
   - 中线：读取 `references/mid-term.md`，使用 `value`。
   - 热点或游资：读取 `references/hot-money.md`，使用 `hot_money`。
2. 调用 `screen_candidates`，传入策略和合理的 `top_n`。
3. 对排名前 3–5 的候选并行调用 `stock_quote`、`stock_technical` 和 `stock_news`。
4. 需要估值确认时调用 `stock_fundamentals`。
5. 排除行情失效、代码异常、重大利空或技术结构已破坏的候选。
6. 输出代码、名称、筛选依据、复核证据、风险和数据时间；结果不足时不要补齐数量。

如需进一步分析某只股票，转入 `$stock-analyzer`。如需形成模拟盘计划，必须另外读取目标 paper run、校验计划并通过 MCP 保存草稿；本 Skill 不直接发布计划。
