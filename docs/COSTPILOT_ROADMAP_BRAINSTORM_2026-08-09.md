# CostPilot Roadmap Brainstorm — What's Next
*Written 2026-08-09 while thinking through options, not building. Nothing here is decided — it's a menu with honest tradeoffs, for you to pick from (or reject entirely) when you're back.*

---

## Where things actually stand right now

Proven and live in production:
- Salesforce OAuth connection with automatic token refresh
- Outcome enrichment for **two** Salesforce object types: Opportunity (dollar value, won/lost) and Case (status only, no invented value/success)
- Business Profile page: account-level rollup, real KPIs, deterministic insights, work-type breakdown, connected systems, live polling
- Ask CostPilot: outcome-aware questions with the causal-language guardrail
- Automated sync script (one manual Heroku Scheduler step pending)

That's a genuinely complete "prove the pattern" phase. The next decision is really: **where does the value compound fastest from here** — more platforms, more depth on Salesforce, more intelligence on top of what exists, or shoring up what's already built.

---

## Option A: Third platform (HubSpot, ServiceNow, or Jira)

This is the most obvious "keep going" move, but it's a materially bigger lift than the Case adapter was, and the three candidates aren't equivalent:

**HubSpot** — closest analog to what's built (Deals ≈ Opportunities, dollar value, won/lost). Needs a whole new OAuth flow (HubSpot's, not Salesforce's), a new adapter, new field mapping (`dealstage`/`amount`/`closedate`). Medium effort. Natural next step if the buyer persona is marketing/sales-led companies without Salesforce.

**ServiceNow** — there's already partial connector infrastructure for it in `routes_connections.py` (OAuth scaffolding exists, per earlier research). Incidents/Cases map naturally to the same "status, no dollar value" pattern the Salesforce Case adapter just proved. Probably the **least net-new work** of the three, since some of the plumbing already exists.

**Jira** — different shape entirely (Issues/Stories, story points, sprints — no dollar value, no clean "won/lost", but a real "Done" outcome and a cycle-time concept). Interesting because it's the one that would prove the pattern works for *engineering* work, not just sales/support — but also the most different from what's built, so more design work, not just more plumbing.

**Honest risk:** picking a third platform before automating the *existing* Salesforce sync fully (see Option D) means demoing breadth before proving depth. A prospect asking "does this update automatically" matters more than "how many logos do you support."

## Option B: Deepen Salesforce instead of going wider

Rather than a third platform, go deeper on the one that's proven:

- **A third Salesforce object type** (e.g. Salesforce Task/Activity, or Account itself) — cheap, since the adapter pattern and shared sync loop already exist. Diminishing returns, but nearly free.
- **Field-level permissions on outcome value** — right now, anyone who can see AI spend can see the linked deal's dollar value. That's a real gap flagged back in the original design doc (section 17) and never addressed. Relevant if this is heading toward an enterprise/regulated buyer.
- **Custom field mapping** — today the adapters hard-code standard Salesforce fields (`Amount`, `StageName`). Real customers customize Salesforce heavily (`ARR__c` instead of `Amount`). The design doc's JSON-mapping-wizard concept (section 9) was never built — without it, "works with your Salesforce" isn't quite true for orgs with custom fields.

## Option C: Move from "record and answer" to "notice and tell you"

Everything built so far is reactive — CostPilot answers questions you ask it. The original design's Stage 10-11 territory (quality signals, forecasting) is a real jump:

- **Budget forecasting**: "Sales is projected to exceed its AI budget by 14% this quarter" — this is *achievable* with what already exists (spend data + budget caps are both real), it's really a math/UI problem, not a new data-source problem. Probably the single highest-leverage "intelligence" feature relative to effort, because no new integrations are needed.
- **Anomaly detection** ("spend on X department jumped 40% overnight") — same story, existing data, no new plumbing.
- **True quality signals** (did the user accept the AI output, did they regenerate it) — this is the one flagged in the design doc as a bigger ask of every integration than anything else so far, because it needs UI-level instrumentation from whatever's generating the AI activity, not just a report-back. Correctly deprioritized before; still true.

## Option D: Finish what's half-done rather than start something new

The least exciting option, arguably the most responsible one:

- **The manual Heroku Scheduler step** — until that one dashboard click happens, "automated" sync isn't actually automated yet. Worth confirming it's done before building on top of it.
- **The full SQL aggregation rewrite** — deferred earlier specifically because of the `provider` staleness risk (a persisted provider value could go stale if the model registry changes). Still true, still unresolved. Not urgent at current data volume, but was explicitly the thing flagged as the real fix for "millions of events."
- **A minor deprecation warning** noticed during testing today (`datetime.utcnow()` is deprecated in newer Python) — harmless today, cheap to fix, will eventually become a real problem if ignored long enough.
- **Cosmetic labels** on the Business Profile page ("Top won business contexts" instead of "opportunities" in one Ask CostPilot phrasing) — small, but it's exactly the kind of rough edge a prospect notices in a demo.

---

## My honest read, if forced to rank these

1. **Confirm the Scheduler job is actually running** (Option D, the cheapest possible check) — no value in anything else here if the "automated" story doesn't actually work unattended yet.
2. **ServiceNow as the third platform** (Option A) — least net-new plumbing of the three platform choices, and it's the one candidate where the Case-shaped "status, no dollar value" adapter pattern transfers almost directly.
3. **Budget forecasting** (Option C) — highest ratio of "impressive in a demo" to "effort required," since it needs zero new data sources.
4. Field-level permissions and custom field mapping (Option B) — not urgent for a demo-stage product, genuinely important the moment a real enterprise security review happens.

But this is a business-priority call as much as a technical one — what matters more depends on who's actually evaluating CostPilot next (a HubSpot-shop prospect makes Option A's HubSpot path suddenly the right answer regardless of my ranking above). Worth deciding with that in mind rather than purely on engineering effort.
