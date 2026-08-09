# CostPilot — Podcast Source Document
*Prepared 2026-08-09. This document describes what CostPilot actually does and what has actually been built and verified as of this date. Nothing in this document is aspirational marketing language — where a capability is early-stage, limited in scope, or not yet built, that is stated explicitly.*

---

## 1. The Problem CostPilot Exists to Solve

Companies are adopting AI everywhere, fast, often without anyone centrally tracking it. A single company might have:

- Sales reps using an AI assistant embedded in Salesforce
- Support agents using AI to draft responses in a helpdesk tool
- Developers using AI coding assistants
- Custom internal tools calling OpenAI, Anthropic, or other providers directly
- Individual teams signing up for AI tools on their own

Each of these uses different AI providers, different models, different departments, and different budgets — and in most companies today, there is no single place that answers basic questions like:

- How much are we actually spending on AI, across everything?
- Which departments or teams are driving that spend?
- Is anyone sending sensitive data (customer PII, legal information, financial data) to an AI model that shouldn't see it?
- Are we using expensive models for tasks that a cheaper model could handle just as well?
- When we spend money on AI, is it actually connected to anything that matters to the business — a deal, a support case, a project — or is it just a number with no context?

This is the same problem companies had with cloud computing a decade ago (spend sprawls across teams, nobody has a consolidated view) — except AI spend is newer, growing faster, and carries additional risks around data sensitivity and quality control that cloud spend didn't have in the same way.

CostPilot is built to be the layer that sits across all of this and answers those questions with real, traceable numbers — not estimates, not guesses.

---

## 2. What CostPilot Actually Is, Architecturally

CostPilot is middleware — software that sits between the places AI gets used (a CRM, a support tool, a custom internal app, an AI agent) and the AI providers themselves (Anthropic, OpenAI, and others). It doesn't replace those systems. It intercepts, records, and optionally governs what flows between them.

There are two ways a system connects to CostPilot:

**1. Routed mode ("control" mode).** CostPilot receives the request itself — the actual prompt/content that would go to an AI model — and CostPilot decides which model to use, prunes unnecessary content out of the request first, checks it against sensitive-data rules, and only then sends it to the AI provider. This is the deeper integration: CostPilot is in the request path, actively making decisions.

**2. Observed mode.** A system that already made its own AI call (using its own model choice, its own provider relationship) simply reports back to CostPilot afterward: "here's what I used, here's what it cost, here's what it was for." CostPilot doesn't intercept anything in this mode — it just records. This is the lighter-weight integration, and it's genuinely universal: any system capable of making one authenticated HTTP call with a JSON payload can report into CostPilot this way, regardless of what platform it is. This has been built and verified working — a synthetic third-party system's report was sent through this exact path, landed correctly in the database, and Ask CostPilot (see below) correctly answered a question using that data through the same reporting pipeline used for native integrations.

Both modes produce the same underlying record: one row of data per AI call, capturing who made it, what model was used, how many tokens went in and out, what it cost, and — where available — what business activity it was connected to.

---

## 3. Core Capabilities (Built and In Production)

### 3.1 Cost Observability
Every AI call recorded by CostPilot — whether routed or observed — becomes a queryable record: user, department, agent, model, provider, input/output tokens, dollar cost, timestamp, and source platform. This is the foundation everything else is built on.

### 3.2 Context Pruning
Before a request reaches an AI model (in routed mode), CostPilot strips out content that doesn't need to be there — irrelevant boilerplate, redundant context, anything that costs tokens without adding value to the actual request. This directly reduces the token count, and therefore the dollar cost, of the call. This is a real, measurable reduction — not a projection.

### 3.3 Model Routing (Tiered)
Not every AI task needs the most expensive, most capable model. CostPilot maintains a tiered model registry (informally: a cheap/fast tier through a premium/most-capable tier) and routes each request to an appropriate tier based on the complexity of the task, rather than defaulting every request to the most expensive model available. Downgrading a simple task from a premium model to an economy model produces a real, calculable dollar savings per call.

### 3.4 Budget Governance
Departments and teams can be assigned monthly AI budget caps. CostPilot tracks spend against those caps in real time, issues warnings as a department approaches its limit, and can automatically throttle further AI usage once a hard cap is reached — preventing one team's runaway AI usage from becoming an unplanned expense with nobody aware until the bill arrives.

