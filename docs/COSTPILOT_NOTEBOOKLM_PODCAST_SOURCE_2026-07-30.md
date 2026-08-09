# CostPilot: Governing, Optimizing, and Explaining Enterprise AI

**NotebookLM podcast source document — July 30, 2026**

## Purpose of this document

This document is a fact base for a NotebookLM-generated podcast about CostPilot. It explains the problem, product, features, architecture, business value, implementation approach, and real-world use cases in language suitable for executives, technology leaders, finance teams, governance teams, Salesforce and ServiceNow professionals, and AI builders.

This is not intended to be read word-for-word as a script. NotebookLM should use it as source material for a natural discussion.

CostPilot is currently a pilot-stage product. Some capabilities are implemented in the application, some have working proofs of concept, and some remain part of the product roadmap. A podcast should not describe pilot functionality as a generally available enterprise service unless that distinction is made.

---

## The shortest explanation of CostPilot

**CostPilot is an AI control, optimization, and accountability layer that sits between business applications and AI models, prunes unnecessary content, governs and routes each request, and explains who used AI, for what business work, at what cost, and with what risk.**

A shorter version is:

**CostPilot helps companies control AI before the bill and explain it afterward.**

CostPilot does not replace an AI model, CRM, service-management platform, or AI agent. It coordinates and governs the traffic between them.

---

## Why businesses need CostPilot

Enterprise AI often begins with a few experiments. A sales team adds an assistant to Salesforce. A support group summarizes cases. An operations team uses AI in ServiceNow. Developers connect internal applications directly to model APIs.

The problem emerges when those isolated experiments become a company-wide system:

- Different teams use different providers and models.
- Simple tasks may be sent to expensive flagship models.
- Long prompts contain repeated emails, boilerplate, and irrelevant context.
- Sensitive customer or company information can reach an AI provider.
- Agents may run repeatedly, collide with one another, or exceed budgets.
- Finance sees a provider bill but cannot explain which work caused it.
- Executives cannot easily compare AI use across departments, users, agents, and customers.
- Governance teams cannot reconstruct why a request was allowed, blocked, rerouted, or pruned.
- Business leaders cannot tell whether AI spend belongs to an account, project, case, engagement, or internal activity.

Traditional API monitoring can count calls. Provider dashboards can show model consumption. Neither automatically provides the business context needed to answer questions such as:

- Which customer accounts generated the most AI cost?
- Who used the most tokens last week?
- Which agents are active, idle, or unusually expensive?
- Why was a request blocked?
- How many tokens did pruning remove?
- Which department is approaching its budget?
- Are we using a premium model for routine work?

CostPilot is built to make each AI request a governed, attributable business event.

---

## The four pillars of CostPilot

### 1. Optimize

CostPilot reduces unnecessary AI cost before it occurs.

Optimization capabilities include:

- Context pruning.
- Token reduction.
- Complexity-based model routing.
- Model-tier selection.
- Price-aware model configuration.
- Spend-avoided calculations.
- Visibility into inefficient routing patterns.

### 2. Govern

CostPilot determines what AI is permitted to do and records why.

Governance capabilities include:

- Sensitive-term policies.
- Allow, flag, escalate, and block outcomes.
- Department budgets and warning thresholds.
- Throttling and explicit supervisor overrides.
- Agent model-tier boundaries.
- Collision handling through block, queue, lock, or skip behavior.
- Reviewed and unreviewed risk-event workflows.

### 3. Attribute

CostPilot connects technical consumption to recognizable business ownership.

Attribution dimensions can include:

- Workspace or company.
- Source platform.
- Department or team.
- User or automated process.
- AI agent.
- Customer or account.
- Project, matter, engagement, case, opportunity, or other business work.
- Source record and parent record.
- Model, tokens, spend, pruning, risk, and timestamp.

### 4. Observe

CostPilot turns the governed activity into operational and executive intelligence.

Observation capabilities include:

- Executive dashboard.
- AI usage attribution reports.
- AgentLake.
- Audit and governance-event history.
- Model-cost and routing reports.
- Savings analysis.
- Ask CostPilot, a conversational analytics assistant.

---

## How a governed AI request works

A complete CostPilot-controlled request can follow this path:

