# OpenAlphaStack 首次使用：从 clone 到离线 Skill 与 Dashboard

这份指南面向第一次使用 OpenAlphaStack 的 Codex Desktop 用户。完成后你会得到
三个可检查的结果：`doctor` 通过、Codex 能用 `$market-analyzer` 读取明确标记的
离线 Demo、浏览器能打开本地 Dashboard。

> OpenAlphaStack 仅用于研究、回测与模拟交易，不连接真实券商，也不承诺收益。

## 预计耗时

- 基础安装与离线 MCP：约 1 分钟。
- Dashboard 依赖与构建：约 1 分钟。
- Codex Desktop 安装插件、重启并完成一次 Skill 对话：约 2–5 分钟。

实际耗时取决于网络、pip/npm 缓存和磁盘速度。本文末尾记录了 2026-07-24 的
一次全新 clone 实测结果。

## 环境要求

| 工具 | 最低版本 | Windows 检查命令 | macOS / Linux 检查命令 |
| --- | --- | --- | --- |
| Git | 无特殊要求 | `git --version` | `git --version` |
| Python | 3.10+ | `py --version` | `python3 --version` |
| Node.js | 20.19+ | `node --version` | `node --version` |
| npm | 随 Node.js 安装 | `npm --version` | `npm --version` |
| Codex | 支持 Plugins 的 Codex Desktop 或 CLI | `codex --version` | `codex --version` |

## 1. Clone 并创建独立 Python 环境

### Windows PowerShell

```powershell
git clone https://github.com/44-99/OpenAlphaStack.git
cd OpenAlphaStack
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

如果 PowerShell 阻止激活脚本，只对当前终端临时放行后重试：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
git clone https://github.com/44-99/OpenAlphaStack.git
cd OpenAlphaStack
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 2. 运行 doctor

```powershell
openalphastack doctor
```

成功时第一行应为：

```text
OpenAlphaStack doctor: PASS
```

基础安装中 `import_fastapi` 可能显示为可选缺失，不影响离线 MCP 和 Skills。安装
Dashboard extra 后它应变为 `[ok]`。需要机器可读结果时使用：

```powershell
openalphastack doctor --json
```

## 3. 把本地插件安装进 Codex

仓库自带 repo marketplace。该流程遵循 Codex 官方的
[本地插件安装说明](https://learn.chatgpt.com/docs/build-plugins#install-a-local-plugin-manually)。
保持终端位于仓库根目录，执行：

```powershell
codex plugin marketplace add .
codex plugin add open-alpha-stack@openalphastack-local
```

然后重启 Codex Desktop，并新建一个以本仓库为工作目录的任务。插件安装后的
Skills 和 MCP 工具只保证在新任务中重新发现；不要用安装前已经打开的旧任务做
验收。

在 Plugins 的 Installed 区域应能看到 `OpenAlphaStack`。如果使用的是团队管理的
Codex，管理员策略可能禁止本地 marketplace 或 MCP；请让管理员允许该本地源。

## 4. 完成离线 Skill → MCP 首次成功

在新任务中复制下面这段提示词：

```text
使用 $market-analyzer 的离线 Demo 模式。必须通过 OpenAlphaStack MCP 依次读取
market_overview 和 market_news；先检查每个响应的 schema_version 与 ok，再展示
meta.source、meta.as_of、meta.freshness.status 和 meta.demo。最后给出一份简短市场
报告，并在标题和结论中明确写明“合成 Demo 数据，不代表今日行情”；不要创建或
发布任何模拟交易计划。
```

成功结果应同时满足：

1. Codex 明确使用 `$market-analyzer` 和 OpenAlphaStack MCP。
2. 两个响应均为 `schema_version=openalphastack.mcp/v1`、`ok=true`。
3. 两个响应均保留来源、截至时间与新鲜度，并显示 `meta.demo=true`。
4. 报告明确写明数据为合成 Demo，不把数值解释成今日行情。
5. 没有调用 `publish_paper_plan`，也没有产生交易或账本事件。

这一步才是 Codex Desktop 的首次激活点。`doctor` 通过只能说明本地文件、Python
依赖和 MCP 配置存在，不能替代一次真实 Skill 对话。

## 5. 构建并打开 Dashboard

回到已激活虚拟环境的终端：

```powershell
python -m pip install -e ".[dashboard]"
npm ci
npm run dashboard:build
openalphastack doctor
openalphastack app start
```

打开 <http://127.0.0.1:8800/dashboard>。成功时页面会显示本地演示数据；另一个
终端访问 <http://127.0.0.1:8800/health> 应返回类似：

```json
{
  "status": "ok",
  "service": "openalphastack-dashboard",
  "time": "..."
}
```

服务默认只监听 `127.0.0.1`。结束时在启动服务的终端按 `Ctrl+C`。

## 常见错误

### Windows 的 `python` 指向 Microsoft Store，无法启动

原因：Windows App Execution Alias 存在，但对应 Python 未安装。创建虚拟环境时
使用 `py -m venv .venv`；激活后再统一使用 `python -m pip ...`。

### `openalphastack` 不是可识别的命令

确认虚拟环境已激活，然后重装基础包：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
openalphastack doctor
```

