# OpenAlphaStack 架构 v5

## 定位

OpenAlphaStack 是面向 A 股研究、回测、模拟执行和审计的开源 Codex 插件。
它打包 MCP 与领域 Skills，同时保持 Python 运行时与模型提供方无关；不会嵌入
或启动任何 Agent CLI。

## 组件

| 层 | 所有者 | 职责 |
|---|---|---|
| Agent 宿主 | Codex Desktop | 任务、定时调度、推理和人工复核 |
| 插件 | `.codex-plugin/plugin.json` | Skills 与 MCP 发现 |
| 工作流 | 领域 Skills | 市场、选股、个股和 T0 分析契约 |
| 协议 | `openalphastack` MCP | 类型化读取与受限模拟盘写入 |
| 领域层 | Python 包 | 数据、风险、计划、状态、账本和回测 |
| 运行时 | 模拟交易引擎 | 交易日历空闲、计划热加载和机械执行 |
| UI | Dashboard | 只读 K 线、账户、工作流和审计视图 |

## 控制流

```text
盘前定时任务
  -> 一个 Codex Agent 组合 market-analyzer + stock-screener + stock-analyzer
  -> 通过 MCP 读取市场与运行数据
  -> publish_paper_plan（仅模拟盘、幂等、乐观并发）
  -> 模拟引擎从 run.sqlite3 刷新更新且已校验的计划
  -> FastLane 应用确定性规则
  -> 在 run.sqlite3 中原子提交账户状态与账本事件
  -> 刷新便于人工查看的 JSON/JSONL 投影和工作流事件
  -> 盘后定时提示词只读复盘事实
```

## 边界规则

- Skills 可以提出建议；Python 负责校验。
- 默认工作流不启动子 Agent；Skills 是由一个 Agent 使用的指令模块。
- 置信度、推理文字和 Agent 风险报告是非阻断审计信息。
- Python 只拒绝契约违规和具体机械执行失败，不评价策略质量。
- MCP 可以发布模拟计划，不能提交真实订单。
- 引擎可以执行当前有效计划，不能调用模型。
- 紧急处理必须是确定性规则并发送通知。
- 缺少、过期或非法计划时进入观察模式。
- SQLite 是每次运行的事实源，JSON/JSONL 只是投影。
- 账户变更和对应账本事件在同一个 SQLite 事务中提交。
- 缺少分钟数据时直接失败；回测不从日线 OHLC 合成分钟 K 线。
- 公开引擎模式只有模拟盘和回测；历史实盘运行只读。

## MCP 功能面

stdio 服务通过 `openalphastack mcp serve` 启动，并在 `.codex/config.toml`
中配置。

读取与计算工具组：

- 市场概览、行情、技术面、基本面和新闻
- 确定性候选筛选与基准回测
- 模拟盘/回测运行快照和账本尾部
- 波动率和仓位计算
- 可选的计划校验预览

唯一可执行写入：原子发布已校验的模拟计划。

`save_plan_draft` 只作为可选、不可执行的人工复核辅助。自动任务只调用一次
`publish_paper_plan`，发布过程会自行完成校验。

每次发布都要求幂等键。可选的 `expected_updated` 对 Agent 读取后可能发生的
计划变更提供乐观并发保护。

### 带版本的响应契约

所有 MCP 工具都返回 `openalphastack.mcp/v1` 信封。消费者先检查 `ok` 再读取
`data`；失败包含稳定的 `error.code`、是否可重试和非敏感详情。市场响应提供
`meta.source`、`meta.as_of` 与 `meta.freshness`。计划和运行快照分别使用
`openalphastack.plan/v1` 与 `openalphastack.run-snapshot/v1`。

资源：

- `openalphastack://contracts/v1`
- `openalphastack://demo/catalog`
- `openalphastack://demo/{dataset}`
- `openalphastack://runs/{run_id}/snapshot`
- `openalphastack://runs/{run_id}/ledger`

内置 Demo 数据是静态合成测试夹具，只用于离线验证 Skill，不能修改或发布
交易计划。Dashboard 的 Demo 账户、计划和账本共享 `demo_data` 所有权边界；
图表与工作流夹具明确只用于 UI 展示。

## 持久化边界

每个模拟盘或回测运行都有一个 `run.sqlite3`，保存运行状态、当前已校验计划和
追加式账本事件。SQLite WAL、完整同步提交和即时写事务构成跨记录原子性边界。
`state.json`、`plan.json` 与 `ledger.jsonl` 用于人工查看和兼容；损坏或过期的
投影不能覆盖有效数据库。

## 调度边界

Codex 定时任务组合领域 Skills，完成盘前研究、盘后复盘或周期评估。这些时间
配方属于任务提示词，不应复制成单独 Skills。定时任务不是实时交易时钟，盘中
时间和执行由 Python 负责。

GitHub Actions 刻意不承担 Agent 调度。托管 Runner 无法访问操作者本地的
Codex 任务、stdio MCP 进程或模拟盘数据库。仓库工作流只做 CI 与部署检查；
先手动证明闭环，再在 Codex Desktop 中配置本地研究自动化。

## Dashboard 边界

Dashboard 不暴露 PowerShell、Claude Code 或 Codex 终端 WebSocket。工作流
提示词可以复制到 Codex Desktop，但浏览器不能执行任意本地命令。

工作流视图只有三个产品阶段：

```text
Research -> Execution -> Evaluation（可选）
```

历史十节点事件在读取时映射到这三个阶段。Dashboard 不提供工作流开关或虚假
重跑队列；调度和重跑属于 Codex Desktop，执行属于引擎。

## 部署

默认监听 `127.0.0.1`。远程部署需要独立的认证反向代理和安全评审。无论网络
如何配置，当前 MCP 写入契约始终仅限模拟盘。
