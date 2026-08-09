# CostPilot Under The Hood Functionality Guide

## Purpose

This document explains what CostPilot does under the hood. It is written for technical reviewers, partners, early pilot customers, and internal collaborators who need to understand the actual functionality behind the dashboards.

CostPilot is not just a dashboard. It is a control layer that sits in front of AI requests, evaluates them, decides how they should be handled, records the decision, and turns that activity into savings, governance, and risk reporting.

## Plain-English Summary

When an AI agent, business app, demo tool, or API client sends a request, CostPilot asks a few questions before the request reaches a model:

- Is this request routine, complex, risky, or blocked?
- Does it contain unnecessary text that can be removed?
- Does it contain sensitive language or regulated information?
- Which department, platform, and agent sent it?
- Which model tier should handle it?
- Is the department close to or over budget?
- How much did the request cost?
- How much would it have cost without CostPilot controls?
- What decision was made, and why?

CostPilot then stores the answer in the database and audit log so dashboards, reports, and executives can see what happened.

## Functional Architecture Map

CostPilot has several functional layers:

- Request intake: accepts AI requests from demo pages, platform connectors, trial proxy calls, or API clients.
- Agent resolution: identifies the agent, department, source platform, and policy settings.
- Context pruning: removes unnecessary prompt text before model routing.
- Sensitive term detection: flags, escalates, or blocks risky payloads.
- Routing engine: chooses Scout, Analyst, Advisor, or Strategist model tier.
- Budget governance: applies department caps, throttling, and override rules.
- Model registry: maps each tier to a model and pricing.
- Model execution: uses simulated or live AI provider calls.
- Cost calculation: records input cost, output cost, total cost, and savings.
- Audit logging: records the rationale and frozen context snapshot.
- Dashboards and reports: show savings, usage, risk, spend, agents, and governance activity.

## End-To-End Request Lifecycle

The normal request path looks like this:

1. A request enters CostPilot through `/api/route`, a demo tool, or a trial proxy endpoint.
2. CostPilot reads the text, department, platform, agent name, and pruning preference.
3. CostPilot checks whether the agent exists in AgentLake.
4. If the agent is new, the system can register it.
5. CostPilot checks the department budget record.
6. Sensitive terms are checked against the policy library.
7. If a blocking term is found, the request can be stopped before reaching an AI model.
8. If an escalation term is found, routing can be forced to a stronger tier.
9. If pruning is enabled, the pruner removes low-value text.
10. The routing engine scores the request based on complexity keywords and token count.
11. Agent tier bounds are applied, if configured.
12. Department throttling is applied, if the department is over budget and no override is active.
13. The model registry selects the model for the chosen tier.
14. CostPilot calls the model or simulated model.
15. Input tokens, output tokens, usage source, and cost are calculated.
16. A token transaction is written.
17. An audit event is written.
18. Dashboards and reports update from stored data.

## Routing Engine

The routing engine decides what level of AI model should handle the request.

CostPilot currently uses four model tiers:

- Tier 1: Scout, the cheapest tier for routine work.
- Tier 2: Analyst, for moderate requests.
- Tier 3: Advisor, for complex requests.
- Tier 4: Strategist, for high-risk, sensitive, or premium work.

Routing considers:

- Prompt token length.
- Complexity keywords.
- Sensitive term actions.
- Explicit tier override tags.
- Agent minimum and maximum tier settings.
- Department throttling rules.
- Model availability in the registry.

The basic complexity logic is:

```text
keyword match + token threshold exceeded = complex
keyword match OR token threshold exceeded = moderate
no keyword match and under threshold = routine
```

That means not every AI request automatically goes to the most expensive model. Routine work can be safely routed to a lower-cost tier, while complex or risky work can be escalated.

## Context Pruning

The context pruner removes text that usually does not help the AI answer the actual question.

Examples of content CostPilot can prune:

- Repeated email headers.
- Forwarded-message chains.
- Reply history after "Original Message" sections.
- Long corporate signatures.
- Legal disclaimer footers.
- HTML tags.
- Inline CSS.
- Raw MIME headers.
- Ticket metadata.
- Empty whitespace.
- Duplicated boilerplate.
- Automated system text.

The pruner is algorithmic. It does not need to ask an AI model to clean the prompt first. It uses fast text-processing rules to remove known noisy patterns before the model call.

CostPilot records:

- Raw token count.
- Clean token count.
- Tokens saved.
- Compression percentage.
- Estimated pruning savings.
- Whether pruning was applied.

This matters because AI providers often charge by input and output tokens. If a company sends repeated email chains and boilerplate into every model call, it pays for text that did not need to be there.