1. A person, workflow, or AI agent starts a task in Salesforce, ServiceNow, HubSpot, custom code, or another connected system.
2. The source sends the request and its business context to CostPilot.
3. CostPilot identifies the workspace, source platform, department, user, agent, record, and related business work when those values are available.
4. The agent is resolved or registered in AgentLake.
5. CostPilot evaluates sensitive terms, risk rules, budgets, and agent policies.
6. If policy blocks the request, it stops before a model receives the content.
7. If allowed, CostPilot prunes unnecessary context when pruning is safe.
8. The routing engine estimates the work's complexity.
9. CostPilot chooses an appropriate model tier and an enabled model from the registry.
10. The request is sent to the model provider.
11. The model response returns through CostPilot to the originating system.
12. CostPilot records the decision, model, token usage, cost, savings, risk, user, agent, and business context.
13. Dashboards, AgentLake, attribution reports, and audit history update.

The central design principle is that the full AI call flows through CostPilot. Passive reporting alone cannot block unsafe content, prune tokens before billing, or change the selected model.

---

## Context pruning

Context pruning is one of CostPilot's most important differentiators.

Business payloads often contain useful content mixed with expensive noise:

- Email signatures.
- Repeated reply chains.
- Forwarded-message headers.
- Legal disclaimers.
- Duplicate paragraphs.
- HTML and formatting artifacts.
- Ticket boilerplate.
- Excess whitespace.
- Repeated machine metadata.

CostPilot attempts to remove content the model does not need while preserving the meaning of the task. It records the original estimate, the cleaned estimate, tokens removed, percentage reduced, and associated savings.

Pruning is not applied blindly. Formatting can be meaningful in source code, SQL, certificates, structured payloads, and other technical content. CostPilot can skip pruning when modification could damage the request.

This matters because routing and pruning solve different problems. Routing chooses a more appropriate model. Pruning reduces the amount billed regardless of which model is used.

---

## Model routing and the model registry

CostPilot uses logical model tiers so every task does not default to the most capable and expensive model.

The current tier concept is:

- **Scout** for routine, high-volume work.
- **Analyst** for moderately complex work.
- **Advisor** for deeper reasoning or sensitive work.
- **Strategist** for the highest-complexity or mission-critical work.

Routing can consider:

- Prompt size.
- Complexity indicators.
- Sensitive-term outcomes.
- Agent-specific tier permissions.
- Department budget state.
- Enabled models and defaults.
- Explicit user or policy constraints.

The Model Registry stores the real models behind those tiers. A registry entry can include the provider, API model ID, display name, tier, enabled state, default state, and input and output prices.

This separation allows a company to change providers or model versions without redesigning every workflow. The business policy can continue to say “routine work belongs in Scout” while administrators change the model assigned to Scout.

Cost calculations generally follow:

`input cost = input tokens / 1,000,000 × input price`

`output cost = output tokens / 1,000,000 × output price`

`total cost = input cost + output cost`

Provider-reported token usage is preferable for production accuracy. Simulated or estimated values must be labeled as such.

---

## Sensitive content, policy, and risk

CostPilot can evaluate requests for configured sensitive terms and policy conditions before content is sent to a model.

Possible outcomes include:

- Allow.
- Allow and flag for review.
- Escalate to a different control path or model tier.
- Block before the provider call.

Sensitive-term matching must use proper token or phrase boundaries. For example, the term “NDA” must not match the letters inside “Monday.”

Policies need flexibility. A protected default may be useful, but customers should not be permanently locked into a setting that does not fit their business. Administrators need controlled options to enable, disable, add, remove, and review terms.

Blocked-event notifications can be marked reviewed without deleting the audit evidence. Budget warnings should remain visible even after blocked events are acknowledged because those are separate operational signals.

---

## Budgets, warnings, and throttling

CostPilot supports budgets at the department level and is evolving budget control for business contexts such as accounts and projects.

Budget behavior can include:

- Monthly caps.
- Percentage-used calculations.
- Near-cap warnings.
- Over-cap alerts.
- Model throttling.
- Recommended action.
- Explicit human override.
- Logged restoration of premium-model access.

An important distinction is that selecting a model tier is not the same as granting a supervisor override. Overrides must be explicit, attributable to a human decision, and preserved in the audit history.

---

## AgentLake

AgentLake is the operational registry for AI agents observed or configured across the business.

It helps answer:

- Which agents exist?
- Which platform and department owns each agent?
- Which agents are actively used?
- Which agents are idle?
- How many calls, tokens, and dollars are associated with each agent?
- Which agents have risk events?
- Which agents need review?
- Which agents contribute to a particular customer or business context?

AgentLake supports compact views, department grouping, usage-oriented rankings, and project or business-context views. It is not meant to replace the source platform's agent builder. It gives the company one cross-platform operating view.

---

## Business Context Engine and record rollups

