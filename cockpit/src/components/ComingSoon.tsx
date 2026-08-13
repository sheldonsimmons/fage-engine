import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Construction } from "lucide-react"

// These sections of the Decision Cockpit concept (What Changed, Business
// Impact trend-lines, Top AI Models by spend) need real period-over-period
// comparison logic or a per-model cost breakdown that doesn't exist in the
// backend yet -- rendering them with invented numbers to match a mockup
// would be exactly the kind of overclaim this project has been correcting
// all session. Shown honestly as not-yet-built instead.
export function ComingSoon({ title, reason }: { title: string; reason: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent className="flex items-start gap-3 text-sm text-muted-foreground">
        <Construction className="mt-0.5 h-4 w-4 shrink-0" />
        <span>{reason}</span>
      </CardContent>
    </Card>
  )
}
