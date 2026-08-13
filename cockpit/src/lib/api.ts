// Thin fetch wrapper for the existing CostPilot FastAPI backend. Same-origin
// in production (FastAPI serves this app's build at /cockpit); proxied by
// Vite in dev (see vite.config.ts) so no CORS handling is needed anywhere.

export function getWorkspaceId(): string {
  return (
    localStorage.getItem("cp_workspace_id") ||
    new URLSearchParams(window.location.search).get("workspace_id") ||
    ""
  )
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status}`)
  }
  return res.json() as Promise<T>
}

export interface DashboardSummary {
  spend_today_usd: number
  spend_month_usd: number
  total_spend_usd: number
  total_cap_usd: number
  overall_budget_pct: number
  total_calls: number
  routing_efficiency_pct: number
  cost_reduction_pct: number
}

export interface SavingsSummary {
  period_days: number
  total_cost_usd: number
  total_saved_usd: number
  cost_if_no_fage_usd: number
}

export interface BudgetDepartment {
  department: string
  monthly_cap_usd: number
  current_spend_usd: number
  used_pct: number
  state: "healthy" | "warning" | "critical" | string
}

export interface ConnectionHealth {
  workspace_id: string
  overall: number
  categories: Record<string, number>
  recommendations: string[]
}

export function fetchDashboard(workspaceId: string) {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""
  return apiGet<DashboardSummary>(`/api/dashboard${qs}`)
}

export function fetchSavings(workspaceId: string, days = 30) {
  const params = new URLSearchParams({ days: String(days) })
  if (workspaceId) params.set("workspace_id", workspaceId)
  return apiGet<SavingsSummary>(`/api/reports/savings?${params}`)
}

export function fetchBudget(workspaceId: string) {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""
  return apiGet<BudgetDepartment[]>(`/api/budget${qs}`)
}

export function fetchConnectionHealth(workspaceId: string) {
  const qs = `?workspace_id=${encodeURIComponent(workspaceId || "default")}`
  return apiGet<ConnectionHealth>(`/api/integrations/connections/health${qs}`)
}
