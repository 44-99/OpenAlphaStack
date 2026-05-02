# AlphaClaude 技能系统

项目级技能文件，bot 启动时自动加载。与 `~/.claude/skills/`（Claude Code 内置技能）完全分离。

## 设计理念

技能按**场景**组织，非按触发词。每个 skill 是一个完整的分析流水线：Agent 读 SKILL.md（路由器）→ 按流水线阶段按需加载 references/（深度知识）。新增策略只需往 references/ 加文件，无需改架构。

## 技能结构

```
skills/
├── trading-principles.md          # 前置技能：交易铁律，始终加载
├── stock-analyzer/                # 个股分析流水线
│   ├── SKILL.md                   # 路由：6 阶段分析流水线
│   └── references/
│       ├── entry-signals.md       # 5 种入场信号检查清单
│       ├── position-management.md # 箱体震荡 — 位置判断
│       ├── advanced.md            # 缠论 + 波浪理论
│       └── risk-checklist.md      # 风险排查清单
├── market-analyzer/               # 市场研判流水线
│   ├── SKILL.md                   # 路由：情绪 → 板块 → 龙头
│   └── references/
│       ├── sentiment-cycle.md     # 情绪周期
│       ├── dragon-head.md         # 龙头策略
│       └── sector-rotation.md     # 板块轮动
└── stock-screener/                # 选股推荐流水线
    ├── SKILL.md                   # 路由：短线/中线/游资
    └── references/
        ├── short-term.md          # 短线筛选参数
        ├── mid-term.md            # 中线筛选参数
        └── hot-money.md           # 游资热点参数
```

## 文件格式

```yaml
---
name: skill-name
description: >
  描述技能的作用场景和适用条件。Agent 据此判断是否激活此技能。
  写得越具体越好 — 包含触发上下文和使用时机。
triggers:          # 可选，触发词列表（模糊匹配）
  - 关键词1
always_load: true  # 可选，始终加载到上下文
---
```

## 技能列表

| Skill | 激活方式 | 场景 |
|-------|----------|------|
| `trading-principles.md` | always_load | 交易铁律，所有分析的前置约束 |
| `stock-analyzer/` | always_load | 个股分析：趋势→信号→位置→风险→建议 |
| `market-analyzer/` | triggers | 市场研判：情绪周期、板块轮动、龙头识别 |
| `stock-screener/` | triggers | 选股推荐：短线/中线/游资筛选 |

## 扩展指南

新增策略无需修改任何现有文件：

- **新入场信号** → 往 `stock-analyzer/references/entry-signals.md` 加一个章节
- **新市场分析方法** → 往 `market-analyzer/references/` 加一个 `.md`
- **新筛选策略** → 往 `stock-screener/references/` 加参数文件 + 更新 `strategies/` JSON
- **通达信公式内化** → 分析公式逻辑 → 归类到对应场景 reference 中（信号类/筛选类/指标类）
