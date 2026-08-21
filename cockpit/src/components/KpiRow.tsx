import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { DollarSign, PiggyBank, Bot, ShieldCheck, Zap } from "lucide-react"
import type { BudgetDepartment, DashboardSummary, SavingsSummary, ConnectionHealth } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 })

// Pruning savings are often tiny (fractions of a cent) -- the standard
// usd() formatter would round small-but-real values down to "$0.00",
// which reads as "nothing saved" when something genuinely was.
const usdPrecise = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: n < 1 ? 4 : 2 })

// dashboard.overall_budget_pct is a company-wide average across every
// department -- one department blowing past its cap is invisible here if
// the rest are healthy (confirmed live: SIM-HISTORICAL-2Y's Engineering
// at 101.5% didn't move this card at all). Status must be driven by the
// worst individual department, not the blended total, or this card can
// say "On Track" while a real department is over budget with nothing
// capping it. Same used_pct >= 100 guard as Recommendations.tsx and
// operate.html's DEPT HEALTH strip, so all three surfaces agree.
function budgetStatus(
  budget: BudgetDepartment[],
): { label: string; tone: "default" | "secondary" | "destructive"; overCount: number } {
  const overCount = budget.filter((b) => b.used_pct >= 100 || b.throttled).length
  if (overCount > 0) return { label: "At Risk", tone: "destructive", overCount }
  const watchCount = budget.filter((b) => b.state === "warning").length
  if (watchCount > 0) return { label: "Watch", tone: "secondary", overCount: 0 }
  return { label: "On Track", tone: "default", overCount: 0 }
}

export function KpiRow({
  dashboard,
  savings,
  health,
  budget,
}: {
  dashboard: DashboardSummary
  savings: SavingsSummary
  health: ConnectionHealth
  budget: BudgetDepartment[]
}) {
  const status = budgetStatus(budget)

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">Total AI Spend</CardTitle>
          <DollarSign className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{usd(dashboard.spend_month_usd)}</div>
          <p className="mt-1 text-xs text-muted-foreground">{dashboard.total_calls.toLocaleString()} calls this month</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">Total Savings ({savings.period_days}d)</CardTitle>
          <PiggyBank className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{usd(savings.total_saved_usd)}</div>
          <p className="mt-1 text-xs text-muted-foreground">vs {usd(savings.cost_if_no_fage_usd)} without routing</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">Tokens Saved</CardTitle>
          <Zap className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{dashboard.tokens_saved_total.toLocaleString()}</div>
          <p className="mt-1 text-xs text-muted-foreground">
            ≈ {usdPrecise(dashboard.pruning_savings_usd)} via pruning &amp; optimization
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">Active Agents</CardTitle>
          <Bot className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{dashboard.agents_active}</div>
          <p className="mt-1 text-xs text-muted-foreground">{dashboard.agents_total} total, {dashboard.agents_idle} idle</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">Budget Status</CardTitle>
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <Badge variant={status.tone} className="text-sm">{status.label}</Badge>
          <p className="mt-2 text-xs text-muted-foreground">
            {status.overCount > 0
              ? `${status.overCount} department${status.overCount === 1 ? "" : "s"} over budget cap`
              : `${usd(dashboard.total_spend_usd)} of ${usd(dashboard.total_cap_usd)} cap · Setup health ${Math.round(health.overall)}%`}
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
