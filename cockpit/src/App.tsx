import { useEffect, useState } from "react"
import { AppSidebar } from "@/components/AppSidebar"
import { KpiRow } from "@/components/KpiRow"
import { SpendOverTime } from "@/components/SpendOverTime"
import { SpendByDepartment } from "@/components/SpendByDepartment"
import { TopModels } from "@/components/TopModels"
import { Recommendations } from "@/components/Recommendations"
import { ComingSoon } from "@/components/ComingSoon"
import { WhatChanged } from "@/components/WhatChanged"
import { AskCostPilot } from "@/components/AskCostPilot"
import { Skeleton } from "@/components/ui/skeleton"
import {
  fetchBudget,
  fetchConnectionHealth,
  fetchDashboard,
  fetchDashboardChanges,
  fetchSavings,
  fetchTopModels,
  getWorkspaceId,
  type BudgetDepartment,
  type ConnectionHealth,
  type DashboardChanges,
  type DashboardSummary,
  type SavingsSummary,
  type TopModelsResponse,
} from "@/lib/api"

interface CockpitData {
  dashboard: DashboardSummary
  savings: SavingsSummary
  budget: BudgetDepartment[]
  health: ConnectionHealth
  changes: DashboardChanges
  topModels: TopModelsResponse
}

function App() {
  const [data, setData] = useState<CockpitData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const workspaceId = getWorkspaceId()

  useEffect(() => {
    Promise.all([
      fetchDashboard(workspaceId),
      fetchSavings(workspaceId, 30),
      fetchBudget(workspaceId),
      fetchConnectionHealth(workspaceId),
      fetchDashboardChanges(workspaceId, 30),
      fetchTopModels(workspaceId, 30, 5),
    ])
      .then(([dashboard, savings, budget, health, changes, topModels]) =>
        setData({ dashboard, savings, budget, health, changes, topModels })
      )
      .catch((err: Error) => setError(err.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="dark flex min-h-screen bg-background text-foreground">
      <AppSidebar />
      <div className="flex-1 overflow-x-hidden">
        <header className="border-b border-border px-8 py-6">
          <h1 className="text-2xl font-semibold tracking-tight">Executive Dashboard</h1>
          <p className="mt-1 text-sm text-muted-foreground">AI spend, usage, and optimization overview</p>
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
              <KpiRow dashboard={data.dashboard} savings={data.savings} health={data.health} />

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
                <SpendOverTime timeline={data.savings.timeline} />
                <SpendByDepartment budget={data.budget} workspaceId={workspaceId} />
                <TopModels models={data.topModels.models} />
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <WhatChanged periodDays={data.changes.period_days} changes={data.changes.changes} />
                <ComingSoon
                  title="Business Impact"
                  reason="Needs a real link between AI activity and business outcomes (pipeline, cases resolved, etc.) beyond what's tracked today."
                />
                <Recommendations items={data.health.recommendations} budget={data.budget} workspaceId={workspaceId} />
              </div>

              <div id="ask-costpilot">
                <AskCostPilot workspaceId={workspaceId} />
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  )
}

export default App
