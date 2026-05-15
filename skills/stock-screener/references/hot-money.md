# 游资热点筛选

**策略名**: hot_money（热钱追踪）
**CLI**: `python -m alphaclaude.tools.screen -s hot_money`

---

## 筛选参数

| 参数 | 范围 | 说明 |
|------|------|------|
| 换手率 | > 10% | 极高活跃度，游资标的特征 |
| 涨幅 | > 3% | 已启动 |
| 量比 | > 2.0 | 显著放量 |
| 成交额 | > 5 亿 | 大盘股也能被游资推动 |
| 排除 | ST / *ST | |

## 风险警示

游资标的波动极大，需特别标注风险：
- 可能单日涨停次日跌停（一字断魂刀）
- 换手率 > 20% 为极度投机
- 建议仓位不超过总资金的 10%
- 止损必须更紧（-3% 即刻止损）

## 二次确认

```bash
python -m alphaclaude.tools.quote {code}            # 现价/换手率/量比
python -m alphaclaude.tools.technical {code} --all  # 技术形态
python -m alphaclaude.tools.flow {code}             # 主力资金方向 —— 游资流入 vs 机构出货
python -m alphaclaude.tools.news market             # 市场热点题材
```

## 仓位建议

- 单支仓位：5-10%（高波动低仓位）
- 同时持仓：不超过 2 支
- 止损：-3% 绝对止损，不幻想
- 当日涨停次日低开 3% → 开盘即止损
