# CostPilot: Complete Product Source for NotebookLM

## Document purpose

This document is source material for explaining CostPilot in a podcast, briefing, presentation, or long-form conversation. It covers the complete product vision, the functionality currently represented in the CostPilot application, the major user journeys, the technical flow, real-world examples, product boundaries, implementation status, and future direction.

This is not a podcast script. It is a fact base that NotebookLM can use to create one.

The most important distinction throughout this document is:

- **Implemented** means the capability exists in the current CostPilot application or codebase.
- **Proof of concept** means the capability exists in a working integration package or test path but is still being validated and hardened.
- **Planned direction** means the idea is part of the product strategy but should not be presented as generally available.

---

## 1. CostPilot in one sentence

**CostPilot is an AI control, optimization, and accountability layer that sits between business applications and AI models, removing unnecessary tokens, selecting the appropriate model, enforcing policies and budgets, and showing which people, agents, customers, and business projects caused the usage.**

An even shorter version:

**CostPilot helps companies control AI before the bill and explain it afterward.**

---

## 2. Why CostPilot exists

Companies are rapidly adding AI to Salesforce, ServiceNow, HubSpot, support systems, internal applications, autonomous agents, and custom software. The first few experiments are usually easy to understand. One team uses one model for one workflow.

The problem appears when usage spreads:

- Many departments begin using different AI tools.
- Multiple AI agents perform overlapping work.
- Simple tasks are sent to premium models.
- Long prompts include repeated or unnecessary context.
- Sensitive business or customer information can reach model providers.
- Finance receives a bill without knowing which business work created it.
- Compliance teams cannot reconstruct why a request was allowed, blocked, or rerouted.
- Project leaders cannot see the true AI cost of delivering work.
- Executives see model-provider spend but not business value or accountability.

Traditional cloud FinOps starts with infrastructure resources. AI introduces a different unit of control: the individual request. Each request can vary in length, risk, complexity, model, price, user, agent, customer, and business purpose.

CostPilot is designed to make each AI request a governed business event.

---

## 3. What CostPilot is and is not

### CostPilot is

- An AI request control layer.
- A context-pruning and token-optimization engine.
- A model-routing system.
- A policy and budget enforcement system.
- An AI agent registry and operations view.
- A work-attribution system for projects, matters, cases, engagements, and other business work.
- An audit and reporting layer.
- A universal integration framework for business systems and custom code.
- A foundation for AI business intelligence.

### CostPilot is not

- An AI model.
- A replacement for Salesforce, ServiceNow, HubSpot, or a project-management system.
- A general-purpose AI agent builder.
- A guarantee that an AI answer is correct.
- A replacement for legal, security, privacy, or compliance judgment.
- A system that can govern requests that never pass through it or never send telemetry to it.
- A complete billing system for rebilling customers.
- A promise that every connector is production-ready today.

CostPilot controls and explains AI execution. It does not try to own every business workflow around that execution.

---

## 4. The four product pillars

CostPilot is organized around four pillars.

### Optimize

Reduce unnecessary AI cost before it happens.

Capabilities include:

- Context pruning.
- Token reduction.
- Model-tier routing.
- Model-price awareness.
- Spend-avoided calculations.
- Model-routing evidence and optimization opportunities.

### Govern

Control what AI is permitted to do.

Capabilities include:

- Sensitive-term policies.
- Allow, flag, escalate, and block decisions.
- Budget caps and throttling.
- Agent tier boundaries.
- Collision behavior.
- Policy testing.
- Blocked-event review and acknowledgement.

### Attribute

Connect technical AI usage to business ownership.

Capabilities include:

- Departments.
- Agents.
- Users.
- Projects, matters, cases, engagements, or custom work terminology.
- Customers or accounts.
- Source platforms and source records.
- Work-level spend, requests, risk, and token usage.

### Observe

Understand activity, outcomes, and trends.

Capabilities include:

- Executive dashboard.
- AgentLake operations.
- Audit history.
- Reports.
- Model-routing outcomes.
- Agent activity and efficiency.
- Savings and risk analysis.

Every major CostPilot feature should support at least one of these pillars.

---

## 5. The end-to-end request journey

A governed CostPilot request generally follows this sequence:

1. A user, workflow, or AI agent begins a task in a business application.
2. The business application sends the request or governance envelope to CostPilot.
3. CostPilot identifies the workspace, platform, department, agent, user, and business-work context when available.
4. CostPilot resolves or registers the agent in AgentLake.
5. CostPilot evaluates sensitive terms and governance policies.
6. A blocked request stops before an AI model is called.
7. An allowed request enters context pruning when pruning is enabled and safe.
8. CostPilot estimates complexity and determines the appropriate model tier.
9. Agent-specific tier limits and department budget rules can modify that decision.
10. CostPilot selects an enabled model from the model registry.
11. The configured provider path is called, or a simulated response is used in demonstration mode.
12. CostPilot records tokens, model, cost, savings, routing rationale, attribution, and risk.
13. The result returns to the originating system.
14. Executive, operational, work-attribution, audit, and reporting views update.

