import { BarChart, CandlestickChart, LineChart, ScatterChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components';
import { init, use as useECharts, type ECharts } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { buildKlineOption } from '../charts/klineOption';
import type { KlineData, KlineLayerKey, KlinePeriod, KlinePlanAnnotation, KlineStructureAnnotation, KlineTradeMarker, LedgerEntry, OverlayKind, PlanData } from '../types';

useECharts([
  BarChart,
  CandlestickChart,
  LineChart,
  ScatterChart,
  DataZoomComponent,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);

interface KlineChartProps {
  code: string;
  name?: string;
  period: KlinePeriod;
  overlay: OverlayKind;
  tradeRefreshKey?: string | number;
  layers?: KlineLayerKey[];
  plan?: PlanData;
  runId?: string;
}

export function KlineChart({ code, name = '', period, overlay, tradeRefreshKey = '', layers = ['trades'], plan = {}, runId }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ECharts | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [source, setSource] = useState('');
  const [klineData, setKlineData] = useState<KlineData | null>(null);
  const [trades, setTrades] = useState<KlineTradeMarker[]>([]);
  const [structures, setStructures] = useState<KlineStructureAnnotation[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    chartRef.current = init(containerRef.current);
    const chart = chartRef.current;
    const resize = () => chart.resize();
    const wheelZoom = createWheelZoomHandler(chart);
    window.addEventListener('resize', resize);
    containerRef.current.addEventListener('wheel', wheelZoom, { passive: false, capture: true });
    const observer = new ResizeObserver(resize);
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', resize);
      containerRef.current?.removeEventListener('wheel', wheelZoom, { capture: true });
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current || !code) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');

    api.kline(code, period, 260)
      .then((data: KlineData) => {
        if (controller.signal.aborted || !chartRef.current) return;
        setSource(data.source || '');
        setKlineData(data);
      })
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || 'K线数据加载失败');
        setKlineData(null);
        chartRef.current?.clear();
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [code, period]);

  useEffect(() => {
    if (!code) return;
    let active = true;
    api.ledgerForCode(code, 200, runId)
      .then((rows) => {
        if (!active) return;
        setTrades(rows.map((row) => ledgerToTradeMarker(row, code)).filter((row) => row.time && row.price > 0));
      })
      .catch(() => {
        if (active) setTrades([]);
      });
    return () => {
      active = false;
    };
  }, [code, tradeRefreshKey, runId]);

  useEffect(() => {
    if (!code || !layers.includes('structures')) {
      setStructures([]);
      return;
    }
    let active = true;
    api.klineAnnotations(code, period, runId)
      .then((data) => {
        if (active) setStructures(data.annotations || []);
      })
      .catch(() => {
        if (active) setStructures([]);
      });
    return () => {
      active = false;
    };
  }, [code, period, layers, runId]);

  useEffect(() => {
    if (!chartRef.current || !klineData) return;
    const option = buildKlineOption(
      klineData,
      overlay,
      layers.includes('trades') ? trades : [],
      layers.includes('plan') ? planToAnnotations(plan, code) : [],
      layers.includes('signals'),
      layers.includes('structures') ? structures : [],
    );
    chartRef.current.clear();
    chartRef.current.setOption(option, { notMerge: true, lazyUpdate: false });
    chartRef.current.resize();
  }, [klineData, overlay, trades, layers, plan, code, structures]);

  return (
    <section className="kline-panel">
      <div className="chart-meta">
        <span>{stockTitle(code, name)}</span>
        <span>{period.toUpperCase()}</span>
        {source ? <span>{source}</span> : null}
        {loading ? <span>加载中</span> : null}
      </div>
      <div className="chart-canvas" ref={containerRef} />
      {error ? <div className="chart-error">{error}</div> : null}
    </section>
  );
}

function stockTitle(code: string, name?: string) {
  const label = (name || '').trim();
  return label && label !== code ? `${label} · ${code}` : code;
}

function planToAnnotations(plan: PlanData, code: string): KlinePlanAnnotation[] {
  const today = new Date().toISOString().slice(0, 10);
  const planDate = (plan.updated || '').slice(0, 10);
  return (plan.buy_candidates || [])
    .filter((candidate) => candidate.code === code)
    .map((candidate) => ({
      code: candidate.code,
      entry_min: candidate.entry_min,
      entry_max: candidate.entry_max,
      stop_loss: candidate.stop_loss,
      take_profit: candidate.take_profit,
      valid_until: candidate.valid_until,
      position_pct: candidate.position_pct,
      strategy: candidate.strategy_type,
      reasoning: candidate.reasoning || candidate.reason,
      plan_updated: plan.updated,
      is_stale: isPlanStale(planDate, candidate.valid_until, today),
      stale_reason: planStaleReason(planDate, candidate.valid_until, today),
    }));
}

function isPlanStale(planDate: string, validUntil = '', today: string) {
  return Boolean((validUntil && validUntil < today) || (planDate && planDate !== today));
}

function planStaleReason(planDate: string, validUntil = '', today: string) {
  if (validUntil && validUntil < today) return `已过期: ${validUntil}`;
  if (planDate && planDate !== today) return `旧计划: ${planDate}`;
  return '';
}

function ledgerToTradeMarker(row: LedgerEntry, fallbackCode: string): KlineTradeMarker {
  return {
    time: row.time || '',
    code: row.symbol || row.code || fallbackCode,
    action: row.decision || row.action || '',
    price: Number(row.price || 0),
    shares: row.shares,
    strategy: row.strategy,
    reasoning: row.reasoning,
    stop_loss: row.stop_loss,
    take_profit: row.take_profit,
    avg_cost: row.avg_cost,
  };
}

function createWheelZoomHandler(chart: ECharts) {
  let lastTime = 0;
  let remainder = 0;

  return (event: WheelEvent) => {
    event.preventDefault();

    const now = performance.now();
    const delta = normalizeWheelDelta(event);
    remainder += delta;

    if (now - lastTime < 42 && Math.abs(remainder) < 18) return;
    lastTime = now;

    const option = chart.getOption() as { dataZoom?: Array<{ start?: number; end?: number }> };
    const current = option.dataZoom?.[0] || {};
    const start = clamp(Number(current.start ?? 60), 0, 100);
    const end = clamp(Number(current.end ?? 100), 0, 100);
    const span = Math.max(end - start, 1);
    const center = start + span * getPointerRatio(chart, event);
    const direction = remainder > 0 ? 1 : -1;
    const magnitude = Math.min(Math.abs(remainder), 120);
    const sensitivity = event.deltaMode === WheelEvent.DOM_DELTA_PIXEL && Math.abs(event.deltaY) < 50 ? 0.0016 : 0.0032;
    const factor = 1 + direction * magnitude * sensitivity;
    const nextSpan = clamp(span * factor, 8, 96);
    const nextStart = clamp(center - nextSpan * getPointerRatio(chart, event), 0, 100 - nextSpan);

    remainder = 0;
    chart.dispatchAction({
      type: 'dataZoom',
      dataZoomIndex: 0,
      start: nextStart,
      end: nextStart + nextSpan,
    });
  };
}

function normalizeWheelDelta(event: WheelEvent) {
  if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) return event.deltaY * 16;
  if (event.deltaMode === WheelEvent.DOM_DELTA_PAGE) return event.deltaY * 240;
  return event.deltaY;
}

function getPointerRatio(chart: ECharts, event: WheelEvent) {
  const rect = chart.getDom().getBoundingClientRect();
  if (!rect.width) return 0.5;
  return clamp((event.clientX - rect.left) / rect.width, 0.05, 0.95);
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}
