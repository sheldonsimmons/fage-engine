# CostPilot Podcast Series: Who Owns the AI Bill?

## Purpose of this source

This document is designed for a **multi-episode NotebookLM podcast series**, not a single repeated product overview. It gives every episode a shared understanding of CostPilot while assigning each episode a different business question, protagonist, tension, and set of capabilities.

The hosts should explain CostPilot clearly, but they should not recite every feature in every episode. Each episode should use the product only where it helps answer that episode's central question.

---

## The shared product foundation

### CostPilot in one sentence

CostPilot is an AI control, optimization, and accountability layer that sits between business applications and AI models, governing each request, reducing unnecessary context, selecting an appropriate model, and showing where AI usage, cost, and risk came from.

### The business problem

Companies are adding AI to Salesforce, ServiceNow, HubSpot, custom applications, automated workflows, and AI agents. Their model invoices can show total consumption, but they often cannot answer the questions business leaders actually ask:

- Which account, customer, project, case, or workflow generated the spend?
- Which employees, departments, and AI agents used the tokens?
- Which models were used, and why?
- What was pruned before the model call?
- Which requests were blocked, rerouted, throttled, or flagged?
- Are expensive models being used when a lower-cost model would work?
- Which AI agents were built but are barely used?
- What can the company change to reduce cost without disabling useful AI?

CostPilot is intended to close that gap. It does not merely display an AI invoice. It governs the request while it is happening and creates a business-readable record of the decision.

### The request journey

When an AI-enabled application sends work through CostPilot, the request moves through a controlled sequence:

1. **Identify the source.** CostPilot records the platform, workspace, user, department, agent, and related business record when those values are available.
2. **Apply business context.** A Salesforce Contact, Opportunity, or Case can roll up to an Account. A ServiceNow task can roll up to a related business service, customer, or parent work record. Custom relationships can be approved during setup.
3. **Evaluate policy and risk.** Sensitive-term rules, department policy, budget state, and other controls determine whether the request can continue.
4. **Prune unnecessary context.** CostPilot removes eligible repeated or irrelevant context before the model call while preserving safety and audit requirements.
5. **Route the request.** CostPilot selects a model tier based on task complexity, risk, policy, agent limits, availability, and cost controls.
6. **Call the model.** Approved requests are sent to the selected model, and the response returns to the originating application.
7. **Attribute and audit.** CostPilot records tokens, cost, model, routing rationale, pruning, user, agent, department, business context, risk events, and source system.

### Major capabilities

#### Model routing

CostPilot can route work across capability tiers such as Scout, Analyst, Advisor, and Strategist. The objective is not to send everything to the cheapest model. It is to use the least expensive model that still satisfies the task's complexity, risk, and policy requirements.

#### Context pruning

CostPilot can reduce eligible input context before a model is called. It records how many tokens were removed and estimates the cost avoided. Pruning is a first-class optimization and governance stage, not an invisible text cleanup step.

#### Governance and budgets

CostPilot can apply sensitive-content rules, block or flag requests, track reviewed risk events, monitor department budgets, warn when budgets approach their limits, and enforce configured controls. Clearing a blocked-event notification should not hide a department that is still near its budget cap.

#### Business attribution

CostPilot links AI activity to people, agents, departments, models, source systems, and business records. The customer can use its own business language—Accounts, Matters, Engagements, Projects, Cases, Work Orders, or another term—instead of being forced into CostPilot terminology.

#### AgentLake

AgentLake is the operational registry for AI agents. It helps answer: What agents exist? Which are active? Which are unused or lightly used? What do they cost? Which models do they invoke? Which departments and business records do they support? AgentLake is about visibility and governance, not merely maintaining a long list of agent cards.

#### Ask CostPilot

Ask CostPilot is a conversational analytics experience for questions about governed AI activity. It is designed to translate natural-language questions into controlled queries, calculate the answer from workspace data, and return evidence and drill-down links. Examples include:

- Who used the most tokens last week?
- Which accounts generated the most AI spend?
- What agents have low or no recent usage?
- Which departments are near their budgets?
- Why were requests blocked?
- What could be rerouted to a lower-cost model?

Answers should remain grounded in the customer's authorized data. The language model helps interpret the question and explain results; it should not invent calculations or receive unrestricted database access.

#### Executive and operational reporting

The executive experience answers where AI spend went, whether it is controlled, and what needs attention. Operational reports support filtering and drill-down by date, person, department, agent, model, source system, risk, and business context. Live customer activity must be clearly distinguishable from simulator data.