Raw record IDs are reliable linking keys, but they are poor executive language. CostPilot's Business Context Engine translates technical records into the terms a company already uses.

During onboarding, a customer can answer simple questions such as:

- What do you call the work you deliver?
- What do you call the customer?
- Which source records represent the parent business context?
- Which related records should roll up to that parent?
- Which dimensions do you want to measure?

One company may call the work “projects.” Another may call it “matters,” “engagements,” “accounts,” “cases,” “clients,” or “initiatives.” CostPilot should use that language throughout navigation, filters, cards, and reports.

### Parent-child example in Salesforce

Assume an Account is the parent business context. The same customer also has Opportunities, Cases, Contacts, Quotes, and custom records.

When AI is used on any of those related records:

- The source record ID remains the permanent technical key.
- The source record name remains visible for drill-down.
- The relationship is resolved to the Account.
- Tokens, requests, cost, risk, user, and agent roll up to that Account.
- The executive can view the Account total and then drill into the contributing records.

This prevents every Opportunity or Case from becoming an unrelated “project.”

### Custom objects

The same principle can support a custom Project object with child tasks, deliverables, cases, or approvals. Metadata discovery can recommend related objects and relationship paths. An administrator approves the mapping rather than manually designing every field connection.

Records without an approved relationship can be tracked separately, left unattributed, or handled according to workspace policy.

---

## User attribution

CostPilot can associate AI consumption with the person or automated identity that initiated the request.

Useful identity data includes:

- External user ID.
- Display name.
- Email.
- Source platform.
- Department or organizational unit.
- Whether the actor is a person, system account, or automated process.

This enables questions such as:

- Who used the most tokens last week?
- Which users generated the most AI spend?
- Which agents did a user call?
- Which accounts or business contexts did that activity support?

The system reports consumption. It should not claim that high or low usage proves employee productivity or business performance.

---

## Ask CostPilot

Ask CostPilot is the conversational analytics layer designed to make the platform useful without requiring leaders to build reports manually.

Example questions include:

- How much did we spend on AI this month?
- Who used the most tokens last week?
- Which five users had the lowest AI spend?
- Which agents generated the most requests?
- Which accounts cost the most in AI?
- Which departments are close to budget?
- How many tokens did pruning remove?
- Why were requests blocked?
- Show the latest risk events.
- Which model is receiving too much routine traffic?
- Where could we save money?

Strong answers should include:

- The exact date range.
- Active filters.
- Whether the data is live, simulated, or mixed.
- The calculation used.
- Supporting evidence.
- Drill-down links.
- A clear statement when the requested data is unavailable.

Ask CostPilot should use natural-language interpretation, but calculations must come from governed CostPilot data rather than from a language model inventing numbers. The product is being hardened to support more question types, follow-up context, governance questions, and reliable fallbacks.

The goal is for Ask CostPilot to be available from any CostPilot page while respecting that page's workspace and filters.

---

## Executive dashboard and reporting

The executive dashboard should answer a small set of important questions at a glance:

- How much AI did the company use?
- How much did it cost?
- How much did pruning and routing avoid?
- Are budgets healthy?
- Are there risks requiring attention?
- Where is usage concentrated?
- Which people, agents, departments, models, and business contexts caused it?

Core metrics include:

- AI spend.
- Annualized savings.
- Tokens pruned.
- Budget used.
- Governed requests.
- Active business contexts.
- Risk and control events.

Reports provide deeper attribution across:

- Date.
- Department.
- Person.
- Agent.
- Source platform.
- Account or business context.
- Record type.
- Model and tier.
- Live versus simulated activity.

Filtering and drill-down are essential. A summary without the ability to see the underlying people, agents, records, and audit events is not sufficient for business accountability.

Large result sets should use bounded panels, paging, compact tables, or expandable sections rather than forcing every component to extend indefinitely down the page.

---

## Audit history

Every governed request can generate evidence describing what CostPilot did and why.

An audit record can include:

- Timestamp.
- Workspace.
- Source system.
- User.
- Agent.
- Department.
- Source record.
- Parent business context.
- Matched policy terms.
- Pruning decision.
- Routing decision.
- Selected tier and model.
- Input and output tokens.
- Cost.
- Risk level.
- Decision outcome.
- Rationale.
- Live or simulator origin.

The audit history supports investigation, compliance review, troubleshooting, and financial explanation. It should not expose sensitive raw payloads unnecessarily.

---

## Salesforce use case

A seller opens an Opportunity and asks Agentforce to summarize it or draft an email.

The governed flow can:

