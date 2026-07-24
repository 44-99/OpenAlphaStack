# 公网只读 MCP 部署

OpenAlphaStack 的公网 MCP 是与本地插件分离的、无状态的只读服务。它用于官方
目录扫描和远程研究调用，不读取操作者本机的运行、持仓、计划、账本或文件。

## 为什么 GitHub Pages 不能直接运行 MCP

`https://44-99.github.io/OpenAlphaStack/` 是静态网站。它适合托管官网、Privacy、
Terms、Support、图片和静态 JSON，但不能运行 Python、维持 ASGI 服务或处理 MCP
的 `initialize`、`tools/list`、`tools/call` 请求。

官方提交表单要求填写生产 MCP server URL，并会扫描工具和元数据。因此最终需要
另一个支持长期运行容器或 Python ASGI 的 HTTPS 服务，例如 Render、Railway、
Fly.io、Azure、AWS、GCP 或自有 VPS。GitHub Pages 继续作为公开政策和支持站点。

参考：[OpenAI Submit plugins - MCP](https://learn.chatgpt.com/docs/submit-plugins#mcp)。

## 公网边界

入口实现位于 `openalphastack.public_mcp_server`，默认路径为 `/mcp`。它只暴露：

| 类别 | 工具 |
| --- | --- |
| 边界与 Demo | `get_public_capabilities`、`list_demo_datasets`、`read_demo_dataset` |
| 市场读取 | `market_overview`、`stock_quote`、`stock_technical`、`stock_fundamentals`、`stock_news`、`market_news` |
| 确定性计算 | `calculate_position_size`、`calculate_volatility`、`run_rule_backtest` |

明确不暴露：

- `list_runs`、运行快照和账本；
- `validate_paper_plan`、`save_plan_draft`、`publish_paper_plan`；
- 本地 Dashboard、SQLite、运行目录、任意文件和 Shell；
- 券商连接、真实订单和任何实盘能力。

所有 12 个工具都声明 `readOnlyHint=true`、`destructiveHint=false` 和
`idempotentHint=true`。访问公开数据源的工具额外声明 `openWorldHint=true`。
股票代码、新闻数量、回测长度和价格数组都有 JSON Schema 上限。

## 本地验证

安装公网依赖并启动：

```powershell
pip install -e ".[public]"
$env:PORT = "8000"
openalphastack mcp serve-public
```

另一个终端检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/
```

真实 MCP 初始化和工具边界由以下测试覆盖：

```powershell
python -m pytest -q tests/contracts/test_public_mcp.py
```

也可构建独立镜像：

```powershell
docker build -f deploy/Dockerfile.public -t openalphastack-public-mcp .
docker run --rm -p 8000:8000 openalphastack-public-mcp
```

该镜像不包含 Dashboard、模拟引擎运行数据或本地 MCP 配置。

## 生产环境变量

| 变量 | 作用 |
| --- | --- |
| `PORT` | HTTP 监听端口，默认 `8000` |
| `OPENALPHASTACK_PUBLIC_HOSTS` | 允许的 HTTP Host，逗号分隔；自定义域名必须填写 |
| `OPENALPHASTACK_PUBLIC_ORIGINS` | 允许的浏览器 Origin，逗号分隔 |
| `OPENAI_APPS_CHALLENGE_TOKEN` | 提交门户生成的域名验证 token；未设置时 challenge URL 返回 404 |
| `RENDER_EXTERNAL_HOSTNAME` | Render 自动提供；服务会自动加入 Host allowlist |

服务启用 DNS rebinding 防护。自定义域名示例：

```text
OPENALPHASTACK_PUBLIC_HOSTS=mcp.example.com
OPENALPHASTACK_PUBLIC_ORIGINS=https://44-99.github.io,https://example.com
```

托管平台负责 TLS、DDoS 防护、实例扩缩和访问日志策略。提交目录前应选择常驻实例，
避免免费实例休眠导致扫描或首次调用超时，并在反向代理增加合理的每 IP 限流。

## 官方目录提交前检查

1. 部署并固定一个不会改变 origin 的 HTTPS URL，例如
   `https://mcp.example.com/mcp`。
2. 检查 `/health`、MCP `initialize`、`tools/list` 和每个工具的正负输入。
3. 把门户 token 配置为 `OPENAI_APPS_CHALLENGE_TOKEN`，确认
   `/.well-known/openai-apps-challenge` 只返回 token 原文。
4. 填写以下静态页面：
   - Privacy: `https://44-99.github.io/OpenAlphaStack/privacy.html`
   - Terms: `https://44-99.github.io/OpenAlphaStack/terms.html`
   - Support: `https://44-99.github.io/OpenAlphaStack/support.html`
5. 准备至少 5 个正常测试与 3 个负向测试，覆盖无效股票代码、越界数量和不可用
   数据源。
6. 在门户执行 Scan Tools，逐项核对名称、描述、输入 Schema、输出和 annotations。

首次提交后的更新应在同一个 MCP origin 上发布兼容版本；不要为了升级改变域名。
