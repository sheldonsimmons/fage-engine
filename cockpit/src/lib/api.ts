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
  agents_total: number
  agents_active: number
  agents_idle: number
}

export interface SavingsTimelinePoint {
  date: string
  cost: number
  calls: number
}

export interface SavingsSummary {
  period_days: number
  total_cost_usd: number
  total_saved_usd: number
  cost_if_no_fage_usd: number
  timeline: SavingsTimelinePoint[]
}

export interface BudgetDepartment {
  department: string
  monthly_cap_usd: number
  current_spend_usd: number
  used_pct: number
  // Real values from core/budget.py's compute_state(): "throttled" means
  // enforcement is actively limiting this department, not just a display
  // warning -- there is no "critical" state in the backend.
  state: "healthy" | "warning" | "throttled" | string
}

export interface ConnectionHealth {
  workspace_id: string
  overall: number
  categories: Record<string, number>
  recommendations: string[]
}

export interface AskResponse {
  title: string
  answer: string
}

export interface DashboardChange {
  metric: string
  label: string
  current: number
  previous: number
  pct_change: number | null
  summary: string
}

export interface DashboardChanges {
  period_days: number
  changes: DashboardChange[]
}

export interface TopModel {
  model: string
  is_tier_only: boolean
  spend_usd: number
  calls: number
  pct_of_total: number
}

export interface TopModelsResponse {
  total_spend_usd: number
  models: TopModel[]
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

export function fetchDashboardChanges(workspaceId: string, days = 30) {
  const params = new URLSearchParams({ days: String(days) })
  if (workspaceId) params.set("workspace_id", workspaceId)
  return apiGet<DashboardChanges>(`/api/dashboard/changes?${params}`)
}

export function fetchTopModels(workspaceId: string, days = 30, limit = 5) {
  const params = new URLSearchParams({ days: String(days), limit: String(limit) })
  if (workspaceId) params.set("workspace_id", workspaceId)
  return apiGet<TopModelsResponse>(`/api/dashboard/top-models?${params}`)
}

export function fetchConnectionHealth(workspaceId: string) {
  const qs = `?workspace_id=${encodeURIComponent(workspaceId || "default")}`
  return apiGet<ConnectionHealth>(`/api/integrations/connections/health${qs}`)
}

// Strips the "WORKSPACE_ID:" prefix legacy department rows carry -- same
// convention core/agentlake.py's display_department() applies server-side
// for every other page; duplicated here rather than adding a new endpoint
// just to move one string operation server-side.
export function displayDepartment(raw: string, workspaceId: string): string {
  const prefix = `${workspaceId}:`
  return raw.startsWith(prefix) ? raw.slice(prefix.length) : raw
}

export async function askCostPilot(workspaceId: string, question: string): Promise<AskResponse> {
  const res = await fetch("/api/reports/bot-efficiency/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, workspace_id: workspaceId || undefined }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Ask CostPilot failed: ${res.status}`)
  }
  return res.json()
}
