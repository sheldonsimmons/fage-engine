import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Lightbulb, AlertTriangle, OctagonAlert } from "lucide-react"
import { displayDepartment, type BudgetDepartment } from "@/lib/api"

interface Recommendation {
  key: string
  severity: "throttled" | "warning" | "info"
  text: string
}

// Budget-derived recommendations use the backend's own authoritative
// per-department `state` (core/budget.py's compute_state(), 80% warning
// threshold) -- not a threshold re-invented here. This is the same field
// /api/budget already returns; nothing new is computed, just surfaced.
// (Worth noting separately: operate.html's DEPT HEALTH strip uses its own
// hardcoded 70% threshold in dashboard.js, which can disagree with this
// 80%-based state for departments in the 70-80% band -- a pre-existing
// inconsistency between two display surfaces, not something introduced
// here.)
function budgetRecommendations(budget: BudgetDepartment[], workspaceId: string): Recommendation[] {
  return budget
    .filter((b) => b.state === "warning" || b.state === "throttled")
    .map((b) => {
      const dept = displayDepartment(b.department, workspaceId)
      const cap = b.monthly_cap_usd.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 })
      if (b.state === "throttled") {
        return {
          key: `budget-${b.department}`,
          severity: "throttled" as const,
          text: `${dept} is over budget (${b.used_pct}% of ${cap}/mo) and is being throttled.`,
        }
      }
      return {
        key: `budget-${b.department}`,
        severity: "warning" as const,
        text: `${dept} is at ${b.used_pct}% of its ${cap}/mo budget cap.`,
      }
    })
}

function Icon({ severity }: { severity: Recommendation["severity"] }) {
  if (severity === "throttled")
    return (
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-500/15 text-red-400">
        <OctagonAlert className="h-4 w-4" />
      </span>
    )
  if (severity === "warning")
    return (
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-amber-400">
        <AlertTriangle className="h-4 w-4" />
      </span>
    )
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-500/15 text-blue-400">
      <Lightbulb className="h-4 w-4" />
    </span>
  )
}

const SEVERITY_ORDER: Record<Recommendation["severity"], number> = { throttled: 0, warning: 1, info: 2 }

export function Recommendations({
  items,
  budget,
  workspaceId,
}: {
  items: string[]
  budget: BudgetDepartment[]
  workspaceId: string
}) {
  const combined: Recommendation[] = [
    ...budgetRecommendations(budget, workspaceId),
    ...items.map((text) => ({ key: text, severity: "info" as const, text })),
  ].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">Recommendations</CardTitle>
      </CardHeader>
      <CardContent>
        {combined.length ? (
          <ul className="space-y-3">
            {combined.map((rec) => (
              <li key={rec.key} className="flex items-start gap-3 text-sm">
                <Icon severity={rec.severity} />
                <span className="pt-1.5">{rec.text}</span>
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
