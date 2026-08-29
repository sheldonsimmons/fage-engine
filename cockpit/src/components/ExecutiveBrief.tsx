import type { BudgetDepartment, BusinessImpact, DashboardSummary, SavingsSummary } from "@/lib/api"
import { displayDepartment } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 })

// A single always-visible headline sentence, assembled deterministically
// from numbers already fetched for this same page load -- not an
// LLM-generated summary. This is the first thing an executive reads, and
// a template built from the exact numbers rendered below it can't
// hallucinate or drift from them; an LLM asked to freely summarize the
// same data could. Same "reporting layer calculates, LLM narrates"
// discipline Ask CostPilot already enforces, just skewed further toward
// "calculates" here since reliability matters most for the one line
// every viewer reads first.
export function ExecutiveBrief({
  dashboard,
  savings,
  budget,
  businessImpact,
  workspaceId,
}: {
  dashboard: DashboardSummary
  savings: SavingsSummary
  budget: BudgetDepartment[]
  businessImpact: BusinessImpact
  workspaceId: string
}) {
  const sentences: string[] = []

  if (businessImpact.has_outcome_data) {
    sentences.push(
      `${usd(dashboard.spend_month_usd)} of AI investment supported ${usd(businessImpact.closed_won_value_usd)} in associated business value this period, with ${usd(savings.total_saved_usd)} in realized savings.`,
    )
  } else {
    sentences.push(
      `${usd(dashboard.spend_month_usd)} of AI investment this period, with ${usd(savings.total_saved_usd)} in realized savings.`,
    )
  }

  const worst = [...budget].sort((a, b) => b.used_pct - a.used_pct)[0]
  if (worst && (worst.used_pct >= 80 || worst.throttled)) {
    sentences.push(`${displayDepartment(worst.department, workspaceId)} is the primary budget risk and should be reviewed.`)
  }

  if (businessImpact.has_outcome_data && businessImpact.outcome_coverage_pct !== null) {
    sentences.push(`Outcome coverage is ${businessImpact.outcome_coverage_pct}%.`)
  }

  return (
    <p className="text-base leading-relaxed text-foreground/90">
      {sentences.join(" ")}
    </p>
  )
}
