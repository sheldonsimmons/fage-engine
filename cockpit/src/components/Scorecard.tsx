import { useEffect, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  fetchBudget,
  fetchConnectionHealth,
  fetchDashboard,
  fetchSavings,
  getWorkspaceId,
  type BudgetDepartment,
  type ConnectionHealth,
  type DashboardSummary,
  type SavingsSummary,
} from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 })

// Budget status is derived from overall_budget_pct with fixed thresholds --
// not a value the backend returns directly. Documented here rather than
// buried in JSX so the rule is easy to find and change.
function budgetStatus(pct: number): { label: string; tone: "default" | "secondary" | "destructive" } {
  if (pct >= 90) return { label: "At Risk", tone: "destructive" }
  if (pct >= 70) return { label: "Watch", tone: "secondary" }
  return { label: "On Track", tone: "default" }
}

function healthTone(pct: number): "default" | "secondary" | "destructive" {
  if (pct >= 80) return "default"
  if (pct >= 50) return "secondary"
  return "destructive"
}

interface ScorecardData {
  dashboard: DashboardSummary
  savings: SavingsSummary
  budget: BudgetDepartment[]
  health: ConnectionHealth
}

export function Scorecard() {
  const [data, setData] = useState<ScorecardData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const workspaceId = getWorkspaceId()
    Promise.all([
      fetchDashboard(workspaceId),
      fetchSavings(workspaceId, 30),
      fetchBudget(workspaceId),
      fetchConnectionHealth(workspaceId),
    ])
      .then(([dashboard, savings, budget, health]) => setData({ dashboard, savings, budget, health }))
      .catch((err: Error) => setError(err.message))
  }, [])

  if (error) {
    return (
      <Card className="border-destructive/50">
        <CardContent className="pt-6 text-sm text-destructive">
          Could not load the scorecard: {error}
        </CardContent>
      </Card>
    )
  }

  if (!data) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i}>
            <CardHeader>
              <Skeleton className="h-4 w-24" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-8 w-32" />
            </CardContent>
          </Card>
        ))}
      </div>
    )
  }

  const { dashboard, savings, health } = data
  const status = budgetStatus(dashboard.overall_budget_pct)

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">AI Spend (this month)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{usd(dashboard.spend_month_usd)}</div>
          <p className="mt-1 text-xs text-muted-foreground">{dashboard.total_calls.toLocaleString()} calls tracked</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">Savings ({savings.period_days}d)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{usd(savings.total_saved_usd)}</div>
          <p className="mt-1 text-xs text-muted-foreground">
            vs {usd(savings.cost_if_no_fage_usd)} without routing
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2 flex flex-row items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">Budget</CardTitle>
          <Badge variant={status.tone}>{status.label}</Badge>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{dashboard.overall_budget_pct.toFixed(1)}%</div>
          <p className="mt-1 text-xs text-muted-foreground">
            {usd(dashboard.total_spend_usd)} of {usd(dashboard.total_cap_usd)} cap
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2 flex flex-row items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">Setup Health</CardTitle>
          <Badge variant={healthTone(health.overall)}>{Math.round(health.overall)}%</Badge>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{Math.round(health.overall)}%</div>
          <p className="mt-1 text-xs text-muted-foreground">
            {health.recommendations.length
              ? `${health.recommendations.length} item(s) need attention`
              : "Nothing to flag right now"}
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
