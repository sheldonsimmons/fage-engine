# CostPilot — Technical & Sales Understanding Guide
*Prepared 2026-08-09. Every capability below is either verified working today (tested live against real production data this session) or clearly marked as not-yet-built. No embellishment — this is meant to make you dangerous in both an engineering conversation and a sales conversation, not to sound impressive.*

---

# PART ONE: Understanding CostPilot Technically

## The one-sentence version

CostPilot is a checkpoint that sits between the places your company uses AI and the AI providers themselves — it watches, records, and optionally controls what happens, the way a **customs checkpoint** watches what crosses a border: it doesn't stop trade from happening, it just makes sure everything that crosses is recorded, inspected for anything dangerous, and taxed/accounted for correctly.

## The architecture, with analogies

### Two ways something connects to CostPilot

**1. Routed mode — CostPilot is "in the room."**
Think of this like having a **skilled travel agent book your flight for you** instead of booking it yourself. You tell the travel agent what you need ("get me to Chicago tomorrow"), and *they* decide which airline, which route, whether to remove unnecessary add-ons, and they keep a receipt. In routed mode, the requesting system hands CostPilot the actual request, and CostPilot decides which AI model to use, strips out unnecessary content first, checks it for anything dangerous, and only then sends it onward.

**2. Observed mode — CostPilot is "told about it afterward."**
This is like **submitting an expense report after a business trip you booked yourself.** You made your own choices, used your own card, but you still log it with finance afterward so it's on the books. Any system — regardless of what platform it is — can report into CostPilot this way with one API call: "here's what I did, here's what it cost." This is the genuinely universal connector: it doesn't require CostPilot to have a custom integration built for every possible AI tool in existence, only a system capable of making one authenticated HTTP request.

Both modes end up producing the same underlying record — a database row saying who, what model, how many tokens, how much it cost, and (where available) what business work it was for.

### Core capabilities, each with the analogy that makes it click

**Cost observability** — like getting one **itemized receipt instead of a single lump-sum charge on a credit card statement.** You don't just know "$4,000 was spent this month" — you know which department, which tool, which person, which model, down to the individual transaction.

**Context pruning** — like **editing a rambling email down to the actual ask before hitting send.** AI models charge by the amount of text they process. If a request carries a bunch of irrelevant boilerplate, you're paying to have the model read and think about text that adds nothing. CostPilot strips that out first.

**Tiered model routing** — like **not hiring a structural engineer to hang a picture frame.** Not every task needs the most expensive, most capable AI model. CostPilot keeps a tiered registry of models (cheap-and-fast through expensive-and-powerful) and routes each request to a tier that actually matches the complexity of the task, rather than defaulting everything to the top tier "just in case."

**Budget governance** — like a **corporate card with a spending limit that automatically locks when the team hits its cap**, with a warning text message before it happens. Departments get monthly AI budgets; CostPilot tracks real-time spend against them and can throttle usage automatically before an overrun becomes a surprise bill.

**Sensitive data protection** — like a **mailroom scanner that flags or blocks packages containing something they shouldn't, before they leave the building.** CostPilot checks outgoing AI requests for patterns like Social Security numbers or credit card numbers and blocks or escalates them before they reach a model.

**Audit trail** — like an **aircraft's black box flight recorder.** Every decision CostPilot makes — which model, why, what was blocked, what the risk level was — gets written to an immutable log, specifically for the "what actually happened" conversation that regulated companies need to be able to have after the fact.

**Ask CostPilot (natural-language reporting)** — like having **a very literal, very honest accountant** answer your questions instead of a smooth-talking analyst. The accountant does the math first, in a spreadsheet, the boring reliable way — and only *then* writes you a sentence describing the answer, and before handing you that sentence, they double-check every number in it against the spreadsheet. If the sentence doesn't match the math, they throw the sentence away and just read you the spreadsheet number directly. That double-check is real, working code — not a suggestion to the accountant to "please be careful."