1. Send the prompt, Salesforce record ID, object type, record name, user, agent, and department to CostPilot.
2. Resolve the Opportunity to its Account.
3. Apply governance and pruning.
4. Select a model.
5. Send the request to the provider.
6. Return the answer to Agentforce.
7. Record the user's tokens and cost under the Account while retaining the Opportunity as the source record.

The same pattern works for Cases, Contacts, Quotes, custom objects, and Flows.

The customer-facing goal is a managed Salesforce package with:

- A guided CostPilot Setup experience.
- Secure organization authorization.
- Packaged credentials.
- The invocable CostPilot action.
- Permission sets.
- Agentforce and Flow entry-point selection.
- Guided verification.

Salesforce still requires an administrator to authorize access, choose which agents or flows CostPilot governs, add the packaged action where appropriate, and activate the configuration. The product goal is to minimize those manual steps rather than hide the fact that Salesforce security requires them.

---

## ServiceNow use case

A change manager asks for an AI summary and recommended next step on a Change Request.

A ServiceNow Flow Designer action can:

1. Accept the prompt, table name, record `sys_id`, task, agent, and department.
2. Load the source record.
3. Send the request through CostPilot.
4. Return the AI response, model, tier, routing decision, cost, tokens, pruning, and work-item information.
5. Store the activity under the ServiceNow Change Request or its approved parent business context.

Only intentional AI requests should be counted. A generic insert-triggered Business Rule that fires on every ordinary record creation would incorrectly represent normal database activity as AI usage and should be disabled.

This demonstrates the universal pattern: the source platform can differ while the CostPilot governance envelope remains consistent.

---

## Additional business use cases

### Professional services

A consulting, accounting, or legal organization attributes AI consumption to matters or engagements. Leaders can see the true AI cost of fixed-fee work, even if the organization does not directly rebill every AI call.

### Customer support

Support agents summarize long cases. CostPilot removes duplicated history, routes routine summaries to a lower-cost model, blocks restricted content when required, and attributes spend to the support team and customer.

### Sales

Sales agents draft outreach, summarize accounts, and analyze opportunities. Activity from related records rolls up to the customer account, allowing leaders to see complete AI consumption by customer.

### Finance and operations

AI reviews invoices or change requests. CostPilot records which workflow, user, record, and model produced the cost while applying risk and budget controls.

### Software development

Developer tools send code-related requests. CostPilot can identify that pruning may be unsafe, preserve structured content, route based on complexity, and attribute activity to a team or initiative.

### Multi-agent operations

Several agents contribute to the same business context. CostPilot can show the combined account or project total, then separate the contribution of each agent and user.

---

## Simulator and pilot data

CostPilot includes an enterprise traffic simulator for demonstrations and testing.

The simulator can generate batches such as 25, 50, or 100 understandable AI requests with:

- Named people.
- Departments.
- Agents.
- Source platforms.
- Business contexts.
- Mostly normal activity.
- A smaller number of deliberate policy or risk events.

Simulator data must be visibly distinguishable from live customer data. Reports and Ask CostPilot should identify whether evidence is live, simulated, or mixed.

Workspace reset options should be explicit:

- **Reset usage data** clears calls, cost, tokens, audit history, and risk events.
- **Reset simulator data** also removes simulator-created accounts, users, agents, and business contexts.
- **Reset entire workspace** is a destructive administrative operation requiring strong confirmation.

---

## Universal implementation approach

CostPilot's universal architecture separates business meaning from platform-specific mechanics.

### Universal governance contract

Every connector should send a common envelope containing:

- Source system.
- Workspace.
- Agent.
- Actor.
- Department.
- Business work.
- Parent context.
- Request and payload.
- Governance preferences.

### Platform adapters

Salesforce may use an invocable Apex action and managed package. ServiceNow may use a Flow Designer action. HubSpot may use an app and workflow action. Custom systems may use Python, Node, Java, Ruby, or direct REST.

The adapter changes. The CostPilot contract does not.

### Metadata discovery

After a secure login, CostPilot can inspect permitted metadata and recommend:

- Likely parent objects.
- Related child objects.
- Name and identifier fields.
- User and ownership fields.
- Department fields.
- Agentforce agents and Flows.

The administrator approves recommendations. CostPilot should not silently infer high-impact mappings without confirmation.

---

## Security and trust principles

Enterprise credibility requires:

- Workspace isolation.
- Reliable customer authentication.
- Encrypted credentials.
- Revocable connections.
- Least-privilege permissions.
- No secrets exposed in frontend code.
- Auditability of policy and override changes.
- Clear data-retention controls.
- Separation of live and simulated data.
- Transparent limitations.

