---
name: stock-screener
description: >
  选股推荐技能。当用户让推荐股票、选股、问"有什么好票""帮我找几个标的"
  "短线有什么机会""中线布局什么"时使用此技能。提供短线/中线/游资三种筛选模式。
triggers:
  - 选股
  - 推荐
  - 标的
  - 机会
  - 短线
  - 中线
  - 游资
  - 热门
  - 筛选
  - 有什么票
  - 荐股
---

# 选股推荐流水线

当用户要求推荐股票时，按以下流水线执行。

## 分析流水线

### 阶段 1：确定筛选模式

根据用户意图选择：

- **短线 (1-5天)**：加载 `references/short-term.md`，运行 `python tools/screen.py -s breakout`
- **中线 (1-4周)**：加载 `references/mid-term.md`，运行 `python tools/screen.py -s value`
- **游资热点**：加载 `references/hot-money.md`，运行 `python tools/screen.py -s hot_money`
- 用户未指定 → 短线优先

### 阶段 2：执行筛选

```
python tools/screen.py -s {strategy}   # JSON 输出筛选结果列表
```

### 阶段 3：二次确认

对筛选结果中排名前 3-5 的标的，逐支获取实时数据验证：

```
python tools/quote.py {code}            # 确认现价/量比仍在筛选范围内
python tools/technical.py {code} --all  # 确认技术形态未被破坏
python tools/news.py {code}             # 排除突发利空
```

### 阶段 4：输出

对每支推荐标的输出：
1. **代码/名称/现价**
2. **匹配的筛选条件**（如：涨幅 5%、换手率 8%、量比 2.1）
3. **技术面确认**：均线排列/MACD 状态
4. **入场建议**：买入价/止损价/止盈价
5. **风险提示**：近期事件/估值注意

最多推荐 5 支，筛选结果不足时如实说明，不硬凑。