The request becomes more than an API call. It becomes a controlled and explainable business transaction.

---

## 6. Context pruning: a major CostPilot capability

Context pruning is one of CostPilot's most important optimization features.

AI providers generally charge according to token usage. Business-system payloads often contain useful information mixed with repeated, stale, or machine-generated noise. Examples include:

- Email reply chains.
- Repeated sender and recipient headers.
- Corporate signatures.
- Legal disclaimers.
- Forwarded-message separators.
- Duplicate paragraphs.
- HTML and formatting artifacts.
- Ticket boilerplate.
- Excess whitespace.
- Repeated system metadata.

CostPilot attempts to remove unnecessary content before the model call while preserving the meaning required to perform the task.

### What CostPilot records

The pruning pipeline can record:

- Original token estimate.
- Cleaned token estimate.
- Tokens removed.
- Reduction percentage.
- Estimated dollar savings.
- Whether pruning was applied or skipped.
- The reason pruning was skipped.

### Safety controls

Pruning must not blindly modify every payload. CostPilot contains protections for content that appears to be:

- Source code.
- SQL.
- Structured technical syntax.
- Keys or certificates.
- Payloads where formatting may be semantically important.

For those payloads, CostPilot can skip pruning to avoid breaking the request.

### Why this matters

Model routing changes which model receives a request. Pruning changes how much content is billed regardless of model. That makes pruning a distinct and defensible source of savings.

### Governance visibility

Pruning is represented as a visible stage in the policy flow and as an executive metric. Administrators can control whether it is enabled, but changes to policy controls must not alter the underlying pruning algorithm unintentionally.

---

## 7. Model routing

CostPilot is designed so every request does not automatically use the most expensive model.

### The four model tiers

- **Scout**: routine, high-volume, lower-cost work.
- **Analyst**: moderately complex work.
- **Advisor**: deeper reasoning for complex or sensitive work.
- **Strategist**: highest-capability models for mission-critical work.

The tier names can be customized for a workspace, but the four-level concept remains useful for routing.

### Routing inputs

The routing engine can consider:

- Prompt length and estimated tokens.
- Complexity keywords.
- Sensitive-term policy results.
- Explicit tier instructions.
- Department budget state.
- Agent minimum and maximum tiers.
- Enabled models in the registry.
- Default model for each tier.
- Requested model where supported.

### One-sentence explanation

**CostPilot scores each request by complexity, risk, token volume, agent rules, budget status, and model availability, then selects the lowest-cost enabled model tier that satisfies those requirements.**

### Routing evidence

The models experience now goes beyond showing a static model catalog. It includes evidence about actual routing outcomes, such as:

- Which models received governed requests.
- How often a model was selected.
- Why requests were routed there.
- Whether a cheaper eligible model could have handled some requests.
- Which model or tier is creating a spend concentration.

This makes model optimization actionable instead of theoretical.

### Important limitation

Routing quality depends on correct model metadata, pricing, availability, and rules. CostPilot must keep the model registry current and must clearly distinguish provider-reported usage from inferred or simulated attribution.

---

## 8. Model registry and cost calculation

The model registry is CostPilot's model catalog, price table, and routing-eligibility map.

Each model can include:

- Display name.
- Provider.
- API model ID.
- CostPilot tier.
- Input price per million tokens.
- Output price per million tokens.
- Cached-input price where applicable.
- Enabled or disabled status.
- Default status for its tier.
- Routing eligibility.

Basic cost calculation:

```text
input cost  = input tokens  / 1,000,000 × input price
output cost = output tokens / 1,000,000 × output price
total cost  = input cost + output cost
```

The executive dashboard can show input versus output cost by real model name. If a provider reports only total cost and not the split, CostPilot may estimate the split from token information. The interface should disclose when a value is inferred.

CostPilot also maintains a known-model preset library to make model registration faster.

---

## 9. Sensitive terms, risk, and policy controls

CostPilot includes a configurable sensitive-term library and pattern-matching controls.

Policy actions include:

- **Flag**: allow the request but create a review signal.
- **Escalate**: route the request to a stronger tier.
- **Block**: stop the request before model execution.

Examples:

- A legal or audit-related term might trigger escalation.
- A risky financial-data term might trigger a flag.
- A social security number or credit-card pattern might trigger a block.

### Word-boundary protection

