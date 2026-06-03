import type { EChartsOption } from 'echarts';
import type { KlineData, OverlayKind } from '../types';
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

export function buildKlineOption(data: KlineData, overlay: OverlayKind): EChartsOption {
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
