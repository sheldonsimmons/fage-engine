# CostPilot Podcast Source Document

## Purpose

This document is a source resource for creating a podcast episode about CostPilot. It explains the product in plain English, but includes enough technical detail for a credible conversation with business leaders, builders, investors, Salesforce professionals, and AI operators.

CostPilot is an AI cost-control and governance layer. It is not an AI model by itself. It sits between business systems and AI providers so every AI request can be inspected, routed, pruned, budgeted, audited, and reported.

The core message:

CostPilot helps companies scale AI agents without losing control of cost, risk, and accountability.

---

## 1. The Problem CostPilot Solves

Companies are adding AI into sales, support, operations, finance, engineering, procurement, and productivity workflows. At first, AI usage may feel small and experimental. But once agents and workflows spread across departments, leaders start asking practical questions:

- Who is using AI?
- Which agents are sending the most requests?
- Which departments are driving spend?
- Are simple tasks being sent to expensive models?
- Are long, messy prompts wasting tokens?
- Is sensitive data being sent to AI providers?
- Can finance or compliance explain what happened later?
- Who owns the risk when an AI agent makes a decision?

Without a control layer, AI usage can become invisible. Each team may connect tools directly to an AI provider. The company gets the bill later, but may not know which department caused it, whether the model choice was appropriate, or whether sensitive content was exposed.

CostPilot is built for that gap.

---

## 2. Simple Analogy

If AI agents are a fleet of vehicles, CostPilot is the dashboard, toll tracker, routing system, budget guardrail, and safety monitor.

It does not replace the vehicle. It helps the business operate the fleet responsibly.

Another analogy:

CostPilot is like air traffic control for AI requests. Business systems send requests. AI providers are the destination. CostPilot checks the route, risk, budget, and flight record before the request moves forward.

---

## 3. What CostPilot Does

CostPilot receives AI requests before they go to an AI model. It evaluates the request and decides what should happen.

It can:

- Identify the source platform, department, and agent.
- Register agents in AgentLake.
- Remove unnecessary text from prompts.
- Detect sensitive terms and risky content.
- Block requests before they reach an AI provider.
- Escalate risky or complex requests to stronger model tiers.
- Route routine work to cheaper model tiers.
- Track spend against department budgets.
- Throttle departments that exceed budget caps.
- Record every decision in an audit log.
- Show savings, risk, routing, pruning, and usage in dashboards.
- Simulate high-volume enterprise AI traffic for demos.

The important shift is that AI requests become governed events, not invisible calls.

---

## 4. The End-to-End Flow

A typical CostPilot request works like this:

1. A business system sends a request.
2. CostPilot identifies the platform, department, and agent.
3. AgentLake registers or updates the agent.
4. CostPilot checks sensitive terms and policy rules.
5. If the request is blocked, it stops before reaching any model.
6. If the request is allowed, CostPilot prunes unnecessary context.
7. The routing engine scores the request for complexity and risk.
8. CostPilot chooses the appropriate model tier.
9. Department budget rules are applied.
10. CostPilot calls the selected model or simulates the call.
11. The result is returned.
12. The transaction and audit event are stored.
13. Dashboards and reports update.

This turns a single AI call into a managed business process.

---

## 5. Model Tiers And Smart Routing

CostPilot uses model tiers so every request does not have to go to the most expensive model.

The current tier structure is:

- Scout: Tier 1, cheapest tier for routine work.
- Analyst: Tier 2, for moderate work.
- Advisor: Tier 3, for more complex work.
- Strategist: Tier 4, flagship or premium tier for complex, sensitive, or high-risk work.

Example:

A password reset request probably does not need a flagship model. CostPilot can route that to Scout.

A contract renewal request with legal language may need Advisor or Strategist.

A request containing credit card information may be blocked before reaching any AI model.

The model tier is not random. CostPilot uses the payload, keywords, token count, sensitive terms, agent rules, department budget, and model registry to make the routing decision.

---

## 6. Model Registry

The Model Registry is CostPilot's price menu and routing map.

It stores which models are available, which provider they belong to, what tier they represent, and what they cost.

Each model record can include:

- Model display name.
- API model ID.
- Provider, such as OpenAI or Anthropic.
- Tier, such as Scout, Analyst, Advisor, or Strategist.
- Input token price.
- Output token price.
- Whether the model is enabled.
- Whether it is the default model for a tier.
- Optional department-specific model settings.

CostPilot uses the registry to calculate cost:

