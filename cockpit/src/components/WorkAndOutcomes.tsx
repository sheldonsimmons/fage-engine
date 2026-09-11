import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Target } from "lucide-react"
import type { BusinessOutcomeRow, WorkOutcomeRow } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 })

const usdCompact = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 })

function SpendColumn({ title, rows }: { title: string; rows: WorkOutcomeRow[] }) {
  const max = Math.max(1, ...rows.map((r) => r.spend_usd))
  return (
    <div className="min-w-0">
      <p className="mb-3 text-xs font-medium text-muted-foreground">{title}</p>
      {rows.length ? (
        <ul className="space-y-2.5">
          {rows.map((row) => (
            <li key={row.name} className="min-w-0">
              <div className="mb-1 flex items-center justify-between gap-2 text-sm">
                <span className="min-w-0 truncate">{row.name}</span>
                <span className="shrink-0 tabular-nums text-muted-foreground">{usdCompact(row.spend_usd)}</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary/70"
                  style={{ width: `${Math.max(4, (row.spend_usd / max) * 100)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">Insufficient data.</p>
      )}
    </div>
  )
}

function OutcomesColumn({ rows }: { rows: BusinessOutcomeRow[] }) {
  return (
    <div className="min-w-0">
      <p className="mb-3 text-xs font-medium text-muted-foreground">Business Outcomes (Associated)</p>
      {rows.length ? (
        <ul className="space-y-2.5">
          {rows.map((row) => (
            <li key={row.name} className="flex items-center justify-between gap-2 text-sm">
              <span className="min-w-0 truncate">{row.name}</span>
              <span className="shrink-0 tabular-nums font-medium text-emerald-600">
                {row.value_usd !== null ? usd(row.value_usd) : `${row.count}/${row.count_total}`}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">Insufficient data.</p>
      )}
    </div>
  )
}

// Real WorkItem (project) and WorkAccount (customer, real names -- no
// segment/tier field exists in the schema, see routes_dashboard.py's
// get_work_outcomes() docstring) spend, plus the same real business-
// outcome totals get_business_impact() already computes elsewhere on
// this page. "Association, not causation" is the exact wording already
// used verbatim on business-profile.html -- never implying AI caused
// these outcomes, only that AI activity is tracked alongside them.
export function WorkAndOutcomes({
  byProject,
  byCustomer,
  businessOutcomes,
}: {
  byProject: WorkOutcomeRow[]
  byCustomer: WorkOutcomeRow[]
  businessOutcomes: BusinessOutcomeRow[]
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Target className="h-4 w-4 text-primary" />
          <CardTitle className="text-sm font-medium">Work &amp; Outcomes</CardTitle>
          <span className="text-xs text-muted-foreground">See how AI cost connects to your projects and customers.</span>
        </div>
        <span className="text-xs font-medium text-muted-foreground">Association, not causation.</span>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-6 sm:grid-cols-3">
        <SpendColumn title="By Project" rows={byProject} />
        <SpendColumn title="By Customer" rows={byCustomer} />
        <OutcomesColumn rows={businessOutcomes} />
      </CardContent>
    </Card>
  )
}
