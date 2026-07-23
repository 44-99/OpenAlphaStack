export type PageKey = 'watch' | 'workflow' | 'account';
export type RunMode = 'paper' | 'live' | 'demo' | 'agent';
export type KlinePeriod = '1m' | '5m' | '15m' | '60m' | 'day' | 'week' | 'month';
export type OverlayKind = 'NONE' | 'MA' | 'EMA' | 'BOLL';
export type KlineLayerKey = 'trades' | 'plan' | 'signals' | 'structures';

export interface DashboardState {
  run_id?: string;
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
  name?: string;
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
  name?: string;
  source?: string;
  change_pct?: number;
}

export interface LedgerEntry {
  run_id?: string;
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
  run_id?: string;
  status?: string;
  is_alive?: boolean;
  process_id?: number | null;
  observation_mode?: boolean;
  observation_reason?: string;
  has_plan?: boolean;
  data_time?: string;
}

export interface RunRecord {
  run_id: string;
  mode: RunMode | string;
  status: string;
  is_alive: boolean;
  process_id?: number | null;
  data_time?: string;
  total_asset?: number;
  cash?: number;
  position_value?: number;
  trade_count?: number;
  holdings_count?: number;
  has_plan?: boolean;
  live_locked?: boolean;
}

export interface RunsResponse {
  runs: RunRecord[];
  selected_run_id?: string;
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

export type KlineStructureKind = 'level' | 'range' | 'trendline' | 'segment' | 'wave' | 'point';
export type KlineStructureTone = 'up' | 'down' | 'neutral' | 'warning';

export interface KlineStructureAnnotation {
  id: string;
  code: string;
  period?: KlinePeriod | 'all';
  kind: KlineStructureKind;
  label: string;
  tone: KlineStructureTone;
  price?: number;
  price_min?: number;
  price_max?: number;
  start_time?: string;
  end_time?: string;
  points?: Array<{ time: string; price: number; label?: string }>;
  source?: {
    event_id?: string;
    node_id?: string;
    skill?: string;
    confidence?: number;
    summary?: string;
  };
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

export interface AgentTimelineEvent {
  event_id: string;
  task_id: string;
  parent_task_id?: string;
  role?: string;
  status: string;
  started_at?: string;
  ended_at?: string;
  summary?: string;
  input_ref?: string;
  output_ref?: string;
  result_ref?: string;
  error?: string;
}

export interface AgentTimelineTask {
  task_id: string;
  parent_task_id?: string;
  role?: string;
  status: string;
  summary?: string;
  input_ref?: string;
  output_ref?: string;
  result_ref?: string;
  events?: AgentTimelineEvent[];
}

export interface AgentRunTimeline {
  run_id: string;
  task_id: string;
  events: AgentTimelineEvent[];
  tasks: Record<string, AgentTimelineTask>;
  warnings: string[];
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
  started_at?: string;
  ended_at?: string;
  duration_ms?: number;
  input_refs?: string[];
  output_refs?: string[];
  artifact_dir?: string;
}

export interface WorkflowGraphEdge {
  from: string;
  to: string;
  kind?: 'data' | 'sequence';
  label?: string;
  refs?: string[];
  required?: boolean;
}

export interface WorkflowGraph {
  run_id: string;
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
  run_status?: string;
  is_alive?: boolean;
  process_id?: number | null;
  data_time?: string;
  observation_mode?: boolean;
  observation_reason?: string;
  calendar_date?: string;
  display_date?: string;
  is_trading_day?: boolean;
  market_status?: 'trading' | 'closed' | 'stale';
  market_message?: string;
}

export interface WorkflowConfigNode {
  enabled: boolean;
  locked: boolean;
  params: Record<string, number | string | boolean>;
}

export interface WorkflowConfig {
  version: number;
  nodes: Record<string, WorkflowConfigNode>;
  updated_at?: string;
}