## Payload Type Detection

CostPilot also checks whether a payload looks like code or structured technical content.

If a payload appears to contain code, private key material, SQL, scripts, or dense technical syntax, CostPilot can avoid aggressive pruning so it does not accidentally remove meaningful content.

The goal is to prune junk, not damage useful context.

## Sensitive Terms And Risk Policy

CostPilot includes a sensitive term library.

Each term can have an action:

- Flag: allow the request, but mark it for review.
- Escalate: route the request to a stronger model tier.
- Block: stop the request before it reaches the model.

Examples:

- A routine renewal question may route to Scout.
- A request mentioning contract review may escalate.
- A request containing credit card or social security language may block.

Risk detection helps CostPilot answer:

- Did sensitive information appear?
- Was the request allowed, escalated, or blocked?
- Why did that decision happen?
- Which department or agent created the risk event?

## Budget Governance

CostPilot tracks department spend against monthly caps.

Each department budget can store:

- Department name.
- Monthly cap.
- Current spend.
- Percentage used.
- Throttle state.
- Override state.
- Throttle tier.
- Raw payload logging setting.
- Raw payload retention period.
- Archive state.

When a department approaches or exceeds its cap, CostPilot can throttle requests to a cheaper model tier unless an override is granted.

This creates a business rule:

```text
If the department is over budget and no override is active,
route expensive requests to the configured throttle tier.
```

That is how CostPilot moves from passive reporting to active cost control.

## Overrides

An override lets a supervisor temporarily allow a department to keep using stronger model tiers even if the department is over budget.

The audit log captures whether an override was active at the time of the decision. That is important because leadership can see whether spend increased because of normal usage or because someone intentionally allowed it.

## Model Registry

The Model Registry is CostPilot's model price menu and routing map.

Each model record can include:

- Display name.
- Provider.
- API model ID.
- Tier.
- Input cost per 1 million tokens.
- Output cost per 1 million tokens.
- Whether the model is enabled.
- Whether it is the default model for a tier.
- Optional department scope.

CostPilot uses the registry to calculate spend:

```text
input cost  = input tokens  / 1,000,000 * model input price
output cost = output tokens / 1,000,000 * model output price
total cost  = input cost + output cost
```

If live provider usage is available, provider-reported tokens are stronger proof. If simulated mode is active, CostPilot estimates token usage.

## Savings Math

CostPilot can show savings from multiple control types.

Savings sources include:

- Economy routing: cheaper model tier used instead of a premium tier.
- Context pruning: unnecessary input tokens removed before the call.
- Budget throttling: spend avoided when departments are capped or slowed down.
- Blocked risk: model spend avoided because a request was stopped.

Example:

```text
All-flagship baseline: $10.00
Actual routed cost:    $6.50
Spend avoided:         $3.50
```

The most important executive idea is simple:

```text
Would have spent - actual AI spend = AI spend avoided
```

## AgentLake Registry

AgentLake is CostPilot's registry of AI agents.

Each agent can store:

- Agent name.
- Department.
- Source platform.
- Permissions.
- Target table or record.
- Status.
- Collision policy.
- Last used time.
- Minimum allowed tier.
- Maximum allowed tier.
- Pruning enabled or disabled.
- Archive status.

AgentLake answers:

- Which agents are connected?
- Which department owns each agent?
- Which platform created the request?
- Which agents are active or idle?
- Which agents are expensive?
- Which agents are creating risk?
- Which agents are saving money?

## Collision Control

CostPilot includes collision-control concepts for agents working on the same target.

The supported collision policies include:

- Lock: one agent gets access while others wait or are blocked.
- Queue: requests can wait behind another agent.
- Skip: an agent can skip work when another process owns the target.

This is useful when multiple AI agents might try to act on the same customer record, ticket, account, or workflow at the same time.

## Audit Log

The audit log is CostPilot's decision record.

For each request, CostPilot can store:

- Event type.
- Timestamp.
- Agent.
- Department.
- Platform.
- Model tier.
- Prompt payload.
- Raw payload, if raw logging is enabled.
- Matched keywords.
- Budget snapshot at time of decision.
- Throttle status.
- Override status.
- Pruning stats.
- Input and output tokens.
- Usage source.
- Decision outcome.
- Plain-English rationale.

This matters because AI governance requires more than a final answer. Leaders need to know what happened before the AI model was called.

## Raw Payload Logging

Raw payload logging is configurable by department.

If raw logging is enabled, the audit view can show the original text before pruning. If raw logging is disabled, CostPilot may only show the cleaned prompt or decision metadata.

