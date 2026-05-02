# 技能系统

技能采用**场景化渐进式展开**设计：SKILL.md 作为路由层描述分析场景，references/ 按需加载深度知识。Agent 通过 description 理解技能用途并自主激活，不依赖关键词触发。

## 技能目录结构

```
skills/
├── trading-principles.md           # 前置技能：7 条交易铁律，始终加载
├── stock-analyzer/                 # 个股深度分析管线
│   ├── SKILL.md                    # 路由：6 阶段分析管线定义
│   └── references/
│       ├── entry-signals.md        # 5 种入场信号（金叉/突破/回踩/底部/一阳三阴）
│       ├── position-management.md  # 箱体震荡战法
│       ├── advanced.md             # 缠论 + 波浪理论
│       └── risk-checklist.md       # 风险排查清单
├── market-analyzer/                # 市场研判管线
│   ├── SKILL.md                    # 路由：市场分析流程
│   └── references/
│       ├── sentiment-cycle.md      # 情绪周期策略
│       ├── dragon-head.md          # 龙头识别策略
│       └── sector-rotation.md      # 板块轮动分析
└── stock-screener/                 # 多因子选股管线
    ├── SKILL.md                    # 路由：筛选流程
    └── references/
        ├── short-term.md           # 短线筛选参数
        ├── mid-term.md             # 中线筛选参数
        └── hot-money.md            # 热钱追踪参数
```

## 分层加载

- **SKILL.md** 启动时加载（Claude Code 注入上下文）。充当局部的分析管线路由器 —— 定义分析步骤顺序、每个步骤使用的工具、references/ 文件的加载条件。
- **references/** 按需加载。包含公式理论、参数阈值、市场条件、评分调整规则。Agent 根据分析进度自主决定何时展开哪个 reference。
- **无 scripts/ 目录** — 所有计算由 `tools/` 下的 CLI 工具完成，JSON 进 JSON 出，通过 Bash 调用。Skills 定义"用哪个工具、怎么组合"，工具执行计算。

## 前置技能

`trading-principles.md` 配置 `always_load: true`，作为交易铁律始终加载到系统提示词中，确保所有分析都遵守统一的风险控制和入场纪律。内容包括：严进策略（不追高）、趋势交易（多头排列）、效率优先（筹码结构）、买点偏好（回踩支撑）、风险排查、估值关注、强势趋势股放宽。

## 与旧版的关键区别

| 维度 | 旧版（11 独立技能） | 新版（3 场景管线） |
|------|---------------------|---------------------|
| 激活方式 | 关键词触发（如"金叉"匹配 ma-golden-cross） | Description-based — Agent 理解用户意图自主选择 |
| 策略组织 | 11 个独立 SKILL.md，各自定义工具链 | 3 个管线按分析阶段编排，策略作为 references 按需加载 |
| 计算层 | `skills/*/scripts/` Python 脚本 | `tools/` 统一 CLI 工具（13 个），所有技能共享 |
| 内容保真 | 从 YAML 迁移时易丢失细节 | 深度知识集中在 references/，一个文件一个主题 |

## 工具与技能的对应

技能管线通过组合工具完成分析。每个工具单一职责，JSON 进 JSON 出：

| 分析阶段 | 使用的工具 |
|----------|-----------|
| 数据获取 | `quote.py`（行情）、`technical.py`（指标）、`fundamental.py`（财务）、`flow.py`（资金）、`news.py`（消息） |
| 趋势研判 | `trend.py`（MA排列/交叉/乖离） |
| 入场信号 | `signal_detector.py`（5 种信号检测） |
| 位置判断 | `pivot.py`（箱体/中枢）、`fibonacci.py`（回撤/扩展） |
| 情绪评估 | `sentiment.py`（换手热度/量能/ATR/均线粘合/综合评分） |
| 筛选推荐 | `screen.py`（多因子选股）、`backtest.py`（历史回测） |
| 持仓管理 | `portfolio.py`（自选股/盈亏概览） |
