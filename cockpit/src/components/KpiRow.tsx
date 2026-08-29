import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { EvidenceBadge } from "@/components/EvidenceBadge"
import { DollarSign, PiggyBank, ShieldCheck, TrendingUp, TrendingDown, Calculator } from "lucide-react"
import type { BudgetDepartment, DashboardSummary, SavingsSummary, ConnectionHealth, BusinessImpact } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 })

const usdCompact = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 })

// A cost-per-outcome in the fractional-cent range (typical for a young
// or low-volume account) rounds to "$0.00" at 2 decimals -- reads as
// "free" when the number is real, just small. Same fix already applied
// on business-profile.html/work-item-profile.html for the equivalent
// ratio.
const usdAdaptive = (n: number) => (n > 0 && n < 0.01 ? `$${n.toFixed(4)}` : usd(n))

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

// Top-row KPIs, reordered per direct executive feedback: AI Investment ->
// Realized Savings -> Budget Health -> Associated Business Value -> Cost
// per Successful Outcome -> Outcome Coverage. Tokens Saved was dropped
// entirely -- a CEO/CFO doesn't need a token count, and its dollar
// equivalent is already folded into Realized Savings, so nothing is lost
// by removing the technical framing. Active Agents moved out of this row
// too (see WhatChanged/AppSidebar for where agent activity is still
// visible) -- it doesn't earn premium scorecard space next to dollar
// figures that describe investment, value, and risk.
export function KpiRow({
  dashboard,
  savings,
  health,
  budget,
  businessImpact,
}: {
  dashboard: DashboardSummary
  savings: SavingsSummary
  health: ConnectionHealth
  budget: BudgetDepartment[]
  businessImpact: BusinessImpact
}) {
  const status = budgetStatus(budget)

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">AI Investment — Month to Date</CardTitle>
          <DollarSign className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{usd(dashboard.spend_month_usd)}</div>
          <p className="mt-1 text-xs text-muted-foreground">{dashboard.total_calls.toLocaleString()} calls this month</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">Realized Savings ({savings.period_days}d)</CardTitle>
          <PiggyBank className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tabular-nums">{usd(savings.total_saved_usd)}</div>
          <p className="mt-1 text-xs text-muted-foreground">vs {usd(savings.cost_if_no_fage_usd)} without routing</p>
        </CardContent>
      </Card>

      {/* Potential Savings -- omitted (not shown as $0) when there's no
          real candidate data yet, e.g. a workspace whose ROUTINE calls
          already all run at the cheapest tier has genuinely nothing to
          flag here. Evidence-labeled "Estimated": this is what these
          calls could have cost at a cheaper tier, never mixed with
          Realized Savings (money already saved). */}
      {businessImpact.potential_savings_usd !== null && businessImpact.potential_savings_evidence !== "insufficient_data" && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Potential Savings</CardTitle>
            <TrendingDown className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {businessImpact.potential_savings_usd > 0 ? (
              <>
                <div className="text-2xl font-semibold tabular-nums">{usdAdaptive(businessImpact.potential_savings_usd)}</div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {businessImpact.potential_savings_top_agents[0]
                    ? `Top: ${businessImpact.potential_savings_top_agents[0].agent_name}`
                    : `${businessImpact.potential_savings_candidate_count} routine calls above Scout tier`}
                </p>
              </>
            ) : (
              // Zero here means "checked, nothing qualifies" -- distinct
              // from the card being hidden outright (insufficient_data,
              // above), so it shouldn't read as broken or missing data.
              <>
                <div className="text-lg font-semibold text-muted-foreground">No qualified opportunities</div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Current workloads do not meet the threshold for a model right-sizing recommendation.
                </p>
              </>
            )}
            <div className="mt-1.5 flex min-w-0 items-center gap-1.5">
              <EvidenceBadge
                evidence={businessImpact.potential_savings_evidence}
                note={businessImpact.potential_savings_note}
              />
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">Budget Health — Month to Date</CardTitle>
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

      {/* Associated Business Value, Cost per Successful Outcome, and
          Outcome Coverage -- only shown once real outcome data exists
          (has_outcome_data), same "omit rather than show a misleading
          zero" rule those pages already follow -- a workspace with no
          CRM outcome sync shouldn't see a $0 Business Value card. Each
          links through to Business Profile (drill-down, not a dead
          end). */}
      {businessImpact.has_outcome_data && (
        <>
          <a href="/business-profile.html" className="block min-w-0 transition-opacity hover:opacity-80">
            <Card className="h-full">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">Associated Business Value</CardTitle>
                <TrendingUp className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-semibold tabular-nums">{usdCompact(businessImpact.closed_won_value_usd)}</div>
                <div className="mt-1.5 flex min-w-0 items-center gap-1.5">
                  <EvidenceBadge
                    evidence={businessImpact.evidence_label}
                    coveragePct={businessImpact.outcome_coverage_pct}
                    note={`${usdCompact(businessImpact.closed_won_value_usd)} in business value is associated with WorkItems containing tracked AI activity. Outcome coverage is ${businessImpact.outcome_coverage_pct ?? 0}%. Association does not imply causation.`}
                  />
                </div>
              </CardContent>
            </Card>
          </a>

          {businessImpact.cost_per_successful_outcome_usd !== null && (
            <a href="/business-profile.html" className="block min-w-0 transition-opacity hover:opacity-80">
              <Card className="h-full">
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">Cost per Successful Outcome</CardTitle>
                  <Calculator className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-semibold tabular-nums">
                    {usdAdaptive(businessImpact.cost_per_successful_outcome_usd)}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">AI investment ÷ {businessImpact.successful_outcomes} won outcomes</p>
                </CardContent>
              </Card>
            </a>
          )}
        </>
      )}
    </div>
  )
}
