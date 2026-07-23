# Feishu notifications

Feishu is an optional outbound notification adapter. OpenAlphaStack no longer
uses a Feishu bot as an Agent conversation host and no longer creates dynamic
Agent schedules from chat commands.

Supported notification categories include engine lifecycle, paper fills, risk
alerts, backtest progress, and postclose summaries. Configure destination chat
IDs through `.env` and keep all trading decisions in Codex Skills plus MCP.

Do not expose run control, arbitrary prompts, or shell commands through Feishu.
