import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EvidenceBadge } from "@/components/EvidenceBadge"
import { OctagonAlert, TrendingDown, TrendingUp, Gauge, UserX, Plug, Lightbulb } from "lucide-react"
import type { Recommendation } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 })

// One icon per detector type -- keeps the list scannable at a glance
// without needing to read every title.
const ICON: Record<string, typeof OctagonAlert> = {
  budget_risk: OctagonAlert,
  model_right_sizing: Gauge,
  high_spend_weak_outcome: TrendingDown,
  high_spend_stalled_work: TrendingDown,
  low_cost_strong_outcome: TrendingUp,
  outcome_coverage_gap: Lightbulb,
  inactive_agent: UserX,
  connection_health: Plug,
}

function RecIcon({ type }: { type: string }) {
  const Icon = ICON[type] ?? Lightbulb
  const isRisk = type === "budget_risk" || type === "high_spend_weak_outcome" || type === "high_spend_stalled_work"
  const bg = isRisk ? "bg-red-500/15 text-red-400" : type === "low_cost_strong_outcome" ? "bg-emerald-500/15 text-emerald-400" : "bg-amber-500/15 text-amber-400"
  return (
    <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${bg}`}>
      <Icon className="h-4 w-4" />
    </span>
  )
}

// Deterministic, not LLM-generated -- every recommendation here comes
// from GET /api/dashboard/recommendations (core/recommendations.py), a
// plain SQL/Python condition over real data. Rendering is generic over
// the shared contract, so a new detector added server-side needs no
// frontend change to show up here.
export function Recommendations({ recommendations }: { recommendations: Recommendation[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">Recommendations</CardTitle>
      </CardHeader>
      <CardContent>
        {recommendations.length ? (
          <ul className="space-y-4">
            {recommendations.map((rec, i) => (
              <li key={`${rec.recommendation_type}-${i}`} className="flex items-start gap-3 text-sm">
                <RecIcon type={rec.recommendation_type} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{rec.title}</span>
                    <EvidenceBadge evidence={rec.confidence} />
                  </div>
                  <p className="mt-0.5 text-muted-foreground">{rec.current_state}</p>
                  {rec.estimated_impact !== null && rec.impact_type === "savings_usd" && (
                    <p className="mt-0.5 font-medium text-emerald-400">
                      Potential savings: {usd(rec.estimated_impact)}/mo
                    </p>
                  )}
                  <p className="mt-1 text-xs text-muted-foreground">{rec.evidence}</p>
                  <p className="mt-1 text-xs font-medium">→ {rec.recommended_action}</p>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">Nothing needs attention right now.</p>
        )}
      </CardContent>
    </Card>
  )
}
