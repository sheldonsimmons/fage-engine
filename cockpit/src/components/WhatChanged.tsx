import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ArrowUp, ArrowDown, Sparkles } from "lucide-react"
import type { DashboardChange } from "@/lib/api"

function ChangeIcon({ change }: { change: DashboardChange }) {
  if (change.pct_change === null) return <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-blue-400" />
  if (change.pct_change >= 0) return <ArrowUp className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
  return <ArrowDown className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
}

// Real period-over-period comparison from GET /api/dashboard/changes --
// every row here is a metric the backend actually diffed against the prior
// period. Metrics with no prior-period baseline are omitted server-side
// (not shown as invented percentages), so an empty list here honestly
// means "nothing to compare yet," not a loading failure.
export function WhatChanged({ periodDays, changes }: { periodDays: number; changes: DashboardChange[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">What Changed</CardTitle>
        <p className="text-xs text-muted-foreground">vs the prior {periodDays} days</p>
      </CardHeader>
      <CardContent>
        {changes.length ? (
          <ul className="space-y-3">
            {changes.map((change) => (
              <li key={change.metric} className="flex gap-3 text-sm">
                <ChangeIcon change={change} />
                <span>{change.summary}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">
            Not enough history yet to compare against the prior period.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
