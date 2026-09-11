import { useEffect, useState } from "react"
import { AppSidebar } from "@/components/AppSidebar"
import { KpiRow } from "@/components/KpiRow"
import { ExecutiveBrief } from "@/components/ExecutiveBrief"
import { SpendOverTime } from "@/components/SpendOverTime"
import { SpendByDepartment } from "@/components/SpendByDepartment"
import { TopModels } from "@/components/TopModels"
import { Recommendations } from "@/components/Recommendations"
import { BusinessImpact } from "@/components/BusinessImpact"
import { WhatChanged } from "@/components/WhatChanged"
import { AskCostPilot } from "@/components/AskCostPilot"
import { PriorityInsights } from "@/components/PriorityInsights"
import { RecommendationHero, topRecommendation } from "@/components/RecommendationHero"
import { WorkAndOutcomes } from "@/components/WorkAndOutcomes"
import { Skeleton } from "@/components/ui/skeleton"
import {
  fetchBudget,
  fetchBusinessImpact,
  fetchConnectionHealth,
  fetchDashboard,
  fetchDashboardChanges,
  fetchRecommendations,
  fetchSavings,
  fetchTopModels,
  fetchWorkOutcomes,
  getWorkspaceId,
  type BudgetDepartment,
  type BusinessImpact as BusinessImpactData,
  type ConnectionHealth,
  type DashboardChanges,
  type DashboardSummary,
  type Recommendation,
  type SavingsSummary,
  type TopModelsResponse,
  type WorkOutcomes,
} from "@/lib/api"

interface CockpitData {
  dashboard: DashboardSummary
  savings: SavingsSummary
  budget: BudgetDepartment[]
  health: ConnectionHealth
  changes: DashboardChanges
  topModels: TopModelsResponse
  businessImpact: BusinessImpactData
  recommendations: Recommendation[]
  workOutcomes: WorkOutcomes
}

function App() {
  const [data, setData] = useState<CockpitData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pendingQuestion, setPendingQuestion] = useState<{ text: string; nonce: number } | undefined>()
  const workspaceId = getWorkspaceId()

  useEffect(() => {
    Promise.all([
      fetchDashboard(workspaceId),
      fetchSavings(workspaceId, 30),
      fetchBudget(workspaceId),
      fetchConnectionHealth(workspaceId),
      fetchDashboardChanges(workspaceId, 30),
      fetchTopModels(workspaceId, 30, 5),
      fetchBusinessImpact(workspaceId),
      fetchRecommendations(workspaceId),
      fetchWorkOutcomes(workspaceId),
    ])
      .then(([dashboard, savings, budget, health, changes, topModels, businessImpact, recommendationsResponse, workOutcomes]) =>
        setData({
          dashboard, savings, budget, health, changes, topModels, businessImpact,
          recommendations: recommendationsResponse.recommendations,
          workOutcomes,
        })
      )
      .catch((err: Error) => setError(err.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Loading /cockpit/#ask-costpilot directly (a bookmark, a sidebar link
  // typed fresh, or the Ask CostPilot sidebar item's own href) hits the
  // browser's native anchor-scroll before this section has rendered --
  // it only exists inside `{data && (...)}` below, so on first paint the
  // element genuinely isn't in the DOM yet and the browser's scroll is a
  // silent no-op. Once `data` arrives and the element exists, do the
  // scroll ourselves.
  useEffect(() => {
    if (data && window.location.hash === "#ask-costpilot") {
      document.getElementById("ask-costpilot")?.scrollIntoView({ behavior: "smooth" })
    }
  }, [data])

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <AppSidebar />
      <div className="flex-1 overflow-x-hidden">
        <header className="border-b border-border px-8 py-6">
          <h1 className="text-2xl font-semibold tracking-tight">Executive Dashboard</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Enterprise AI economics, business impact, and optimization
            {/* Active Agents moved out of the top KPI row (executive
                feedback: it doesn't warrant premium scorecard space next
                to dollar figures) -- kept visible here rather than
                dropped, pending a dedicated Agent Portfolio section. */}
            {data && ` · ${data.dashboard.agents_active} of ${data.dashboard.agents_total} agents active`}
          </p>
        </header>

        <main className="space-y-6 px-8 py-6">
          {error && (
            <div className="rounded-md border border-destructive/50 p-4 text-sm text-destructive">
              Could not load the dashboard: {error}
            </div>
          )}

          {!data && !error && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-28 w-full" />
                ))}
              </div>
              <Skeleton className="h-64 w-full" />
            </div>
          )}

          {data && (
            <>
              <ExecutiveBrief
                dashboard={data.dashboard}
                savings={data.savings}
                budget={data.budget}
                businessImpact={data.businessImpact}
                workspaceId={workspaceId}
              />
              <KpiRow dashboard={data.dashboard} savings={data.savings} health={data.health} budget={data.budget} businessImpact={data.businessImpact} />

              {/* Ask CostPilot sits right after the glance-value Brief/KPIs
                  (not above them -- those are shaped by direct executive
                  feedback, see KpiRow's own ordering comment) and above the
                  charts/recommendations below, per user decision. Priority
                  Insights sits alongside it (a curated top-3 view of the
                  same real /api/dashboard/recommendations data the full
                  Recommendations list below also renders in full) --
                  see PriorityInsights.tsx for the bucketing. */}
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <div className="space-y-4 lg:col-span-2">
                  <div id="ask-costpilot">
                    <AskCostPilot workspaceId={workspaceId} pendingQuestion={pendingQuestion} />
                  </div>
                  {(() => {
                    const rec = topRecommendation(data.recommendations)
                    return rec ? (
                      <RecommendationHero
                        recommendation={rec}
                        onAskAboutIt={(text) => setPendingQuestion({ text, nonce: Date.now() })}
                      />
                    ) : null
                  })()}
                </div>
                <PriorityInsights recommendations={data.recommendations} />
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
                <SpendOverTime timeline={data.savings.timeline} />
                <SpendByDepartment budget={data.budget} workspaceId={workspaceId} />
                <TopModels models={data.topModels.models} />
              </div>

              <WorkAndOutcomes
                byProject={data.workOutcomes.by_project}
                byCustomer={data.workOutcomes.by_customer}
                businessOutcomes={data.workOutcomes.business_outcomes}
              />

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <WhatChanged periodDays={data.changes.period_days} changes={data.changes.changes} />
                <BusinessImpact data={data.businessImpact} />
                <Recommendations recommendations={data.recommendations} />
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  )
}

export default App
