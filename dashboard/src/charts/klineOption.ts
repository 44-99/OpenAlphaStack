import type { EChartsOption } from 'echarts';
import type { KlineData, KlinePlanAnnotation, KlineStructureAnnotation, KlineTechnicalSignal, KlineTradeMarker, OverlayKind } from '../types';
import { calcBOLL, calcEMA, calcMA } from './indicators';

const UP_COLOR = '#ff3b30';
const DOWN_COLOR = '#22b573';
const TRADE_COST_COLOR = '#f0b84a';
const PLAN_UPPER_COLOR = '#41e0c9';
const PLAN_DASH = [16, 10];
const TRADE_PIN_BORDER = '#07131c';

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
  const bucket = resolveTradeBucket(time, dates);
  if (bucket) return bucket;
  return '';
}

function resolveTradeBucket(time: string, dates: string[]) {
  const tradeMs = parseDateMs(time);
  if (!Number.isFinite(tradeMs)) return '';
  const buckets = dates
    .map((date) => ({ date, ms: parseDateMs(date) }))
    .filter((item) => Number.isFinite(item.ms))
    .sort((left, right) => left.ms - right.ms);
  if (!buckets.length) return '';

  const fallbackSpan = inferBucketSpanMs(buckets.map((item) => item.ms));
  for (let index = 0; index < buckets.length; index += 1) {
    const start = buckets[index].ms;
    const end = buckets[index + 1]?.ms ?? start + fallbackSpan;
    if (tradeMs >= start && tradeMs < end) return buckets[index].date;
  }
  return '';
}

function inferBucketSpanMs(values: number[]) {
  const spans = values
    .slice(1)
    .map((value, index) => value - values[index])
    .filter((value) => value > 0)
    .sort((left, right) => left - right);
  if (!spans.length) return 24 * 60 * 60 * 1000;
  return spans[Math.floor(spans.length / 2)];
}

function parseDateMs(value: string) {
  if (!value) return NaN;
  const normalized = value.includes('T') ? value : value.replace(' ', 'T');
  const ms = new Date(normalized).getTime();
  if (Number.isFinite(ms)) return ms;
  const compact = value.match(/^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?/);
  if (!compact) return NaN;
  const [, year, month, day, hour = '00', minute = '00'] = compact;
  return new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute)).getTime();
}

