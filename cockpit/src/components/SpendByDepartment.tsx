import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { Pie, PieChart, Cell } from "recharts"
import type { BudgetDepartment } from "@/lib/api"
import { displayDepartment } from "@/lib/api"

const COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"]

const usd = (n: number) => `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`

export function SpendByDepartment({ budget, workspaceId }: { budget: BudgetDepartment[]; workspaceId: string }) {
  const rows = budget
    .map((b) => ({ name: displayDepartment(b.department, workspaceId), value: b.current_spend_usd }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
  const total = rows.reduce((sum, r) => sum + r.value, 0)

  const chartConfig = Object.fromEntries(
    rows.map((r, i) => [r.name, { label: r.name, color: COLORS[i % COLORS.length] }])
  ) satisfies ChartConfig

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
      <CardContent className="flex items-center gap-6">
        <ChartContainer config={chartConfig} className="h-[180px] w-[180px] shrink-0">
          <PieChart>
            <ChartTooltip content={<ChartTooltipContent formatter={(value) => usd(Number(value))} />} />
            <Pie data={rows} dataKey="value" nameKey="name" innerRadius={55} outerRadius={80} strokeWidth={2}>
              {rows.map((r, i) => (
                <Cell key={r.name} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
          </PieChart>
        </ChartContainer>
        <div className="flex-1 space-y-2 text-sm">
          {rows.map((r, i) => (
            <div key={r.name} className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
                <span>{r.name}</span>
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <span>{total ? Math.round((r.value / total) * 100) : 0}%</span>
                <span className="tabular-nums text-foreground">{usd(r.value)}</span>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
