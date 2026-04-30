# AlphaClaude

AI stock trading bot powered by Claude Code. 飞书股票分析机器人，群聊/DM 互动，定时推送A股分析。

## 架构

```
飞书 WebSocket 长连接 (feishu/ws.py)
    ↕
FastAPI (main.py)
    ├── feishu/ — 飞书 API (认证、发消息、收事件)
    ├── stock/ — 股票数据 (akshare: 行情、热度、筛选)
    ├── claude/ — Claude Code CLI 封装
    └── scheduler/ — 定时任务 (工作日 9:00/12:00/15:30)
```

**事件接收**: WebSocket 长连接为主（直连飞书，无需 ngrok），Webhook 为备用。

## 关键技术

- **飞书认证**: `feishu/auth.py` — 获取 tenant_access_token，自动缓存刷新
- **事件接收**: `feishu/ws.py` — WebSocket 长连接，自动重连
- **消息解析**: `feishu/bot.py` — parse_event() 统一处理 WebSocket 和 Webhook 事件
- **股票数据**: `stock/data.py` — 用 akshare 获取大盘、热门股、多因子筛选
- **Claude 分析**: `claude/client.py` — 通过 `claude -p` 调用
- **定时任务**: `scheduler/tasks.py` — APScheduler，工作日触发
- **对话历史**: `data/conversations.json` — 持久化，每个会话保留最近 50 条

## 启动命令

```bash
pip install -r requirements.txt
python main.py  # 运行在 0.0.0.0:8800
```

## 飞书配置

- 事件订阅选择「使用长连接接收事件」模式（无需配置 Request URL）
- 权限: im:message, im:message:read

## 推荐策略

- **短线 (1-5天)**: 涨幅 2-9%, 换手率 3-20%, 量比 >1.5, 成交额 >1亿
- **中线 (1-4周)**: PE 0-50, PB 0-8, 涨幅 1-7%, 换手率 2-15%
- 每支推荐必须给出具体买入价/止损价/止盈价
