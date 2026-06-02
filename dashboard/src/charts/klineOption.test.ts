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
    expect(option.tooltip).toMatchObject({
      backgroundColor: 'transparent',
      borderColor: 'transparent',
      padding: 0,
    });
  });
});