### 3.5 Sensitive Data Protection
CostPilot maintains a library of sensitive-term patterns (Social Security numbers, credit card numbers, passport numbers, and similar) and can block requests containing them before they ever reach an AI model, or escalate certain categories (legal, HR-related content) for review rather than silently blocking or silently allowing them. This runs on every routed request, not as an opt-in add-on.

### 3.6 Governance Audit Trail
Every decision CostPilot makes — which model was used, why, whether anything was blocked, what the risk classification was — is written to an immutable audit log. This exists specifically for companies in regulated industries or with internal compliance requirements who need to be able to answer "what did our AI systems actually do" after the fact, not just "what do we think they did."

### 3.7 Ask CostPilot (Natural-Language Reporting)
Rather than requiring someone to build a custom report or dashboard for every question, CostPilot has a conversational interface — "Ask CostPilot" — that answers plain-English questions like "how much did we spend on AI last month," "who used the most tokens this week," or "which department is closest to its budget cap." Every number in every answer traces back to a real, deterministically calculated figure from the underlying data — the system does the math in code first, and if it also generates a natural-language sentence describing the answer, that sentence is checked against the calculated numbers before being shown, and discarded in favor of the plain calculated answer if it doesn't match. This is a real, code-enforced check, not a policy — it was built specifically because a fluent AI-generated sentence can silently swap or invent a number, and that failure mode needed a real technical guardrail, not just an instruction telling the AI not to do that.

### 3.8 Business Context Linkage
An AI call doesn't happen in a vacuum — it usually happens because someone is doing a specific piece of work: helping with a sales opportunity, resolving a support case, working on a project. CostPilot can capture that context (which business record the AI activity was supporting) if the source system provides it, and roll AI activity up to that business record rather than treating every AI call as an anonymous, disconnected event.

---

## 4. Business Outcome Enrichment (New — Built and Verified 2026-08-09)

This is the newest and most significant capability, and it changes what kind of question CostPilot can answer.

**The problem it solves:** Knowing "$196 was spent on AI helping with this sales opportunity" is useful. Knowing "and that opportunity closed for $600,000" is far more useful — it tells you whether the AI activity was associated with something that actually mattered to the business, not just that it happened.

**How it works, concretely:**

1. AI activity gets tied to a business record (a Salesforce Opportunity, for example) at the time it happens, the same way described in section 3.8.
2. Separately — hours, days, or weeks later — CostPilot connects to the source system (Salesforce, via a real OAuth-authenticated connection) and pulls the current status of that business record: is it still open, was it won, was it lost, and what was it worth.
3. CostPilot stores that outcome against the business record, not against any individual AI event — because many AI events can happen over the life of one deal, and the deal's outcome is a single fact about the deal, not about any one of those events.
4. CostPilot also keeps a running history of outcome changes over time (open → negotiation → closed), not just the current snapshot, so future analysis isn't limited to "what does it look like right now."
5. Ask CostPilot can then answer questions that combine both sides: "which won opportunities had the highest AI investment," "how much AI spend was associated with opportunities we lost."

**This was built and verified against a real, live production Salesforce organization on 2026-08-09** — not a simulation. A real Salesforce Opportunity ("United Oil SLA," Closed Won, $120,000) was synced through a real OAuth connection using real Salesforce API calls, correctly stored in CostPilot, correctly combined with a real AI activity record ($0.42 of tracked spend), and correctly surfaced through Ask CostPilot as "United Oil & Gas Corp. — $0.42 in tracked AI spend on a Closed Won account."

**Scope, stated plainly:** this currently works for one specific case — Salesforce Opportunities. It is a proven pattern, not yet a universal one. Extending it to other object types (support cases, projects) or other platforms (HubSpot, ServiceNow, Jira) is future work, following the same pattern, not yet built.

**The rule enforced around this, in code, not just as a guideline:** CostPilot will never say "AI generated $120,000" or "AI caused this deal to close." Association is not causation, and the system has an explicit, tested check that blocks any AI-generated sentence trying to claim that AI caused a business outcome, forcing it back to a factual statement like "this $120,000 Closed Won opportunity had $0.42 of tracked AI activity associated with it." This distinction — contribution versus attribution — is treated as a hard requirement, not a style preference, because overclaiming what AI spend "did" for a business is a genuinely dangerous failure mode for a tool whose entire purpose is giving executives accurate numbers.

