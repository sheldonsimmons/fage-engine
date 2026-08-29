import { Badge } from "@/components/ui/badge"

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
  early_signal: "Early Signal",
  meaningful: "Meaningful",
  executive_eligible: "Executive",
}

export function EvidenceBadge({ evidence }: { evidence: string }) {
  const label = LABELS[evidence] ?? evidence
  const variant = evidence === "early_signal" ? "secondary" : evidence === "associated" ? "outline" : "default"
  return (
    <Badge variant={variant} className="max-w-full overflow-hidden text-ellipsis uppercase tracking-wide">
      {label}
    </Badge>
  )
}