export function buildKlineOption(
  data: KlineData,
  overlay: OverlayKind,
  trades: KlineTradeMarker[] = [],
  plans: KlinePlanAnnotation[] = [],
  showSignals = false,
  structures: KlineStructureAnnotation[] = [],
): EChartsOption {
  const dates = data.dates;
  const closes = data.close.map(Number);
  const ohlc = dates.map((_, index) => [
    Number(data.open[index]),
    Number(data.close[index]),
    Number(data.low[index]),
    Number(data.high[index]),
  ]);
  const series: Array<Record<string, unknown>> = [
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
  const planLines = collectPlanLines(plans);
  const planAreas = collectPlanAreas(plans);
  const structureLines = collectStructureLines(structures);
  const structureAreas = collectStructureAreas(structures);
  const technicalSignals = showSignals ? detectTechnicalSignals(data) : [];

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
  } else if (overlay === 'BOLL') {
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
        symbol: action.label === '买' ? 'triangle' : 'path://M0,10 L10,0 L20,10 Z',
        symbolRotate: action.label === '买' ? 0 : 180,
        symbolSize: action.label === '买' || action.label === '卖' ? 24 : 28,
        symbolOffset: [0, action.label === '买' ? 12 : -12],
        itemStyle: {
          color: action.tone,
          borderColor: TRADE_PIN_BORDER,
          borderWidth: 2,
          shadowBlur: 8,
          shadowColor: `${action.tone}70`,
        },
        label: {
          show: true,
          formatter: action.label,
          position: action.label === '买' ? 'bottom' : 'top',
          distance: 5,
          color: '#f4f7fb',
          backgroundColor: 'rgba(7, 13, 20, 0.86)',
          borderColor: `${action.tone}99`,
          borderWidth: 1,
          borderRadius: 4,
          padding: [2, 4],
          fontSize: 10,
          fontWeight: 800,
        },
      };
    })
    .filter(Boolean);

  const allLines = [...riskLines, ...planLines, ...structureLines];
  const allAreas = [...planAreas, ...structureAreas];
  if (allLines.length || allAreas.length) {
    series[0] = {
      ...(series[0] as object),
      ...(allLines.length ? { markLine: {
        symbol: 'none',
        silent: true,
        data: allLines,
        lineStyle: { width: 1.2, opacity: 0.82 },
        label: {
          color: '#d8e0ea',
          backgroundColor: 'rgba(8, 13, 20, 0.82)',
          borderColor: 'rgba(113, 129, 151, 0.32)',
          borderWidth: 1,
          borderRadius: 4,
          padding: [3, 5],
          position: 'insideStartTop',
          distance: [-6, 0],
          formatter: '{b}',
        },
      } } : {}),
      ...(allAreas.length ? { markArea: {
        silent: true,
        data: allAreas,
        itemStyle: { color: 'rgba(214, 161, 59, 0.08)' },
        label: { color: '#ffd37a', fontSize: 10 },
      } } : {}),
    };
  }

  if (plans.length) {
    const lastDate = dates[dates.length - 1];
    series.push({
      name: '计划执行',
      type: 'scatter',
      xAxisIndex: 0,
      yAxisIndex: 0,
      symbol: 'diamond',
      symbolSize: 18,
      data: plans
        .map((plan) => {
          const entry = midpoint(plan.entry_min, plan.entry_max);
          if (!lastDate || !Number.isFinite(entry)) return null;
          return {
            name: '计划',
            value: [lastDate, entry],
            plan,
            itemStyle: { color: plan.is_stale ? '#708099' : '#d6a13b', borderColor: '#081018', borderWidth: 1 },
          };
        })
        .filter(Boolean),
      z: 18,
      tooltip: { show: true },
    });
  }

  if (technicalSignals.length) {
    series.push({
      name: '技术信号',
      type: 'scatter',
      xAxisIndex: 0,
      yAxisIndex: 0,
      symbol: 'roundRect',
      symbolSize: 18,
      data: technicalSignals.map((signal) => ({
        name: signal.label,
        value: [signal.time, signal.price],
        signal,
        itemStyle: {
          color: signal.tone === 'up' ? UP_COLOR : signal.tone === 'down' ? DOWN_COLOR : '#d6a13b',
          borderColor: '#081018',
          borderWidth: 1,
        },
        label: {
          show: true,
          formatter: signal.label,
          color: '#081018',
          fontSize: 9,
          fontWeight: 800,
        },
      })),
      z: 16,
      tooltip: { show: true },
    });
  }

  collectStructureSeries(structures).forEach((item) => series.push(item));

  if (tradePoints.length) {
    series.push({
      name: '交易结果',
      type: 'scatter',
      xAxisIndex: 0,
      yAxisIndex: 0,
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
      triggerOn: 'mousemove|click|mousewheel',
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
        const signalPoint = rows.find((row) => row.seriesName === '技术信号') as { data?: { signal?: KlineTechnicalSignal } } | undefined;
        if (signalPoint?.data?.signal) {
          const signal = signalPoint.data.signal;
          const tone = signal.tone === 'up' ? UP_COLOR : signal.tone === 'down' ? DOWN_COLOR : '#d6a13b';
          return `
            <div class="kline-tooltip kline-tooltip--signal">
              <div class="kline-tooltip__head">
                <span class="kline-tooltip__code">${data.code}</span>
                <span class="kline-tooltip__date">${signal.time}</span>
              </div>
              <div class="kline-tooltip__price" style="color:${tone}">
                ${signal.label} ${formatNumber(signal.price)}
                <small>${signal.kind}</small>
              </div>
              <p class="kline-tooltip__reason">${signal.detail}</p>
            </div>
          `;
        }
        const planPoint = rows.find((row) => row.seriesName === '计划执行') as { data?: { plan?: KlinePlanAnnotation } } | undefined;
        if (planPoint?.data?.plan) {
          const plan = planPoint.data.plan;
          return `
            <div class="kline-tooltip kline-tooltip--plan">
              <div class="kline-tooltip__head">
                <span class="kline-tooltip__code">${plan.code}</span>
                <span class="kline-tooltip__date">${plan.is_stale ? plan.stale_reason || '旧计划' : plan.valid_until ? `有效至 ${plan.valid_until}` : '计划候选'}</span>
              </div>
              <div class="kline-tooltip__price" style="color:${plan.is_stale ? '#708099' : '#d6a13b'}">
                入场 ${formatNumber(Number(plan.entry_min || NaN))} - ${formatNumber(Number(plan.entry_max || NaN))}
                <small>${plan.position_pct || 0}% / ${plan.strategy || '--'}</small>
              </div>
              <div class="kline-tooltip__grid">
                <span>STOP</span><b>${formatNumber(Number(plan.stop_loss || NaN))}</b>
                <span>TAKE</span><b>${formatNumber(Number(plan.take_profit || NaN))}</b>
                <span>VALID</span><b>${plan.valid_until || '--'}</b>
              </div>
              ${plan.reasoning ? `<p class="kline-tooltip__reason">${plan.reasoning}</p>` : ''}
            </div>
          `;
        }
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
        const structurePoint = rows.find((row) => row.seriesName === '结构点' || row.seriesName === '结构线') as { data?: { structure?: KlineStructureAnnotation } } | undefined;
        if (structurePoint?.data?.structure) {
          return structureTooltip(data.code, structurePoint.data.structure);
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
    lines.push({ yAxis: Number(latest.avg_cost), name: `交易成本 ${formatNumber(Number(latest.avg_cost))}`, lineStyle: { color: TRADE_COST_COLOR, type: 'solid', width: 1.6 } });
  }
  if (latest.stop_loss) {
    lines.push({ yAxis: Number(latest.stop_loss), name: `交易止损 ${formatNumber(Number(latest.stop_loss))}`, lineStyle: { color: DOWN_COLOR, type: 'solid', width: 1.6 } });
  }
  if (latest.take_profit) {
    lines.push({ yAxis: Number(latest.take_profit), name: `交易止盈 ${formatNumber(Number(latest.take_profit))}`, lineStyle: { color: UP_COLOR, type: 'solid', width: 1.6 } });
  }
  return lines;
}

function collectPlanLines(plans: KlinePlanAnnotation[]) {
  const latest = plans[0];
  if (!latest) return [];
  const lines = [];
  const staleStyle = latest.is_stale ? { color: '#708099', opacity: 0.58, type: 'dotted' } : null;
  const planLight = latest.is_stale ? '#708099' : PLAN_UPPER_COLOR;
  const prefix = latest.is_stale ? '旧计划' : '计划';
  if (latest.entry_max) {
    lines.push({ yAxis: Number(latest.entry_max), name: `${prefix}上沿 ${formatNumber(Number(latest.entry_max))}`, lineStyle: staleStyle || planLineStyle(planLight) });
  }
  if (latest.stop_loss) {
    lines.push({ yAxis: Number(latest.stop_loss), name: `${prefix}止损 ${formatNumber(Number(latest.stop_loss))}`, lineStyle: staleStyle || planLineStyle(DOWN_COLOR) });
  }
  if (latest.take_profit) {
    lines.push({ yAxis: Number(latest.take_profit), name: `${prefix}止盈 ${formatNumber(Number(latest.take_profit))}`, lineStyle: staleStyle || planLineStyle(UP_COLOR) });
  }
  return lines;
}

function planLineStyle(color: string) {
  return {
    color,
    type: PLAN_DASH,
    width: 1.9,
    opacity: 0.94,
    cap: 'round',
  };
}

function collectPlanAreas(plans: KlinePlanAnnotation[]): Array<[{ name: string; yAxis: number }, { yAxis: number }]> {
  return plans
    .filter((plan) => Number.isFinite(Number(plan.entry_min)) && Number.isFinite(Number(plan.entry_max)))
    .map((plan) => [
      { name: plan.is_stale ? '旧计划入场区间' : '计划入场区间', yAxis: Number(plan.entry_min) },
      { yAxis: Number(plan.entry_max) },
    ] as [{ name: string; yAxis: number }, { yAxis: number }]);
}

function collectStructureLines(structures: KlineStructureAnnotation[]) {
  return structures
    .filter((item) => item.kind === 'level' && Number.isFinite(Number(item.price)))
    .map((item) => ({
      yAxis: Number(item.price),
      name: `${item.label} ${formatNumber(Number(item.price))}`,
      lineStyle: {
        color: structureColor(item.tone),
        type: item.tone === 'warning' ? 'dashed' : 'solid',
        width: 1.1,
        opacity: 0.78,
      },
      structure: item,
    }));
}

function collectStructureAreas(structures: KlineStructureAnnotation[]) {
  return structures
    .filter((item) => item.kind === 'range' && Number.isFinite(Number(item.price_min)) && Number.isFinite(Number(item.price_max)))
    .map((item) => [
      {
        name: item.label,
        yAxis: Number(item.price_min),
        itemStyle: { color: structureAreaColor(item.tone) },
      },
      { yAxis: Number(item.price_max) },
    ]);
}

function collectStructureSeries(structures: KlineStructureAnnotation[]): Array<Record<string, unknown>> {
  const series: Array<Record<string, unknown>> = [];
  const lineItems = structures.filter((item) => ['trendline', 'segment', 'wave'].includes(item.kind) && (item.points || []).length >= 2);
  lineItems.forEach((item) => {
    series.push({
      name: '结构线',
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: (item.points || []).map((point) => ({
        value: [point.time, point.price],
        structure: item,
      })),
      symbol: item.kind === 'wave' ? 'circle' : 'none',
      symbolSize: item.kind === 'wave' ? 7 : 0,
      lineStyle: {
        color: structureColor(item.tone),
        width: item.kind === 'wave' ? 1.7 : 1.4,
        type: item.kind === 'segment' ? 'dashed' : 'solid',
        opacity: 0.86,
      },
      label: {
        show: item.kind === 'wave',
        formatter: (params: { dataIndex?: number }) => item.points?.[Number(params.dataIndex)]?.label || '',
        color: '#dffbf7',
        fontSize: 10,
      },
      z: 15,
      tooltip: { show: true },
    });
  });

  const points = structures.filter((item) => item.kind === 'point');
  if (points.length) {
    series.push({
      name: '结构点',
      type: 'scatter',
      xAxisIndex: 0,
      yAxisIndex: 0,
      symbol: 'triangle',
      symbolSize: 16,
      data: points.flatMap((item) => {
        const rawPoints = item.points?.length ? item.points : [{ time: item.start_time || item.end_time || '', price: Number(item.price), label: item.label }];
        return rawPoints
          .filter((point) => point.time && Number.isFinite(Number(point.price)))
          .map((point) => ({
            name: point.label || item.label,
            value: [point.time, Number(point.price)],
            structure: item,
            itemStyle: { color: structureColor(item.tone), borderColor: '#081018', borderWidth: 1 },
            label: {
              show: true,
              formatter: point.label || item.label,
              color: '#081018',
              fontSize: 9,
              fontWeight: 800,
            },
          }));
      }),
      z: 17,
      tooltip: { show: true },
    });
  }
  return series;
}

function structureColor(tone: KlineStructureAnnotation['tone']) {
  if (tone === 'up') return UP_COLOR;
  if (tone === 'down') return DOWN_COLOR;
  if (tone === 'warning') return '#d6a13b';
  return '#41e0c9';
}

function structureAreaColor(tone: KlineStructureAnnotation['tone']) {
  if (tone === 'up') return 'rgba(255, 59, 48, 0.08)';
  if (tone === 'down') return 'rgba(34, 181, 115, 0.08)';
  if (tone === 'warning') return 'rgba(214, 161, 59, 0.10)';
  return 'rgba(65, 224, 201, 0.08)';
}

function structureTooltip(code: string, structure: KlineStructureAnnotation) {
  const source = structure.source || {};
  const confidence = Number.isFinite(Number(source.confidence)) ? `${Number(source.confidence).toFixed(0)}%` : '--';
  const priceText = structure.kind === 'range'
    ? `${formatNumber(Number(structure.price_min))} - ${formatNumber(Number(structure.price_max))}`
    : Number.isFinite(Number(structure.price))
      ? formatNumber(Number(structure.price))
      : '--';
  return `
    <div class="kline-tooltip kline-tooltip--structure">
      <div class="kline-tooltip__head">
        <span class="kline-tooltip__code">${code}</span>
        <span class="kline-tooltip__date">${structure.kind}</span>
      </div>
      <div class="kline-tooltip__price" style="color:${structureColor(structure.tone)}">
        ${structure.label}
        <small>${priceText}</small>
      </div>
      <div class="kline-tooltip__grid">
        <span>SKILL</span><b>${source.skill || '--'}</b>
        <span>NODE</span><b>${source.node_id || '--'}</b>
        <span>CONF</span><b>${confidence}</b>
      </div>
      ${source.summary ? `<p class="kline-tooltip__reason">${source.summary}</p>` : ''}
    </div>
  `;
}

function midpoint(min?: number, max?: number) {
  const low = Number(min);
  const high = Number(max);
  if (Number.isFinite(low) && Number.isFinite(high)) return (low + high) / 2;
  if (Number.isFinite(low)) return low;
  if (Number.isFinite(high)) return high;
  return NaN;
}

function detectTechnicalSignals(data: KlineData): KlineTechnicalSignal[] {
  const closes = data.close.map(Number);
  const highs = data.high.map(Number);
  const lows = data.low.map(Number);
  const volumes = data.volume.map(Number);
  const ma5 = calcMA(closes, 5);
  const ma10 = calcMA(closes, 10);
  const boll = calcBOLL(closes, 20);
  const signals: KlineTechnicalSignal[] = [];

  for (let index = 1; index < data.dates.length; index += 1) {
    const prev5 = ma5[index - 1];
    const prev10 = ma10[index - 1];
    const cur5 = ma5[index];
    const cur10 = ma10[index];
    if (prev5 != null && prev10 != null && cur5 != null && cur10 != null) {
      if (prev5 <= prev10 && cur5 > cur10) {
        signals.push({
          time: data.dates[index],
          price: lows[index],
          kind: 'ma_golden_cross',
          label: '金叉',
          detail: `MA5 ${formatNumber(cur5)} 上穿 MA10 ${formatNumber(cur10)}`,
          tone: 'up',
        });
      } else if (prev5 >= prev10 && cur5 < cur10) {
        signals.push({
          time: data.dates[index],
          price: highs[index],
          kind: 'ma_death_cross',
          label: '死叉',
          detail: `MA5 ${formatNumber(cur5)} 下穿 MA10 ${formatNumber(cur10)}`,
          tone: 'down',
        });
      }
    }

    const avgVol = average(volumes.slice(Math.max(0, index - 5), index));
    const changePct = closes[index - 1] ? ((closes[index] - closes[index - 1]) / closes[index - 1]) * 100 : 0;
    if (avgVol > 0 && volumes[index] > avgVol * 1.8 && changePct > 1.5) {
      signals.push({
        time: data.dates[index],
        price: highs[index],
        kind: 'volume_breakout',
        label: '放量',
        detail: `成交量为近5根均量 ${formatNumber(volumes[index] / avgVol)} 倍，涨幅 ${changePct.toFixed(2)}%`,
        tone: 'up',
      });
    }

    const upper = boll.upper[index];
    const lower = boll.lower[index];
    if (upper != null && highs[index] >= upper) {
      signals.push({
        time: data.dates[index],
        price: highs[index],
        kind: 'boll_upper_touch',
        label: '上轨',
        detail: `触及 BOLL 上轨 ${formatNumber(upper)}`,
        tone: 'neutral',
      });
    }
    if (lower != null && lows[index] <= lower) {
      signals.push({
        time: data.dates[index],
        price: lows[index],
        kind: 'boll_lower_touch',
        label: '下轨',
        detail: `触及 BOLL 下轨 ${formatNumber(lower)}`,
        tone: 'neutral',
      });
    }
  }

  return signals.slice(-24);
}

function average(values: number[]) {
  const valid = values.filter((value) => Number.isFinite(value));
  if (!valid.length) return 0;
  return valid.reduce((sum, value) => sum + value, 0) / valid.length;
}