```text
input cost = input tokens / 1,000,000 * model input price
output cost = output tokens / 1,000,000 * model output price
total cost = input cost + output cost
```

When a real provider returns usage numbers, CostPilot can use provider-reported input and output token counts. When running in simulated mode, CostPilot uses estimates. For a production-grade deployment, the strongest proof comes from provider-reported usage plus an up-to-date model registry.

---

## 7. Context Pruning

Context pruning is one of the clearest ways CostPilot shows savings.

AI providers charge based on tokens. If a prompt includes old email chains, repeated headers, signatures, disclaimers, and extra whitespace, the company may pay for a lot of content the model does not need.

CostPilot's pruner is an algorithm that quickly removes common noisy patterns before the model call. The goal is not to change the meaning. The goal is to remove waste.

Examples of what the pruner can remove:

- Repeated email headers like From, To, CC, Sent, Subject.
- Long forwarded-message chains.
- Reply history that repeats the same context.
- Corporate signatures.
- Legal disclaimers.
- Ticket metadata.
- HTML tags.
- Inline scripts and styles.
- Excess blank lines.
- Duplicate paragraphs.
- Automated system boilerplate.

Before pruning:

```text
From: customer@example.com
To: support@company.com
Subject: RE: Renewal question

Hi, can you confirm the renewal price?

-----Original Message-----
From: sales@company.com
To: customer@example.com
Subject: RE: Renewal question

Hi, can you confirm the renewal price?

CONFIDENTIALITY NOTICE: This email may contain confidential information...
John Smith
Senior Account Manager
Company Inc.
Phone: 555-123-4567
```

After pruning:

```text
Customer asks: Can you confirm the renewal price?
```

CostPilot records:

- Raw token count.
- Clean token count.
- Tokens saved.
- Reduction percentage.
- Estimated pruning savings.

This gives the user a visible before-and-after story.

Important safety detail:

CostPilot includes code-detection logic. If a payload looks like code, SQL, private key material, or a structured technical snippet, pruning can be skipped so syntax is not accidentally damaged.

---

## 8. Sensitive Terms, Risk, And Blocking

CostPilot includes a sensitive term and policy library.

Terms can trigger different actions:

- Flag: allow the request but log it for review.
- Escalate: route the request to a stronger model tier.
- Block: stop the request before it reaches an AI model.

Examples:

- "audit" may be flagged.
- "legal" or "contract dispute" may escalate.
- "credit card number" or "SSN" may block.

CostPilot can also detect patterns such as phone numbers or email addresses, depending on configuration.

This matters because AI governance is not only about cost. It is also about preventing sensitive data from going places it should not go.

Plain-English example:

A support agent receives a message that includes a customer credit card number. CostPilot can stop that request before any AI provider receives it, then log why the request was blocked.

---

## 9. Department Budgets And Throttling

CostPilot tracks AI spend by department.

Each department can have:

- Monthly budget cap.
- Current spend.
- Used percentage.
- Throttle status.
- Override status.
- Raw payload logging setting.
- Retention setting.
- Throttle floor or maximum allowed tier.

Example:

Sales has a monthly cap. If Sales exceeds the cap, CostPilot can throttle future Sales requests to a cheaper tier unless someone grants an override.

This gives finance and operations teams a practical control:

AI usage can continue, but premium model spend can be limited when a department is over budget.

CostPilot can show:

- How much each department has spent.
- Which departments are healthy.
- Which departments are near the cap.
- Which departments are throttled.
- Which departments have override active.

This turns AI spend from an after-the-fact bill into an active control system.

---

## 10. AgentLake Registry

AgentLake is CostPilot's registry of AI agents.

It answers:

- Which AI agents exist?
- What department owns each agent?
- What platform did the agent come from?
- Is the agent active or idle?
- When was it last used?
- What model tiers can it use?
- Is pruning enabled for that agent?

An AgentLake record can include:

- Agent name.
- Department.
- Platform.
- Target object or record type.
- Status.
- Last active time.
- Tier bounds.
- Pruner setting.
- Archive status.

Example:

`SF-CaseBot` belongs to Support and comes from Salesforce.

`Renewal Quote Agent` belongs to Sales.

`Invoice Review Agent` belongs to Finance.

AgentLake gives the business a map of its AI workforce.

---

## 11. Agent Tier Bounds

Each registered agent can have its own tier settings.

For example:

- A simple support triage bot may be allowed to use Scout through Analyst.
- A legal contract intake agent may be allowed to use Advisor through Strategist.
- A sales follow-up agent may have a minimum tier of Analyst.

