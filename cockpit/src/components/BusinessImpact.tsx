import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EvidenceBadge } from "@/components/EvidenceBadge"
import { Cpu } from "lucide-react"
import type { BusinessImpact as BusinessImpactData } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 })

const usdPrecise = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 10 ? 2 : 0 })

// Ratios computed from this workspace's real spend land anywhere from
// sub-cent to tens of dollars -- usd() above always rounds to 0 decimals
// (correct for the large Pipeline/Closed-Won rows it's built for, which
// is why it isn't changed here), but applying it to a per-outcome ratio
// silently collapsed real, non-trivial values to "$0". Confirmed live:
// a real cost-per-won-opportunity of $0.079247 rendered as "$0" --
// not a sub-cent value needing more decimals, just a value under $1
// that usd()'s 0-decimal rounding was never meant to handle. Picks
// decimal precision by magnitude instead of delegating to usd().
const usdAdaptive = (n: number) => {
  if (n === 0) return "$0"
  if (n < 0.01) return `$${n.toFixed(4)}`
  if (n < 100) return `$${n.toFixed(2)}`
  return usd(n)
}

// Real data from GET /api/dashboard/business-impact -- a workspace-wide
// widening of the same WorkItemOutcome query that already powers Business
// Profile's per-account "Business Outcomes" panel. Only meaningful for
// workspaces with an outcome-sync-connected platform (Salesforce today);
// has_outcome_data distinguishes that from "genuinely nothing here."
export function BusinessImpact({ data }: { data: BusinessImpactData }) {
  if (!data.has_outcome_data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-muted-foreground">Business Impact</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          No business outcome data yet -- connect a CRM with outcome sync (e.g. Salesforce) to see
          Opportunities, pipeline value, and resolved cases here.
        </CardContent>
      </Card>
    )
  }

  const rows: { label: string; value: string; trendPct?: number | null }[] = [
    { label: "Opportunities Won", value: data.opportunities_won.toLocaleString() },
    { label: "Opportunities Open", value: data.opportunities_open.toLocaleString() },
    { label: "Opportunities Lost", value: data.opportunities_lost.toLocaleString() },
    { label: "Pipeline Value", value: usd(data.pipeline_value_usd) },
    { label: "Closed Won Value", value: usd(data.closed_won_value_usd) },
  ]
  if (data.support_cases_total > 0) {
    rows.push({ label: "Support Cases Resolved", value: `${data.support_cases_resolved} / ${data.support_cases_total}` })
  }
  // Deeper economics -- each only appended when its denominator is
  // non-zero (e.g. no won opportunities yet -> no Cost per Won
  // Opportunity row), same "omit rather than show a misleading number"
  // rule as everywhere else this session. trendPct (30d vs prior 30d) is
  // shown alongside when the backend has enough history to compute one;
  // omitted (not zeroed) otherwise.
  if (data.cost_per_won_opportunity_usd !== null) {
    rows.push({
      label: "Cost per Won Opportunity", value: usdAdaptive(data.cost_per_won_opportunity_usd),
      trendPct: data.trend_pct_change.cost_per_won_opportunity_usd,
    })
  }
  if (data.ai_investment_on_lost_opportunities_usd !== null) {
    rows.push({
      label: "AI Investment on Lost Opportunities", value: usdAdaptive(data.ai_investment_on_lost_opportunities_usd),
      trendPct: data.trend_pct_change.ai_investment_on_lost_opportunities_usd,
    })
  }
  if (data.avg_ai_investment_per_opportunity_usd !== null) {
    rows.push({
      label: "Avg. AI Investment per Opportunity", value: usdAdaptive(data.avg_ai_investment_per_opportunity_usd),
      trendPct: data.trend_pct_change.avg_ai_investment_per_opportunity_usd,
    })
  }
  if (data.support_cost_per_resolution_usd !== null) {
    rows.push({
      label: "Support Cost per Resolution", value: usdAdaptive(data.support_cost_per_resolution_usd),
      trendPct: data.trend_pct_change.support_cost_per_resolution_usd,
    })
  }
  // Outcome Coverage stays here as the one place it's independently
  // browsable (also summarized in the Associated Business Value evidence
  // badge above) rather than duplicated into its own KPI card too.
  if (data.outcome_coverage_pct !== null) {
    rows.push({ label: "Outcome Coverage", value: `${data.outcome_coverage_pct}%` })
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-sm font-medium text-muted-foreground">Business Impact</CardTitle>
        <EvidenceBadge evidence={data.evidence_label} />
      </CardHeader>
      <CardContent className="space-y-2">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{r.label}</span>
            <span className="flex items-center gap-1.5">
              <span className="tabular-nums font-medium">{r.value}</span>
              {/* All four trended rows are cost/investment figures -- lower
                  is always the improvement, so a negative delta is green
                  regardless of which row it's on. */}
              {r.trendPct != null && (
                <span className={`text-xs tabular-nums ${r.trendPct < 0 ? "text-emerald-400" : "text-amber-400"}`}>
                  {r.trendPct < 0 ? "↓" : "↑"} {Math.abs(r.trendPct)}%
                </span>
              )}
            </span>
          </div>
        ))}
        {/* One compact line, not another row -- association, not causation:
            AI activity tied to these outcomes, never framed as having
            caused them. All-time, outcome-linked spend only -- a
            different scope than the workspace's Month-to-Date AI
            Investment KPI above the fold, so it's labeled explicitly
            rather than reading as a second, disagreeing "total spend." */}
        <div className="mt-3 flex items-center gap-1.5 border-t border-border pt-3 text-xs text-muted-foreground">
          <Cpu className="h-3.5 w-3.5" />
          <span>
            AI investment tied to known outcomes (all-time): {usdPrecise(data.ai_spend_usd)} · {data.ai_tokens_total.toLocaleString()} tokens
          </span>
        </div>
        <a href="/business-profile.html" className="mt-2 block text-xs font-medium text-primary hover:underline">
          View per-account business profiles →
        </a>
      </CardContent>
    </Card>
  )
}