CostPilot can only control calls that pass through it or provide compatible telemetry. It should never claim visibility into AI traffic it did not receive.

---

## What makes CostPilot different

CostPilot combines functions that are often separated:

- API gateways can route traffic but may not understand business ownership.
- Provider dashboards show model usage but usually lack cross-platform context.
- FinOps tools track spend but may not govern individual prompts before execution.
- Security tools detect risk but may not optimize tokens and model selection.
- CRM and ITSM platforms know the records but do not provide one cross-provider AI control plane.

CostPilot connects the request, user, agent, model, policy, cost, and business record in one explainable chain.

The strategic progression is:

**AI gateway → governance → AI FinOps → business-context intelligence**

The Business Context Engine ties those stages together.

---

## Current maturity and honest product boundaries

The current product demonstrates the core CostPilot thesis:

- Governed request routing.
- Context pruning.
- Model tiers and registry.
- Policy and budget controls.
- AgentLake.
- Business-context attribution.
- Salesforce and ServiceNow integration proofs.
- Executive and detailed reporting.
- Audit evidence.
- Simulation.
- Conversational analysis through Ask CostPilot.

Before broad enterprise launch, the product still needs continued hardening in areas such as:

- Multi-tenant authentication and workspace enforcement.
- Managed-package installation and upgrade reliability.
- Connector permission minimization.
- Broader automated testing.
- Data retention and deletion controls.
- Production monitoring and failure recovery.
- More complete question coverage in Ask CostPilot.
- High-volume performance validation.
- Clear customer onboarding and support procedures.

This does not weaken the product story. It separates a working pilot from a production promise.

---

## Suggested podcast narrative

### Opening

AI adoption is accelerating faster than companies can explain or control it. The central problem is no longer simply obtaining access to a model. It is understanding thousands of AI decisions spread across people, agents, platforms, customers, and workflows.

### Middle

Explain the journey of one request:

- It begins inside a business application.
- CostPilot identifies its business context.
- Policy checks it.
- Pruning removes waste.
- Routing selects an appropriate model.
- The provider returns an answer.
- CostPilot attributes and audits the result.

Then expand from one request to a company-wide view across Salesforce, ServiceNow, agents, users, departments, accounts, and models.

### Closing

The long-term opportunity is not merely reducing an API bill. It is giving businesses a control and intelligence layer for AI work: knowing where AI is used, what it costs, what it touches, why it was allowed, and how to improve it.

---

## Useful sound bites

- “CostPilot controls AI before the bill and explains it afterward.”
- “Every AI request should have an owner, a purpose, a policy decision, and a cost.”
- “The model bill tells you what you bought. CostPilot tells you why the business bought it.”
- “Pruning removes waste before a model can charge for it.”
- “The goal is not to send every task to the cheapest model. It is to use the least expensive model appropriate for the work.”
- “A record ID is the technical key; business context is the language leaders understand.”
- “CostPilot does not score employee productivity. It explains AI consumption.”
- “AI governance is strongest when control, cost, and business context share the same evidence.”
- “AgentLake is the operating view for a company's growing AI workforce.”
- “Ask CostPilot turns governed telemetry into questions an executive can actually ask.”

---

## Questions for the podcast hosts

- Why do provider dashboards struggle to explain business ownership?
- How is governing an AI request different from monitoring an API?
- Why is context pruning a separate value proposition from model routing?
- When should a request be blocked rather than flagged?
- How should companies balance cost savings against model quality?
- Why should Opportunities, Cases, and Contacts roll up to a customer Account?
- What can leaders responsibly learn from user-level AI consumption?
- What should they avoid inferring from it?
- How can one governance contract work across Salesforce, ServiceNow, HubSpot, and custom code?
- What manual steps will always remain because of enterprise security?
- Why must live and simulated activity be visibly separated?
- What evidence should accompany an AI-generated executive answer?
- How does CostPilot evolve from middleware into AI business intelligence?

---

## Final summary

CostPilot addresses a growing enterprise problem: AI is becoming distributed across applications, agents, users, providers, and business processes while financial and governance visibility remains fragmented.

CostPilot creates a controlled path for AI requests. It prunes unnecessary context, applies policy, manages budgets, chooses an appropriate model, records the result, and connects that result to the people and business work that caused it.

Its most important idea is not simply cheaper model routing. It is complete accountability:

**who used AI, through which agent, for which customer or business context, using which model, at what token cost, under which policy decision, and with what evidence.**

