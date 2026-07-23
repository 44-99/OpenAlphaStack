import { lazy, Suspense, type CSSProperties, type FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import {
  BookOpenCheck,
  BriefcaseBusiness,
  ChevronDown,
  GitBranch,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Plus,
  Radar,
  Search,
  ShieldAlert,
  Square,
  type LucideIcon,
} from 'lucide-react';
import { api, normalizeWatchlist } from './api';
import type {
  CacheStatus,
  DashboardState,
  EngineStatus,
  KlineLayerKey,
  KlinePeriod,
  LedgerEntry,
  OverlayKind,
  PageKey,
  PlanData,
  RunRecord,
  StockSearchItem,
  WatchlistItem,
  WorkflowEvent,
  WorkflowGraph,
} from './types';

const KlineChart = lazy(() => import('./components/KlineChart').then((module) => ({ default: module.KlineChart })));
const WorkflowBoard = lazy(() => import('./components/WorkflowBoard').then((module) => ({ default: module.WorkflowBoard })));

const periods: Array<{ key: KlinePeriod; label: string }> = [
  { key: '1m', label: '1分' },
  { key: '5m', label: '5分' },
  { key: '15m', label: '15分' },
  { key: '60m', label: '60分' },
  { key: 'day', label: '日线' },
  { key: 'week', label: '周线' },
  { key: 'month', label: '月线' },
];

const overlays: OverlayKind[] = ['NONE', 'MA', 'EMA', 'BOLL'];
const overlayLabels: Record<OverlayKind, string> = {
  NONE: '无',
  MA: '均线',
  EMA: 'EMA',
  BOLL: '布林',
};
const klineLayerItems: Array<{ key: KlineLayerKey; label: string; enabled: boolean }> = [
  { key: 'trades', label: '交易', enabled: true },
  { key: 'plan', label: '计划', enabled: true },
  { key: 'signals', label: '信号', enabled: true },
  { key: 'structures', label: '结构', enabled: true },
];
const pageItems: Array<{ key: PageKey; label: string; Icon: LucideIcon }> = [
  { key: 'watch', label: '盯盘', Icon: Radar },
  { key: 'workflow', label: '流程', Icon: GitBranch },
  { key: 'account', label: '账户', Icon: BriefcaseBusiness },
];

function money(value?: number) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function pnl(value?: number, pct?: number) {
  const amount = Number(value || 0);
  const sign = amount >= 0 ? '+' : '';
  return `${sign}${money(amount)} (${sign}${Number(pct || 0).toFixed(2)}%)`;
}

export default function App() {
  const injected = window.__DATA__ || {};
  const [page, setPage] = useState<PageKey>('watch');
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>(injected.run_id || '');
  const [runPickerOpen, setRunPickerOpen] = useState(false);
  const [runActionMessage, setRunActionMessage] = useState('');
  const [runActionBusy, setRunActionBusy] = useState('');
  const [selectedCode, setSelectedCode] = useState('000001');
  const [searchedStock, setSearchedStock] = useState<StockSearchItem | null>(null);
  const [period, setPeriod] = useState<KlinePeriod>('1m');
  const [overlay, setOverlay] = useState<OverlayKind>('NONE');
  const [klineLayers, setKlineLayers] = useState<KlineLayerKey[]>(['trades']);
  const [state, setState] = useState<DashboardState>({
    total_asset: injected.state?.total_asset || 0,
    cash: injected.state?.cash || 0,
    position_value: injected.state?.position_value || 0,
    day_pnl: injected.state?.day_pnl || 0,
    day_return_pct: injected.state?.day_return_pct || 0,
  });
  const [positions, setPositions] = useState<DashboardState['positions']>({});
  const [plan, setPlan] = useState<PlanData>(injected.plan_summary || {});
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [engine, setEngine] = useState<EngineStatus>({});
  const [cache, setCache] = useState<CacheStatus>({});
  const [workflowEvents, setWorkflowEvents] = useState<WorkflowEvent[]>([]);
  const [workflowGraph, setWorkflowGraph] = useState<WorkflowGraph | undefined>();
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [leftWidth, setLeftWidth] = useState(190);
  const selectionInitialized = useRef(false);
  const selectedRun = useMemo(() => runs.find((run) => run.run_id === selectedRunId), [runs, selectedRunId]);

  const watchlistNames = useMemo(() => {
    const map = new Map<string, string>();
    watchlist.forEach((item) => {
      if (item.code && item.name) map.set(item.code, item.name);
    });
    return map;
  }, [watchlist]);

  const aiWatchlist = useMemo(() => {
    const map = new Map<string, WatchlistItem>();
    (plan.buy_candidates || []).forEach((candidate) => {
      if (candidate.code) {
        map.set(candidate.code, {
          code: candidate.code,
          name: candidate.name || watchlistNames.get(candidate.code),
          source: candidate.strategy_type || '候选',
        });
      }
    });
    Object.entries(positions || {}).forEach(([code, pos]) => {
      map.set(code, { code, name: watchlistNames.get(code), source: pos.strategy || '持仓' });
    });
    return [...map.values()];
  }, [plan.buy_candidates, positions, watchlistNames]);

  const userWatchlist = useMemo(() => {
    const ai = new Set(aiWatchlist.map((item) => item.code));
    return watchlist.filter((item) => !ai.has(item.code));
  }, [aiWatchlist, watchlist]);

  async function refreshRuns(preferredRunId = selectedRunId) {
    const data = await api.runs();
    setRuns(data.runs || []);
    const preferred = data.runs.find((run) => run.run_id === preferredRunId);
    const nextRunId = preferred?.run_id || data.selected_run_id || data.runs[0]?.run_id || '';
    if (nextRunId) setSelectedRunId(nextRunId);
    return nextRunId;
  }

  useEffect(() => {
    refreshRuns().catch((error: Error) => setRunActionMessage(error.message || '运行列表读取失败'));
    Promise.allSettled([
      api.cacheStatus().then(setCache),
      api.watchlist().then((raw) => setWatchlist(normalizeWatchlist(raw))),
    ]);
  }, []);

  useEffect(() => {
    if (!selectedRunId) return;
    Promise.allSettled([
      api.state(selectedRunId).then((data) => {
        setState((current) => ({ ...current, ...data }));
        setPositions(data.positions || {});
      }).catch(() => {
        setState({ total_asset: 0, cash: 0, position_value: 0, day_pnl: 0, day_return_pct: 0 });
        setPositions({});
      }),
      api.plan(selectedRunId).then(setPlan).catch(() => setPlan({})),
      api.ledger(selectedRunId).then(setLedger).catch(() => setLedger([])),
      api.engineStatus(selectedRunId).then(setEngine).catch(() => setEngine({})),
      api.workflowEvents(selectedRunId).then((data) => setWorkflowEvents(data.events)).catch(() => setWorkflowEvents([])),
      api.workflowGraph(selectedRunId).then(setWorkflowGraph).catch(() => setWorkflowGraph(undefined)),
    ]);
  }, [selectedRunId]);

  useEffect(() => {
    if (!selectedRunId) return;
    let active = true;
    const refresh = () => {
      Promise.allSettled([
        api.runs().then((data) => {
          if (!active) return;
          setRuns(data.runs || []);
          if (!data.runs?.some((run) => run.run_id === selectedRunId)) {
            const nextRunId = data.selected_run_id || data.runs?.[0]?.run_id || '';
            if (nextRunId) setSelectedRunId(nextRunId);
          }
        }),
        api.engineStatus(selectedRunId).then((data) => {
          if (active) setEngine(data);
        }),
        api.workflowEvents(selectedRunId).then((data) => {
          if (active) setWorkflowEvents(data.events);
        }),
        api.workflowGraph(selectedRunId).then((data) => {
          if (active) setWorkflowGraph(data);
        }),
      ]);
    };
    const timer = window.setInterval(refresh, 10000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selectedRunId]);

  useEffect(() => {
    const first = aiWatchlist[0]?.code || userWatchlist[0]?.code;
    if (!selectionInitialized.current && first) {
      selectionInitialized.current = true;
      setSelectedCode(first);
    }
  }, [aiWatchlist, userWatchlist]);

  const selectedStockName = useMemo(() => {
    if (searchedStock?.code === selectedCode) return searchedStock.name || '';
    const item = [...aiWatchlist, ...userWatchlist].find((candidate) => candidate.code === selectedCode);
    return item?.name || watchlistNames.get(selectedCode) || '';
  }, [aiWatchlist, searchedStock, selectedCode, userWatchlist, watchlistNames]);

  useEffect(() => {
    const source = new EventSource('/api/stream');
    source.addEventListener('nav', (event) => {
      const data = JSON.parse(event.data) as DashboardState;
      if (data.run_id && selectedRunId && data.run_id !== selectedRunId) return;
      setState((current) => ({ ...current, ...data }));
      setPositions(data.positions || {});
    });
    source.addEventListener('trade', (event) => {
      const data = JSON.parse(event.data) as LedgerEntry;
      if (data.run_id && selectedRunId && data.run_id !== selectedRunId) return;
      setLedger((current) => [data, ...current].slice(0, 200));
    });
    source.addEventListener('plan_updated', (event) => {
      setPlan((current) => ({ ...current, ...JSON.parse(event.data) }));
    });
    source.addEventListener('workflow_event', (event) => {
      const data = JSON.parse(event.data) as WorkflowEvent;
      if (data.run_id && selectedRunId && data.run_id !== selectedRunId) return;
      setWorkflowEvents((current) => [data, ...current.filter((item) => item.event_id !== data.event_id)].slice(0, 500));
      api.workflowGraph(selectedRunId).then(setWorkflowGraph).catch(() => undefined);
    });
    source.onerror = () => {
      source.close();
    };
    return () => source.close();
  }, [selectedRunId]);

  const cacheLayer = cache.kline_cache || cache.minute_cache;
  const tradeRefreshKey = ledger[0]?.seq || ledger[0]?.time || ledger.length;

  async function clearCache() {
    if (!confirm('清空本地K线缓存？交易数据、自选股和新闻缓存不会删除。')) return;
    const result = await api.clearKlineCache();
    setCache(result);
  }

  async function startPaperRun() {
    setRunActionBusy('start-paper');
    setRunActionMessage('');
    try {
      const result = await api.startRun('paper');
      const runId = result.run.run_id;
      await refreshRuns(runId);
      setSelectedRunId(runId);
      setRunActionMessage(result.run.existing ? '已有模拟盘正在运行，已切换查看。' : '新模拟盘已启动。');
    } catch (error) {
      setRunActionMessage(error instanceof Error ? error.message : '模拟盘启动失败');
    } finally {
      setRunActionBusy('');
    }
  }

  async function resumeRun(run: RunRecord) {
    if (run.mode === 'live') return;
    setRunActionBusy(`resume-${run.run_id}`);
    setRunActionMessage('');
    try {
      const result = await api.resumeRun(run.run_id);
      const runId = result.run.run_id;
      await refreshRuns(runId);
      setSelectedRunId(runId);
      setRunActionMessage(result.run.existing ? '该模拟盘已在运行。' : '模拟盘已恢复运行。');
    } catch (error) {
      setRunActionMessage(error instanceof Error ? error.message : '恢复失败');
    } finally {
      setRunActionBusy('');
    }
  }

  async function stopRun(run: RunRecord) {
    setRunActionBusy(`stop-${run.run_id}`);
    setRunActionMessage('');
    try {
      await api.stopRun(run.run_id);
      await refreshRuns(run.run_id);
      setRunActionMessage('已发送关闭指令。');
    } catch (error) {
      setRunActionMessage(error instanceof Error ? error.message : '关闭失败');
    } finally {
      setRunActionBusy('');
    }
  }

  return (
    <div
      className={`terminal-app ${leftCollapsed ? 'left-collapsed' : ''}`}
      style={{
        '--left-width': `${leftCollapsed ? 64 : leftWidth}px`,
      } as CSSProperties}
    >
      <header className="topbar">
        <div className="brand">OpenAlphaStack</div>
        <Stat label="总资产" value={money(state.total_asset)} />
        <Stat label="现金" value={money(state.cash)} />
        <Stat label="持仓" value={money(state.position_value)} />
        <Stat label="当日" value={pnl(state.day_pnl, state.day_return_pct)} tone={Number(state.day_pnl) >= 0 ? 'up' : 'down'} />
        <RunControlCenter
          runs={runs}
          selectedRun={selectedRun}
          open={runPickerOpen}
          busyKey={runActionBusy}
          message={runActionMessage}
          onToggle={() => setRunPickerOpen((value) => !value)}
          onClose={() => setRunPickerOpen(false)}
          onSelect={(runId) => {
            setSelectedRunId(runId);
            setRunPickerOpen(false);
          }}
          onStartPaper={startPaperRun}
          onResume={resumeRun}
          onStop={stopRun}
        />
        <div className="top-spacer" />
        <Stat label="K线缓存" value={`${Number(cacheLayer?.mb || 0).toFixed(2)} MB / ${cacheLayer?.files || 0} 个`} />
        <button className="ghost-button" onClick={clearCache}>清缓存</button>
        <span className={`engine-pill ${engine.observation_mode ? 'warn' : ''}`}>{engine.status || '--'}</span>
      </header>

      <aside className="sidebar">
        <button className="sidebar-toggle" onClick={() => setLeftCollapsed((value) => !value)} title={leftCollapsed ? '展开侧边栏' : '收起侧边栏'}>
          {leftCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          {!leftCollapsed ? <span>收起</span> : null}
        </button>
        <nav>
          {pageItems.map((item) => (
            <button key={item.key} className={page === item.key ? 'active' : ''} onClick={() => setPage(item.key)} title={item.label}>
              <span className="nav-icon"><item.Icon size={18} strokeWidth={1.9} /></span>
              <span className="nav-label">{item.label}</span>
            </button>
          ))}
        </nav>
        {!leftCollapsed ? (
          <>
            <StockSearch
              selected={selectedCode}
              onSelect={(item) => {
                selectionInitialized.current = true;
                setSearchedStock(item);
                setSelectedCode(item.code);
                setPage('watch');
              }}
            />
            <WatchSection title="AI 盯盘" icon={BookOpenCheck} items={aiWatchlist} selected={selectedCode} onSelect={setSelectedCode} empty="暂无盯盘标的" />
            <WatchSection title="我的自选" icon={Radar} items={userWatchlist} selected={selectedCode} onSelect={setSelectedCode} empty="飞书 /portfolio 管理" />
          </>
        ) : null}
      </aside>
      {!leftCollapsed ? <ResizeHandle side="left" width={leftWidth} onResize={setLeftWidth} /> : null}

      <main className="workspace">
        {page === 'watch' ? (
          <>
            <Suspense fallback={<div className="empty compact">K线加载中</div>}>
              <KlineChart code={selectedCode} name={selectedStockName} period={period} overlay={overlay} tradeRefreshKey={tradeRefreshKey} layers={klineLayers} plan={plan} runId={selectedRunId} />
            </Suspense>
            <div className="control-strip">
              {periods.map((item) => (
                <button key={item.key} className={period === item.key ? 'active' : ''} onClick={() => setPeriod(item.key)}>
                  {item.label}
                </button>
              ))}
              <span className="divider" />
              <span className="control-label">辅助</span>
              {overlays.map((item) => (
                <button key={item} className={overlay === item ? 'active' : ''} onClick={() => setOverlay(item)}>
                  {overlayLabels[item]}
                </button>
              ))}
              <span className="subchart-label">副图: 成交量</span>
              <span className="divider" />
              {klineLayerItems.map((item) => (
                <button
                  key={item.key}
                  className={klineLayers.includes(item.key) ? 'active' : ''}
                  disabled={!item.enabled}
                  onClick={() => setKlineLayers((current) => toggleLayer(current, item.key))}
                  title={item.key === 'structures' ? '显示 Agent / skill 输出的结构化画线' : undefined}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </>
        ) : null}
        {page === 'workflow' ? (
          <Suspense fallback={<div className="empty compact">流程加载中</div>}>
            <WorkflowBoard
              graph={workflowGraph}
              events={workflowEvents}
              plan={plan}
              ledger={ledger}
              onCopyPrompt={(text) => void navigator.clipboard.writeText(text)}
            />
          </Suspense>
        ) : null}
        {page === 'account' ? <Account positions={positions || {}} ledger={ledger} /> : null}
      </main>
    </div>
  );
}

function toggleLayer(current: KlineLayerKey[], key: KlineLayerKey) {
  return current.includes(key) ? current.filter((item) => item !== key) : [...current, key];
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return <div className="stat"><span>{label}</span><strong className={tone || ''}>{value}</strong></div>;
}

function RunControlCenter({ runs, selectedRun, open, busyKey, message, onToggle, onClose, onSelect, onStartPaper, onResume, onStop }: {
  runs: RunRecord[];
  selectedRun?: RunRecord;
  open: boolean;
  busyKey: string;
  message: string;
  onToggle: () => void;
  onClose: () => void;
  onSelect: (runId: string) => void;
  onStartPaper: () => void;
  onResume: (run: RunRecord) => void;
  onStop: (run: RunRecord) => void;
}) {
  const controlRef = useRef<HTMLDivElement | null>(null);
  const paperRuns = runs.filter((run) => run.mode === 'paper' || run.mode === 'demo');
  const agentRuns = runs.filter((run) => run.mode === 'agent');
  const liveRuns = runs.filter((run) => run.mode === 'live');
  const liveLocked = true;

  useEffect(() => {
    if (!open) return;

    function handlePointerDown(event: PointerEvent) {
      if (!isOutsideRunControl(event.target, controlRef.current)) return;
      onClose();
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [onClose, open]);

  return (
    <div className="run-control" ref={controlRef}>
      <button className="run-control-trigger" onClick={onToggle} title="选择或控制模拟盘/实盘" aria-expanded={open}>
        <span className={`run-dot ${selectedRun?.is_alive ? 'alive' : 'stopped'}`} />
        <span className="run-trigger-main">
          <strong>{selectedRun ? modeLabel(selectedRun.mode) : '选择运行'}</strong>
          <small>{selectedRun?.run_id || '暂无 run'}</small>
        </span>
        <span className="run-trigger-meta">{selectedRun?.status || '--'}</span>
        <ChevronDown size={16} />
      </button>
      {open ? (
        <div className="run-popover">
          <header>
            <div>
              <strong>运行控制台</strong>
              <span>当前查看: {selectedRun?.run_id || '--'}</span>
            </div>
            <button onClick={onStartPaper} disabled={Boolean(busyKey)} title="新建并启动模拟盘">
              <Plus size={14} />新建模拟盘
            </button>
          </header>
          <RunGroup
            title="模拟盘"
            runs={paperRuns}
            selectedRunId={selectedRun?.run_id || ''}
            busyKey={busyKey}
            emptyText="暂无模拟盘记录"
            onSelect={onSelect}
            onResume={onResume}
            onStop={onStop}
          />
          <RunGroup
            title="Agent 任务"
            runs={agentRuns}
            selectedRunId={selectedRun?.run_id || ''}
            busyKey={busyKey}
            emptyText="暂无 Agent 任务记录"
            onSelect={onSelect}
            onResume={onResume}
            onStop={onStop}
          />
          <section className="run-group locked">
            <div className="run-group-title">
              <span>实盘</span>
              <small><ShieldAlert size={13} />实盘未准入</small>
            </div>
            {liveRuns.length ? (
              liveRuns.map((run) => (
                <RunRow
                  key={run.run_id}
                  run={run}
                  selected={selectedRun?.run_id === run.run_id}
                  busyKey={busyKey}
                  liveLocked={liveLocked}
                  onSelect={onSelect}
                  onResume={onResume}
                  onStop={onStop}
                />
              ))
            ) : (
              <p className="run-empty">暂无实盘记录。BrokerAdapter、安全闸门和人工确认完成前不能新建实盘。</p>
            )}
          </section>
          {message ? <div className="run-message">{message}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

export function isOutsideRunControl(target: EventTarget | null, control: Pick<HTMLDivElement, 'contains'> | null) {
  if (!target || !control) return false;
  return !control.contains(target as Node);
}

function RunGroup({ title, runs, selectedRunId, busyKey, emptyText, onSelect, onResume, onStop }: {
  title: string;
  runs: RunRecord[];
  selectedRunId: string;
  busyKey: string;
  emptyText?: string;
  onSelect: (runId: string) => void;
  onResume: (run: RunRecord) => void;
  onStop: (run: RunRecord) => void;
}) {
  return (
    <section className="run-group">
      <div className="run-group-title">
        <span>{title}</span>
        <small>{runs.length} 个</small>
      </div>
      {runs.length ? runs.map((run) => (
        <RunRow
          key={run.run_id}
          run={run}
          selected={selectedRunId === run.run_id}
          busyKey={busyKey}
          onSelect={onSelect}
          onResume={onResume}
          onStop={onStop}
        />
      )) : <p className="run-empty">{emptyText || '暂无记录'}</p>}
    </section>
  );
}

function RunRow({ run, selected, busyKey, liveLocked = false, onSelect, onResume, onStop }: {
  run: RunRecord;
  selected: boolean;
  busyKey: string;
  liveLocked?: boolean;
  onSelect: (runId: string) => void;
  onResume: (run: RunRecord) => void;
  onStop: (run: RunRecord) => void;
}) {
  const busy = busyKey.endsWith(run.run_id);
  const canControl = run.mode !== 'demo' && run.mode !== 'agent' && !liveLocked;
  return (
    <article className={`run-row ${selected ? 'selected' : ''} ${run.is_alive ? 'alive' : 'stopped'}`}>
      <button className="run-row-main" onClick={() => onSelect(run.run_id)}>
        <span className={`run-dot ${run.is_alive ? 'alive' : 'stopped'}`} />
        <span>
          <strong>{run.run_id}</strong>
          <small>{run.data_time || '无数据时间'} / 净值 {money(run.total_asset)} / 持仓 {run.holdings_count || 0}</small>
        </span>
      </button>
      <div className="run-row-actions">
        <button onClick={() => onSelect(run.run_id)} className={selected ? 'active' : ''}>查看</button>
        {run.is_alive ? (
          <button onClick={() => onStop(run)} disabled={!canControl || busy} title={liveLocked ? '实盘控制已锁定' : '关闭该运行'}>
            {run.mode === 'live' ? <ShieldAlert size={12} /> : <Square size={12} />}
            {run.mode === 'live' ? '锁定' : '关闭'}
          </button>
        ) : (
          <button onClick={() => onResume(run)} disabled={!canControl || busy} title={liveLocked ? '实盘控制已锁定' : '恢复该模拟盘'}>
            {run.mode === 'live' ? <ShieldAlert size={12} /> : <Play size={12} />}
            {run.mode === 'live' ? '锁定' : '开启'}
          </button>
        )}
      </div>
    </article>
  );
}

function modeLabel(mode?: string) {
  if (mode === 'paper') return '模拟盘';
  if (mode === 'live') return '实盘';
  if (mode === 'demo') return '演示';
  if (mode === 'agent') return 'Agent任务';
  return mode || '--';
}

export function chooseStockSearchResult(query: string, results: StockSearchItem[]) {
  const normalized = query.trim().toLocaleLowerCase();
  return results.find((item) => item.code === normalized)
    || results.find((item) => item.name?.toLocaleLowerCase() === normalized)
    || (results.length === 1 ? results[0] : undefined);
}

function StockSearch({ selected, onSelect }: {
  selected: string;
  onSelect: (item: StockSearchItem) => void;
}) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<StockSearchItem[]>([]);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);

  function resultLimit(text: string) {
    return /^[\u3400-\u9fff]$/.test(text) ? 100 : 20;
  }

  useEffect(() => {
    const text = query.trim();
    if (!text) {
      setResults([]);
      setMessage('');
      setLoading(false);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      api.stockSearch(text, resultLimit(text))
        .then((data) => {
          if (cancelled) return;
          setResults(data.results || []);
          if (!data.results?.length) setMessage('未找到沪深 A 股');
          else if (resultLimit(text) === 100 && data.results.length === 100) setMessage('单字结果较多，请继续输入或从列表选择');
          else setMessage('');
        })
        .catch((error: Error) => {
          if (cancelled) return;
          setResults([]);
          setMessage(error.message || '搜索失败');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 280);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  function select(item: StockSearchItem) {
    onSelect(item);
    setQuery('');
    setResults([]);
    setMessage('');
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = query.trim();
    if (!text) return;
    setLoading(true);
    try {
      const data = await api.stockSearch(text, resultLimit(text));
      const match = chooseStockSearchResult(text, data.results || []);
      if (match) select(match);
      else if (data.results?.length) {
        setResults(data.results);
        setMessage(`找到 ${data.results.length} 个结果，请从列表选择`);
      } else setMessage('未找到沪深 A 股');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '搜索失败');
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="stock-search">
      <form onSubmit={submit}>
        <Search size={14} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="代码 / 中文名称"
          aria-label="搜索股票代码或中文名称"
          autoComplete="off"
        />
        {loading ? <span className="stock-search-loading">···</span> : null}
      </form>
      {results.length ? (
        <div className="stock-search-results">
          {results.map((item) => (
            <button key={`${item.market}-${item.code}`} className={selected === item.code ? 'selected' : ''} onClick={() => select(item)}>
              <span className="watch-primary">{item.name}</span>
              <small>{item.code} · {item.market.toUpperCase()}</small>
            </button>
          ))}
        </div>
      ) : null}
      {message ? <p>{message}</p> : null}
    </section>
  );
}

function WatchSection({ title, icon: Icon, items, selected, empty, onSelect }: {
  title: string;
  icon: LucideIcon;
  items: WatchlistItem[];
  selected: string;
  empty: string;
  onSelect: (code: string) => void;
}) {
  return (
    <section className="watch-section">
      <h3><Icon size={13} />{title}</h3>
      {items.length === 0 ? <p>{empty}</p> : null}
      {items.map((item) => (
        <button key={item.code} className={selected === item.code ? 'selected' : ''} onClick={() => onSelect(item.code)} title={watchItemTitle(item)}>
          <span className="watch-primary">{item.name || item.code}</span>
          {item.name ? <small>{watchItemMeta(item)}</small> : null}
        </button>
      ))}
    </section>
  );
}

function watchItemTitle(item: WatchlistItem) {
  return item.name ? `${item.name} ${item.code}` : item.code;
}

function watchItemMeta(item: WatchlistItem) {
  return [item.code, item.source].filter(Boolean).join(' · ');
}

function Holdings({ positions }: { positions: Record<string, NonNullable<DashboardState['positions']>[string]> }) {
  const entries = Object.entries(positions);
  if (!entries.length) return <Empty text="暂无持仓" />;
  return (
    <div className="card-grid">
      {entries.map(([code, pos]) => (
        <article className="info-card" key={code}>
          <header><strong>{code}</strong><span>{money(pos.unrealized_pnl)}</span></header>
          <p>持仓 {pos.shares} 股</p>
          <p>成本 {pos.avg_cost}</p>
          <p>现价 {pos.current_price}</p>
          <p>策略 {pos.strategy || '--'}</p>
        </article>
      ))}
    </div>
  );
}

function Ledger({ rows }: { rows: LedgerEntry[] }) {
  if (!rows.length) return <Empty text="暂无成交记录" />;
  return (
    <table className="ledger">
      <thead><tr><th>时间</th><th>类型</th><th>代码</th><th>价格</th><th>数量</th><th>策略</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={row.seq || index}><td>{row.time}</td><td>{row.decision}</td><td>{row.symbol}</td><td>{row.price}</td><td>{row.shares}</td><td>{row.strategy}</td></tr>)}</tbody>
    </table>
  );
}

function Account({ positions, ledger }: {
  positions: Record<string, NonNullable<DashboardState['positions']>[string]>;
  ledger: LedgerEntry[];
}) {
  return (
    <section className="account-board">
      <div className="account-panel">
        <header>
          <strong>持仓</strong>
          <span>{Object.keys(positions).length} 只</span>
        </header>
        <Holdings positions={positions} />
      </div>
      <div className="account-panel">
        <header>
          <strong>成交账本</strong>
          <span>最近 {ledger.length} 条</span>
        </header>
        <Ledger rows={ledger} />
      </div>
    </section>
  );
}

function Logs({ rows }: { rows: string[] }) {
  return <div className="logs">{rows.map((row, index) => <p key={`${row}-${index}`}>{row}</p>)}</div>;
}

function Empty({ text }: { text: string }) {
  return <div className="empty">{text}</div>;
}

function ResizeHandle({ side, width, onResize }: { side: 'left' | 'right'; width: number; onResize: (width: number) => void }) {
  return (
    <div
      className={`resize-handle ${side}`}
      onPointerDown={(event) => {
        const startX = event.clientX;
        const startWidth = width;

        const move = (moveEvent: PointerEvent) => {
          const delta = moveEvent.clientX - startX;
          const next = side === 'left' ? startWidth + delta : startWidth - delta;
          onResize(Math.min(side === 'left' ? 320 : 560, Math.max(side === 'left' ? 150 : 300, next)));
        };
        const done = () => {
          window.removeEventListener('pointermove', move);
          window.removeEventListener('pointerup', done);
        };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', done);
      }}
    />
  );
}
