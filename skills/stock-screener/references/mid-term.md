# 中线筛选 (1-4 周)

**策略名**: value（价值中线）
**CLI**: `python tools/screen.py -s value`

---

## 筛选参数

| 参数 | 范围 | 说明 |
|------|------|------|
| PE | 0 ~ 50 | 排除亏损（PE<0）和过度高估 |
| PB | 0 ~ 8 | 资产价值支撑 |
| 涨幅 | 1% ~ 7% | 温和上涨，不追涨停 |
| 换手率 | 2% ~ 15% | 适度活跃 |
| 成交额 | > 5000 万 | 流动性底线 |
| 价格 | 10 ~ 300 元 | 中高价区间 |
| 排除 | ST / *ST | 排除风险警示股 |

## 二次确认

对筛选结果前 5 名：

```bash
python tools/quote.py {code}              # 现价确认
python tools/technical.py {code} --all    # 均线多头排列、布林带位置
python tools/fundamental.py {code}        # PE/PB/ROE/营收增速 + 行业分位排名
python tools/news.py {code}               # 排除利空
```

## 行业比较

`python tools/fundamental.py {code}` 返回 `industry_rank` 字段：
- PE 在同行业分位：< 30% 为低估，> 70% 为高估
- ROE 在同行业分位：> 50% 为优质

## 单支输出格式

```json
{
  "code": "600519",
  "name": "贵州茅台",
  "price": 1850.00,
  "change_pct": 3.5,
  "pe": 28.5,
  "pb": 7.2,
  "roe": 25.3,
  "industry_pe_percentile": 35,
  "ma_alignment": "多头排列",
  "signal": "价值中线",
  "entry_price": 1830.00,
  "stop_loss": 1750.00,
  "target": 2000.00,
  "holding_days": "1-4 周"
}
```

## 仓位建议

- 单支仓位：20-30%
- 同时持仓：不超过 5 支
- 总仓位上限：70%（中线可较重仓）
- 分批建仓，不在同一日满仓
