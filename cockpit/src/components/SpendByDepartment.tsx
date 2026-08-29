import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { BudgetDepartment } from "@/lib/api"
import { displayDepartment } from "@/lib/api"

const usd = (n: number) => `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`

// Horizontal ranked bars, not a pie chart -- comparing five-plus
// near-equal wedges by eye is genuinely harder than comparing bar
// lengths, and this is the same treatment TopModels.tsx already uses for
// the equivalent "rank by spend" view, so the two ranked breakdowns on
// this page now read consistently instead of one being a pie and the
// other a bar list.
export function SpendByDepartment({ budget, workspaceId }: { budget: BudgetDepartment[]; workspaceId: string }) {
  const rows = budget
    .map((b) => ({ name: displayDepartment(b.department, workspaceId), value: b.current_spend_usd }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
  const total = rows.reduce((sum, r) => sum + r.value, 0)
  const maxSpend = Math.max(...rows.map((r) => r.value), 0.0001)

  if (!rows.length) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-muted-foreground">Spend by Department</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">No department spend recorded yet.</CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">Spend by Department</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="space-y-3">
          {rows.map((r) => (
            <li key={r.name} className="text-sm">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="truncate font-medium">{r.name}</span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {usd(r.value)} · {total ? Math.round((r.value / total) * 100) : 0}%
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${Math.max((r.value / maxSpend) * 100, 2)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
        <a href="/reports.html" className="mt-4 block text-xs font-medium text-primary hover:underline">
          View full department scorecard →
        </a>
      </CardContent>
    </Card>
  )
}
