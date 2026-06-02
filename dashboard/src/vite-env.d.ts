/// <reference types="vite/client" />

interface Window {
  __DATA__?: InitialDashboardData;
}

interface InitialDashboardData {
  has_active_run?: boolean;
  run_id?: string;
  state?: Partial<DashboardState>;
  plan_summary?: Partial<PlanData>;
}
