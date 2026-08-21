import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Lightbulb, AlertTriangle, OctagonAlert } from "lucide-react"
import { displayDepartment, type BudgetDepartment } from "@/lib/api"

interface Recommendation {
  key: string
  severity: "over" | "throttled" | "warning" | "info"
  text: string
}

// Budget-derived recommendations use the backend's own authoritative
// per-department `state` (core/budget.py's _enrich(), 80% warning
// threshold) as the baseline -- but `state` only ever reaches "throttled"
// if live enforcement actually fired, which never happens for
// demo/simulated workspaces (current_spend_usd there is a stale counter
// -- see core/budget.py). A department can sail past 100% of its real,
// recomputed spend and still report state:"warning", identical to one at
// 81% -- confirmed live (SIM-HISTORICAL-2Y's Engineering: used_pct 101.5,
// throttled false, state "warning"). operate.html's DEPT HEALTH strip
// already guards against exactly this by computing "OVER" client-side
// from the percentage itself, not the state field alone
// (frontend/js/dashboard.js's isOver = pct >= 100 || throttled) -- this
// applies that same guard here, so the two surfaces can't disagree again.
function budgetRecommendations(budget: BudgetDepartment[], workspaceId: string): Recommendation[] {
  return budget
    .filter((b) => b.state === "warning" || b.state === "throttled" || b.used_pct >= 100)
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
      if (b.used_pct >= 100) {
        return {
          key: `budget-${b.department}`,
          severity: "over" as const,
          text: `${dept} is over budget (${b.used_pct}% of ${cap}/mo) with no throttle in effect -- spend is not being capped.`,
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
  if (severity === "over" || severity === "throttled")
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

// "over" ranks first -- an over-cap department with no active throttle is
// actively overspending right now with nothing stopping it, which is a
// more urgent signal than one already being contained by enforcement.
const SEVERITY_ORDER: Record<Recommendation["severity"], number> = { over: 0, throttled: 1, warning: 2, info: 3 }

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
