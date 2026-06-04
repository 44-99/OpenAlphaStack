import type {
  CacheStatus,
  DashboardState,
  EngineStatus,
  KlineData,
  KlinePeriod,
  KlineStructureAnnotation,
  LedgerEntry,
  PlanData,
  WatchlistItem,
  AgentProvider,
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

export const api = {
  state: () => getJson<DashboardState>('/api/state'),
  plan: () => getJson<PlanData>('/api/plan'),
  ledger: () => getJson<LedgerEntry[]>('/api/ledger?limit=50'),
  ledgerForCode: (code: string, limit = 200) =>
    getJson<LedgerEntry[]>(`/api/ledger?limit=${limit}&code=${code}`),
  engineStatus: () => getJson<EngineStatus>('/api/engine/status'),
  cacheStatus: () => getJson<CacheStatus>('/api/cache/status'),
  watchlist: () => getJson<unknown[]>('/api/watchlist'),
  kline: (code: string, period: KlinePeriod, limit = 200) =>
    getJson<KlineData>(`/api/kline/${code}?period=${period}&limit=${limit}`),
  klineAnnotations: (code: string, period: KlinePeriod) =>
    getJson<{ code: string; period: KlinePeriod; annotations: KlineStructureAnnotation[] }>(
      `/api/kline/${code}/annotations?period=${period}`,
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
  workflowArtifact: (runId: string, eventId: string, name: string) =>
    getJson<{ run_id: string; event_id: string; name: string; content: string }>(
      `/api/workflow/runs/${runId}/artifacts/${eventId}/${name}`,
    ),
  clearKlineCache: () =>
    fetch('/api/cache/kline/clear', { method: 'POST' }).then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || `${response.status}`);
      return data as CacheStatus & { removed_files?: number };
    }),
  streamAgent: async (
    provider: AgentProvider,
    payload: { message: string; session_id: string; context: Record<string, unknown> },
    onChunk: (chunk: string) => void,
  ) => {
    const response = await fetch(`/api/agent/${provider}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data?.error || `${response.status}`);
    }
    if (!response.body) return;

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      onChunk(decoder.decode(value, { stream: true }));
    }
    const tail = decoder.decode();
    if (tail) onChunk(tail);
  },
};

export function normalizeWatchlist(raw: unknown[]): WatchlistItem[] {
  return raw.map((item) => {
    if (typeof item === 'string') return { code: item, change_pct: 0 };
    const obj = item as { symbol?: string; code?: string; change_pct?: number };
    return {
      code: obj.symbol || obj.code || '',
      change_pct: obj.change_pct ?? 0,
    };
  }).filter((item) => item.code);
}