#### Universal integration approach

CostPilot is being designed around a universal request contract and platform adapters. Salesforce, ServiceNow, HubSpot, and custom applications express the same core facts—source, actor, business work, request, and governance context—even though each platform uses different objects and field names.

The desired customer experience is:

1. Install or connect CostPilot.
2. Authorize metadata discovery.
3. Tell CostPilot what the business calls its work and customers.
4. Approve suggested parent-child relationships.
5. Choose which AI agents, flows, or application entry points CostPilot should govern.
6. Run a verified test request.
7. Begin measuring live activity.

### Honest product status

CostPilot has working product capabilities and proof-of-concept integrations, but the story must distinguish present functionality from launch work still being hardened.

Working or demonstrated capabilities include governed routing, pruning, policy evaluation, budgets, audit records, attribution, dashboards, AgentLake, Salesforce and ServiceNow proof workflows, a traffic simulator, and an expanding Ask CostPilot analytics experience.

Pilot-readiness work includes reducing package installation friction, strengthening customer login and workspace isolation, expanding connector coverage, improving automatic relationship discovery, validating guided setup across different customer environments, and broadening the accuracy and conversational memory of Ask CostPilot.

CostPilot should not claim to measure employee productivity, prove business outcomes, or judge whether a person is effective. It measures AI consumption, routing, governance, attribution, and operational signals.

---

# Season One: Ten Different Ways to Understand Enterprise AI

## Episode 1 — The Invisible AI Invoice

**Primary question:** A company knows what it paid for AI, but does it know what the money accomplished or where it went?

**Perspective:** CFO and AI platform owner.

**Cold open:** The finance team receives a growing model invoice. It lists tokens and models, but it does not say which customers, departments, employees, or automated agents created the bill.

**Central tension:** Provider billing is technically accurate but not business-readable.

**CostPilot capabilities to feature:** business attribution, executive reporting, filters, drill-down evidence.

**Scenario:** Compare a single monthly provider total with a CostPilot view that breaks the same activity down by account, department, user, agent, model, and source platform.

**Debate for the hosts:** Is AI spend simply another cloud infrastructure cost, or does its connection to business work make it more like a measurable operating expense?

**Closing question:** If a company cannot explain where its AI spend went, can it responsibly increase that spend?

## Episode 2 — The Request Before the Model

**Primary question:** What happens between a user clicking an AI button and a model returning an answer?

**Perspective:** Enterprise architect and application owner.

**Cold open:** A Salesforce user asks an agent to summarize an opportunity. Instead of jumping directly to a flagship model, the request first passes through CostPilot.

**Central tension:** Speed and convenience versus control and explainability.

**CostPilot capabilities to feature:** request gateway, governance sequence, pruning, routing rationale, audit trail.

**Scenario:** Follow one request from Salesforce through identification, policy evaluation, pruning, model selection, response, and attribution.

**Debate for the hosts:** Does middleware slow AI down, or is a small control layer the price of using AI safely at enterprise scale?

**Closing question:** What decisions should happen before a prompt reaches a model?

## Episode 3 — The Account That Ate the AI Budget

**Primary question:** How can AI activity across Contacts, Opportunities, Cases, and custom records become one coherent customer story?

**Perspective:** Revenue leader and Salesforce administrator.

**Cold open:** AI is used on an Account, two Opportunities, a Contact, and a support Case. Without business relationships, the activity looks like five unrelated projects.

**Central tension:** Technical record IDs versus the way the business thinks about a customer.

**CostPilot capabilities to feature:** Business Context Engine, parent-child mappings, custom language, record rollups.

**Scenario:** Show related Salesforce activity rolling up to one Account while preserving each source record for drill-down. Contrast this with records that have no approved relationship and remain separate.

**Debate for the hosts:** How much mapping can software safely infer, and what should an administrator explicitly approve?

**Closing question:** Is the true unit of AI accountability a request, a record, a customer, or all three?

## Episode 4 — The Agent Nobody Used

**Primary question:** What has the company built, and is anyone actually using it?

**Perspective:** Chief AI officer and operations manager.

**Cold open:** The company celebrates launching dozens of AI agents, but several have never handled a governed request and others are used only once a month.

**Central tension:** AI inventory versus genuine adoption.

**CostPilot capabilities to feature:** AgentLake, unused and low-usage analysis, cost per request, drill-down by department and business context.

**Scenario:** Compare a high-volume support agent, an expensive low-volume specialist, and an unused agent. Discuss what each pattern might mean without pretending usage alone measures value.

