import type { ComponentType } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ArrowUp, ArrowDown, Sparkles, DollarSign, Phone, Cpu } from "lucide-react"
import type { DashboardChange } from "@/lib/api"

// Colored circular badges, one per metric type -- matches the reference
// mockup's icon treatment. Color is metric identity (spend=red-ish,
// agents=green, model mix=blue), not severity -- the arrow inside already
// carries up/down. new_agents has no direction (it's a count-of-new-things
// event, not a before/after comparison), so it gets a fixed icon instead
// of an arrow.
const METRIC_STYLE: Record<string, { bg: string; icon: ComponentType<{ className?: string }> }> = {
  spend: { bg: "bg-red-500/15 text-red-400", icon: DollarSign },
  calls: { bg: "bg-blue-500/15 text-blue-400", icon: Phone },
  model_mix: { bg: "bg-indigo-500/15 text-indigo-400", icon: Cpu },
  new_agents: { bg: "bg-emerald-500/15 text-emerald-400", icon: Sparkles },
}

function ChangeIcon({ change }: { change: DashboardChange }) {
  const style = METRIC_STYLE[change.metric] ?? { bg: "bg-muted text-muted-foreground", icon: Sparkles }
  const Icon = style.icon
  return (
    <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${style.bg}`}>
      <Icon className="h-4 w-4" />
    </span>
  )
}

function DirectionBadge({ pctChange }: { pctChange: number | null }) {
  if (pctChange === null) return null
  const isUp = pctChange >= 0
  const Icon = isUp ? ArrowUp : ArrowDown
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${isUp ? "text-emerald-400" : "text-red-400"}`}>
      <Icon className="h-3 w-3" />
      {Math.abs(pctChange)}%
    </span>
  )
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
          <ul className="space-y-4">
            {changes.map((change) => (
              <li key={change.metric} className="flex items-start gap-3">
                <ChangeIcon change={change} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{change.label}</span>
                    <DirectionBadge pctChange={change.pct_change} />
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">{change.summary}</p>
                </div>
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
