---
name: 底部放量
triggers:
  - 底部放量
  - 底部
  - 筑底
  - 地量见底
description: 检测长期下跌后底部放量信号，潜在趋势反转。
tools:
  - tools/quote.py
  - tools/technical.py
  - tools/fundamental.py
priority: 60
core_rules: [2, 5]
---

# 底部放量

## 反转判定标准

### 1. 持续下跌确认
调用 `python tools/technical.py {code} --all`：
- 股价从 20 日高点到近期低点跌幅 > 15%
- 趋势状态应为空头或强空头

### 2. 量能异动
- 当日成交量 > 5 日均量的 3 倍
- 调用 `python tools/quote.py {code}` → 量比 > 3.0
- 该异动应出现在前期极度缩量之后

### 3. 价格企稳
- 当日K线收阳（收盘价 > 开盘价）
- 价格守住近期低点
- 最好出现长下影线，显示买方支撑

### 4. 确认因素（关联理念5：风险排查）
- 调用 `python tools/news.py {code}` 确认是否有基本面催化
- 调用 `python tools/fundamental.py {code}` 配合估值判断
- 筹码分布：平均成本接近现价（成本收敛）

### 5. 风险提示（关联理念2：趋势交易）
- 这是反转信号，风险高于趋势跟踪
- 仓位建议较小（最多 2-3 成）
- 止损必须严格（设在近期低点下方）

## 评分调整
- 底部放量确认：+8
- 配合阳线 + 新闻催化：额外 +5
- 止损设在近期低点
