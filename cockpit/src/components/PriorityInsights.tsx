import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { OctagonAlert, Gauge, ShieldAlert } from "lucide-react"
import type { Recommendation } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 })

// Same 3-bucket grouping the product spec asks for (Budget risk /
// Optimization opportunity / Governance gap), built entirely from the
// same GET /api/dashboard/recommendations data Recommendations.tsx
// already renders as a flat list -- no new backend endpoint, no new
// detector. There is no "governance gap" detector type today; the
// closest real signals (stale connections, coverage gaps, inactive
// agents) are grouped under that label here since they're all, in
// substance, "something in how AI activity is being governed/tracked
// needs attention" -- not a new category invented server-side.
const BUCKETS: { key: string; label: string; icon: typeof OctagonAlert; types: string[] }[] = [
  { key: "budget_risk", label: "Budget risk", icon: OctagonAlert, types: ["budget_risk"] },
  {
    key: "optimization",
    label: "Optimization opportunity",
    icon: Gauge,
    types: ["model_right_sizing", "high_spend_weak_outcome", "high_spend_stalled_work", "low_cost_strong_outcome"],
  },
  {
    key: "governance_gap",
    label: "Governance gap",
    icon: ShieldAlert,
    types: ["outcome_coverage_gap", "inactive_agent", "connection_health"],
  },
]

const PRIORITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 }

const PRIORITY_BADGE: Record<string, string> = {
  critical: "bg-red-500/15 text-red-600",
  high: "bg-amber-500/15 text-amber-600",
  medium: "bg-blue-500/15 text-blue-600",
  low: "bg-muted text-muted-foreground",
}

const ICON_TONE: Record<string, string> = {
  budget_risk: "bg-red-500/15 text-red-600",
  optimization: "bg-amber-500/15 text-amber-600",
  governance_gap: "bg-blue-500/15 text-blue-600",
}

function topOf(recommendations: Recommendation[], types: string[]): Recommendation | null {
  const matches = recommendations.filter((r) => types.includes(r.recommendation_type))
  if (!matches.length) return null
  return [...matches].sort((a, b) => (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9))[0]
}

// Only non-empty buckets render -- an empty bucket is omitted, never
// shown as a fabricated "all clear" card, matching this codebase's
// existing "omit rather than show a misleading state" convention (e.g.
// KpiRow.tsx's Potential Savings card).
export function PriorityInsights({ recommendations }: { recommendations: Recommendation[] }) {
  const cards = BUCKETS.map((bucket) => ({ bucket, rec: topOf(recommendations, bucket.types) })).filter(
    (c): c is { bucket: (typeof BUCKETS)[number]; rec: Recommendation } => c.rec !== null
  )

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-sm font-medium">Priority Insights</CardTitle>
        <a href="/reports.html?tab=impact" className="text-xs text-primary hover:underline">
          View all →
        </a>
      </CardHeader>
      <CardContent className="space-y-3">
        {cards.length ? (
          cards.map(({ bucket, rec }) => {
            const Icon = bucket.icon
            return (
              <div key={bucket.key} className="rounded-lg border p-3">
                <div className="flex items-start gap-2.5">
                  <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${ICON_TONE[bucket.key]}`}>
                    <Icon className="h-3.5 w-3.5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{bucket.label}</span>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${PRIORITY_BADGE[rec.priority] ?? PRIORITY_BADGE.low}`}>
                        {rec.priority}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{rec.current_state}</p>
                    {rec.estimated_impact !== null && rec.impact_type !== "none" && (
                      <p className="mt-1 text-xs font-medium">
                        {rec.impact_type === "savings_usd" ? "Potential savings: " : "Risk: "}
                        {usd(rec.estimated_impact)}/mo
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )
          })
        ) : (
          <p className="text-sm text-muted-foreground">Nothing needs attention right now.</p>
        )}
      </CardContent>
    </Card>
  )
}
