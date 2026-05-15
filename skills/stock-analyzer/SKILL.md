---
name: stock-analyzer
description: >
  个股分析流水线。当用户询问某支具体股票、提到股票代码/名称、想要技术面/基本面分析、
  或问"能不能买/卖/持有X"时使用此技能。提供趋势判断、入场信号扫描、位置评估、风险排查、
  以及可操作的买卖建议（含买入价、止损价、止盈价）。
always_load: true
---

# 个股分析流水线

收到个股分析请求后，按以下流水线执行。每个阶段调用对应工具获取数据，基于数据判断，
不凭空猜测。

## 分析流水线

### 阶段 1：数据获取（并行）

同时获取三类数据，减少等待：

```
python -m alphaclaude.tools.quote {code}            # 实时行情：现价/涨幅/换手率/量比/PE/PB
python -m alphaclaude.tools.technical {code} --all  # 技术指标：MA/MACD/RSI/KDJ/布林带/量价
python -m alphaclaude.tools.news {code}             # 近期新闻：公告/研报/情绪
```

根据用户问题按需追加：

```
python -m alphaclaude.tools.fundamental {code}      # PE/PB/ROE/营收增速/行业对比
python -m alphaclaude.tools.flow {code}             # 主力资金/大单方向
```

### 阶段 2：趋势与市场环境判定

基于 `quote.py` 和 `technical.py` 返回的数据：

1. **均线排列**（核心）：MA5 >= MA10 >= MA20 且 MA20 斜率向上 → 多头结构；MA20 斜率向下 → 偏空
2. **MACD 状态**：零轴上方/下方、金叉/死叉、红柱/绿柱变化方向
3. **量价关系**：放量上涨（健康）、缩量上涨（谨慎）、放量滞涨（警惕分歧）、放量下跌（风险）
4. **乖离率**：现价 vs MA5，> 5% 不追高，< 2% 最佳买点区间
5. **趋势强度**：综合以上给出 BEAR / WEAK_BEAR / SIDEWAYS / BULL / STRONG_BULL 判定

趋势判定决定后续策略选择：
- STRONG_BULL / BULL：优先加载 `references/entry-signals.md` 扫描入场信号
- SIDEWAYS：优先加载 `references/position-management.md` 判断箱体位置
- BEAR / WEAK_BEAR：降低仓位建议，等待底部信号，加载 `references/entry-signals.md` 关注底部放量

### 阶段 3：信号扫描

加载 `references/entry-signals.md`，基于已获取的数据逐项扫描：

| # | 信号 | 适用市场 | 核心条件 |
|---|------|----------|----------|
| 1 | 均线金叉 | trending_up | MA5 近 3 日上穿 MA10 + 量比 > 1.2 |
| 2 | 放量突破 | trending_up | 收盘站上阻力位 + 量比 > 2.0 + 强势收盘 |
| 3 | 缩量回踩 | trending_up, trending_down, sideways | 价格回踩 MA5/MA10 + 缩量(<70%均量) + 多头排列前提 |
| 4 | 底部放量 | trending_down | 20日高点跌幅>15% + 量比>3.0 + 收阳 + 守住低点 |
| 5 | 一阳三阴 | trending_up | 大阳→三小阴缩量→突破阳，5日K线形态 |

根据趋势判定只扫描适用市场的信号。匹配到信号后按 reference 中的评分规则调整评分。

### 阶段 3.5：风险量化（仓位约束）

在做出买入建议前，必须先评估仓位风险：

```
python -m alphaclaude.tools.risk {code} --capital {总资金}
```

获取以下约束：
1. **波动率等级**：low/medium/high/extreme，决定基础仓位上限
2. **仓位上限**：position_limit_adjusted（波动率 × 相关性调整后的百分比上限）
3. **建议股数**：max_shares（按资金和仓位上限计算的手数）
4. **回撤预警**：current_drawdown_pct > 15% 时警告，建议降低仓位

最终建议仓位不得超过 risk.py 返回的 position_limit_adjusted。

### 阶段 4：位置判断

加载 `references/position-management.md`：
- 识别支撑/阻力位（布林带、均线、前期高低点）
- 判断现价所处区间（箱底/箱中/箱顶）
- 箱底区域（距支撑 ≤5%）→ 买入信号，箱顶区域（距阻力 ≤5%）→ 减仓信号

### 阶段 5：风险排查

加载 `references/risk-checklist.md`，逐项检查：
- 减持公告、业绩预亏、监管处罚、行业政策利空、大额解禁 → 一票否决
- PE 是否明显高于行业均值
- 获利比例是否过高（> 90% 警惕获利回吐）

### 阶段 6：输出

综合以上，给出结构化结论，包含：

1. **趋势判定**：多头/空头/震荡，均线排列状态
2. **匹配信号**：触发了哪个入场信号（如有），评分调整
3. **位置判断**：当前价格所处区间
4. **风险提示**：已排查的风险点
5. **操作建议**：买入/观望/减仓，含具体价位
   - 买入价：基于匹配信号的理想买点
   - 止损价：基于匹配信号的止损位
   - 止盈价：基于阻力位和信号目标
   - 建议仓位：基于信号强度和风险等级
6. **数据时效性**：标注数据时间

若数据不足以得出结论，明确说明缺少什么数据，不编造。

### 阶段 7：信号提交

当给出买入/卖出建议且用户确认后，调用 signal 工具提交信号至模拟盘：

```
python -m alphaclaude.tools.signal submit \
  --symbol {代码} --action {buy/sell} \
  --entry {买入价} --stop {止损价} --target {止盈价} \
  --confidence {置信度0-100} --strategy {策略名} \
  --reasoning "{操作理由，≤200字}" \
  --deviation {乖离率%}
```

工具会自动执行硬校验（止损位、风险回报比 ≥1.5:1、乖离率上限、置信度范围），校验通过后写入 `data/signals.jsonl` 并返回 trade_id。校验失败则返回具体错误，修正后重试。

**注意**：仅在用户明确要求下单或确认操作时才提交。分析阶段不自动提交。
