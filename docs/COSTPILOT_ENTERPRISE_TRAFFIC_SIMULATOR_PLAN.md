# CostPilot Enterprise Traffic Simulator Plan

## Purpose

The CostPilot Enterprise Traffic Simulator generates realistic synthetic AI agent traffic across departments so viewers can see CostPilot route, prune, block, and log requests at scale.

The simulator is not meant to replace a real customer integration. It is a demonstration and testing tool that shows what CostPilot looks like when a business has multiple AI agents sending requests through the governance pipeline.

## Positioning

Use this language in the product:

"Generate synthetic AI traffic across departments to see CostPilot route, prune, block, and log requests at scale."

Use this disclosure:

"Synthetic demo traffic. Every request is fake, but each one runs through the real CostPilot routing, pruning, budget, and audit pipeline."

Avoid language about investors, fundraising, or private sales conversations.

## First Version Scope

Build a standalone page:

`/traffic-simulator.html`

The page should:

- Let the user choose simulation size.
- Let the user choose traffic style.
- Let the user choose company profile.
- Generate realistic synthetic AI requests.
- POST those requests to `/api/route`.
- Show live progress while requests are sent.
- Show summary counts after the run.
- Provide a button to open the dashboard and reports.

## Simulation Sizes

The first version should support:

- Quick: 10 requests
- Standard: 25 requests
- Full Demo: 50 requests
- Heavy Run: 100 requests

Default selection:

- Full Demo: 50 requests

The 50-request run should be the primary demo path.

## Recommended 50-Request Demo Mix

The default Full Demo run should include enough variety to show CostPilot operating like a business-wide control layer.

Suggested mix:

- 18 routine savings requests
- 10 pruning showcase requests
- 8 moderate or complex routing requests
- 8 risk or escalation requests
- 4 blocked sensitive-data requests
- 2 executive or productivity requests

This mix makes the dashboard feel active while still showing a clear money-saving story, a pruning story, and a governance story.

## Send Pattern

Requests should be sent in small waves so the viewer can see activity build.

Suggested pattern:

- Send 5 requests at a time.
- Add a short delay between waves.
- Show progress after each request completes.
- Do not send all 50 requests at once.

This makes the simulator feel alive and avoids unnecessary load spikes.

## Traffic Styles

### Balanced

Mix of routine, moderate, complex, pruning-heavy, and blocked requests.

Best for:

- General product demo.
- Showing the full CostPilot workflow.

### Savings-Heavy

Mostly routine and moderate requests with some messy payloads.

Best for:

- Showing lower-cost routing.
- Showing routine work staying efficient.

### Risk-Heavy

More legal, compliance, sensitive-data, and blocked scenarios.

Best for:

- Showing governance, risk controls, and audit value.

### Pruning Showcase

Long email chains, repeated system logs, duplicated notes, signatures, and footer text.

Best for:

- Showing tokens stripped before routing.
- Showing avoidable prompt waste.

## Company Profiles

### Enterprise SaaS

Departments:

- Support
- Sales
- Operations
- Finance
- Procurement
- Executive

### Retail Services

Departments:

- Customer Service
- Store Operations
- Inventory
- Finance
- Procurement
- Executive

### Manufacturing

Departments:

- Operations
- Maintenance
- Supply Chain
- Finance
- Procurement
- Executive

### Professional Services

Departments:

- Client Success
- Sales
- Delivery
- Finance
- Operations
- Executive

## Agent Names

Use friendly names that appear clearly in AgentLake and reporting.

Examples:

- Support Resolution Agent
- Renewal Assist Agent
- Operations Planning Agent
- Supplier Sourcing Agent
- Invoice Review Agent
- Executive Summary Agent
- Inventory Forecast Agent
- Field Service Triage Agent
- Customer Refund Agent
- Meeting Notes Agent

Avoid long IDs or names that look like system-generated records.

## Request Categories

### Customer Service

Use cases:

- Summarize customer issue.
- Draft refund response.
- Route support status update.
- Review return request.
- Explain next steps to customer.

### Operations And Manufacturing

Use cases:

- Summarize equipment status.
- Review maintenance note.
- Identify inventory risk.
- Draft production update.
- Summarize field issue.

### Document Processing

Use cases:

- Extract invoice line items.
- Summarize vendor document.
- Review purchase order text.
- Parse shipment note.
- Summarize form submission.

### Procurement

Use cases:

- Draft RFQ email.
- Summarize supplier comparison.
- Prepare vendor follow-up.
- Review procurement status.
- Generate purchasing checklist.

### General Productivity

Use cases:

- Summarize meeting notes.
- Draft executive update.
- Summarize data findings.
- Prepare project status.
- Rewrite team update.

## Request Template Types

Each generated request should be built from reusable templates.

### Routine

Short, low-risk, no complexity terms.

Expected behavior:

- Route to Scout or low-cost tier when policy allows.

### Moderate

Longer, but not risky.

Expected behavior:

- Route to Analyst or mid-tier if length/complexity triggers apply.

### Complex

Contains work that needs stronger reasoning, planning, or analysis.

Expected behavior:

- Route to Advisor or higher tier depending on current policy.

### Pruning Showcase

Contains repeated headers, signatures, footers, duplicated updates, or system logs.

Expected behavior:

- Show tokens saved by pruning.

Pruning examples should be obvious to the viewer. The payload should look wasteful before CostPilot processes it.

Examples:

- Long email chains with repeated `RE: RE: RE:` headers.
- Repeated signatures from multiple replies.
- Repeated CRM status updates.
- Meeting transcripts with filler phrases and duplicated speaker labels.
- System logs with repeated timestamps and repeated status lines.
- Invoice or PDF-like text with repeated page headers and footers.
- Procurement email chains with copied boilerplate.
- Support ticket threads with duplicated customer notes.

The intended reaction is:

"I would not want to pay an AI model to read all of that."

### Blocked

Contains sensitive terms that should not be sent to an AI model.

Expected behavior:

- Block request and log the reason.

### Escalated

Contains legal, compliance, contract, or high-risk language.

Expected behavior:

- Escalate according to policy and log the rationale.

## Live Progress Counters

During the run, show:

- Requests sent
- Successful requests
- Blocked requests
- Scout
- Analyst
- Advisor
- Strategist
- Pruned requests
- Tokens saved
- Estimated spend

## End-Of-Run Summary

After the simulation completes, show:

- Total requests governed
- Routing split by tier
- Pruned requests
- Tokens saved
- Blocked requests
- Estimated spend
- Button: Open Dashboard
- Button: Open Reports
- Button: Run Another Simulation

## Safety And Honesty

The simulator must make clear that:

- The data is synthetic.
- The requests are fake.
- The CostPilot pipeline is real.
- The audit/dashboard updates are real for the current demo environment.

## Implementation Notes

Prefer a standalone frontend implementation first:

- `frontend/traffic-simulator.html`
- `frontend/js/traffic_simulator.js`

The simulator can call the existing `/api/route` endpoint.

No backend changes should be required for the first version unless rate limiting, demo tagging, or reset behavior becomes necessary later.

## Future Enhancements

Possible later features:

- Demo data reset.
- Run history.
- Export simulation report.
- Scenario editor.
- Save/load custom simulation mixes.
- Provider-specific model pricing demos.
- Workspace selector.
- Continuous traffic mode.
- Scheduled demo run.