This matters because not every agent should have the same power or cost profile.

Agent tier bounds help answer:

- Should this agent be allowed to use premium models?
- Should this agent be prevented from going below a certain tier?
- Should routine agents be forced to stay cheaper?

---

## 12. Audit Log

The audit log is the proof layer.

Every governed request can create an audit record showing what happened and why.

Audit records can include:

- Timestamp.
- Event type.
- Department.
- Agent.
- Platform.
- Model tier.
- Risk level.
- Decision outcome.
- Cost.
- Raw token count.
- Clean token count.
- Tokens saved.
- Matched keywords.
- Budget snapshot.
- Plain-English rationale.
- Whether the raw payload was logged.

Example rationale:

```text
Routine call routed to Scout tier. No high-risk keywords detected. Payload pruned from 1,018 tokens to 238 tokens, saving 780 tokens.
```

Or:

```text
Request blocked by sensitive term policy. Credit card language was detected. No tokens were consumed. No data was sent to the AI provider.
```

This is valuable for compliance, troubleshooting, management review, and executive confidence.

---

## 13. Executive Dashboard

The executive dashboard is designed for leaders who need a quick answer.

It shows:

- Total AI spend avoided.
- Economy routing percentage.
- Requests governed.
- Context pruning savings.
- Projected annual savings.
- Department health.
- Tier split.
- Spend versus all-flagship baseline.
- Monthly spend.
- Routing efficiency.
- Requests governed.
- Savings breakdown.

The key story:

CostPilot shows what the organization spent, what it avoided spending, and why.

The "all-flagship baseline" is important. It estimates what cost would look like if every request went to the premium model tier. The difference between that baseline and CostPilot's actual routing/pruning decisions becomes the savings story.

---

## 14. Operations Dashboard

The operations dashboard is for people monitoring live AI activity.

It includes:

- AgentLake registry.
- Department budget utilization.
- AI decision audit log.
- Agent efficiency rank.
- Routing insights.
- Keywords driving complex routing.
- Compliance events.
- 30-day spend and activity trends.
- Governance event stream.

This page answers:

- What is happening right now?
- Which agents are active?
- Which departments are near budget limits?
- Which requests were blocked, escalated, or routed?
- Which agents are creating cost?
- Where are tokens being pruned?

---

## 15. Reports

CostPilot includes reporting views for different audiences.

Reports can include:

- Savings report.
- Risk and compliance report.
- Department report.
- Bot efficiency report.
- Agent activity report.
- ROI calculator.

The reports help different teams answer different questions.

Finance may care about cost and savings.

Security may care about blocked requests and sensitive terms.

Operations may care about agent performance and volume.

AI leaders may care about model tier distribution and routing quality.

---

## 16. Bot Efficiency

Bot Efficiency is designed to review how agents perform.

It can show:

- Agent scorecards.
- Cost behavior.
- Routing behavior.
- Pruning opportunity.
- Potential recommendations.

The idea is to help teams see which bots are efficient and which ones may need tuning.

For example:

An agent that sends huge prompts but saves few tokens may need better prompt design.

An agent that constantly escalates to Strategist may need clearer tier rules or better classification.

---

## 17. ROI Calculator

The ROI calculator helps estimate the business value of CostPilot.

It can help answer:

- How many AI calls does the company expect per month?
- What would it cost if all calls used premium models?
- What percentage could be routed to lower-cost tiers?
- How much could pruning reduce token usage?
- What might annual savings look like?

This helps a buyer understand the business case before a full deployment.

---

## 18. Trial And Workspace Flow

CostPilot includes a trial experience.

A new user can:

- Start a trial.
- Create or access a workspace.
- Choose a platform.
- Generate setup code.
- Send test traffic.
- View dashboard results.
- Request an upgrade.

Trial accounts can track:

- Workspace ID.
- Trial days remaining.
- Usage limits.
- Spend limits.
- Setup status.
- Upgrade interest.

This is important because buyers need to experience the value quickly.

---

## 19. Onboarding And Connectors

CostPilot supports multiple platform paths:

- Salesforce.
- ServiceNow.
- HubSpot.
- Zendesk.
- Custom API.
- Python.
- Node.js.
- Java.
- Ruby.
- REST/cURL.

The onboarding flow helps users define:

- Platform.
- Department.
- Agent name.
- Object or table.
- Fields to send.
- Optional return fields.

