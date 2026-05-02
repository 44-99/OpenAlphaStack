# 技能系统

技能采用**渐进式展开**设计，保持初始 prompt 精简，同时让 Claude Code 按需获取深度领域知识。

## 技能目录结构

```
skills/ma-golden-cross/
├── SKILL.md              # 路由: 触发词、何时使用、加载哪个 reference、工具编排声明
├── references/
│   ├── golden-cross.md   # 金叉买入信号: 公式逻辑、参数、止损
│   └── death-cross.md    # 死叉卖出信号: 同上结构
└── scripts/
    └── ma_signal.py      # Claude Code 按需执行: akshare → 计算交叉信号
```

## 分层加载

- **SKILL.md** 启动时加载（Claude Code 注入上下文）。充当路由器 — _何时_ 使用该技能以及 _读取哪个_ reference 文件。YAML frontmatter 声明 `triggers`（触发词）、`tools`（依赖的工具链）和 `priority`（优先级）。
- **references/** 按需加载。包含公式理论、参数依据、市场条件说明、调优指南。
- **scripts/** 由 Claude Code 通过 Bash 执行。获取数据并计算信号的 Python 脚本。

## 前置技能

`trading-principles.md` 配置 `always_load: true`，作为交易铁律始终加载到系统提示词中，确保所有分析都遵守统一的风险控制和入场纪律。内容包括：严进策略（不追高）、趋势交易（多头排列）、效率优先（筹码结构）、买点偏好（回踩支撑）、风险排查、估值关注、强势趋势股放宽。

## 11 套策略技能

| # | 技能名称 | 类型 | 触发词 | 工具链 | 说明 |
|---|----------|------|--------|--------|------|
| 1 | 均线金叉 | 趋势 | 金叉/均线金叉/MA金叉 | `quote → technical(均线+MACD)` | MA5 上穿 MA10 + 量能确认 |
| 2 | 默认多头趋势 | 趋势 | 趋势/多头/走势分析 | `quote → technical(均线排列)` | 默认分析框架 |
| 3 | 放量突破 | 形态 | 放量突破/突破/放量 | `quote → technical(量比+阻力位) → flow(主力)` | 区分真突破与假突破 |
| 4 | 缩量回踩 | 反转 | 缩量回踩/回踩/缩量 | `quote → technical(均线+量能)` | 低吸策略 |
| 5 | 底部放量 | 反转 | 底部放量/底部/筑底 | `quote → technical(量价) → fundamental(PE)` | 配合估值判断 |
| 6 | 箱体震荡 | 形态 | 箱体/震荡/区间 | `quote → technical(布林带+支撑阻力)` | 下沿买入上沿卖出 |
| 7 | 缠论 | 框架 | 缠论/中枢/背驰 | `quote → technical(MACD+均线)` | 中枢、背驰、买卖点 |
| 8 | 波浪理论 | 框架 | 波浪/艾略特/五浪 | `quote → technical(趋势+MACD)` | 斐波那契回撤位 |
| 9 | 龙头策略 | 形态 | 龙头/龙头战法/板块龙头 | `quote → flow(板块资金) → news` | 板块轮动中识别领涨股 |
| 10 | 情绪周期 | 形态 | 情绪/情绪周期/冰点 | `quote(market) → flow(north) → news(market)` | 市场情绪研判 |
| 11 | 一阳三阴 | 形态 | 一阳三阴/阳包阴 | `quote → technical(K线形态+量能)` | 反转信号 |

> **为什么是 Skills 而不是 JSON 配置**：策略不只是筛选条件，更是分析框架。Claude Code 需要的是自然语言的策略方法论（什么时候用、怎么判断、注意事项），而不是一套硬编码的数值阈值。Skills 的渐进式展开设计让策略深度可调——日常分析只加载 SKILL.md，深度研究时按需展开 references/。
