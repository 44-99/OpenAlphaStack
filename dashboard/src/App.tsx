import { type CSSProperties, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  BookOpenCheck,
  BriefcaseBusiness,
  ClipboardList,
  ListChecks,
  PanelLeftClose,
  PanelLeftOpen,
  Radar,
  type LucideIcon,
} from 'lucide-react';
import { api, normalizeWatchlist } from './api';
import { AgentPanel } from './components/AgentPanel';
import { KlineChart } from './components/KlineChart';
import type {
  CacheStatus,
  DashboardState,
  EngineStatus,
  KlinePeriod,
  LedgerEntry,
  OverlayKind,
  PageKey,
  PlanData,
  WatchlistItem,
} from './types';

const periods: Array<{ key: KlinePeriod; label: string }> = [
  { key: 'day', label: '日K' },
  { key: 'week', label: '周K' },
  { key: '1m', label: '1分' },
  { key: '5m', label: '5分' },
  { key: '15m', label: '15分' },
  { key: '60m', label: '60分' },
];

const overlays: OverlayKind[] = ['MA', 'EMA', 'BOLL'];
const pageItems: Array<{ key: PageKey; label: string; Icon: LucideIcon }> = [
  { key: 'watch', label: '盯盘', Icon: Radar },
  { key: 'holdings', label: '持仓', Icon: BriefcaseBusiness },
  { key: 'plan', label: '计划', Icon: ClipboardList },
  { key: 'ledger', label: '成交', Icon: ListChecks },
  { key: 'logs', label: '日志', Icon: Activity },
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
  const [selectedCode, setSelectedCode] = useState('000001');
  const [period, setPeriod] = useState<KlinePeriod>('day');
  const [overlay, setOverlay] = useState<OverlayKind>('MA');
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
  const [events, setEvents] = useState<string[]>(['AlphaClaude React Dashboard started']);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [leftWidth, setLeftWidth] = useState(190);
  const [rightWidth, setRightWidth] = useState(380);

  const aiWatchlist = useMemo(() => {
    const map = new Map<string, WatchlistItem>();
    (plan.buy_candidates || []).forEach((candidate) => {
      if (candidate.code) map.set(candidate.code, { code: candidate.code, source: candidate.strategy_type || '候选' });
    });
    Object.entries(positions || {}).forEach(([code, pos]) => {
      map.set(code, { code, source: pos.strategy || '持仓' });
    });
    return [...map.values()];
  }, [plan.buy_candidates, positions]);

  const userWatchlist = useMemo(() => {
    const ai = new Set(aiWatchlist.map((item) => item.code));
    return watchlist.filter((item) => !ai.has(item.code));
  }, [aiWatchlist, watchlist]);

  useEffect(() => {
    Promise.allSettled([
      api.state().then((data) => {
        setState((current) => ({ ...current, ...data }));
        setPositions(data.positions || {});
      }),
      api.plan().then(setPlan),
      api.ledger().then(setLedger),
      api.engineStatus().then(setEngine),
      api.cacheStatus().then(setCache),
      api.watchlist().then((raw) => setWatchlist(normalizeWatchlist(raw))),
    ]);
  }, []);

  useEffect(() => {
    const first = aiWatchlist[0]?.code || userWatchlist[0]?.code;
    if (selectedCode === '000001' && first) setSelectedCode(first);
  }, [aiWatchlist, selectedCode, userWatchlist]);

  useEffect(() => {
    const source = new EventSource('/api/stream');
    source.addEventListener('nav', (event) => {
      const data = JSON.parse(event.data) as DashboardState;
      setState((current) => ({ ...current, ...data }));
      setPositions(data.positions || {});
    });
    source.addEventListener('trade', (event) => {
      const data = JSON.parse(event.data) as LedgerEntry;
      setLedger((current) => [data, ...current].slice(0, 200));
      setEvents((current) => [`成交 ${data.symbol || data.code || ''} @ ${data.price || ''}`, ...current].slice(0, 100));
    });
    source.addEventListener('plan_updated', (event) => {
      setPlan((current) => ({ ...current, ...JSON.parse(event.data) }));
    });
    source.addEventListener('connected', () => {
      setEvents((current) => ['SSE connected', ...current].slice(0, 100));
    });
    source.onerror = () => {
      setEvents((current) => ['SSE disconnected', ...current].slice(0, 100));
      source.close();
    };
    return () => source.close();
  }, []);

  const cacheLayer = cache.kline_cache || cache.minute_cache;

  async function clearCache() {
    if (!confirm('清空本地K线缓存？交易数据、自选股和新闻缓存不会删除。')) return;
    const result = await api.clearKlineCache();
    setCache(result);
    setEvents((current) => [`已清理K线缓存 ${result.removed_files || 0} 个文件`, ...current]);
  }

  return (
    <div
      className={`terminal-app ${leftCollapsed ? 'left-collapsed' : ''} ${rightCollapsed ? 'right-collapsed' : ''}`}
      style={{
        '--left-width': `${leftCollapsed ? 64 : leftWidth}px`,
        '--right-width': `${rightCollapsed ? 54 : rightWidth}px`,
      } as CSSProperties}
    >
      <header className="topbar">
        <div className="brand">AlphaClaude</div>
        <Stat label="总资产" value={money(state.total_asset)} />
        <Stat label="现金" value={money(state.cash)} />
        <Stat label="持仓" value={money(state.position_value)} />
        <Stat label="当日" value={pnl(state.day_pnl, state.day_return_pct)} tone={Number(state.day_pnl) >= 0 ? 'up' : 'down'} />
        <div className="top-spacer" />
        <Stat label="K线缓存" value={`${Number(cacheLayer?.mb || 0).toFixed(2)} MB / ${cacheLayer?.files || 0} 个`} />
        <button className="ghost-button" onClick={clearCache}>清缓存</button>
        <span className={`engine-pill ${engine.observation_mode ? 'warn' : ''}`}>{engine.status || '--'}</span>
      </header>

      <aside className="sidebar">
        <button className="sidebar-toggle" onClick={() => setLeftCollapsed((value) => !value)} title={leftCollapsed ? '展开侧边栏' : '收起侧边栏'}>
          {leftCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          {!leftCollapsed ? <span>Navigator</span> : null}
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
            <WatchSection title="AI 盯盘" icon={BookOpenCheck} items={aiWatchlist} selected={selectedCode} onSelect={setSelectedCode} empty="暂无盯盘标的" />
            <WatchSection title="我的自选" icon={Radar} items={userWatchlist} selected={selectedCode} onSelect={setSelectedCode} empty="飞书 /portfolio 管理" />
          </>
        ) : null}
      </aside>
      {!leftCollapsed ? <ResizeHandle side="left" width={leftWidth} onResize={setLeftWidth} /> : null}

      <main className="workspace">
        {page === 'watch' ? (
          <>
            <KlineChart code={selectedCode} period={period} overlay={overlay} />
            <div className="control-strip">
              {periods.map((item) => (
                <button key={item.key} className={period === item.key ? 'active' : ''} onClick={() => setPeriod(item.key)}>
                  {item.label}
                </button>
              ))}
              <span className="divider" />
              {overlays.map((item) => (
                <button key={item} className={overlay === item ? 'active' : ''} onClick={() => setOverlay(item)}>
                  {item}
                </button>
              ))}
              <button className="active" disabled>VOL</button>
            </div>
          </>
        ) : null}
        {page === 'holdings' ? <Holdings positions={positions || {}} /> : null}
        {page === 'plan' ? <Plan plan={plan} /> : null}
        {page === 'ledger' ? <Ledger rows={ledger} /> : null}
        {page === 'logs' ? <Logs rows={events} /> : null}
      </main>
      {!rightCollapsed ? <ResizeHandle side="right" width={rightWidth} onResize={setRightWidth} /> : null}
      <AgentPanel
        collapsed={rightCollapsed}
        selectedCode={selectedCode}
        period={period}
        overlay={overlay}
        onToggle={() => setRightCollapsed((value) => !value)}
      />
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return <div className="stat"><span>{label}</span><strong className={tone || ''}>{value}</strong></div>;
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
        <button key={item.code} className={selected === item.code ? 'selected' : ''} onClick={() => onSelect(item.code)}>
          <span>{item.code}</span>
        </button>
      ))}
    </section>
  );
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

function Plan({ plan }: { plan: PlanData }) {
  if (!plan.market_bias) return <Empty text="暂无计划数据" />;
  return (
    <div className="plan-board">
      <article><h3>I 市场方向</h3><p>{plan.market_bias} / {plan.bias_confidence || 0}%</p><small>{plan.bias_reasoning}</small></article>
      <article><h3>II 候选标的</h3>{(plan.buy_candidates || []).map((item) => <p key={item.code}>{item.code} {item.strategy_type} {item.entry_min}-{item.entry_max}</p>)}</article>
      <article><h3>III 风控</h3><p>单仓 {plan.rules?.max_single_position_pct || 25}%</p><p>总仓 {plan.rules?.max_total_position_pct || 80}%</p></article>
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
