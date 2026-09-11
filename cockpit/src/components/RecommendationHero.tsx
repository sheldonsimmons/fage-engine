import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { EvidenceBadge } from "@/components/EvidenceBadge"
import { CheckCircle2, Sparkles } from "lucide-react"
import type { Recommendation } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 })

const PRIORITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 }

export function topRecommendation(recommendations: Recommendation[]): Recommendation | null {
  if (!recommendations.length) return null
  return [...recommendations].sort((a, b) => (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9))[0]
}

// The single highest-priority recommendation, presented as the page's
// "next best action." No recommendation detector today outputs a
// submittable proposed value (only Ask CostPilot's own agent tool
// computes one, and only once a user names a specific number) -- so
// unlike a real governed action's Approve/Reject buttons (see
// ask-costpilot-render.js's renderAskProposalCard for that real, wired
// flow), this card's action hands the recommendation to Ask CostPilot
// rather than fabricating a one-click "Apply" that has nothing real to
// execute yet.
export function RecommendationHero({
  recommendation,
  onAskAboutIt,
}: {
  recommendation: Recommendation
  onAskAboutIt: (question: string) => void
}) {
  const question = recommendation.affected_department
    ? `Should we adjust ${recommendation.affected_department}'s AI budget?`
    : `How do we address: ${recommendation.title}?`

  return (
    <Card className="border-emerald-500/30 bg-emerald-500/5">
      <CardContent className="flex flex-wrap items-start justify-between gap-4 pt-6">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-600">
            <CheckCircle2 className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium uppercase tracking-wide text-emerald-600">Recommendation</span>
              <EvidenceBadge evidence={recommendation.confidence} />
            </div>
            <p className="mt-1 text-sm font-semibold">{recommendation.title}</p>
            {recommendation.estimated_impact !== null && recommendation.impact_type !== "none" && (
              <p className="mt-1 text-sm text-muted-foreground">
                {recommendation.impact_type === "savings_usd" ? "Estimated savings: " : "Risk if unaddressed: "}
                <span className="font-medium text-foreground">{usd(recommendation.estimated_impact)}/mo</span>
              </p>
            )}
            <p className="mt-1 text-xs text-muted-foreground">{recommendation.evidence}</p>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-stretch gap-2 sm:flex-row">
          <Button size="sm" onClick={() => onAskAboutIt(question)}>
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
            Ask CostPilot about this
          </Button>
          <a
            href="/reports.html?tab=impact"
            className="inline-flex items-center justify-center rounded-md border px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent"
          >
            Review evidence
          </a>
        </div>
      </CardContent>
    </Card>
  )
}