**Debate for the hosts:** When should a company retire, consolidate, promote, or investigate an agent?

**Closing question:** Is an unused agent a failed investment, an undiscovered capability, or a necessary standby tool?

## Episode 5 — The Prompt That Should Never Leave

**Primary question:** What should happen when an AI request contains sensitive or prohibited information?

**Perspective:** Risk officer, legal counsel, and business user.

**Cold open:** A seemingly routine request contains a sensitive term. CostPilot detects it before the model call.

**Central tension:** Preventing exposure without blocking legitimate business language or locking every company into identical policies.

**CostPilot capabilities to feature:** configurable sensitive terms, word-boundary matching, blocking and flagging, review workflow, audit evidence.

**Scenario:** Explain why a term such as “NDA” must not be triggered simply because those letters appear inside “Monday.” Then contrast a false positive with a genuine policy event.

**Debate for the hosts:** Should protected policies be immutable, or should administrators always retain a controlled option to disable or replace them?

**Closing question:** What does responsible flexibility look like in AI governance?

## Episode 6 — The Expensive Model Problem

**Primary question:** Are companies paying for more model capability than their work requires?

**Perspective:** FinOps leader and AI engineering lead.

**Cold open:** A flagship model represents most of the AI bill but only a minority of requests.

**Central tension:** Maximum capability versus appropriate capability.

**CostPilot capabilities to feature:** model tiers, routing rules, model registry, spend concentration, rerouting opportunities.

**Scenario:** Separate routine summarization, moderate analysis, sensitive reasoning, and mission-critical decisions. Show how task complexity and policy—not a simplistic cheapest-model rule—shape routing.

**Debate for the hosts:** How much quality risk should a company accept to reduce AI cost?

**Closing question:** What evidence would a business need before rerouting production work to a cheaper model?

## Episode 7 — The Tokens That Never Reached a Model

**Primary question:** Can a company reduce AI cost before model selection even begins?

**Perspective:** AI engineer and knowledge-management leader.

**Cold open:** An agent repeatedly sends long instructions, duplicated history, and irrelevant context with every request.

**Central tension:** More context feels safer, but unnecessary context increases cost, latency, and exposure.

**CostPilot capabilities to feature:** context pruning, token savings, annualized savings, pruning auditability.

**Scenario:** Compare the candidate input, the pruned input, and the model-call cost while explaining what must be protected from unsafe removal.

**Debate for the hosts:** When does aggressive context reduction become a quality or compliance risk?

**Closing question:** Is the cheapest token the one routed to a lower-cost model, or the one never sent at all?

## Episode 8 — One Company, Three Systems

**Primary question:** Can AI governance work consistently across Salesforce, ServiceNow, HubSpot, and custom applications?

**Perspective:** CIO and enterprise integration architect.

**Cold open:** Sales works in Salesforce, operations works in ServiceNow, marketing works in HubSpot, and engineering has custom APIs. Each team describes work differently.

**Central tension:** Universal governance versus platform-specific setup.

**CostPilot capabilities to feature:** universal request contract, metadata discovery, business-context templates, connector adapters.

**Scenario:** Express an Opportunity, a Change Request, and a custom engagement through the same source-actor-work-request structure while retaining their native business names.

**Debate for the hosts:** How close can enterprise integration come to true plug-and-play when customers have custom objects, custom security, and unique automation?

**Closing question:** What is the smallest amount of manual configuration a responsible universal connector can require?

## Episode 9 — Ask the AI Ledger

**Primary question:** Can an executive ask a plain-language question and receive a defensible answer about company AI usage?

**Perspective:** CEO and data-governance leader.

**Cold open:** The CEO asks, “Which employees used the most tokens last week, and which agents contributed to that?”

**Central tension:** Natural conversation versus factual, authorized analytics.

**CostPilot capabilities to feature:** Ask CostPilot, controlled query planning, conversational follow-ups, evidence, calculations, drill-down links, live-versus-simulator labels.

**Scenario:** Begin with a broad question, narrow to Salesforce activity, change the date range, reorder the result, and open supporting activity without losing conversational context.

**Debate for the hosts:** Should a language model directly query production data, or should it be limited to a governed analytics layer?

**Closing question:** What makes an AI answer trustworthy enough for an executive decision?

## Episode 10 — From Pilot to Enterprise Control Plane

**Primary question:** What must be true before a company relies on CostPilot in production?