Sensitive-term detection must match the intended term, not an accidental substring. For example, `NDA` should not match the word `Monday`. Correct word-boundary handling prevents false positives.

### Configurability

Organizations have different policies and use cases. Sensitive terms—including recommended or protected starter terms—must be configurable. Administrators can enable, disable, update, or remove terms according to their authority and business requirements.

### Policy sequence

The Govern experience explains the order of decisions, including:

1. Sensitive-data and policy checks.
2. Pruning eligibility.
3. Complexity and routing.
4. Agent constraints.
5. Budget enforcement.
6. Execution and audit.

### Blocked-event review

Blocked requests remain part of the audit history, but executives should not be forced to see a permanent unreviewed count forever. CostPilot supports acknowledging blocked events as reviewed without deleting the underlying audit evidence.

---

## 10. Department budgets and throttling

Departments can have separate AI budgets.

A department budget can include:

- Monthly cap.
- Current spend.
- Percentage used.
- Throttle state.
- Override state.
- Maximum tier allowed while throttled.
- Retention and raw-payload settings.

### When a department approaches or exceeds its cap

CostPilot can:

- Warn.
- Restrict future requests to cheaper tiers.
- Block according to policy.
- Allow an authorized override.

The goal is not always to stop AI. Often the better response is to preserve the workflow while preventing uncontrolled premium-model spend.

### Executive interpretation

Budget health should distinguish:

- Healthy usage.
- Near-cap warning.
- Over-cap departments.
- Risk events unrelated to budget.

Recommended actions should point users to the actual control that needs attention.

---

## 11. AgentLake

AgentLake is CostPilot's registry and operational view of AI agents.

It answers:

- Which agents exist?
- Which agents are actually being used?
- Which agents have never been used?
- Which agents are expensive?
- Which agents are pruning efficiently?
- Which agents need review?
- Which platforms and departments own them?
- Which projects use them?

### Agent registration

Agents can be registered explicitly or appear when they make their first governed request.

An agent record can include:

- Name.
- Department.
- Source platform.
- Status.
- Request count.
- Spend.
- Average cost per call.
- Tokens pruned.
- Last activity.
- Minimum and maximum model tier.

### Usage-focused view

AgentLake's ranking is designed around agent usage and attention, not a vague score alone. It helps users see:

- Frequently used agents.
- Low-usage agents.
- Never-used agents.
- High-cost agents.
- Agents requiring review.

### Multiple views

To avoid an overwhelming wall of cards, AgentLake includes:

- Overview.
- Department-grouped, collapsible view.
- All-agents view.
- Projects view.

Expanded department and project groups should remain open while the page refreshes.

### Agent administration boundary

AgentLake owns operational visibility. Administrative actions such as lifecycle management and permissions belong in Administration, with links between the experiences.

---

## 12. Collision control

A collision occurs when multiple agents attempt to claim or operate on the same record or work item at the same time.

CostPilot supports collision behavior such as:

- **Block**: reject the later request.
- **Queue**: hold it until the resource is available.
- **Skip**: do not perform the conflicting action.

Collision events can be recorded separately from the resulting action. For example, “26 blocked, 26 collision” can mean 26 collision events occurred and all 26 were handled with the block policy.

Collision controls are useful when agents can modify business records. They are less relevant for purely read-only requests.

---

## 13. Work Attribution

Work Attribution connects AI usage to what the business was trying to accomplish.

The universal internal concept is a **work item**, but customers can use their own language:

- Project.
- Matter.
- Case.
- Engagement.
- Campaign.
- Deal.
- Incident.
- Work order.
- Client assignment.
- Custom terminology.

### Work records

A CostPilot work item can include:

- External identifier.
- Name.
- Owner.
- Department.
- Customer or account.
- Status.
- Monthly AI budget.
- Source platform.
- Source object and record ID.
- Business Context template.
- Request count.
- Spend.
- Tokens.
- Pruning.
- Risk events.
- Last activity.

### Multiple agents per project

One project can use multiple AI agents. CostPilot supports:

- Assigning existing agents to a project.
- Creating an agent while assigning it to a project.
- Viewing the project team.
- Removing an agent assignment.
- Tracking usage by agent within the project context.

### Multiple users per project

CostPilot also supports project participants and user attribution.

A project-user relationship can include:

- External user ID.
- Name.
- Email.
- Role.
- Status.
- Whether the user can use AI for that project.
- Source platform.

This allows CostPilot to answer not only “Which project spent the money?” but also “Which user and agent caused the request?”

### What Work Attribution does not replace

CostPilot does not manage:

- Project schedules.
- Milestones.
- Files.
- Task dependencies.
- General collaboration.