### Codex 中看不到 `$market-analyzer`

依次确认：

```powershell
codex plugin marketplace list
codex plugin list
```

确认 `openalphastack-local` 与 `open-alpha-stack` 存在后，重启 Codex Desktop 并
新建任务。安装后的 Skill 不保证注入到旧任务。

### MCP 无法启动或提示找不到 `openalphastack`

Codex 启动 stdio MCP 时需要能在环境中找到安装后的控制台命令。先在同一个仓库
终端运行 `openalphastack doctor`；如果失败，重新激活 `.venv`。也可以用下面的
命令只检查服务能否启动，看到服务等待 stdio 输入后按 `Ctrl+C`：

```powershell
openalphastack mcp serve
```

### Dashboard 返回“请先构建”

```powershell
npm ci
npm run dashboard:build
```

### 端口 8800 已被占用

先停止占用 8800 的旧 OpenAlphaStack 进程。当前 `openalphastack app start` 固定
使用 8800；如果需要自定义端口，请提交功能请求，不要直接把 Dashboard 暴露到
局域网或互联网。

## 2026-07-24 全新环境验证记录

验证对象：`b43d15ee53ea5da712e36d409d859029b2ca4e00`，Windows、Git 2.55.0、
Python 3.14.6、Node.js 24.18.0、npm 11.16.0。使用新的临时目录、全新 clone、
全新 `.venv` 和 `npm ci`；以下数字只代表该机器的一次实测。

| 步骤 | 结果 | 实测耗时 |
| --- | --- | ---: |
| `git clone` | 通过 | 约 18.3 秒 |
| `py -m venv .venv` | 通过 | 2.81 秒 |
| `python -m pip install -e .` | 通过 | 约 26.3 秒 |
| `openalphastack doctor --json` | 通过；FastAPI 为可选缺失 | 2.87 秒 |
| 真实 stdio MCP：列工具并读两个 Demo 数据集 | 通过；18 个工具，两个响应均 `demo=true` | 1.03 秒 |
| `python -m pip install -e ".[dashboard]"` | 通过 | 25.23 秒 |
| `npm ci` | 通过 | 9.47 秒 |
| `npm run dashboard:build` | 通过 | 9.02 秒 |
| `openalphastack app start` + HTTP 检查 | `/health` 200、`/dashboard` 200 | 约 2.0 秒内就绪 |

验证中发现并处理：

- Windows 裸 `python` 命中了失效的 Microsoft Store alias，文档改为先用 `py` 创建
  venv。
- 原锁文件的 `npm audit` 报 8 项漏洞（含 1 项 critical）；发布准备已升级相关
  前端依赖，当前 `npm audit` 为 0 项。
- 仓库原先只有 plugin manifest，没有 repo marketplace；现已补齐安装入口。
- Skill 校验脚本依赖 `PyYAML`，并应使用 `python -X utf8` 读取中文 Skill；贡献者
  执行开发基线前需安装开发依赖。

本次 shell 验证覆盖了 plugin manifest、Skill 结构与真实 MCP transport，但没有在
一个全新的 Codex Desktop 用户配置中写入 marketplace 并启动新任务，因此上面的
第 3–4 步仍是发布前唯一需要人工点击确认的验收项。

## 下一步

- 了解执行边界：[架构说明](architecture.md)
- 查看全部 Skills：[Skills 文档](skills.md)
- 参与贡献：[贡献指南](../CONTRIBUTING.md)
- 报告安全问题：[安全策略](../SECURITY.md)
