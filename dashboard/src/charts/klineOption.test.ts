import { describe, expect, it } from 'vitest';
import { buildKlineOption } from './klineOption';
import type { KlineData } from '../types';

const sample: KlineData = {
  code: '000001',
  dates: ['2026-06-01', '2026-06-02', '2026-06-03'],
  open: [10, 11, 12],
  high: [12, 13, 14],
  low: [9, 10, 11],
  close: [11, 12, 13],
  volume: [1000, 1200, 900],
};

const signalSample: KlineData = {
  code: '000001',
  dates: Array.from({ length: 24 }, (_, index) => `2026-06-${String(index + 1).padStart(2, '0')}`),
  open: [
    12, 11.8, 11.6, 11.4, 11.2, 11, 10.8, 10.6, 10.4, 10.2, 10, 10.1,
    10.3, 10.6, 10.9, 11.3, 11.8, 12.4, 13.1, 13.9, 14.8, 15.6, 16.4, 17.2,
  ],
  high: [
    12.2, 12, 11.8, 11.6, 11.4, 11.2, 11, 10.8, 10.6, 10.4, 10.2, 10.4,
    10.6, 10.9, 11.2, 11.7, 12.3, 12.9, 13.8, 14.8, 15.9, 16.8, 17.8, 18.6,
  ],
  low: [
    11.8, 11.6, 11.4, 11.2, 11, 10.8, 10.6, 10.4, 10.2, 10, 9.8, 9.9,
    10.1, 10.3, 10.6, 11, 11.5, 12, 12.7, 13.5, 14.2, 15, 15.8, 16.6,
  ],
  close: [
    11.9, 11.7, 11.5, 11.3, 11.1, 10.9, 10.7, 10.5, 10.3, 10.1, 10, 10.2,
    10.5, 10.8, 11.1, 11.6, 12.1, 12.7, 13.5, 14.4, 15.3, 16.2, 17.1, 18,
  ],
  volume: [
    1000, 980, 960, 940, 920, 900, 880, 860, 840, 820, 800, 850,
    900, 950, 1000, 1100, 1200, 1300, 3600, 3800, 3900, 4000, 4200, 4500,
  ],
};