Those remain in Salesforce, ServiceNow, HubSpot, legal-practice software, project-management tools, or other systems of record.

---

## 14. Business Context Engine

The Business Context Engine is the concept that ties universal integrations to business meaning.

The strategic insight is:

**Do not sell field mapping. Sell business understanding.**

Instead of beginning onboarding with technical questions, CostPilot begins with:

**“What do you call your work?”**

The user can choose a suggested term or enter a custom value.

CostPilot can then learn:

- What the organization calls its unit of work.
- What system stores that work.
- Which field identifies it.
- Which field names it.
- Who owns it.
- Which customer it belongs to.
- Which users and agents participate.
- What the organization wants to measure.

### Business Context Templates

Templates provide reusable semantic mappings for:

- Salesforce.
- ServiceNow.
- HubSpot.
- Custom applications.
- Partner-defined systems.

Templates can describe common meanings such as:

- Work ID.
- Work name.
- Owner.
- Customer.
- Status.
- Content.
- Department.

The long-term product progression is:

1. AI gateway.
2. Governance.
3. AI FinOps.
4. AI business intelligence.

Business context is what allows CostPilot to move from technical telemetry to business intelligence.

---

## 15. Universal integration architecture

CostPilot is designed around one connector lifecycle rather than a different product for each platform.

### Universal connector contract

A connector describes:

- Platform identity.
- Authentication method.
- Discovery capability.
- Source objects.
- Field metadata.
- Semantic mappings.
- Runtime request envelope.
- Connection status.

The lifecycle is:

1. Connect.
2. Authenticate.
3. Discover metadata.
4. Recommend mappings.
5. Let the user approve or adjust.
6. Save the mapping.
7. Validate the runtime connection.
8. Govern and attribute requests.

### Why this matters

A Salesforce customer, ServiceNow customer, HubSpot customer, and custom-code customer should experience the same CostPilot concepts:

- Connect the system.
- Let CostPilot inspect metadata.
- Confirm business meaning.
- Send governed requests.
- See results in the same dashboards.

Only the adapter should differ.

---

## 16. Connection Registry and metadata discovery

CostPilot includes a persistent connection registry.

A connection record can store:

- Workspace.
- Platform.
- Display name.
- Environment.
- Authentication state.
- Instance URL.
- Encrypted access and refresh tokens.
- Discovered objects.
- Approved mappings.
- Connection timestamps and status.

### Salesforce discovery

The current Salesforce adapter supports:

- OAuth authorization.
- Production or Developer Edition login.
- Sandbox login path.
- Salesforce object discovery.
- Field metadata discovery.
- Semantic mapping recommendations.
- User approval and saved mappings.

For example, CostPilot can inspect the metadata for `CostPilot_Project__c`, `Opportunity`, or `Case`, then recommend fields for project ID, name, owner, customer, status, and content.

CostPilot inspects metadata, not customer record contents, during this discovery step.

### Security

- OAuth tokens are encrypted at rest.
- Callback URLs must match the registered Salesforce External Client App.
- The Salesforce authorization host is validated.
- The application should request only required scopes.
- Client IDs, client secrets, workspace keys, and provider keys must not be placed in frontend JavaScript or Apex source.

### Current adapter status

- Salesforce metadata discovery is implemented.
- ServiceNow and HubSpot are represented in the universal connector architecture, but equivalent live OAuth discovery adapters remain planned work.

This distinction should be stated clearly.

---

## 17. Salesforce project and Agentforce proof of concept

CostPilot includes a Salesforce DX proof-of-concept package.

The package creates:

- `CostPilot_Project__c`.
- `CostPilot_Project_Member__c`.
- Project fields.
- Membership fields.
- Tabs.
- Permission sets.
- An Apex Invocable Method action named **Govern AI Work with CostPilot**.
- Apex tests.

### Salesforce project record

The project object can contain:

- Project ID.
- Name.
- Owner.
- Account.
- Department.
- Status.
- Monthly AI budget.
- Selected model.
- Estimated AI cost.
- CostPilot decision.
- Tracking ID.
- Last AI request.

### Salesforce membership record

The project-member object can connect:

- Salesforce user.
- CostPilot project.
- Role.
- Status.
- AI-use permission.
- Added date.

### Agentforce action

The Apex action sends:

- Salesforce record ID.
- Task description.
- Project details.
- Department.
- Agent name.
- Requested model.
- Current Salesforce user identity.
- Project membership.
- Customer context.

CostPilot returns:

- Allowed or blocked.
- Decision.
- Reason.
- Project.
- Selected model and tier.
- Estimated cost.
- Project budget remaining.
- Tracking ID.

### Required Salesforce setup

The proof requires:

- Deployed metadata.
- Assigned CostPilot permission set.
- A Salesforce External Client App for inbound metadata discovery.
- A Named Credential for Salesforce-to-CostPilot callouts.
- A custom External Credential principal containing the CostPilot workspace key.
- An `X-CostPilot-Key` custom header.
- Agentforce topic instructions that actually invoke the Apex action.
- Input mapping for Salesforce Record ID and Task Description.

### Current proof status

The Salesforce package and Apex action are implemented and tested. The direct Apex diagnostic reaches the live CostPilot endpoint. End-to-end Agentforce execution is still being configured and validated, particularly the Salesforce Named Credential header and Agentforce action invocation. It should be described as a proof of concept under active validation, not as a finished AppExchange product.

---

## 18. Onboarding and setup

CostPilot's onboarding is designed to start with business language.

### Primary onboarding question

**What do you call your work?**

Suggested values can be provided, but users must always have a custom-value option.

### Guided setup

The guided setup can:

- Capture work terminology.
- Select a source platform.
- Choose what to measure.
- Create a Business Context template.
- Connect a supported platform.
- Discover metadata.
- Approve mappings.
- Generate integration code for custom paths.
- Run a CostPilot contract test.

### Code and API paths

Code/API choices remain first-class setup paths:

- Python.
- Node.js.
- Java.
- Ruby.
- REST/cURL.

They should not be hidden inside a generic “Custom” option.

### Generated integration guidance

Generated setup can include:

- Endpoint.
- Headers.
- Payload fields.
- Return fields.
- Agent and department defaults.
- Platform-specific examples.

For Salesforce, optional write-back fields must be handled dynamically so the Apex class compiles even when those custom fields do not exist.

---

## 19. Executive dashboard

The Executive page answers:

**Is our company's AI usage healthy, optimized, and under control?**

It is intentionally a quick-glance experience rather than a complete operational console.

### Executive filters

Filters remain at the top and can include:

- Department.
- Platform.
- Agent.
- Time period.
- Tier.
- Risk.

Saved filters can affect displayed totals. Clearing filters returns to the all-workspace view.

### Headline metrics

The executive view can show:

- AI spend.
- Spend avoided.
- Annualized savings pace.
- Budget used.
- Governed requests.
- Risk and controls.
- Tokens removed through pruning.

### Decision-oriented components

- Actual spend versus uncontrolled baseline.
- Savings mix.
- Input versus output cost by model.
- Spend concentration by department or project.
- Material risk requiring attention.
- Optimization opportunities.

### Accessibility

Color must not be the only way information is communicated. CostPilot uses:

- Text labels.
- Icons.
- Patterns or structure.
- Status words.
- Sufficient contrast.

Legends are useful when a chart contains multiple series or encodings, but unnecessary legends should not create more visual noise.

---

## 20. Govern and Policy

The Govern page answers:

**What is CostPilot permitted to allow, change, route, queue, or block?**

It owns:

- Pruning policy.
- Routing rules.
- Sensitive terms.
- Budget behavior.
- Collision behavior.
- Tier naming and threshold references.
- Policy testing.
- Exceptions.

The page should show the decision sequence rather than presenting unrelated controls as a collection of buttons.

---

## 21. Audit and governance event history

CostPilot records why decisions were made.

Audit information can include:

- Timestamp.
- Agent.
- Department.
- Source platform.
- Work item.
- User.
- Model and tier.
- Routing decision.
- Routing reason.
- Sensitive-term matches.
- Blocked or allowed outcome.
- Token counts.
- Tokens removed.
- Cost.
- Savings.
- Risk level.

### Audit log versus operational stream

These experiences are related but should not be identical:

- The operational governance stream shows recent exceptions and live activity.
- The audit log supports investigation and historical evidence.

### Raw payload controls

Raw request content can create privacy and security risk. CostPilot includes configuration around raw-payload logging and retention. A production deployment should default to the least sensitive storage necessary.

### Exports

Audit and reporting data can be exported for analysis. Exported files should be treated according to the organization's security and retention requirements.

---

## 22. Reports

The Reports area answers:

**What patterns have developed over time?**

Report categories include:

- Savings.
- Risk and compliance.
- Departments.
- Agent activity.
- Agent efficiency.
- Work attribution.
- Time series.
- ROI.

Reports can explain:

- How much was spent.
- How much was avoided.
- Whether pruning or routing produced the savings.
- Which departments or projects are driving usage.
- Which agents are active or unused.
- Where risk events are concentrated.
- How activity changes over time.

The Executive page owns today's headline. Reports owns historical detail, comparison, and export.

---

## 23. Savings and ROI

CostPilot measures or estimates multiple savings sources.

### Routing savings

The difference between:

- Cost of the model CostPilot selected.
- Cost of a more expensive uncontrolled baseline.

### Pruning savings

The cost avoided by removing tokens before the model call.

### Blocked-request savings

The cost that would have been incurred if a blocked request had gone to a model.

### Budget-control savings

The cost avoided when throttling or tier limits prevent premium-model usage.

### Annualized savings

An annual figure is a projection based on the current pace, not guaranteed realized savings. The interface should label it as annualized or projected.

### ROI tools

CostPilot includes savings and ROI calculators for estimating value before or during adoption.

---

## 24. Proxy and provider execution

CostPilot includes OpenAI-compatible and Anthropic-compatible proxy paths for workspace-based routing.

The proxy can:

- Validate the CostPilot workspace key.
- Enforce trial or workspace state.
- Evaluate policy.
- Prune context.
- Route the request.
- Call a configured provider.
- Return CostPilot response headers.
- Log the transaction.

Provider credentials can be:

- Customer-provided in supported trial paths.
- Managed server-side in a controlled deployment.

Provider keys must not be exposed to browser code or Salesforce Apex.

### Live and simulated modes

CostPilot has supported simulation for demonstrations and testing. Production claims should identify whether:

- A real provider was called.
- Token usage came from the provider.
- Cost was calculated from registry pricing.
- Values were simulated or inferred.

---

## 25. Voice Guard

Voice Guard is a supporting CostPilot capability focused on transcripts.

It can:

- Process completed transcripts.
- Detect or redact sensitive information.
- Record redaction events.
- Report voice-governance statistics.

Voice Guard demonstrates that CostPilot's governance model can apply beyond typed prompts. It remains a supporting feature rather than the core product definition.

---

## 26. Trial, workspace, and account flow

CostPilot includes a trial and workspace model.

A trial account can contain:

- User and company identity.
- Workspace ID.
- CostPilot workspace secret.
- Trial dates.
- Usage caps.
- Plan.
- Platform.
- Setup status.
- Provider-key mode.

Workspace identity separates usage and prefixes department activity internally.

Current product paths include:

- Trial registration.
- Workspace status.
- Usage limits.
- First-call verification.
- Upgrade request.

Production expansion would require stronger identity management, subscription billing, tenant administration, support workflows, and enterprise provisioning.

---

## 27. Security model

CostPilot's security principles include:

- Keep secrets out of source code.
- Encrypt integration tokens at rest.
- Store provider credentials server-side.
- Use OAuth where appropriate.
- Use Named Credentials and External Credentials for Salesforce callouts.
- Send only required fields.
- Avoid raw-payload storage by default.
- Validate callback and authorization hosts.
- Apply least-privilege scopes and permissions.
- Preserve an audit trail.
- Separate metadata discovery from customer-record ingestion.

### Important current limitations

The current application is a developing product and proof environment. Before broad enterprise deployment, it requires continued hardening around:

- Authentication and authorization.
- Tenant isolation.
- Secret rotation.
- Audit immutability.
- Encryption and key management.
- Availability and disaster recovery.
- Data retention.
- Compliance certifications.
- Monitoring and incident response.
- Connector permission reviews.

These are not reasons the product concept fails. They are the normal difference between a working product proof and a broadly deployable enterprise platform.

---

## 28. Resilience and availability

CostPilot is currently a centralized control layer. If the hosted CostPilot service or internet connection is unavailable, live cloud-based governance can be unavailable.

Possible resilience strategies include:

- Clearly defined fail-open or fail-closed policies.
- Local policy caches.
- Queued requests.
- Retry behavior.
- Health checks.
- Multiple deployment regions.
- Customer-hosted or private deployment options.
- Limited offline enforcement for selected rules.

These strategies are product direction unless specifically implemented and validated for a deployment.

---

## 29. Real-world scenario: Salesforce project work

A sales user opens a Salesforce project called **ACME Renewal 2026**.

The project includes:

- Project code.
- Account.
- Owner.
- Department.
- Status.
- AI budget.
- Project members.

The user asks Agentforce to summarize the renewal history and recommend next steps.

The intended governed flow is:

1. Agentforce calls the CostPilot Apex action.
2. Salesforce sends the current record, task, user, membership, and project context.
3. CostPilot validates the workspace.
4. CostPilot resolves or creates the corresponding work item.
5. CostPilot checks whether the user may use AI for the project.
6. CostPilot evaluates sensitive terms.
7. CostPilot prunes unnecessary context if safe.
8. CostPilot selects a model.
9. CostPilot records project, user, agent, tokens, model, cost, and risk.
10. Agentforce receives the governance result and tracking ID.

The result can be reported at several levels:

