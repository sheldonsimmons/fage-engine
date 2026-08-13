import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { Area, AreaChart, CartesianGrid, XAxis } from "recharts"
import type { SavingsTimelinePoint } from "@/lib/api"

const chartConfig = {
  cost: {
    label: "AI Spend",
    color: "var(--chart-1)",
  },
} satisfies ChartConfig

const usd = (n: number) => `$${n.toFixed(2)}`

export function SpendOverTime({ timeline }: { timeline: SavingsTimelinePoint[] }) {
  const total = timeline.reduce((sum, p) => sum + p.cost, 0)

  return (
    <Card className="col-span-1 lg:col-span-2">
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">AI Spend Over Time</CardTitle>
        <div className="text-2xl font-semibold tabular-nums">{usd(total)}</div>
      </CardHeader>
      <CardContent>
        <ChartContainer config={chartConfig} className="h-[220px] w-full">
          <AreaChart data={timeline} margin={{ left: 0, right: 12, top: 8 }}>
            <CartesianGrid vertical={false} />
            <XAxis
              dataKey="date"
              tickLine={false}
              axisLine={false}
              tickMargin={8}
              minTickGap={32}
              tickFormatter={(value: string) => {
                const d = new Date(value)
                return d.toLocaleDateString("en-US", { month: "short", day: "numeric" })
              }}
            />
            <ChartTooltip
              content={
                <ChartTooltipContent
                  labelFormatter={(value) => new Date(value as string).toLocaleDateString("en-US", { month: "long", day: "numeric" })}
                  formatter={(value) => usd(Number(value))}
                />
              }
            />
            <defs>
              <linearGradient id="fillCost" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--color-cost)" stopOpacity={0.6} />
                <stop offset="95%" stopColor="var(--color-cost)" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <Area dataKey="cost" type="monotone" fill="url(#fillCost)" stroke="var(--color-cost)" strokeWidth={2} />
          </AreaChart>
        </ChartContainer>
      </CardContent>
    </Card>
  )
}