This gives companies a tradeoff:

- More audit detail.
- Less raw sensitive data retention.

## Dashboards

CostPilot has executive and operational dashboards.

Executive dashboard answers:

- Are we wasting money?
- How much AI spend did we avoid?
- What would we have spent without controls?
- What did we actually spend?
- Which department or agent is driving cost?
- Are budgets healthy?
- Are risk events increasing?
- How much text did pruning remove?

Operational dashboard answers:

- Which agents are active?
- Which departments are throttled?
- Which requests were blocked or escalated?
- Which agents are most efficient?
- Which budgets need action?
- Which audit events need review?

## Reports

CostPilot reports are designed for deeper review.

Report areas include:

- Savings.
- Risk and compliance.
- Departments.
- Bot efficiency.
- Agent activity.
- ROI calculations.

Reports help a buyer or operator move from "this looks interesting" to "where is the money going, who owns it, and what should we do next?"

## Demo CRM And Traffic Simulator

CostPilot includes demo tools so people can understand the product without connecting their real Salesforce, ServiceNow, HubSpot, Slack, or custom AI platform.

The demo CRM lets a tester submit realistic business records through CostPilot.

The traffic simulator generates many realistic enterprise AI requests across departments, agents, platforms, and use cases.

The simulator is useful because it shows CostPilot working at scale:

- Many departments.
- Many agents.
- Many platforms.
- Routine requests.
- Complex requests.
- Prune-heavy requests.
- Blocked requests.
- Budget pressure.
- Executive reporting.

## Trial And Workspace Flow

CostPilot includes a trial and workspace flow.

A customer can:

1. Create a trial workspace.
2. Select a platform or connector type.
3. Map required fields.
4. Generate integration code.
5. Send a test request.
6. View the decision in dashboards and audit logs.

This is important because CostPilot needs to be understandable even before a full enterprise integration exists.

## Live AI Versus Simulated AI

CostPilot can run in simulated mode or live provider mode.

In simulated mode:

- CostPilot estimates token usage.
- CostPilot simulates model responses.
- This is useful for demos and safe testing.

In live mode:

- CostPilot calls a real provider model.
- OpenAI and Anthropic usage numbers can be used when the provider returns them.
- Provider-reported usage gives stronger cost proof than estimates.

Each transaction can store a usage source so reports can distinguish estimated usage from provider-reported usage.

## Security And Privacy Controls

Current controls include:

- Sensitive term blocking.
- PII-oriented detection terms.
- Department raw payload logging toggle.
- Raw payload retention setting.
- Trial secret key for proxy calls.
- Audit records for decisions.
- Soft archive for departments and agents.

Important hardening work before broad production:

- Encrypt customer API keys with real key management.
- Add stronger authentication and authorization.
- Enforce tenant isolation across all customer data.
- Add rate limiting and abuse controls.
- Add formal retention enforcement.
- Add admin roles and permission boundaries.
- Add pricing update workflow for provider model costs.
- Add production-grade observability and alerting.

## What Makes CostPilot Different From A Provider Usage Dashboard

Provider dashboards usually show usage after the model call happened.

CostPilot is designed to act before and during the request:

- It can prune the payload before spend happens.
- It can route the request to a cheaper tier.
- It can block sensitive data before it reaches the model.
- It can throttle departments before budgets get out of control.
- It can attribute spend to departments, agents, platforms, and business workflows.
- It can explain why a decision was made.

That is the difference between cost reporting and cost control.

## Technical Review Talking Points

If someone technical asks how CostPilot works, use this explanation:

CostPilot is a Python/FastAPI application with a SQLAlchemy-backed database and static frontend dashboards. The core backend routes incoming AI requests through a governance pipeline. That pipeline resolves the agent and department, optionally prunes low-value context, checks sensitive terms, scores complexity, applies agent tier bounds, applies department budget rules, selects a model from the model registry, calculates cost using input and output token rates, and writes both token transactions and audit events. Dashboards and reports are built from those stored records.

## 8th Grade Explanation

Imagine every AI request is like a package being shipped.

Without CostPilot, every package might get sent by the most expensive overnight shipping method, even if it is just a simple note.

CostPilot checks the package first:

- Is it simple or complicated?
- Is there junk inside that can be removed?
- Is there private information inside?
- Who is sending it?
- Which department pays for it?
- Should it go cheap, normal, or premium?
- Should it be blocked?

Then CostPilot writes down what it did and shows the business how much money was saved.

That is the core idea: control the AI traffic before the bill gets out of hand.
