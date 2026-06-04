export type PageKey = 'watch' | 'holdings' | 'plan' | 'ledger' | 'logs';
export type WorkbenchMode = 'watch' | 'workflow' | 'review';
export type KlinePeriod = '1m' | '5m' | '15m' | '60m' | 'day' | 'week' | 'month';
export type OverlayKind = 'MA' | 'EMA' | 'BOLL';
export type AgentProvider = 'claude' | 'codex';
export type KlineLayerKey = 'trades' | 'plan' | 'signals' | 'structures';

export interface DashboardState {
  total_asset: number;
  cash: number;
  position_value: number;
  day_pnl: number;
  day_return_pct: number;
  positions?: Record<string, Position>;
  data_time?: string;
}

export interface Position {
  shares: number;
  avg_cost: number;
  current_price: number;
  stop_loss?: number;
  strategy?: string;
  unrealized_pnl?: number;
}

export interface PlanCandidate {
  code: string;
  strategy_type?: string;
  entry_min?: number;
  entry_max?: number;
  stop_loss?: number;
  take_profit?: number;
  valid_until?: string;
  position_pct?: number;
  reason?: string;
  reasoning?: string;
}

export interface PlanData {
  updated?: string;
  updated_by?: string;
  market_bias?: string;
  bias_confidence?: number;
  bias_reasoning?: string;
  buy_candidates?: PlanCandidate[];
  avoid_sectors?: string[];
  rules?: {
    max_single_position_pct?: number;
    max_total_position_pct?: number;
    stop_loss_mode?: string;
  };
}

export interface WatchlistItem {
  code: string;
  source?: string;
  change_pct?: number;
}

export interface LedgerEntry {
  seq?: number;
  time?: string;
  decision?: string;
  action?: string;
  symbol?: string;
  code?: string;
  price?: number;
  shares?: number;
  strategy?: string;
  reasoning?: string;
  stop_loss?: number;
  take_profit?: number;
  avg_cost?: number;
}

export interface CacheStatus {
  kline_cache?: {
    files: number;
    mb: number;
    updated_at?: string;
  };
  minute_cache?: {
    files: number;
    mb: number;
    updated_at?: string;
  };
}

export interface EngineStatus {
  status?: string;
  observation_mode?: boolean;
  observation_reason?: string;
  has_plan?: boolean;
  data_time?: string;
}

export interface KlineData {
  code: string;
  source?: string;
  dates: string[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  volume: number[];
}

export interface KlineTradeMarker {
  time: string;
  code: string;
  action: string;
  price: number;
  shares?: number;
  strategy?: string;
  reasoning?: string;
  stop_loss?: number;
  take_profit?: number;
  avg_cost?: number;
}

export interface KlinePlanAnnotation {
  code: string;
  entry_min?: number;
  entry_max?: number;
  stop_loss?: number;
  take_profit?: number;
  valid_until?: string;
  position_pct?: number;
  strategy?: string;
  reasoning?: string;
  plan_updated?: string;
  stale_reason?: string;
  is_stale?: boolean;
}

export interface KlineTechnicalSignal {
  time: string;
  price: number;
  kind: 'ma_golden_cross' | 'ma_death_cross' | 'volume_breakout' | 'boll_upper_touch' | 'boll_lower_touch';
  label: string;
  detail: string;
  tone: 'up' | 'down' | 'neutral';
}

export interface WorkflowEvent {
  event_id: string;
  run_id: string;
  phase: string;
  node_id: string;
  node_name: string;
  status: string;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number;
  input_refs?: string[];
  output_refs?: string[];
  summary?: string;
  error?: string;
  artifact_dir?: string;
}

export interface WorkflowGraphNode {
  id: string;
  name: string;
  enabled: boolean;
  locked: boolean;
  status: string;
  summary?: string;
  last_event_id?: string;
  phase?: string;
}

export interface WorkflowGraphEdge {
  from: string;
  to: string;
}

export interface WorkflowGraph {
  run_id: string;
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
}
