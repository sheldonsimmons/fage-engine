import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Lightbulb } from "lucide-react"

// Real data -- the same rule-based recommendations the Connector Manager's
// health panel shows, computed from actual workspace state (no dollar
// estimates attached, because the backend doesn't compute those yet; the
// mockup's per-recommendation "$6,240/mo" figures aren't something this
// renders, since nothing generates real numbers like that today).
export function Recommendations({ items }: { items: string[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">Recommendations</CardTitle>
      </CardHeader>
      <CardContent>
        {items.length ? (
          <ul className="space-y-3">
            {items.map((item) => (
              <li key={item} className="flex gap-3 text-sm">
                <Lightbulb className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                <span>{item}</span>
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
