# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)，变更按
[Keep a Changelog](https://keepachangelog.com/) 结构记录。

## [Unreleased]

### Added

- 独立无状态公网只读 MCP，包含 12 个市场读取、确定性计算与
  合成 Demo 工具，以及 Streamable HTTP 和 Docker 部署入口。
- 公网 MCP 的 Privacy、Terms、Support 页面、域名验证端点和官方目录
  提交检查清单。

### Security

- 公网工具统一声明只读、非破坏、幂等 annotations，并限制股票代码、
  新闻数量、回测天数和价格序列大小。
- 公网入口排除本地 run、快照、账本、草稿与模拟计划发布，并启用
  Host/Origin 防护。

## [0.1.0] - 2026-07-24

### Added

- Codex 插件 manifest、四个 A 股领域 Skills 与本地 repo marketplace。
- 18 个带版本信封的 MCP 工具，包括确定性的离线 Demo 数据集。
- paper/backtest-only Python 引擎、SQLite 事务状态、幂等计划发布与追加式审计投影。
- 本地 FastAPI + React Dashboard，覆盖股票搜索、K 线、计划、持仓、账本与
  Research → Execution → Evaluation 工作流。
- `openalphastack doctor`、真实 stdio MCP 冒烟脚本和完整测试基线。
- 中文首次使用指南、贡献指南、安全策略、Bug/功能请求模板和 v0.1.0 Release
  Notes。

### Changed

- 默认研究工作流收敛为一个 Codex Agent 按需组合 Skills，不再要求默认多 Agent。
- 首次安装使用独立 venv、`npm ci`、明确预期输出和可复制的离线 Codex 提示词。
- 前端构建链升级至 Vite 7、Vitest 4 与 ECharts 6；Node.js 最低版本相应明确为
  20.19。

### Security

- 所有可变更 MCP 工具保持 paper-only，不公开真实下单、Shell 或任意文件写入。
- Dashboard 默认只监听 `127.0.0.1`。
- 更新前端依赖锁；`npm audit` 从 8 个已知问题降为 0。

### Validation

- 在 Windows 的全新 clone/venv 中验证基础安装、doctor、真实 stdio MCP 两个离线
  Demo 数据集、Dashboard 构建及 HTTP 200。
- Codex Desktop 的 marketplace 安装、重启和新任务 Skill 对话保留为发布前人工
  验收项；shell 验证不冒充 GUI 验证。

[Unreleased]: https://github.com/44-99/OpenAlphaStack/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/44-99/OpenAlphaStack/releases/tag/v0.1.0
