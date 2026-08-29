import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"

// Same evidence-label vocabulary used on business-profile.html and
// work-item-profile.html (Measured/Associated/Early Signal/Meaningful
// Sample/Executive-Eligible) -- this is the one place in /cockpit/ that
// visually badges it, matching those pages' trust-signal treatment
// instead of leaving it as text-only association language.
// Kept short deliberately -- these render inside a narrow KPI card
// column, and the Badge component's base style is whitespace-nowrap +
// w-fit, so a longer label (confirmed live: "Meaningful Sample" in
// uppercase+tracking-wide) overflows the card's edge instead of wrapping
// or shrinking. "Meaningful"/"Executive" alone still read clearly next
// to the KPI they're describing.
const LABELS: Record<string, string> = {
  measured: "Measured",
  associated: "Associated",
  estimated: "Estimated",
  early_signal: "Early Signal",
  meaningful: "Meaningful",
  executive_eligible: "Executive",
}

export function EvidenceBadge({
  evidence,
  coveragePct,
  note,
}: {
  evidence: string
  // When provided, pairs the evidence word with coverage inline (e.g.
  // "Associated · 75% Coverage") so the trust signal doesn't require a
  // hover to see at all -- the hover just adds the full disclosure.
  coveragePct?: number | null
  note?: string
}) {
  const label = LABELS[evidence] ?? evidence
  const variant = evidence === "early_signal" ? "secondary" : evidence === "associated" || evidence === "estimated" ? "outline" : "default"
  // "Coverage" dropped from the inline text -- confirmed live that
  // "Executive · 97.4% Coverage" doesn't fit a KPI card's width even with
  // truncation and was clipping unreadably. The full word still appears
  // in the hover tooltip's `note`.
  const text = coveragePct !== undefined && coveragePct !== null ? `${label} · ${coveragePct}%` : label
  const badge = (
    <Badge variant={variant} className="block max-w-full truncate uppercase tracking-wide">
      {text}
    </Badge>
  )
  if (!note) return badge
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className="block max-w-full min-w-0 cursor-help">{badge}</TooltipTrigger>
        <TooltipContent className="max-w-64 text-xs normal-case">{note}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