**Business context linkage** — like **writing "Q3 client dinner — Acme Corp" on a restaurant receipt** instead of just filing it as "$340, unspecified." An AI call by itself is just a cost. Tagging it to the piece of work it supported (a sales opportunity, a support case) makes the number mean something.

**Business outcome enrichment (the newest piece)** — like the difference between **knowing what you spent on a marketing campaign versus knowing whether it actually led to a sale.** Most tools stop at "here's the AI activity and its cost." CostPilot goes one step further for one proven case today: it separately checks back with Salesforce, days or weeks later, to see what actually happened to the deal that AI activity was supporting — did it close, for how much — and connects the two facts together, without ever conflating "happened at the same time as" with "caused."

**The contribution-vs-causation guardrail** — like a **weather forecaster who notices ice cream sales and drowning deaths both rise every summer, and is professionally required to say "these are correlated, both driven by hot weather" rather than "ice cream causes drowning."** CostPilot has an actual, tested, code-level check that blocks any system-generated sentence claiming AI *caused* a business result. It's allowed to say "this deal, worth $120,000, had $0.42 of tracked AI activity associated with it." It is not allowed to say "AI generated $120,000." That's enforced in code, the same way the numeric fact-check above is — not a company policy, a technical constraint.

### The technical honesty section (say this in engineering conversations, not just here)

- Real Salesforce OAuth integration exists and works — verified against a live org today, including automatic token refresh (the app fixes its own expired login credentials without a human re-authorizing every two hours).
- The outcome-enrichment mechanism above works for exactly one case today: **Salesforce Opportunities.** The pattern is proven and designed to extend to other object types and platforms, but that extension hasn't been built yet.
- There's no auto-scheduled sync yet — pulling fresh outcome data from Salesforce happens on request, not on a timer, today.
- "Real-time" dashboards in this app mean **polling every few seconds**, not a push/websocket architecture. Said plainly: it's not instant, it's fast-refreshing.
- Aggregation/reporting queries are built to run inside the database (SQL `SUM`/`GROUP BY`), specifically so the system doesn't fall over as data volume grows — this was a real bug found and fixed this week (a report that took 0.02 seconds at 47 rows took 2.5 seconds at 20,000 rows before the fix).

---

# PART TWO: Understanding CostPilot as a Sales Story

## The pitch, in one paragraph

Every company is adopting AI right now, across a dozen tools and teams, with nobody centrally tracking what it costs, whether it's being used safely, or whether it's connected to anything the business actually cares about. That's the same problem companies had with cloud computing a decade ago — except AI spend is growing faster, and it carries data-sensitivity risk cloud spend never had. CostPilot is the layer that sits across all of it and gives a straight, provable answer to "what are we spending, is it safe, and is it worth it" — not a guess, not a vendor's self-reported summary, an actual number that traces back to a real recorded event every time.

## Who actually buys this (buyer personas)

- **CFO / Finance leadership** — wants one consolidated number instead of a dozen scattered AI vendor invoices, and wants budget overruns caught before the bill arrives, not after.
- **VP Engineering / Platform** — wants to stop every team from defaulting to the most expensive model for every task, and wants a governance layer that doesn't require rebuilding every internal tool.
- **Compliance / Legal / Security** — wants proof, not a promise, that sensitive data isn't leaking into AI models, and wants an audit trail for regulators or internal review.
- **RevOps / Sales leadership** — wants to know whether the AI tools the team is paying for are actually touching the deals and accounts that matter, not just generating activity.
- **CIO** — wants a single governance layer across a fragmented AI vendor landscape without owning integration work for every tool that shows up.

## The real proof point to use in a conversation (not a hypothetical)

Today, live, against a real Salesforce production org: a real Opportunity ("United Oil SLA") accumulated real AI activity ($0.42 tracked spend, 1 request — small because it was a test, but real, not simulated). Separately, CostPilot connected to Salesforce via a real OAuth session and pulled the Opportunity's actual status: **Closed Won, $120,000.** CostPilot then correctly answered the question "which won opportunities had AI spend associated with them" with that exact deal — and, when tested, correctly *refused* to generate the sentence "AI generated $120,000," producing instead "this $120,000 Closed Won opportunity had $0.42 of tracked AI activity associated with it."

