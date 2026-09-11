import type { BudgetDepartment, BusinessImpact, DashboardSummary, Recommendation, SavingsSummary } from "@/lib/api"
import { displayDepartment } from "@/lib/api"

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 100 ? 2 : 0 })

// Best-effort only -- the cockpit app has no login/identity flow of its
// own. cp_user_email is set by the legacy pages' login (global-nav.js),
// shared via localStorage since both are served from the same origin;
// it's only present if this browser already signed into one of those
// pages. No name is invented when it isn't set -- the greeting just
// drops the name rather than showing a fake or placeholder one.
function greetingName(): string | null {
  const email = localStorage.getItem("cp_user_email")
  if (!email) return null
  const local = email.split("@")[0]
  const first = local.split(/[._-]/)[0]
  return first ? first.charAt(0).toUpperCase() + first.slice(1) : null
}

function timeOfDayGreeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return "Good morning"
  if (hour < 18) return "Good afternoon"
  return "Good evening"
}

type Status = "healthy" | "attention" | "critical"

const STATUS_LABEL: Record<Status, string> = {
  healthy: "Healthy",
  attention: "Attention Needed",
  critical: "Critical",
}

const STATUS_STYLE: Record<Status, string> = {
  healthy: "bg-emerald-500/15 text-emerald-700",
  attention: "bg-amber-500/15 text-amber-700",
  critical: "bg-red-500/15 text-red-700",
}

// Same over-cap / near-cap thresholds KpiRow.tsx's budgetStatus() and
// operate.html's DEPT HEALTH strip already use, so all three surfaces
// agree on what "Healthy" vs "Critical" means for this workspace.
function overallStatus(budget: BudgetDepartment[]): Status {
  if (budget.some((b) => b.used_pct >= 100 || b.throttled)) return "critical"
  if (budget.some((b) => b.state === "warning")) return "attention"
  return "healthy"
}

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
  recommendations,
  workspaceId,
}: {
  dashboard: DashboardSummary
  savings: SavingsSummary
  budget: BudgetDepartment[]
  businessImpact: BusinessImpact
  recommendations: Recommendation[]
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

  const name = greetingName()
  const status = overallStatus(budget)
  const topRec = [...recommendations].sort(
    (a, b) => ({ critical: 0, high: 1, medium: 2, low: 3 }[a.priority] ?? 9) - ({ critical: 0, high: 1, medium: 2, low: 3 }[b.priority] ?? 9),
  )[0]
  // Reflects when THIS page loaded the data, not a claimed server-side
  // processing timestamp -- the backend doesn't return one today, and
  // fabricating a more specific-sounding freshness figure would be worse
  // than an honest "as of now."
  const asOf = new Date().toLocaleString("en-US", { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" })

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-semibold tracking-tight">
          {timeOfDayGreeting()}{name ? `, ${name}` : ""}
        </h2>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[status]}`}>
          {STATUS_LABEL[status]}
        </span>
      </div>
      <p className="text-base leading-relaxed text-foreground/90">{sentences.join(" ")}</p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>Last {savings.period_days} days</span>
        <span>Data as of {asOf}</span>
        {topRec && (
          <button
            type="button"
            onClick={() => document.getElementById("recommendation-hero")?.scrollIntoView({ behavior: "auto", block: "start" })}
            className="text-primary hover:underline"
          >
            Recommended next: {topRec.title} →
          </button>
        )}
      </div>
    </div>
  )
}
