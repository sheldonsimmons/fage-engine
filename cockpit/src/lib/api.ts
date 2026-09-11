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
  // Real MTD spend from departments with no configured DepartmentBudget
  // row yet -- included in total_spend_usd (which is now the same total
  // as spend_month_usd) but excluded from overall_budget_pct's
  // numerator, since cap utilization can't be measured against a cap
  // that doesn't exist for that department.
  unbudgeted_spend_usd: number
  overall_budget_pct: number
  total_calls: number
  routing_efficiency_pct: number
  cost_reduction_pct: number
  agents_total: number
  agents_active: number
  agents_idle: number
  tokens_saved_total: number
  // Estimate: tokens_saved_total priced at the Advisor-tier input rate --
  // pruning saves against whatever model the call was actually using, so
  // this is a documented approximation, not an exact accounting figure.
  pruning_savings_usd: number
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
  // Real values from core/budget.py's _enrich(): "throttled" means live
  // enforcement is actively limiting this department, not just a display
  // warning -- there is no "critical"/"over" state in the backend, so a
  // department can be past 100% of used_pct while state is still
  // "warning" (enforcement never fired for demo/simulated workspaces).
  // Callers that need to detect that must check used_pct >= 100
  // separately, not rely on state alone -- see Recommendations.tsx.
  state: "healthy" | "warning" | "throttled" | string
  throttled: boolean
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

export interface BusinessImpact {
  has_outcome_data: boolean
  opportunities_won: number
  opportunities_lost: number
  opportunities_open: number
  pipeline_value_usd: number
  closed_won_value_usd: number
  support_cases_total: number
  support_cases_resolved: number
  ai_spend_usd: number
  ai_tokens_total: number
  // Coverage/evidence, both scoped to the whole workspace (no account
  // name filter -- see routes_dashboard.py's get_business_impact()).
  outcome_coverage_pct: number | null
  successful_outcomes: number
  evidence_label: "early_signal" | "meaningful" | "executive_eligible"
  cost_per_successful_outcome_usd: number | null
  cost_per_won_opportunity_usd: number | null
  ai_investment_on_lost_opportunities_usd: number | null
  avg_ai_investment_per_opportunity_usd: number | null
  support_cost_per_resolution_usd: number | null
  // 30d-vs-prior-30d trend for the four ratios above -- null (not 0)
  // whenever a window's denominator is empty, never a fabricated trend.
  trend_pct_change: {
    cost_per_won_opportunity_usd: number | null
    ai_investment_on_lost_opportunities_usd: number | null
    avg_ai_investment_per_opportunity_usd: number | null
    support_cost_per_resolution_usd: number | null
  }
  potential_savings_usd: number | null
  potential_savings_evidence: "insufficient_data" | "early_signal" | "estimated"
  potential_savings_candidate_count: number
  potential_savings_top_agents: { agent_id: number | null; agent_name: string; potential_savings_usd: number }[]
  potential_savings_note: string
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

export function fetchBusinessImpact(workspaceId: string) {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""
  return apiGet<BusinessImpact>(`/api/dashboard/business-impact${qs}`)
}

export function fetchConnectionHealth(workspaceId: string) {
  const qs = `?workspace_id=${encodeURIComponent(workspaceId || "default")}`
  return apiGet<ConnectionHealth>(`/api/integrations/connections/health${qs}`)
}

// One shared contract every Recommendations engine detector returns
// (core/recommendations.py) -- deterministic, not LLM-generated. The
// frontend renders one shape regardless of which detector produced it.
export interface Recommendation {
  id: string
  recommendation_type: string
  title: string
  why_it_matters: string
  evidence: string
  current_state: string
  recommended_action: string
  estimated_impact: number | null
  impact_type: "savings_usd" | "risk_usd" | "none"
  confidence: "measured" | "estimated" | "associated" | "early_signal"
  priority: "critical" | "high" | "medium" | "low"
  affected_count: number
  examples?: string[]
  affected_agent: string | null
  affected_department: string | null
  affected_work_item: string | null
  source_metrics: Record<string, unknown>
  generated_at: string
}

export function fetchRecommendations(workspaceId: string) {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""
  return apiGet<{ workspace_id: string | null; recommendations: Recommendation[] }>(
    `/api/dashboard/recommendations${qs}`
  )
}

// GET /api/dashboard/work-outcomes -- top projects (WorkItem) and top
// customers (WorkAccount, real account names -- there is no
// customer-segment/tier field in the schema) ranked by AI spend, plus the
// same real business-outcome totals get_business_impact() already
// computes. See routes_dashboard.py's get_work_outcomes() docstring.
export interface WorkOutcomeRow {
  name: string
  spend_usd: number
}

export interface BusinessOutcomeRow {
  name: string
  value_usd: number | null
  count?: number
  count_total?: number
}

export interface WorkOutcomes {
  workspace_id: string | null
  by_project: WorkOutcomeRow[]
  by_customer: WorkOutcomeRow[]
  business_outcomes: BusinessOutcomeRow[]
}

export function fetchWorkOutcomes(workspaceId: string) {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""
  return apiGet<WorkOutcomes>(`/api/dashboard/work-outcomes${qs}`)
}

// Strips the "WORKSPACE_ID:" prefix legacy department rows carry -- same
// convention core/agentlake.py's display_department() applies server-side
// for every other page; duplicated here rather than adding a new endpoint
// just to move one string operation server-side.
export function displayDepartment(raw: string, workspaceId: string): string {
  const prefix = `${workspaceId}:`
  return raw.startsWith(prefix) ? raw.slice(prefix.length) : raw
}

// voiceMeta tags a question that came from the mic, exactly matching
// global-nav.js's normalizedAskPayload() convention (modality="voice" +
// transcription_confidence) -- Ask CostPilot itself doesn't change
// behavior for a voice-originated question, this is purely so the
// interaction gets logged accurately.
export async function askCostPilot(
  workspaceId: string,
  question: string,
  voiceMeta?: { confidence: number | null } | null,
): Promise<AskResponse> {
  const body: Record<string, unknown> = { question, workspace_id: workspaceId || undefined }
  if (voiceMeta) {
    body.modality = "voice"
    if (typeof voiceMeta.confidence === "number") body.transcription_confidence = voiceMeta.confidence
  }
  const res = await fetch("/api/reports/bot-efficiency/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}))
    throw new Error(errBody.detail || `Ask CostPilot failed: ${res.status}`)
  }
  return res.json()
}

// CostPilot Voice (Phase 1) -- see backend/api/routes_ask_voice.py's
// module docstring. These two endpoints do ONLY speech-to-text and
// text-to-speech; the transcript still goes through askCostPilot() above
// exactly like a typed question.
export async function transcribeVoiceQuestion(blob: Blob): Promise<{ transcript: string; confidence: number | null }> {
  const form = new FormData()
  form.append("audio", blob, "question.webm")
  const res = await fetch("/api/ask-voice/transcribe", { method: "POST", body: form })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || "Could not transcribe that clip.")
  return { transcript: data.transcript || "", confidence: typeof data.confidence === "number" ? data.confidence : null }
}

export async function speakText(text: string): Promise<Blob> {
  const res = await fetch("/api/ask-voice/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  })
  if (!res.ok) throw new Error("Speech unavailable")
  return res.blob()
}
