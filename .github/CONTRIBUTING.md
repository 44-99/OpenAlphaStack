# Contributing to OpenAlphaStack

感谢你帮助改进 OpenAlphaStack。项目欢迎可复现的 Bug 报告、边界清晰的功能建议
和聚焦的 Pull Request。

开始前请先阅读 [Agent 开发指南](../docs/agent-guide.md)。以下边界不能通过普通功能 PR
绕过：应用不启动 Agent/LLM 子进程；Agent 工作流属于 `skills/`；MCP 工具必须
有类型且范围受限；Python 引擎保持确定性；公开执行只允许回测和模拟盘。

## 报告问题

- Bug 请使用 Bug report 模板，并提供最小复现、完整错误文本和环境版本。
- 功能建议请先说明用户问题，再说明方案；涉及实盘下单的请求不在项目范围内。
- 安全问题不要提交公开 Issue，请按 [安全策略](SECURITY.md) 私下报告。

## 本地开发环境

Windows PowerShell：

```powershell
git clone https://github.com/44-99/OpenAlphaStack.git
cd OpenAlphaStack
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[all]" pytest PyYAML
npm ci
```

macOS / Linux：

```bash
git clone https://github.com/44-99/OpenAlphaStack.git
cd OpenAlphaStack
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[all]" pytest PyYAML
npm ci
```

然后运行：

```powershell
openalphastack doctor
```

预期第一行是 `OpenAlphaStack doctor: PASS`。完整首次使用流程见
[首次使用指南](../docs/getting-started.md)。

## 修改原则

- 一次 PR 解决一个明确问题，避免顺手重构无关模块。
- 行为变化必须补测试；尤其是 MCP 校验、paper-only 边界、幂等性、状态恢复和
  SQLite 原子提交。
- 不提交 `.env`、`data/` 下的运行数据、真实账户信息或私密飞书内容。
- Dashboard 默认保持 `127.0.0.1`，没有认证和网络策略前不要扩大监听范围。
- 缺失或过期计划必须保持观察模式，不能让引擎自行猜测交易计划。
- 推理文字和置信度只用于审计展示，不能成为执行资格或硬风控条件。

## 提交前验证

先运行与你的修改直接相关的最小测试，再运行完整基线：

```powershell
npm run dashboard:test
npm run dashboard:build
python -m pytest -q
python -m compileall -q src\openalphastack
python -X utf8 C:\Users\Admin\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\market-analyzer
npm audit
```

最后一条 Skill 校验命令来自本机 Codex 安装路径；其他平台请把脚本路径替换为
自己的 Codex skill-creator 路径。它需要 `PyYAML`；`-X utf8` 用于避免中文 Windows
默认编码读取中文 `SKILL.md` 失败。

涉及 MCP transport 或模拟计划发布时，再执行 README 中的真实 stdio 冒烟测试。
任何测试无法运行都应在 PR 描述中写明原因，不要把未验证写成已通过。

## Pull Request 清单

- [ ] 修改符合 Agent 开发指南和 paper-only 边界。
- [ ] 新行为有测试或可复现的验证证据。
- [ ] 文档、示例和预期输出已同步。
- [ ] 没有提交 secret、运行数据或无关文件。
- [ ] 已列出执行过的命令及结果。
- [ ] Breaking change、迁移步骤和已知限制已明确说明。

维护者会优先处理范围小、证据完整、不会模糊 Agent 与确定性执行边界的改动。
