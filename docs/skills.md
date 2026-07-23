# 领域 Skills

OpenAlphaStack 按可复用分析能力组织 Skills，而不是按执行时间拆分。Codex 普通
任务和定时提示词按需组合它们。

| Skill | 职责 | 主要 MCP 工具 |
|---|---|---|
| `market-analyzer` | 市场、情绪、板块和龙头分析 | `market_overview`、`market_news` |
| `stock-screener` | 确定性筛选和候选复核 | `screen_candidates`、行情、技术面、新闻 |
| `stock-analyzer` | 基于证据的单只股票分析 | 行情、技术面、基本面、新闻、风险 |
| `t0-intraday` | 现有持仓的 T0 可行性和约束 | 行情、技术面、仓位计算 |

## 定时任务组合

盘前任务通常在一个 Codex Agent 中组合 `market-analyzer`、`stock-screener` 和
`stock-analyzer`。它们是指令模块，不是子 Agent。Agent 只调用一次
`publish_paper_plan` 发布自动模拟计划，发布内部完成校验。草稿和校验预览工具
仅用于显式人工复核流程。

盘后任务直接读取运行快照和不可变账本，再按需调用 `market-analyzer` 或
`stock-analyzer` 做归因。策略和成本复盘是定时提示词或普通 Codex 任务，不是
单独 Skills。

编辑 Skill 后，使用 `skill-creator` 的 `quick_validate.py` 验证每项 Skill。
