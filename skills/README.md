# AlphaClaude 技能系统

项目级技能文件，放在此目录下，bot 启动时自动加载。与 `~/.claude/skills/`（Claude Code 内置技能）完全分离。

## 快速开始

1. 在此目录创建一个 `.md` 文件
2. 写入 YAML frontmatter 定义触发词和行为
3. 重启 bot 即可生效

## 文件格式

```markdown
---
name: 技能名称
triggers:
  - 触发词1
  - 触发词2
description: 简短描述（可选）
---

当用户消息匹配到上述触发词时，以下内容将自动注入到 Claude 的分析上下文中。

在这里写分析提示、规则、注意事项等。Claude 会遵循这些指示。
```

## 触发规则

- 用户在飞书发送的消息中包含 `triggers` 中任一关键词时，技能内容会被注入到 Claude 的系统提示中
- 匹配是大小写不敏感的模糊匹配（消息中包含触发词即可）
- 多个技能同时匹配时，所有匹配的技能都会被注入

## 示例

参考 `trading-principles.md` — 前置技能，始终加载。其他技能按触发词匹配后注入分析框架。

## 高级用法（给懂 Agent 的人）

可以在此目录放置 Python 脚本，通过 `register_command()` 注册自定义 `/` 指令。

```python
# skills/my_command.py
def register(commands: dict):
    commands["/mycmd"] = lambda chat_id, args: f"执行自定义命令: {args}"
```

Python 技能文件以 `.py` 结尾，需实现 `register(commands)` 函数。bot 启动时会自动导入。