describe('buildKlineOption', () => {
  it('binds dataZoom to both price and volume x axes', () => {
    const option = buildKlineOption(sample, 'MA');
    const dataZoom = option.dataZoom as Array<Record<string, unknown>>;
    expect(dataZoom).toHaveLength(2);
    expect(dataZoom[0]).toMatchObject({
      type: 'inside',
      xAxisIndex: [0, 1],
      filterMode: 'none',
      zoomOnMouseWheel: false,
      minValueSpan: 20,
    });
    expect(dataZoom[1]).toMatchObject({
      type: 'slider',
      xAxisIndex: [0, 1],
      filterMode: 'none',
    });
  });

  it('enables crosshair tooltip and links x axes', () => {
    const option = buildKlineOption(sample, 'BOLL');
    expect(option.tooltip).toMatchObject({
      trigger: 'item',
      axisPointer: { type: 'cross', snap: true },
    });
    expect(option.axisPointer).toMatchObject({
      link: [{ xAxisIndex: [0, 1] }],
    });
  });

  it('shows the time pointer label only on the main K-line x axis', () => {
    const option = buildKlineOption(sample, 'MA');
    const axes = option.xAxis as Array<Record<string, { label?: { show?: boolean } }>>;

    expect(axes[0].axisPointer?.label?.show).toBe(true);
    expect(axes[1].axisPointer?.label?.show).toBe(false);
  });

  it('uses a custom dark financial tooltip formatter', () => {
    const option = buildKlineOption(sample, 'MA');
    const tooltip = option.tooltip as { formatter: (params: unknown) => string };
    const html = tooltip.formatter([
      {
        seriesType: 'candlestick',
        value: [10, 11, 9, 12],
        dataIndex: 0,
        axisValueLabel: '2026-06-01',
      },
    ]);

    expect(html).toContain('kline-tooltip');
    expect(html).toContain('000001');
    expect(html).toContain('OPEN');
    expect(html).toContain('VOL');
    expect(html).toContain('涨 +1.00 / +10.00%');
    expect(option.tooltip).toMatchObject({
      backgroundColor: 'transparent',
      borderColor: 'transparent',
      padding: 0,
    });
  });

  it('renders trading result markers and risk lines', () => {
    const option = buildKlineOption(sample, 'MA', [
      {
        time: '2026-06-02',
        code: '000001',
        action: 'buy',
        price: 12,
        shares: 100,
        strategy: 'test',
        stop_loss: 10.8,
        take_profit: 14,
        avg_cost: 12,
      },
    ]);
    const series = option.series as Array<Record<string, unknown>>;
    const tradeSeries = series.find((item) => item.name === '交易结果') as Record<string, unknown>;
    const candleSeries = series.find((item) => item.name === 'K线') as Record<string, unknown>;

    expect(tradeSeries).toMatchObject({ type: 'scatter' });
    expect(candleSeries.markLine).toBeTruthy();
  });

  it('renders plan execution layer when plan annotations are provided', () => {
    const option = buildKlineOption(sample, 'MA', [], [
      {
        code: '000001',
        entry_min: 11.2,
        entry_max: 12.4,
        stop_loss: 10.5,
        take_profit: 14.2,
        valid_until: '2026-06-05',
        position_pct: 20,
        strategy: 'breakout',
      },
    ]);
    const series = option.series as Array<Record<string, unknown>>;
    const planSeries = series.find((item) => item.name === '计划执行') as Record<string, unknown>;
    const candleSeries = series.find((item) => item.name === 'K线') as Record<string, unknown>;

    expect(planSeries).toMatchObject({ type: 'scatter' });
    expect(candleSeries.markLine).toBeTruthy();
    expect(candleSeries.markArea).toBeTruthy();
  });

  it('marks stale plan lines as old plan labels', () => {
    const option = buildKlineOption(sample, 'MA', [], [
      {
        code: '000001',
        entry_min: 11.2,
        entry_max: 12.4,
        stop_loss: 10.5,
        take_profit: 14.2,
        is_stale: true,
        stale_reason: '旧计划: 2026-06-01',
      },
    ]);
    const series = option.series as Array<Record<string, unknown>>;
    const candleSeries = series.find((item) => item.name === 'K线') as Record<string, unknown>;
    const markLine = candleSeries.markLine as { data: Array<{ name: string }> };

    expect(markLine.data.some((item) => item.name.startsWith('旧计划'))).toBe(true);
  });

  it('renders technical signal layer when enabled', () => {
    const option = buildKlineOption(signalSample, 'MA', [], [], true);
    const series = option.series as Array<Record<string, unknown>>;
    const signalSeries = series.find((item) => item.name === '技术信号') as Record<string, unknown>;

    expect(signalSeries).toMatchObject({ type: 'scatter' });
    expect(signalSeries.data).toBeTruthy();
  });

  it('renders structured annotation layer from agent outputs', () => {
    const option = buildKlineOption(sample, 'MA', [], [], false, [
      {
        id: 'support-1',
        code: '000001',
        period: 'day',
        kind: 'level',
        label: '中枢支撑',
        tone: 'up',
        price: 10.5,
        source: { skill: 'pivot', confidence: 76, summary: '支撑聚类' },
      },
      {
        id: 'trend-1',
        code: '000001',
        period: 'day',
        kind: 'trendline',
        label: '上升趋势线',
        tone: 'neutral',
        points: [
          { time: '2026-06-01', price: 9.5 },
          { time: '2026-06-03', price: 11.2 },
        ],
      },
    ]);
    const series = option.series as Array<Record<string, unknown>>;
    const candleSeries = series.find((item) => item.name === 'K线') as Record<string, unknown>;
    const structureSeries = series.find((item) => item.name === '结构线') as Record<string, unknown>;
    const markLine = candleSeries.markLine as { data: Array<{ name: string }> };

    expect(markLine.data.some((item) => item.name.includes('中枢支撑'))).toBe(true);
    expect(structureSeries).toMatchObject({ type: 'line' });
  });
});
