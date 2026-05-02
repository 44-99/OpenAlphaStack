# AlphaClaude 技能系统

项目级技能文件，bot 启动时自动加载。与 `~/.claude/skills/`（Claude Code 内置技能）完全分离。

## 技能结构

支持两种布局：

### 根目录 `.md` 文件（简单技能）

适用于无需脚本和深度参考资料的技能，直接放在 `skills/` 下：
- `trading-principles.md` — 前置技能，始终加载

### 子目录格式（渐进式展开）

适用于复杂策略，支持按需展开 `references/` 和 `scripts/`：

```
skills/ma-golden-cross/
├── SKILL.md              # 路由：触发词、工具链、分析框架（启动时加载）
├── references/           # 深度知识，按需读取（可选）
│   ├── golden-cross.md
│   └── death-cross.md
└── scripts/              # 计算脚本，Claude Code 按需执行（可选）
    └── ma_signal.py
```

## 文件格式

```markdown
---
name: 技能名称
triggers:
  - 触发词1
  - 触发词2
description: 简短描述
tools:
  - tools/quote.py
  - tools/technical.py
priority: 20
core_rules: [1, 2, 3]
---

分析框架内容...
```

## 触发规则

- 用户在飞书发送的消息中包含 `triggers` 中任一关键词时，技能内容注入 Claude 系统提示
- 大小写不敏感模糊匹配（消息中包含触发词即可）
- 多个技能同时匹配时，全部注入

## 前置技能

`always_load: true` 的技能始终加载。`trading-principles.md` 作为交易铁律前置技能，优先级最高。

## 策略技能列表（11 套）

| # | 技能 | 目录 | 触发词 | 优先级 |
|---|------|------|--------|--------|
| 1 | 默认多头趋势 | `bull-trend/` | 趋势/多头/走势分析 | 10 (默认) |
| 2 | 均线金叉 | `ma-golden-cross/` | 金叉/均线金叉/MA金叉 | 20 |
| 3 | 放量突破 | `volume-breakout/` | 放量突破/突破/放量 | 30 |
| 4 | 缩量回踩 | `shrink-pullback/` | 缩量回踩/回踩/缩量 | 40 |
| 5 | 箱体震荡 | `box-oscillation/` | 箱体/震荡/区间 | 50 |
| 6 | 底部放量 | `bottom-volume/` | 底部放量/底部/筑底 | 60 |
| 7 | 缠论 | `chan-theory/` | 缠论/中枢/背驰 | 70 |
| 8 | 波浪理论 | `wave-theory/` | 波浪/艾略特/五浪 | 80 |
| 9 | 龙头策略 | `dragon-head/` | 龙头/龙头战法/板块龙头 | 90 |
| 10 | 情绪周期 | `emotion-cycle/` | 情绪/情绪周期/冰点 | 100 |
| 11 | 一阳三阴 | `one-yang-three-yin/` | 一阳三阴/阳包阴 | 110 |

## 高级用法

可以在技能目录放置 Python 脚本，通过 `register_command()` 注册自定义 `/` 指令：

```python
# skills/my_command.py
def register(commands: dict):
    commands["/mycmd"] = lambda chat_id, args: f"执行自定义命令: {args}"
```

Python 技能文件以 `.py` 结尾，需实现 `register(commands)` 函数。bot 启动时自动导入。