For Salesforce, CostPilot can provide Apex and Flow-oriented setup examples.

For API users, CostPilot can provide direct request examples.

The goal is quick setup without forcing the customer to understand the full backend.

---

## 20. Demo CRM

The Demo CRM is a lightweight fake CRM experience.

It exists because not every tester or buyer has Salesforce, ServiceNow, or another platform ready.

The demo lets people:

- Pick a platform-like scenario.
- Enter their own case or customer data.
- Run the request through CostPilot.
- See the decision.
- Watch the dashboard update.

This helps people understand CostPilot without needing a real enterprise integration first.

---

## 21. Traffic Simulator

The traffic simulator generates high-volume realistic AI activity.

It can send many requests across:

- Customer service.
- Sales.
- Operations.
- Manufacturing.
- Procurement.
- Document processing.
- Finance.
- General productivity.

It is designed to show what CostPilot looks like at scale.

The simulator can create:

- Routine requests.
- Complex requests.
- Compliance-heavy requests.
- Blocked requests.
- Prune-heavy email chains.
- Multi-department traffic.
- Multiple platforms.
- Multiple agents.

This is useful for demos because one or two requests may not show the full value. Fifty or more requests can show patterns:

- Which departments drive cost.
- Which agents use expensive tiers.
- Where pruning saves tokens.
- Where policy blocks risky data.
- How dashboards change under volume.

---

## 22. Live Mode And Simulated Mode

CostPilot supports simulated and live behavior.

In simulated mode:

- CostPilot estimates usage.
- Model responses may be simulated.
- This is useful for demos, testing, and traffic generation.

In live mode:

- CostPilot can call a real AI provider.
- Provider-reported token counts can be stored when available.
- Cost calculations can become more accurate.

The difference matters.

Estimated usage is useful for demo and planning.

Provider-reported usage is stronger proof for production reporting.

---

## 23. Usage Source

CostPilot can label usage as:

- Provider reported.
- Estimated.

This tells the user how strong the cost evidence is.

Provider reported means the AI provider returned actual usage numbers.

Estimated means CostPilot calculated usage based on an internal estimate.

This distinction helps keep the product honest.

---

## 24. Voice Guard

Voice Guard supports transcript-related governance.

It can track:

- Raw transcript when allowed.
- Clean transcript.
- Redaction count.
- PII types found.
- Detection method.
- Confidence score.
- Review flag.

This matters because AI usage is not only text boxes and CRM records. Voice transcripts can also contain sensitive information.

---

## 25. Governance Event Stream

The governance event stream shows recent AI decisions.

It can show:

- Routine routing.
- Escalations.
- Blocked requests.
- Budget enforcement.
- Pruning savings.
- Matched keywords.
- Agent context.
- Budget context.
- Prompt preview.
- Audit download link.

This stream makes AI governance feel live.

Instead of waiting for a monthly bill, the user can see decisions as they happen.

---

## 26. Downloadable Audit Files

CostPilot can provide audit files for individual events.

An audit file can help with:

- Compliance review.
- Debugging.
- Customer proof.
- Internal investigation.
- Pilot validation.

The audit file should represent the decision record, including routing, cost, pruning, matched keywords, and context where allowed by logging policy.

---

## 27. Raw Payload Logging

Raw payload logging is configurable.

When enabled, users may see a "View Original" option in the audit detail.

When disabled, CostPilot may only show the cleaned or summarized payload.

This is intentional because raw payloads may contain sensitive information.

The tradeoff:

- Raw logging improves debugging and proof.
- Raw logging increases privacy and security responsibility.

Production customers should decide retention rules carefully.

---

## 28. Security And Privacy Boundaries

CostPilot includes useful governance controls, but it is not a full security platform by itself.

Current controls include:

- Sensitive term blocking.
- Department raw payload logging toggles.
- Audit records.
- Agent archive instead of hard delete.
- Trial secret key flow.
- PII-oriented detection patterns.

Production hardening should include:

- Strong authentication.
- Tenant isolation.
- Role-based access control.
- Real encryption for customer keys.
- Rate limiting.
- Audit retention policy enforcement.
- Backup and restore process.
- Production monitoring and alerting.
- Provider pricing update workflow.

The podcast should be clear: CostPilot is a strong MVP/private pilot product, but broad enterprise production requires additional hardening.

---

## 29. What CostPilot Is Not

CostPilot is not:

- An AI model.
- A replacement for Salesforce or other systems.
- A replacement for legal or compliance teams.
- A perfect DLP system.
- A guarantee that AI responses are correct.
- A tool that can govern AI calls that bypass CostPilot.
- A magic source of pricing truth unless the model registry is kept current.

This honesty is important. CostPilot's value is control, visibility, routing, pruning, and auditability.

---

## 30. Example Story: Sales Renewal

A sales agent receives a long customer renewal thread.

The thread includes:

- Repeated email headers.
- Old replies.
- Pricing discussion.
- Discount request.
- Contract language.
- Customer urgency.

Without CostPilot:

The agent may send the entire thread to a premium AI model. The company pays for all the repeated content and may not know why the model was selected.

With CostPilot:

1. The request is received from the Sales department.
2. The agent is identified as Renewal Quote Agent.
3. The pruner removes repeated headers and old thread noise.
4. Contract language triggers higher-risk handling.
5. The request routes to Advisor or Strategist.
6. CostPilot records the token savings and routing rationale.
7. The executive dashboard shows spend avoided.

The business gets a cleaner workflow and proof of what happened.

---

## 31. Example Story: Support Block

A support request includes credit card language.

Without CostPilot:

The request may be sent directly to an AI provider.

With CostPilot:

1. Sensitive content is detected.
2. The request is blocked.
3. No model call is made.
4. No tokens are consumed.
5. The audit log records why it was blocked.

This shows CostPilot as a safety layer, not just a savings tool.

---

## 32. Example Story: Operations Budget Control

Operations starts using AI heavily for inventory, vendor, and scheduling work.

Mid-month, usage spikes.

Without CostPilot:

The bill may surprise finance later.

With CostPilot:

1. Operations spend is tracked against its cap.
2. When the department reaches the cap, CostPilot throttles premium routing.
3. Routine work continues on cheaper tiers.
4. High-tier access can require override.
5. The dashboard shows budget-cap savings.

This shows CostPilot as a financial control system.

---

## 33. Why CostPilot Matters Now

AI is moving from experiments to operations.

When AI is experimental, a company may only care whether the tool works.

When AI becomes operational, the company must care about:

- Cost.
- Accountability.
- Risk.
- Visibility.
- Controls.
- Ownership.

CostPilot is built for the second phase: when AI becomes part of daily business operations.

---

## 34. Best Podcast Themes

Good themes for the episode:

- AI agents need rules of engagement.
- Model choice is now a business decision.
- Token waste is hidden spend.
- Governance should happen before the AI call, not only after.
- A company needs a system of record for AI decisions.
- AI adoption will create new operational roles and responsibilities.
- Cost control and safety can support innovation instead of slowing it down.

---

## 35. Suggested Podcast Structure

1. Open with the problem: AI usage is spreading faster than governance.
2. Explain CostPilot in one sentence.
3. Walk through one request from CRM to AI provider.
4. Explain smart routing and model tiers.
5. Explain context pruning with an email-chain example.
6. Explain sensitive term blocking.
7. Explain department budgets and throttling.
8. Explain AgentLake and audit logs.
9. Walk through the dashboards and reports.
10. Explain demo CRM and traffic simulator.
11. Discuss current MVP stage and production hardening.
12. Close with the future: companies need AI control layers.

---

## 36. Short Podcast-Friendly Description

CostPilot is a governance and cost-control layer for AI agents. It sits between business systems and AI providers, evaluates each request, removes unnecessary context, chooses the right model tier, enforces budget rules, blocks sensitive content when needed, and records every decision in an audit trail.

The goal is simple: help companies adopt AI faster without losing control of cost, risk, and accountability.

---

## 37. Key Sound Bites

"CostPilot is not the AI model. It is the control layer around the AI model."

"The expensive part of AI is not always the model. Sometimes it is the messy context companies send into the model."

"Every AI request should have a reason, a cost, a risk level, and an audit trail."

"AI agents need rules of engagement."

"CostPilot helps companies answer the question: what happened before, during, and after the AI call?"

"The dashboard is not just reporting. It is proof that AI usage is being governed."

---

## 38. Final Summary

CostPilot helps companies manage AI requests as business events.

It connects:

- Cost control.
- Smart model routing.
- Context pruning.
- Sensitive data blocking.
- Department budgets.
- Agent governance.
- Audit logging.
- Executive reporting.
- Pilot demos and traffic simulation.

As AI agents become more common, companies will need more than prompts and models. They will need control layers that explain who used AI, what it cost, whether it was safe, and why each decision happened.

That is the role CostPilot is trying to fill.