That's a small dollar amount by design (it was a test), but it's a **real, live, end-to-end proof** of the actual value proposition — not a mockup, not a hypothetical.

## Use-case stories (short version, for a sales conversation)

1. **"We don't know what we're spending on AI"** — teams are adopting AI independently, finance has no consolidated view. CostPilot's universal reporting (one API call from anything) gives one number instead of a dozen scattered invoices.
2. **"A team blew through budget and nobody caught it"** — real-time budget caps with automatic throttling catch this while it's happening, not when the invoice arrives.
3. **"We're worried about sensitive data reaching an AI model"** — automatic blocking/escalation of SSNs, financial data, etc., before it ever reaches a model, with an audit trail proving the control exists.
4. **"We're probably overpaying by defaulting to the most expensive model for everything"** — tiered routing and context pruning cut this automatically, no manual model selection required.
5. **"We use AI inside Salesforce and have zero visibility into it"** — the Agentforce integration governs and records that activity the same as anything else.
6. **"We can't tell if our AI spend is connected to anything that matters"** — the business outcome enrichment story above, directly.
7. **"We need an audit trail, not just a bill"** — the governance log, built specifically for this.

## Honest limitations (know these before a sales call, don't get caught by them)

- Outcome tracking today = **Salesforce Opportunities only.** Not HubSpot, not ServiceNow, not Jira, not other CRMs. Say "we've proven the pattern on Salesforce and are extending it" — don't imply it's already universal.
- Not autonomous. CostPilot observes, enforces explicit rules (budget caps, sensitive-data blocks), and answers questions. It doesn't yet take independent optimization action on your behalf.
- Sync is on-demand, not scheduled, for outcome data today.
- Small team, actively building. Some pieces (cost tracking, budget governance, the audit trail) are mature. The outcome-enrichment piece is brand new — built and proven this week.

## Likely objections, and honest answers

**"Isn't this just a proxy in front of an API?"**
The routing/pruning piece is proxy-shaped, yes — but the value isn't the proxy, it's what happens around it: budget enforcement, sensitive-data blocking, the audit trail, and now the connection to actual business outcomes. A plain API proxy doesn't know what a Closed Won opportunity is worth.

**"Why wouldn't we just build this ourselves?"**
You could build the cost-tracking piece in a sprint. The parts that take real time are the ones that don't show up in a demo: token pruning that's actually safe, a tiered routing decision engine, a sensitive-data detector that doesn't produce false positives constantly, an audit system that holds up under compliance review, and — as of this week — a business-outcome correlation engine with a hard-coded guardrail against overclaiming AI's impact. That last part specifically is easy to get wrong (overclaiming) or never build (underclaiming the value) — CostPilot has already made and tested that decision.

**"How is this different from a generic cloud-cost tool?"**
Cloud cost tools track infrastructure spend. CostPilot tracks AI activity specifically, including things cloud tools have no concept of: which model was used for which task, whether sensitive data was involved, and — uniquely — whether that AI activity is connected to a business outcome like a closed deal.

**"What happens when we're not just using Salesforce?"**
Today: only Salesforce Opportunities get the outcome-correlation treatment. The architecture is explicitly designed so other platforms (HubSpot, ServiceNow, Jira) follow the same pattern later — but that extension work hasn't happened yet. Be straight about that; it's a roadmap item, not a current feature.

---

## Quick-reference numbers you can cite with confidence

- Real Salesforce Opportunity synced live: "United Oil SLA," Closed Won, **$120,000**.
- Real AI activity tracked against it: **$0.42**, 1 request (small because it was a verification test, not production volume).
- Reporting performance fix this week: a report that was **0.02s at 47 rows** degraded to **2.5s at 20,000 rows** before being fixed — now handles that same 20,000-row case in **under 1 second**.
- The causal-language guardrail has been tested against both a bad sentence ("AI generated $600,000") — correctly blocked — and a good one ("this deal had $196 of tracked AI activity") — correctly allowed through.
