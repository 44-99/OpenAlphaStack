import type {
  CacheStatus,
  DashboardState,
  EngineStatus,
  KlineData,
  KlinePeriod,
  KlineStructureAnnotation,
  LedgerEntry,
  PlanData,
  RunsResponse,
  WatchlistItem,
  AgentRunTimeline,
  WorkflowConfig,
  WorkflowEvent,
  WorkflowGraph,
} from './types';

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof data?.error === 'string' ? data.error : `${response.status}`;
    throw new Error(message);
  }
  return data as T;
}

function runQuery(runId?: string) {
  return runId ? `run_id=${encodeURIComponent(runId)}` : '';
}

function encodePath(path: string) {
  return path.split('/').map((part) => encodeURIComponent(part)).join('/');
}

export const api = {
  runs: (mode = 'all') => getJson<RunsResponse>(`/api/runs?mode=${encodeURIComponent(mode)}`),
  startRun: (mode: 'paper' | 'live') =>
    fetch('/api/runs/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || `${response.status}`);
      return data as { run: { run_id: string; existing?: boolean } };
    }),
  resumeRun: (runId: string) =>
    fetch(`/api/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST' }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || `${response.status}`);
      return data as { run: { run_id: string; existing?: boolean } };
    }),
  stopRun: (runId: string) =>
    fetch(`/api/runs/${encodeURIComponent(runId)}/stop`, { method: 'POST' }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || `${response.status}`);
      return data as { run: { run_id: string } };
    }),
  state: (runId?: string) => getJson<DashboardState>(`/api/state${runId ? `?${runQuery(runId)}` : ''}`),
  plan: (runId?: string) => getJson<PlanData>(`/api/plan${runId ? `?${runQuery(runId)}` : ''}`),
  ledger: (runId?: string) => getJson<LedgerEntry[]>(`/api/ledger?limit=50${runId ? `&${runQuery(runId)}` : ''}`),
  ledgerForCode: (code: string, limit = 200, runId?: string) =>
    getJson<LedgerEntry[]>(`/api/ledger?limit=${limit}&code=${code}${runId ? `&${runQuery(runId)}` : ''}`),
  engineStatus: (runId?: string) => getJson<EngineStatus>(`/api/engine/status${runId ? `?${runQuery(runId)}` : ''}`),
  cacheStatus: () => getJson<CacheStatus>('/api/cache/status'),
  watchlist: () => getJson<unknown[]>('/api/watchlist'),
  kline: (code: string, period: KlinePeriod, limit = 200) =>
    getJson<KlineData>(`/api/kline/${code}?period=${period}&limit=${limit}`),
  klineAnnotations: (code: string, period: KlinePeriod, runId?: string) =>
    getJson<{ code: string; period: KlinePeriod; annotations: KlineStructureAnnotation[] }>(
      `/api/kline/${code}/annotations?period=${period}${runId ? `&${runQuery(runId)}` : ''}`,
    ),
  workflowEvents: (runId = 'active', limit = 500) =>
    getJson<{ run_id: string; events: WorkflowEvent[] }>(`/api/workflow/runs/${runId}/events?limit=${limit}`),
  workflowGraph: (runId = 'active') =>
    getJson<WorkflowGraph>(`/api/workflow/runs/${runId}/graph`),
  workflowConfig: (runId = 'active') =>
    getJson<WorkflowConfig>(`/api/workflow/runs/${runId}/config`),
  saveWorkflowConfig: (runId: string, config: WorkflowConfig) =>
    fetch(`/api/workflow/runs/${runId}/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || `${response.status}`);
      return data as WorkflowConfig;
    }),
  workflowNodeRerun: (runId: string, nodeId: string) =>
    fetch(`/api/workflow/runs/${runId}/nodes/${nodeId}/rerun`, { method: 'POST' }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || `${response.status}`);
      return data as { run_id: string; request: Record<string, unknown>; event: WorkflowEvent };
    }),
  workflowArtifact: (runId: string, eventId: string, name: string) =>
    getJson<{ run_id: string; event_id: string; name: string; content: string }>(
      `/api/workflow/runs/${runId}/artifacts/${eventId}/${name}`,
    ),
  agentRunTimeline: (runId: string, taskId: string) =>
    getJson<AgentRunTimeline>(
      `/api/workflow/runs/${runId}/agent-runs/${encodeURIComponent(taskId)}/timeline`,
    ),
  agentRunArtifact: (runId: string, taskId: string, artifactRef: string) =>
    getJson<{ run_id: string; task_id: string; artifact_ref: string; content: string }>(
      `/api/workflow/runs/${runId}/agent-runs/${encodeURIComponent(taskId)}/artifacts/${encodePath(artifactRef)}`,
    ),
  clearKlineCache: () =>
    fetch('/api/cache/kline/clear', { method: 'POST' }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || `${response.status}`);
      return data as CacheStatus & { removed_files?: number };
    }),
};

export function normalizeWatchlist(raw: unknown[]): WatchlistItem[] {
  return raw.map((item) => {
    if (typeof item === 'string') return { code: item, change_pct: 0 };
    const obj = item as { symbol?: string; code?: string; name?: string; source?: string; change_pct?: number };
    return {
      code: obj.symbol || obj.code || '',
      name: obj.name || undefined,
      source: obj.source || undefined,
      change_pct: obj.change_pct ?? 0,
    };
  }).filter((item) => item.code);
}