- Company.
- Sales department.
- ACME customer.
- ACME Renewal 2026 project.
- Salesforce user.
- Agentforce agent.
- Selected model.

That is the difference between model billing and business accountability.

---

## 30. Real-world scenario: ServiceNow incident

An IT support agent asks AI to summarize a ServiceNow incident.

CostPilot can receive:

- Incident ID.
- Short description.
- Assignment group.
- Priority.
- User.
- Agent.
- Department.

CostPilot checks risk, removes ticket boilerplate, routes the task, and attributes the request to the incident or work order.

The universal connector and Business Context model already define how this should work. A live ServiceNow OAuth metadata-discovery adapter remains planned.

---

## 31. Real-world scenario: professional services

A consulting, accounting, legal, or agency team uses AI while delivering client work.

The organization may call the work:

- Matter.
- Engagement.
- Project.
- Client assignment.

CostPilot attributes AI usage to that unit of work.

This can support:

- True cost per engagement.
- Better fixed-fee pricing.
- Margin analysis.
- Customer-level AI usage.
- Internal allocation.
- Defensible pass-through billing where permitted.

Rebilling is only one use case. The more universal use case is understanding the real cost of delivering work.

---

## 32. Real-world scenario: sensitive information

A support request contains a payment-card number.

CostPilot detects the configured sensitive pattern and blocks the request before model execution.

The event records:

- Source.
- Agent.
- Department.
- User.
- Work item.
- Policy match.
- Block reason.
- Timestamp.

An authorized reviewer later acknowledges the event as reviewed. The audit record remains.

---

## 33. Real-world scenario: budget pressure

Marketing is approaching its monthly AI cap.

Instead of turning AI off completely, CostPilot applies a throttle:

- Routine requests remain available.
- Premium tiers are restricted.
- An authorized supervisor can grant an override.
- The executive dashboard shows the material budget signal.
- Reports show the effect of throttling over time.

---

## 34. Product page map

### Executive

Primary question:

**Is company AI usage healthy, optimized, and under control?**

### Operate / AgentLake

Primary question:

**Which agents and live requests are being used, and which need attention?**

### Work

Primary question:

**Which business work is consuming AI resources?**

### Govern

Primary question:

**What is CostPilot permitted to allow, modify, route, queue, or block?**

### Models

Primary question:

**Which models can CostPilot use, and what do they cost?**

### Reports

Primary question:

**What patterns have developed over time?**

### Administration

Primary question:

**How is this CostPilot workspace configured and managed?**

### Connect and Setup

Primary question:

**How does CostPilot understand the business and connect to its systems?**

This page ownership keeps CostPilot from becoming a collection of overlapping dashboards.

---

## 35. Implementation status summary

### Implemented in the current application

- FastAPI backend and HTML/JavaScript frontend.
- Executive dashboard and filters.
- Context pruning and pruning metrics.
- Sensitive-term policy library.
- Routing tiers and configurable routing.
- Model registry and known-model presets.
- Department budgets, caps, throttling, and overrides.
- AgentLake registry and usage views.
- Collision handling concepts and controls.
- Audit history and blocked-event acknowledgement.
- Savings, risk, department, time-series, agent, and efficiency reports.
- Work items and customer/account records.
- Project-agent assignments.
- Project-user attribution.
- Business Context templates and business-first onboarding.
- Universal connector contract.
- Persistent connection registry.
- Salesforce OAuth metadata discovery and mapping approval.
- Trial and workspace flow.
- Provider proxy paths.
- Voice Guard endpoints.
- Salesforce DX project and Agentforce Apex action.

### Proof of concept or active validation

- Full Salesforce Agentforce-to-CostPilot-to-model workflow.
- Salesforce workspace-key Named Credential configuration.
- Project membership enforcement in a live Agentforce conversation.
- Automated write-back of all CostPilot results to Salesforce.
- Broad multi-user enterprise deployment.

### Planned direction

- Live ServiceNow OAuth and metadata discovery.
- Live HubSpot OAuth and metadata discovery.
- Partner template builder and connector marketplace.
- Packaged Salesforce installation such as AppExchange distribution.
- Enterprise identity, billing, and provisioning.
- Broader offline or high-availability deployment options.
- MCP support after behavior and data models are proven.
- Deeper AI business intelligence and unit-economics analysis.

---

## 36. The universal product strategy

The universal CostPilot experience should be:

1. Sign in to a source platform.
2. Let CostPilot inspect metadata.
3. Tell CostPilot what the organization calls its work.
4. Approve recommended business mappings.
5. Install or activate a thin runtime adapter.
6. Send the first governed request.
7. See the same CostPilot dashboards regardless of source platform.

Salesforce, ServiceNow, HubSpot, and custom code should not require different CostPilot products. They should use different adapters around the same:

