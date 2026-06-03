import type { EChartsOption } from 'echarts';
import type { KlineData, KlineTradeMarker, OverlayKind } from '../types';
import { calcBOLL, calcEMA, calcMA } from './indicators';

const UP_COLOR = '#ff3b30';
const DOWN_COLOR = '#22b573';

function formatNumber(value: number) {
  return Number.isFinite(value) ? value.toFixed(2) : '--';
}

function formatVolume(value: number) {
  if (!Number.isFinite(value)) return '--';
  if (value >= 100000000) return `${(value / 100000000).toFixed(2)}亿`;
  if (value >= 10000) return `${(value / 10000).toFixed(2)}万`;
  return value.toLocaleString('zh-CN');
}

function normalizeTradeAction(action: string) {
  const text = action.toLowerCase();
  if (text.includes('buy') || text.includes('买')) return { label: '买', tone: UP_COLOR };
  if (text.includes('stop')) return { label: '止损', tone: DOWN_COLOR };
  if (text.includes('profit')) return { label: '止盈', tone: UP_COLOR };
  if (text.includes('sell') || text.includes('卖')) return { label: '卖', tone: DOWN_COLOR };
  return { label: action || '交易', tone: '#d6a13b' };
}

function resolveTradeCategory(time: string, dates: string[]) {
  if (dates.includes(time)) return time;
  const minute = time.slice(0, 16);
  if (dates.includes(minute)) return minute;
  const day = time.slice(0, 10);
  if (dates.includes(day)) return day;
  return '';
}

