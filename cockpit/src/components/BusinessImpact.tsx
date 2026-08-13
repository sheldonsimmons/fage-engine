import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { BusinessImpact as BusinessImpactData } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 })

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

  const rows = [
    { label: "Opportunities Won", value: data.opportunities_won.toLocaleString() },
    { label: "Opportunities Open", value: data.opportunities_open.toLocaleString() },
    { label: "Opportunities Lost", value: data.opportunities_lost.toLocaleString() },
    { label: "Pipeline Value", value: usd(data.pipeline_value_usd) },
    { label: "Closed Won Value", value: usd(data.closed_won_value_usd) },
  ]
  if (data.support_cases_total > 0) {
    rows.push({ label: "Support Cases Resolved", value: `${data.support_cases_resolved} / ${data.support_cases_total}` })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">Business Impact</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{r.label}</span>
            <span className="tabular-nums font-medium">{r.value}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
