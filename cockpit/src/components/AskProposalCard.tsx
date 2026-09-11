import { useState } from "react"
import { Button } from "@/components/ui/button"
import { confirmAskProposal, rejectAskProposal, type AskProposal } from "@/lib/api"

const usd = (n: number) =>
  Number(n || 0).toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 })

function formatValue(value?: Record<string, unknown>): string {
  if (!value) return "—"
  const entries = Object.entries(value)
  if (!entries.length) return "—"
  const [key, val] = entries[0]
  if (typeof val === "number" && key.toLowerCase().includes("usd")) return usd(val)
  return String(val)
}

const STATUS_LABEL: Record<string, string> = {
  awaiting_confirmation: "Awaiting confirmation",
  executed: "Executed",
  rejected: "Cancelled",
  expired: "Expired — ask again to create a new proposal",
}

// The ONLY action type with a real executor today is a budget-cap change
// (backend/core/action_proposals.py's EXECUTORS dict has exactly one
// entry) -- this card only ever renders when a response carries a real
// `proposal` object, so there is structurally no way for any other
// recommendation to grow a fake Confirm/Execute button. Mirrors
// ask-costpilot-render.js's renderAskProposalCard exactly (same fields,
// same wording), just as a React component calling the same two real
// endpoints (POST /api/ask/actions/{id}/confirm|reject).
export function AskProposalCard({ proposal: initial }: { proposal: AskProposal }) {
  const [proposal, setProposal] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isPending = proposal.status === "awaiting_confirmation"
  const sim = proposal.simulation_result

  async function handle(action: "confirm" | "reject") {
    setBusy(true)
    setError(null)
    try {
      const updated = action === "confirm" ? await confirmAskProposal(proposal.id) : await rejectAskProposal(proposal.id)
      setProposal(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 rounded-lg border border-primary/30 bg-primary/5 p-4 text-sm">
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-primary">Proposed action</p>
      <div className="flex flex-wrap items-center gap-2">
        <strong>{proposal.target_id || proposal.target_type || "Proposed change"}</strong>
        <span className="text-muted-foreground">
          {formatValue(proposal.current_value)} → {formatValue(proposal.proposed_value)}
        </span>
      </div>
      {proposal.reason && <p className="mt-1 text-muted-foreground">{proposal.reason}</p>}

      {sim && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-md bg-background/60 p-3 text-xs sm:grid-cols-4">
          <div><dt className="text-muted-foreground">Current spend</dt><dd className="font-medium tabular-nums">{usd(sim.current_period_spend_usd)}</dd></div>
          <div><dt className="text-muted-foreground">Projected end-of-period</dt><dd className="font-medium tabular-nums">{usd(sim.projected_period_end_spend_usd)}</dd></div>
          {sim.proposed_cap_usd !== undefined && (
            <div><dt className="text-muted-foreground">Proposed cap</dt><dd className="font-medium tabular-nums">{usd(sim.proposed_cap_usd)}</dd></div>
          )}
          {sim.headroom_usd !== undefined && (
            <div><dt className="text-muted-foreground">Headroom</dt><dd className="font-medium tabular-nums">{usd(sim.headroom_usd)}</dd></div>
          )}
          {sim.will_exceed_cap !== undefined && (
            <div className="col-span-2 sm:col-span-4">
              <span className={sim.will_exceed_cap ? "font-medium text-amber-700" : "font-medium text-emerald-700"}>
                {sim.will_exceed_cap
                  ? `The proposed cap would likely be exceeded${sim.projected_exceed_date ? ` around ${sim.projected_exceed_date}` : ""}.`
                  : "The proposed cap would not be exceeded at the current pace."}
              </span>
            </div>
          )}
        </dl>
      )}

      <p className="mt-3 text-xs text-muted-foreground">
        Risk: {proposal.risk_level || "low"} · Status: {STATUS_LABEL[proposal.status] || proposal.status}
      </p>
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}

      {isPending && (
        <div className="mt-3 flex gap-2">
          <Button size="sm" disabled={busy} onClick={() => handle("confirm")}>Confirm</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => handle("reject")}>Cancel</Button>
        </div>
      )}
    </div>
  )
}
