# 短线筛选 (1-5 天)

**策略名**: breakout（放量突破）
**CLI**: `MCP `screen_candidates(strategy="breakout")``

---

## 筛选参数

| 参数 | 范围 | 说明 |
|------|------|------|
| 涨幅 | 2% ~ 9% | 过滤平盘（<2%）和涨停（>9% 买不到） |
| 换手率 | 3% ~ 20% | 活跃筹码，过滤无人问津和过度投机 |
| 量比 | > 1.5 | 当日放量，有资金关注 |
| 成交额 | > 1 亿 | 过滤流动性不足的标的 |
| 价格 | 5 ~ 200 元 | 过滤低价仙股和高价控盘股 |
| 排除 | ST / *ST | 排除风险警示股 |

## 二次确认

对筛选结果前 5 名，逐支确认：

```bash
MCP `stock_quote(code)`            # 现价/量比/换手率是否仍在筛选范围内
MCP `stock_technical(code, indicator="all")`  # 均线是否多头排列、MACD 是否金叉或零轴上
MCP `stock_news(code)`             # 排除突发利空
```

## 单支输出格式

```json
{
  "code": "600519",
  "name": "贵州茅台",
  "price": 1850.00,
  "change_pct": 5.2,
  "turnover_rate": 4.8,
  "volume_ratio": 2.1,
  "amount": 5200000000,
  "ma_alignment": "MA5>MA10>MA20",
  "macd_status": "零轴上方金叉",
  "risk_notes": "无重大利空",
  "entry_price": 1845.00,
  "stop_loss": 1790.00,
  "target": 1950.00,
  "signal": "放量突破"
}
```

## 仓位建议

- 单支仓位：15-25%
- 同时持仓：不超过 3 支
- 总仓位上限：50%（短线快速轮动）