---

## 5. Real-World Use Cases

These are described in terms of the actual problem a real team would have, and how CostPilot's current, built capabilities address it — not hypothetical future capabilities.

### Use Case 1: "We don't know what we're spending on AI"
A mid-size company has multiple teams independently adopting AI tools — some through a CRM integration, some through internal tools built by engineering, some through direct provider accounts. Finance has no consolidated view. CostPilot's observed-mode ingestion lets every one of these sources report into one place with a single API call, and cost observability (3.1) gives finance and leadership one number instead of a dozen scattered invoices.

### Use Case 2: "One department is burning through budget and nobody noticed until the invoice came"
A support or sales team ramps up AI usage without anyone tracking it against a budget, and the first anyone hears about it is an unexpectedly large bill. Budget governance (3.4) — caps, real-time tracking, warnings, automatic throttling — catches this while it's happening, not after.

### Use Case 3: "We're worried someone will paste sensitive customer data into an AI tool"
A company handling regulated or sensitive data (healthcare, financial services, legal) needs assurance that AI tools aren't being used to process Social Security numbers, financial account details, or similarly sensitive data without appropriate controls. Sensitive data protection (3.5) blocks or escalates this automatically, and the audit trail (3.6) provides evidence of that control for compliance purposes.

### Use Case 4: "We're probably overpaying for AI by defaulting everything to the most expensive model"
A team uses one premium AI model for everything by default, including simple tasks that a cheaper model would handle equally well. Tiered model routing and context pruning (3.2, 3.3) reduce that cost automatically, without anyone having to manually choose a cheaper model for each request.

### Use Case 5: "We use AI inside Salesforce (via Agentforce) and have no visibility into it"
A company has adopted Salesforce's AI agent tooling directly inside their CRM — sales reps and support staff triggering AI actions from within Salesforce workflows — and has no separate visibility into what that AI activity costs or how it's used, since it happens inside the CRM itself. CostPilot's Salesforce integration governs and records this activity the same way it would any other source, giving the company the same cost and governance visibility they'd otherwise be missing entirely for CRM-embedded AI use.

### Use Case 6: "We can't tell if our AI investment is actually connected to anything that matters"
A revenue operations or sales leadership team wants to know whether the AI tools their team is paying for are actually being used on the deals and accounts that matter, not just generating activity in the abstract. Business outcome enrichment (section 4) directly answers this for Salesforce Opportunities today: which won or lost deals had AI activity associated with them, and how much.

### Use Case 7: "Leadership wants an audit trail, not just a bill"
A regulated company needs to be able to demonstrate, after the fact, exactly what an AI system did, why a particular model was chosen, and whether any risky content was involved in a given interaction — not just what it cost. The governance audit trail (3.6) exists specifically for this.

---

## 6. What CostPilot Is Not (Stated Plainly, Not to Undersell but to Be Accurate)

- It does not replace Salesforce, HubSpot, ServiceNow, or any other business system — it observes and correlates AI activity connected to them.
- It does not currently have working outcome-tracking for HubSpot, ServiceNow, or Jira — only Salesforce Opportunities, as of this document's date.
- It does not have a fully automatic real-time sync from Salesforce yet — the outcome sync described in section 4 runs on request today, not on a fixed automatic schedule; that is a near-term but not-yet-built piece.
- It does not claim that AI activity causes business outcomes — by design, and enforced in code, it only ever reports association.
- It is not yet a fully autonomous cost-optimization system that takes automated action on your behalf — today it observes, reports, governs against explicit rules (like budget caps and sensitive-term blocks), and answers questions; broader autonomous optimization is a stated future direction, not a current capability.

---

## 7. A Brief, Honest Note on Where This Stands

CostPilot is an actively developed, working system with real production deployments and real integrations, built by a small team. Some capabilities (cost observability, budget governance, sensitive-data blocking, model routing, the audit trail, Ask CostPilot) are mature and have been in use for a while. Others (business outcome enrichment, described in section 4) are new as of this week, proven against real data, but narrower in scope than the eventual vision. This document reflects that honestly rather than presenting early-stage work as more mature than it is.
