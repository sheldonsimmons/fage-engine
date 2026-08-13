import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { TopModel } from "@/lib/api"

const usd = (n: number) => `$${n.toFixed(n < 10 ? 3 : 2)}`

// Real per-model spend from GET /api/dashboard/top-models -- a SQL GROUP
// BY on the exact model string CostPilot recorded, not a guess. Rows the
// backend flagged is_tier_only (model_name was never set, so it fell back
// to the coarser Scout/Analyst/Advisor/Strategist tier) are labeled "tier
// only" instead of presented as if they were a specific model.
export function TopModels({ models }: { models: TopModel[] }) {
  const maxSpend = Math.max(...models.map((m) => m.spend_usd), 0.0001)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">Top AI Models by Spend</CardTitle>
      </CardHeader>
      <CardContent>
        {models.length ? (
          <ul className="space-y-3">
            {models.map((m) => (
              <li key={m.model} className="text-sm">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="truncate font-medium">
                    {m.model}
                    {m.is_tier_only && <span className="ml-1.5 text-xs font-normal text-muted-foreground">(tier only)</span>}
                  </span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {usd(m.spend_usd)} · {m.pct_of_total}%
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${Math.max((m.spend_usd / maxSpend) * 100, 2)}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No model spend recorded yet.</p>
        )}
      </CardContent>
    </Card>
  )
}