**Perspective:** Prospective customer, security reviewer, and CostPilot product leader.

**Cold open:** The proof of concept works, but the customer asks a harder question: Can hundreds of users, multiple departments, and several platforms safely depend on it?

**Central tension:** Product vision versus operational readiness.

**CostPilot capabilities to feature:** installation path, workspace isolation, permissions, connector verification, data retention, observability, failure behavior.

**Scenario:** Walk through the desired pilot journey—connect, discover, approve mappings, select agents or flows, verify a governed request, and observe live data—then identify the controls needed for production scale.

**Debate for the hosts:** Which imperfections are acceptable in a pilot, and which must be resolved before any real customer data is governed?

**Closing question:** Is CostPilot ultimately middleware, FinOps, governance software, business intelligence, or a new category combining all four?

---

## Series editorial rules

These rules are important. They are intended to prevent NotebookLM from generating ten versions of the same CostPilot introduction.

1. Begin each episode with its assigned cold open, not a generic statement that “AI is changing everything.”
2. Give the complete product explanation only in Episode 1. Later episodes may use the one-sentence definition and then move immediately to their central question.
3. Feature no more than three primary CostPilot capabilities in an episode.
4. Use the assigned professional perspectives so the conversations sound different.
5. Include a genuine tradeoff or skeptical argument. The hosts should not sound like an advertisement.
6. Use one detailed business scenario rather than listing every feature.
7. Clearly label current product behavior, demonstrated proofs, and future or pilot-readiness work.
8. Never describe simulator activity as live customer activity.
9. Do not infer employee productivity, intent, or business success from token usage alone.
10. End with the episode's unresolved closing question so the audience has something to consider.
11. Avoid repeating the same analogies, sound bites, and conclusions across episodes.
12. Explain technical ideas in business language before introducing implementation details.

---

## Master NotebookLM instruction

Use the following instruction when generating the complete series:

> Create a business and technology podcast series about CostPilot using the ten episode plans in this source. Preserve the shared product facts, but make every episode meaningfully different in structure, protagonist, tension, examples, and conclusion. Do not turn each episode into a complete feature overview. Begin with the assigned cold open, center the discussion on the assigned primary question, and feature no more than three relevant CostPilot capabilities. Include a thoughtful disagreement or risk in every episode. Clearly distinguish working functionality, demonstrated proof-of-concept behavior, and future pilot-readiness work. Never treat simulator data as live customer data, and never equate AI consumption with employee productivity. Use natural conversation, concrete business examples, and plain language. End each episode with its assigned closing question.

## Prompt for “What should the AI hosts focus on in this episode?”

For an individual episode, paste this and replace the bracketed fields:

> Focus on **[EPISODE TITLE]** and its central question: **[PRIMARY QUESTION]**. Tell the story primarily from the perspectives of **[PERSPECTIVES]**. Open with **[COLD OPEN]**. Explore the tension between **[CENTRAL TENSION]**, including a skeptical viewpoint rather than presenting a sales pitch. Explain only these CostPilot capabilities: **[UP TO THREE CAPABILITIES]**. Use the episode's concrete scenario and clearly distinguish current functionality from work still being hardened. Do not repeat the complete CostPilot overview or reuse the opening and conclusion from another episode. End by discussing: **[CLOSING QUESTION]**.

---

## Optional alternative series formats

The same source can support additional seasons without repeating Season One.

### Season Two: The Investigation Desk

Each episode begins with an unusual CostPilot signal—a budget spike, unused agent, blocked request, expensive model, unassigned activity, or failed relationship mapping—and investigates the cause using filters and audit evidence.

### Season Three: The Implementation Room

Each episode follows a different role through implementation: Salesforce administrator, ServiceNow developer, security officer, data architect, AI agent owner, department leader, and executive sponsor.

### Season Four: The Governance Debate

Each episode presents two defensible opposing positions: centralized versus departmental budgets, strict blocking versus flexible review, aggressive pruning versus maximum context, automatic mapping versus administrator approval, and flagship models versus tiered routing.

---

## Final framing

CostPilot's larger idea is that enterprise AI should not be an unexplained stream of prompts and invoices. Every governed request should have an accountable source, an understandable business context, an appropriate model, a visible cost, a policy decision, and evidence that authorized users can inspect.

The podcast series should not merely ask, “What features does CostPilot have?” It should repeatedly ask a more useful set of questions: Where is enterprise AI being used? Who and what is using it? What is it costing? What was prevented or optimized? What requires attention? And can the company prove the answers?