- Business Context.
- Governance envelope.
- Routing pipeline.
- Attribution model.
- Audit model.
- Reporting model.

This is how CostPilot becomes universal without becoming generic or meaningless.

---

## 37. Product positioning

CostPilot can be positioned as:

- AI FinOps before the bill.
- Governance at request time.
- Business attribution for AI.
- An AI control plane for enterprise applications.
- The Business Context Engine for AI usage.

The strongest combined positioning is:

**CostPilot controls each AI request, optimizes its cost, and connects it to the business work and people responsible for it.**

---

## 38. Podcast themes

Strong podcast themes include:

### AI spend is not just a finance problem

The model bill is the end of a chain of operational decisions.

### The best time to control AI cost is before the request

Reports explain the past. CostPilot also changes the request before money is spent.

### Pruning is different from routing

Routing chooses the right model. Pruning removes unnecessary billable content.

### AI accountability needs business context

A model name and token count do not explain why the company spent the money.

### Universal does not mean one giant field-mapping screen

Universal setup requires a shared semantic model and platform-specific discovery adapters.

### Agent sprawl creates a new operations problem

Companies need to know which agents exist, which are used, which are expensive, and which overlap.

### Governance should produce evidence

Every block, reroute, throttle, and optimization should have a human-readable reason.

### Project attribution is bigger than rebilling

It supports pricing, margin, budgeting, client transparency, and operational decision-making.

---

## 39. Useful sound bites

- “CostPilot helps companies control AI before the bill and explain it afterward.”
- “Every AI request should have a cost, a reason, an owner, and a business purpose.”
- “Routing chooses the right model; pruning removes what the model never needed.”
- “A provider invoice tells you what model was used. CostPilot tells you why the business used it.”
- “AgentLake answers which AI agents exist and which ones are actually doing work.”
- “Business Context turns token telemetry into project, customer, user, and agent accountability.”
- “CostPilot does not replace Salesforce or ServiceNow. It makes their AI activity governable.”
- “The universal solution is one control model with thin adapters, not a different product for every platform.”
- “Governance without attribution tells you what happened. Attribution tells you who and what it happened for.”

---

## 40. Questions a podcast host can explore

- Why are AI costs harder to explain than ordinary software costs?
- What makes an AI request governable?
- How can pruning reduce cost without reducing meaning?
- How does CostPilot decide which model to use?
- Why do companies need an AI agent registry?
- What is the difference between an agent, a user, and a project?
- Why is project or matter attribution valuable outside legal services?
- How can CostPilot work across Salesforce, ServiceNow, HubSpot, and custom code?
- What metadata can CostPilot discover automatically?
- What must still be configured in each business platform?
- What happens when a request contains sensitive information?
- How should CostPilot behave when a department exceeds its budget?
- What happens if CostPilot or the internet is unavailable?
- Which parts are implemented today, which are proof-of-concept, and which are future direction?
- How could CostPilot evolve from gateway to governance to AI business intelligence?

---

## 41. Glossary

### Agent

A named AI workflow or software actor that sends governed requests.

### AgentLake

CostPilot's AI-agent registry and operational usage view.

### Business Context

The semantic connection between AI usage and business concepts such as project, customer, owner, user, and status.

### Collision

Two agents attempting conflicting work on the same resource.

### Context pruning

Removing unnecessary content before model execution while preserving required meaning.

### Governed request

An AI request evaluated by CostPilot's policy, routing, budget, attribution, and audit pipeline.

### Model registry

The catalog of models, providers, tiers, prices, availability, and defaults.

### Project or work item

The business unit of work to which AI usage is attributed.

### Routing

Selecting the appropriate model tier and enabled model for a request.

### Sensitive term

A configured word, phrase, or pattern that triggers a policy action.

### Throttling

Restricting model eligibility or request behavior because of budget or policy.

### Workspace

A CostPilot customer or tenant context used to separate configuration and usage.

### Workspace key

A secret used by an integration to authenticate a CostPilot workspace request.

---

## 42. Final summary

CostPilot began as AI cost-control middleware, but its coherent product identity is broader and still focused:

- It removes unnecessary tokens.
- It selects appropriate models.
- It enforces risk and budget controls.
- It inventories agents.
- It attributes usage to departments, users, customers, and business work.
- It records why decisions were made.
- It provides executive and operational visibility.
- It connects to business platforms through a shared universal architecture.

The product has not abandoned its original purpose. It has added the context required to make cost control useful at enterprise scale.

The clearest final description is:

**CostPilot is the control, optimization, and accountability layer for enterprise AI—governing each request, reducing unnecessary cost, and connecting AI usage to the business work that created it.**