export function buildKlineOption(data: KlineData, overlay: OverlayKind, trades: KlineTradeMarker[] = []): EChartsOption {
  const dates = data.dates;
  const closes = data.close.map(Number);
  const ohlc = dates.map((_, index) => [
    Number(data.open[index]),
    Number(data.close[index]),
    Number(data.low[index]),
    Number(data.high[index]),
  ]);
  const series: NonNullable<EChartsOption['series']> = [
    {
      name: 'K线',
      type: 'candlestick',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: ohlc,
      clip: true,
      progressive: 0,
      itemStyle: {
        color: UP_COLOR,
        color0: DOWN_COLOR,
        borderColor: UP_COLOR,
        borderColor0: DOWN_COLOR,
        borderWidth: 1.35,
      },
    },
  ];
  const riskLines = collectRiskLines(trades);

  if (overlay === 'MA') {
    [5, 10, 20, 60].forEach((period) => {
      series.push({
        name: `MA${period}`,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: calcMA(closes, period),
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1, opacity: 0.75 },
      });
    });
  } else if (overlay === 'EMA') {
    [5, 10, 20, 60].forEach((period) => {
      series.push({
        name: `EMA${period}`,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: calcEMA(closes, period),
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1, opacity: 0.75, type: 'dashed' },
      });
    });
  } else {
    const boll = calcBOLL(closes, 20);
    series.push(
      {
        name: 'MID',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: boll.mid,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1, color: '#d6a13b', opacity: 0.9 },
      },
      {
        name: 'UPPER',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: boll.upper,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1, color: UP_COLOR, opacity: 0.55, type: 'dashed' },
      },
      {
        name: 'LOWER',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: boll.lower,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1, color: DOWN_COLOR, opacity: 0.55, type: 'dotted' },
      },
    );
  }

  const tradePoints = trades
    .map((trade) => {
      const category = resolveTradeCategory(trade.time, dates);
      if (!category || !Number.isFinite(Number(trade.price))) return null;
      const action = normalizeTradeAction(trade.action);
      return {
        name: action.label,
        value: [category, Number(trade.price), action.label],
        trade,
        itemStyle: { color: action.tone, borderColor: '#081018', borderWidth: 1 },
        label: {
          show: true,
          formatter: action.label,
          color: '#081018',
          fontSize: 10,
          fontWeight: 800,
        },
      };
    })
    .filter(Boolean);

  if (riskLines.length) {
    series[0] = {
      ...(series[0] as object),
      markLine: {
        symbol: 'none',
        silent: true,
        data: riskLines,
        lineStyle: { width: 1.2, type: 'dashed', opacity: 0.82 },
        label: {
          color: '#d8e0ea',
          backgroundColor: 'rgba(8, 13, 20, 0.82)',
          borderColor: 'rgba(113, 129, 151, 0.32)',
          borderWidth: 1,
          borderRadius: 4,
          padding: [3, 5],
          formatter: '{b}',
        },
      },
    };
  }

  if (tradePoints.length) {
    series.push({
      name: '交易结果',
      type: 'scatter',
      xAxisIndex: 0,
      yAxisIndex: 0,
      symbol: 'pin',
      symbolSize: 34,
      data: tradePoints,
      z: 20,
      tooltip: { show: true },
    });
  }

  series.push({
    name: 'VOL',
    type: 'bar',
    xAxisIndex: 1,
    yAxisIndex: 1,
    data: data.volume.map((value, index) => ({
      value,
      itemStyle: {
        color: Number(data.close[index]) >= Number(data.open[index]) ? UP_COLOR : DOWN_COLOR,
        borderWidth: 0,
        opacity: Number(data.close[index]) >= Number(data.open[index]) ? 0.68 : 0.36,
      },
    })),
    clip: true,
    progressive: 0,
  });

  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: [
      { left: '4.2%', right: '1.6%', top: '3%', bottom: '22%' },
      { left: '4.2%', right: '1.6%', top: '79%', bottom: '8%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        gridIndex: 0,
        boundaryGap: true,
        axisLine: { lineStyle: { color: '#263047' } },
        axisLabel: { color: '#708099', fontSize: 10 },
        axisPointer: {
          label: {
            show: true,
            backgroundColor: '#07131c',
            borderColor: 'rgba(65, 224, 201, 0.42)',
            color: '#edf7f5',
            fontFamily: 'JetBrains Mono, Cascadia Code, monospace',
            fontSize: 10,
          },
        },
      },
      {
        type: 'category',
        data: dates,
        gridIndex: 1,
        boundaryGap: true,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: '#263047' } },
        axisPointer: { label: { show: false } },
      },
    ],
    yAxis: [
      {
        type: 'value',
        gridIndex: 0,
        scale: true,
        splitLine: { lineStyle: { color: '#182032' } },
        axisLabel: { color: '#708099', fontSize: 10 },
      },
      {
        type: 'value',
        gridIndex: 1,
        splitLine: { show: false },
        axisLabel: { color: '#708099', fontSize: 9 },
      },
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        filterMode: 'none',
        zoomOnMouseWheel: false,
        moveOnMouseMove: true,
        moveOnMouseWheel: false,
        preventDefaultMouseMove: true,
        minValueSpan: 20,
        throttle: 80,
        start: 60,
        end: 100,
      },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        filterMode: 'none',
        height: 18,
        bottom: 4,
        borderColor: '#263047',
        fillerColor: 'rgba(214, 161, 59, 0.18)',
        handleStyle: { color: '#d6a13b' },
        textStyle: { color: '#708099' },
        minValueSpan: 20,
        start: 60,
        end: 100,
      },
    ],
    tooltip: {
      trigger: 'item',
      triggerOn: 'mousemove|click',
      confine: true,
      backgroundColor: 'transparent',
      borderColor: 'transparent',
      padding: 0,
      extraCssText: 'box-shadow:none;background:transparent;border:none;',
      axisPointer: {
        type: 'cross',
        snap: true,
        animation: false,
        crossStyle: { color: 'rgba(65, 224, 201, 0.72)', width: 1, type: 'dashed' },
        lineStyle: { color: 'rgba(65, 224, 201, 0.72)', width: 1, type: 'dashed' },
        label: {
          backgroundColor: '#07131c',
          borderColor: 'rgba(65, 224, 201, 0.5)',
          color: '#dffbf7',
          fontFamily: 'JetBrains Mono, Cascadia Code, monospace',
          fontSize: 11,
        },
      },
      formatter(params) {
        const rows = Array.isArray(params) ? params : [params];
        const tradePoint = rows.find((row) => row.seriesName === '交易结果') as { data?: { trade?: KlineTradeMarker } } | undefined;
        if (tradePoint?.data?.trade) {
          const trade = tradePoint.data.trade;
          const action = normalizeTradeAction(trade.action);
          return `
            <div class="kline-tooltip kline-tooltip--trade">
              <div class="kline-tooltip__head">
                <span class="kline-tooltip__code">${trade.code}</span>
                <span class="kline-tooltip__date">${trade.time}</span>
              </div>
              <div class="kline-tooltip__price" style="color:${action.tone}">
                ${action.label} ${formatNumber(Number(trade.price))}
                <small>${trade.shares || 0} 股 / ${trade.strategy || '--'}</small>
              </div>
              <div class="kline-tooltip__grid">
                <span>STOP</span><b>${formatNumber(Number(trade.stop_loss || NaN))}</b>
                <span>TAKE</span><b>${formatNumber(Number(trade.take_profit || NaN))}</b>
                <span>COST</span><b>${formatNumber(Number(trade.avg_cost || NaN))}</b>
              </div>
              ${trade.reasoning ? `<p class="kline-tooltip__reason">${trade.reasoning}</p>` : ''}
            </div>
          `;
        }
        const candle = rows.find((row) => row.seriesType === 'candlestick');
        if (!candle || !Array.isArray(candle.value)) return '';
        const [open, close, low, high] = candle.value as number[];
        const axisLabel = (candle as { axisValueLabel?: string }).axisValueLabel || candle.name;
        const index = Number(candle.dataIndex);
        const volume = Number(data.volume[index]);
        const change = close - open;
        const changePct = open ? (change / open) * 100 : 0;
        const up = change >= 0;
        const tone = up ? UP_COLOR : DOWN_COLOR;
        const sign = up ? '+' : '';
        const direction = up ? '涨' : '跌';
        return `
          <div class="kline-tooltip">
            <div class="kline-tooltip__head">
              <span class="kline-tooltip__code">${data.code}</span>
              <span class="kline-tooltip__date">${axisLabel}</span>
            </div>
            <div class="kline-tooltip__price" style="color:${tone}">
              ${formatNumber(close)}
              <small>${direction} ${sign}${formatNumber(change)} / ${sign}${changePct.toFixed(2)}%</small>
            </div>
            <div class="kline-tooltip__grid">
              <span>OPEN</span><b>${formatNumber(open)}</b>
              <span>HIGH</span><b>${formatNumber(high)}</b>
              <span>LOW</span><b>${formatNumber(low)}</b>
              <span>CLOSE</span><b>${formatNumber(close)}</b>
              <span>VOL</span><b>${formatVolume(volume)}</b>
            </div>
          </div>
        `;
      },
    },
    axisPointer: {
      link: [{ xAxisIndex: [0, 1] }],
    },
    series,
  };
}

function collectRiskLines(trades: KlineTradeMarker[]) {
  const latest = [...trades].reverse().find((trade) => trade.stop_loss || trade.take_profit || trade.avg_cost);
  if (!latest) return [];
  const lines = [];
  if (latest.avg_cost) {
    lines.push({ yAxis: Number(latest.avg_cost), name: `成本 ${formatNumber(Number(latest.avg_cost))}`, lineStyle: { color: '#d6a13b' } });
  }
  if (latest.stop_loss) {
    lines.push({ yAxis: Number(latest.stop_loss), name: `止损 ${formatNumber(Number(latest.stop_loss))}`, lineStyle: { color: DOWN_COLOR } });
  }
  if (latest.take_profit) {
    lines.push({ yAxis: Number(latest.take_profit), name: `止盈 ${formatNumber(Number(latest.take_profit))}`, lineStyle: { color: UP_COLOR } });
  }
  return lines;
}
