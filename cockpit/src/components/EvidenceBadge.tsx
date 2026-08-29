import { Badge } from "@/components/ui/badge"

// Same evidence-label vocabulary used on business-profile.html and
// work-item-profile.html (Measured/Associated/Early Signal/Meaningful
// Sample/Executive-Eligible) -- this is the one place in /cockpit/ that
// visually badges it, matching those pages' trust-signal treatment
// instead of leaving it as text-only association language.
const LABELS: Record<string, string> = {
  measured: "Measured",
  associated: "Associated",
  early_signal: "Early Signal",
  meaningful: "Meaningful Sample",
  executive_eligible: "Executive-Eligible",
}

export function EvidenceBadge({ evidence }: { evidence: string }) {
  const label = LABELS[evidence] ?? evidence
  const variant = evidence === "early_signal" ? "secondary" : evidence === "associated" ? "outline" : "default"
  return (
    <Badge variant={variant} className="uppercase tracking-wide">
      {label}
    </Badge>
  )
}
