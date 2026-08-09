# CostPilot: Updated Source Document for Podcast Creation

## Document purpose

This document is source material for creating a podcast about CostPilot. It is
written for NotebookLM or another AI production tool. It covers the complete
product story, including the original AI cost-control capabilities, context
pruning, governance, AgentLake, work attribution, business context, universal
integration design, and the working Salesforce proof of concept.

The podcast should explain CostPilot in plain business language. It should not
sound like a feature inventory or imply that every proof-of-concept capability
is already a packaged enterprise product.

---

## 1. CostPilot in one sentence

CostPilot is an AI control and accountability layer that sits between business
applications and AI models, safely reducing unnecessary context, governing and
routing every request, and showing which user, agent, project, record, and
model generated the token usage and cost.

## 2. The core problem

Companies are adding AI to Salesforce, ServiceNow, HubSpot, internal
applications, support tools, workflow platforms, and autonomous agents. Each
system can generate AI activity, but the company often cannot answer basic
questions:

- Which business process caused the AI expense?
- Which employee or digital agent initiated it?
- Which customer, account, project, matter, case, or opportunity benefited?
- Was the full prompt necessary?
- Was the selected model more expensive than the work required?
- Did sensitive information reach an external model?
- What happened when a budget or policy threshold was reached?
- Can the decision be reconstructed later for finance or compliance?

Provider dashboards can show model usage. Business applications can show
records and users. Traditional FinOps tools can show cloud spend. The missing
layer is the connection between the AI request and the business context in
which it occurred.

CostPilot is designed to provide that connection.

## 3. A simple analogy

CostPilot is a combination of:

- an air-traffic controller that directs requests to the appropriate model;
- a baggage inspector that removes unnecessary context before takeoff;
- a budget controller that enforces spending rules;
- a traffic cop that coordinates agents attempting to touch the same record;
- and a receipt that shows who traveled, why, where they went, what it cost,
  and what controls were applied.

## 4. The four product pillars

### Optimize

CostPilot reduces unnecessary token usage through context pruning and routes
work to the lowest appropriate model tier.

### Govern

CostPilot evaluates sensitive terms, department budgets, project budgets,
model rules, agent limits, and collision policies before or during execution.

### Attribute

CostPilot links AI activity to the originating user, agent, source platform,
business record, and project or other unit of work.

### Observe

CostPilot turns each decision into operational, financial, and audit evidence
visible in dashboards, reports, AgentLake, and the audit log.

## 5. The governed request journey

A request can originate in Salesforce, ServiceNow, HubSpot, a custom
application, an API workflow, or an autonomous agent.

The intended request path is:

1. The source system sends the AI task and its available business context.
2. CostPilot identifies the workspace, department, user, agent, source record,
   and project or business context.
3. Sensitive-term and policy controls evaluate the request.
4. CostPilot determines whether pruning is safe for the content type.
5. Context pruning removes unnecessary material when enabled and appropriate.
6. The routing engine evaluates complexity, risk, budgets, agent constraints,
   and any approved tier instruction.
7. The model registry resolves the tier to a real enabled model.
8. In live mode, CostPilot calls the selected provider and returns the response.
9. In simulation mode, CostPilot records the governed decision without
   purchasing a provider response.
10. CostPilot records tokens, cost, pruning, attribution, routing rationale,
    and policy outcomes.
11. Dashboards, project reports, AgentLake, and the audit trail update.

This architecture means CostPilot is not merely observing AI after the fact.
When all model calls flow through it, CostPilot can govern the request before
the model receives it.

## 6. Context pruning

Context pruning is a major CostPilot capability, not a minor optimization.

AI workflows often send repeated signatures, quoted email chains, boilerplate,
duplicate context, excessive whitespace, and other text that adds tokens
without improving the answer. CostPilot can remove this material before the
request reaches the model.

For every governed request, CostPilot can record:

- original token count;
- cleaned token count;
- tokens removed;
- compression percentage;
- whether pruning ran;
- and the resulting cost effect.

Pruning is designed with safety controls. Code-like payloads can bypass unsafe
natural-language cleanup, and an agent can have pruning disabled when its work
requires complete verbatim context. The pruning algorithm should remain
independent from dashboard or integration redesigns.

Pruning creates two forms of value:

- direct token and cost reduction;
- less irrelevant context competing for the model's attention.

## 7. Intelligent model routing

CostPilot uses a four-tier abstraction:

- **Scout:** routine, fast, inexpensive work;
- **Analyst:** moderate analysis and structured reasoning;
- **Advisor:** complex or sensitive business work;
- **Strategist:** highest-capability or explicitly escalated work.

The tiers are business policies, not hardcoded provider names. A company can
map Scout to one OpenAI model, Advisor to an Anthropic model, or replace either
one as pricing and capabilities change.

Routing can consider:

- payload length;
- complexity signals;
- sensitive-term policies;
- department budget status;
- project budget status;
- agent minimum and maximum tiers;
- model availability;
- cascade or fallback behavior;
- and an explicit tier instruction.

The simple explanation is:

> CostPilot examines the work, risk, configured limits, and available models,
> then selects the least expensive approved model capable of handling the
> request.

## 8. Model registry and cost calculation

The model registry stores the models a company has approved, including:

- display name;
- provider;
- provider model ID;
- CostPilot tier;
- input-token price;
- output-token price;
- enabled or disabled status;
- default model for a tier;
- and optional department restrictions.

This allows reports to show real model names rather than only abstract tiers.
CostPilot can compare model usage by spend, requests, and average cost per
request. It can also distinguish provider-reported usage from estimated usage.

One model dominating spend is not automatically an error. A model can represent
most spending while handling a smaller portion of requests because premium
output tokens cost far more. The important question is whether the underlying
work justified that routing.

## 9. Sensitive terms and policy controls

Companies can define terms or patterns that should trigger an action:

- **Flag:** allow the request but mark it for review.
- **Escalate:** send the request to a higher-capability tier.
- **Block:** stop the request before it reaches the model.

Matching must use correct boundaries so a term such as `NDA` does not match the
letters inside `Monday`.

Recommended terms can be enabled, disabled, or removed according to company
policy. CostPilot should not permanently lock a business into a protected
setting because different industries and use cases have different risk models.

A blocked event records the reason and consumes no provider tokens.

## 10. Budget governance

CostPilot supports department budgets and project-level AI budgets.

Department controls can include:

- monthly cap;
- current spend;
- warning state;
- throttled state;
- maximum tier while throttled;
- supervisor override;
- and monthly reset.

Project controls can include:

- monthly AI budget;
- warning threshold;
- and an action such as warn, throttle, or block.

Budget throttling does not have to stop all AI work. A policy can continue the
request on a less expensive model tier.

A true **budget override** is a deliberate supervisor action that temporarily
removes a budget throttle. It is different from a **tier override**, which
means a workflow explicitly requested a model tier for testing or policy
reasons. These concepts must be labeled separately in audit messages.

## 11. The audit trail

CostPilot records a timestamped explanation of each governed decision. An audit
event can include:

- department;
- initiating user;
- agent;
- project or work record;
- source record;
- selected tier and model;
- routing rationale;
- sensitive terms;
- budget snapshot;
- input, output, and pruned tokens;
- cost;
- risk classification;
- and final outcome.

The audit log answers, "Why did CostPilot make this decision?"

The governance event stream is a lighter operational view that answers, "What
important activity is happening now?" The two views are related but serve
different users.

Raw-payload retention can be controlled separately because storing complete
prompts creates security and privacy obligations.

## 12. AgentLake

AgentLake is CostPilot's registry and operational view of AI agents.

It can show:

- agent name;
- department;
- source platform;
- active, idle, queued, or locked state;
- last use;
- request count;
- total cost;
- average cost per call;
- tokens pruned;
- assigned projects;
- and review status.

AgentLake is not a separate model router. It is the control and accountability
view for the digital workers generating governed activity.

To avoid an overwhelming wall of cards, AgentLake supports an executive
overview, department groupings, an all-agents registry, usage-oriented ranking,
and project views.

## 13. Collision control

Two agents can attempt to update the same record at the same time. CostPilot's
collision controls can apply one of three policies:

- **Lock:** prevent both operations until a supervisor reviews the conflict.
- **Queue:** let the current operation finish and place the second in line.
- **Skip:** let the first continue and abandon the competing operation.

This is distinct from budget blocking. A collision describes coordination
between agents targeting shared work.

## 14. Projects and work attribution

CostPilot uses a universal project concept to group AI activity around a
business purpose. The visible label can be adapted:

- project;
- matter;
- engagement;
- case;
- claim;
- account;
- campaign;
- work order;
- or a custom term.

The underlying purpose is the same: connect token usage and cost to meaningful
business work.

A project can include:

- one or more source-system records;
- one or more users;
- one or more agents;
- department;
- owner;
- status;
- budget;
- token usage;
- spend;
- risk events;
- and last activity.

CostPilot does not need to decide whether a Salesforce opportunity eventually
closed or whether an employee was "productive." Its defensible role is to show
which users and agents generated which AI usage for which records and projects.

## 15. Parent and child record linking

Business work often spans several records:

- a Salesforce Account;
- multiple Opportunities under that Account;
- Contacts associated with the Account;
- Cases;
- Quotes;
- and custom child objects.

If every record becomes an unrelated CostPilot project, reporting fragments.
CostPilot therefore supports source links that roll related records into one
canonical project.

The source-system record ID remains the permanent linking key. The record name
is the human-readable label.

For example:

- Account `001...` can be the parent project.
- Opportunity `006...` can be a linked source record.
- A Case and Contact can be additional linked records.
- Every AI call retains its exact origin record while rolling cost up to the
  shared parent.

An admin can define the parent object for a custom implementation, and CostPilot
can suggest child relationships discovered from the source metadata.

## 16. User attribution

CostPilot's user-attribution layer connects an external platform identity to
the governed request.

For Salesforce, an attributed call can include:

- Salesforce user ID;
- name;
- email;
- selected record ID and name;
- agent name;
- source object;
- and department.

This allows reporting such as:

> User A used Agent X on Opportunity Y, generating 2,400 input tokens, 600
> output tokens, 700 pruned tokens, and $0.04 of model cost.

The goal is accountability and cost visibility, not employee surveillance or
automatic performance judgment.

## 17. The Business Context Engine

CostPilot should not sell "field mapping" as the customer experience. It should
sell business understanding.

The Business Context Engine asks a simple onboarding question:

> What do you call the work you want to measure?

The customer can choose a common term or enter a custom value. CostPilot then
uses a Business Context Template to identify:

- the parent business object;
- related child records;
- user identity;
- agent identity;
- department;
- budget fields;
- status fields;
- and the permanent external key.

Out-of-the-box templates can support Salesforce and ServiceNow. Partners can
build templates for other systems and specialized industries.

## 18. Universal integration design

The long-term product should feel equally simple for Salesforce, ServiceNow,
HubSpot, and custom-code users.

Each connector should produce the same normalized envelope:

- workspace;
- source system;
- source object type;
- source record ID;
- source record name;
- parent or account key;
- user identity;
- agent identity;
- department;
- project or context identity;
- task text;
- and requested model when applicable.

After normalization, the same CostPilot pipeline handles pruning, policy,
routing, model selection, budgets, attribution, and audit.

This keeps the product universal without pretending every platform has the same
objects or setup process.

## 19. Connection and metadata discovery

Instead of requiring an administrator to type every field name, CostPilot's
connection workflow can:

1. ask the administrator to authenticate to the business platform;
2. read permitted object and field metadata;
3. identify likely parent, child, user, status, owner, and budget fields;
4. recommend a mapping;
5. allow the administrator to confirm or change it;
6. test the connection;
7. and save the approved template.

The administrator remains in control. Discovery proposes a configuration; it
does not silently govern unrelated company data.

## 20. Salesforce proof of concept

The current Salesforce proof demonstrates a complete governed path.

Salesforce contains:

- `CostPilot_Project__c` for projects;
- `CostPilot_Project_Member__c` for users assigned to projects;
- an Apex Agentforce action;
- a Named Credential for secure CostPilot callouts;
- a permission set;
- and project, tracking, decision, model, and cost fields.

The Agentforce action sends the Salesforce request to CostPilot. CostPilot
resolves the business context, evaluates policy, prunes when safe, selects the
model, records the activity, and returns a structured result to Salesforce.

The proof can use Accounts, Opportunities, CostPilot Projects, and supported
custom relationships. Parent and child Salesforce records can roll into a
single CostPilot project while preserving the exact originating record.

## 21. Salesforce Load Generator

The Salesforce-native CostPilot Load Generator was added to create repeatable,
attributed proof activity without relying only on a browser traffic simulator.

An administrator can select:

- Salesforce record type;
- Salesforce record;
- Salesforce user;
- visible agent name;
- department;
- number of calls;
- routing mix;
- and simulation or live mode.

The routing mixes are:

- **Balanced:** exercises Scout, Analyst, Advisor, and Strategist evenly.
- **Natural:** lets CostPilot classify the generated task normally.
- **Economy:** favors Scout and Analyst and avoids Strategist.
- **Complex:** exercises Advisor and Strategist.

The component shows queued, running, completed, failed, and stopped states,
plus completed and remaining calls.

## 22. Simulation mode versus live mode

Simulation and live activity must be clearly distinguished.

### Simulation mode

Simulation runs attribution, sensitive-term evaluation, pruning, routing,
budgets, transaction recording, and audit logic without purchasing a provider
response. It is intended for demonstrations, validation, and reporting tests.

The Salesforce proof limits a simulation run to 25 calls.

### Live mode

Live mode sends the request through CostPilot to the selected LLM and returns
the response. It consumes provider resources and can incur cost.

The Salesforce proof limits a live run to three calls to protect a small
Trailhead or development org.

Simulation events should be visibly labeled across the audit log, executive
dashboard, project reporting, and AgentLake so test activity is not mistaken
for employee production usage.

## 23. Executive dashboard

The executive dashboard should answer:

> Is AI spend controlled, attributable, and producing measurable savings?

Useful executive metrics include:

- AI spend;
- annualized savings;
- spend avoided;
- budget used;
- governed requests;
- risk and control events;
- projects with AI activity;
- attribution rate;
- unattributed requests;
- spend concentration;
- model cost and usage;
- actual spend versus an uncontrolled baseline;
- and the most important recommended action.

Filters can include:

- department;
- platform;
- agent;
- date;
- tier;
- risk;
- project;
- and person.

Color cannot be the only signal. Labels, icons, patterns, and text must support
people with color-vision differences.

## 24. Project and user reporting

Project reporting can answer:

- What did this project cost?
- Which linked records produced the usage?
- Which users initiated it?
- Which agents performed it?
- Which models were used?
- How many tokens were input, output, and pruned?
- Which requests were blocked, throttled, or escalated?

Person-level comparison can show usage and cost differences, but CostPilot
should avoid claiming that one employee is more productive merely because they
used fewer tokens. The data supports investigation and coaching; business
outcomes require additional context.

## 25. Agent efficiency reporting

Agent efficiency can include:

- request volume;
- active usage;
- total cost;
- average cost per call;
- routing distribution;
- pruning savings;
- failure or block rates;
- and project coverage.

An agent marked "Never" or "Idle" should mean no matching recent transaction
was found, not necessarily that the external agent has never existed or run.
Identity consistency between the source agent name and CostPilot registration
is essential.

## 26. Savings measurement

CostPilot can calculate several distinct savings categories:

- tokens removed through pruning;
- expensive routing avoided by selecting a lower approved tier;
- provider cost avoided by blocked requests;
- budget-control savings;
- and difference from an uncontrolled model-routing baseline.

Savings should be presented with calculation assumptions. Estimated savings
must not be described as provider-confirmed cash savings unless billing data
supports that conclusion.

## 27. Security and deployment boundaries

CostPilot uses platform credentials and workspace keys to authorize governed
requests. Secrets should be stored in secure credential systems such as
Salesforce Named Credentials, not hardcoded in Apex or browser JavaScript.

Production readiness requires:

- authentication and authorization review;
- secret rotation;
- tenant isolation;
- encryption;
- retention policy;
- provider-key strategy;
- least-privilege platform scopes;
- monitoring;
- failure handling;
- and legal and compliance review.

A successful Trailhead proof demonstrates behavior and architecture. It does
not by itself make the integration a finished AppExchange package.

## 28. What is implemented now

The working CostPilot application currently demonstrates:

- governed request routing;
- four model tiers;
- configurable model registry;
- context pruning;
- pruning safety controls;
- sensitive-term flag, escalate, and block policies;
- department and project budgets;
- budget throttling and supervisor override state;
- AgentLake registry and multiple views;
- agent collision policies;
- agent tier limits;
- project and source-record attribution;
- multiple users and agents per project;
- parent and child source linking;
- work-attribution reporting;
- executive and operational dashboards;
- model cost and usage reporting;
- audit detail and exports;
- onboarding and connector generation;
- connection and metadata-discovery foundations;
- Salesforce project objects and memberships;
- Salesforce Agentforce-to-CostPilot governed calls;
- Salesforce-attributed simulation and live load generation;
- and simulation support in the CostPilot backend.

## 29. Important current boundaries

The podcast should be candid about these boundaries:

- The Salesforce integration is a working proof, not yet a one-click managed
  package.
- ServiceNow and HubSpot share the universal connector design, but do not yet
  have the same depth of deployed proof as Salesforce.
- Simulation executes CostPilot governance but does not return a purchased LLM
  answer.
- Estimated token or cost data must be distinguished from provider-reported
  usage.
- User token efficiency is not the same as employee productivity.
- CostPilot provides governance evidence, not legal advice or automatic
  regulatory compliance.
- A current audit-language issue can confuse explicit test tier selection with
  a human budget override. The concepts are being separated into tier override
  and budget override labels.

## 30. A complete example

Sheldon opens an ACME Opportunity in Salesforce and asks Agentforce to summarize
the record.

Salesforce sends:

- the Opportunity ID as the permanent source key;
- the Opportunity name as the visible label;
- Salesforce as the source system;
- Opportunity as the source type;
- Sheldon's user identity;
- the active Agentforce agent name;
- department;
- and the task text.

CostPilot:

1. links the Opportunity to the ACME parent project;
2. records the exact Opportunity as the origin;
3. checks the user's project membership;
4. evaluates sensitive terms;
5. safely prunes unnecessary context;
6. checks project and Sales budgets;
7. routes the request to the appropriate model tier;
8. resolves a real model from the registry;
9. sends the live call or records a simulation;
10. attributes the resulting tokens and cost to Sheldon, the agent, the
    Opportunity, and the ACME project;
11. and writes the rationale to the audit trail.

Later, AI work on the ACME Account, another Opportunity, a Case, or a Contact
can retain its own source record while rolling up to the same ACME project.

## 31. The strategic product progression

CostPilot can be understood as a progression:

1. **AI gateway:** put model calls through one controlled path.
2. **AI governance:** enforce risk, budget, model, and coordination policies.
3. **AI FinOps:** measure tokens, cost, pruning, routing, and savings.
4. **AI accountability:** connect usage to users, agents, projects, and records.
5. **AI business intelligence:** understand how AI resources are being applied
   across real business work.

The Business Context Engine ties these stages together. It turns raw API calls
into business-understandable activity.

## 32. Recommended podcast focus

The hosts should focus on the shift from generic AI cost monitoring to
business-level AI accountability.

The strongest narrative is:

> Companies do not only need to know how many tokens they purchased. They need
> to know why those tokens were used, which business work they supported,
> whether unnecessary context was removed, whether the correct model was
> selected, and what controls were applied before the request left the company.

The episode should spend meaningful time on:

- pruning as both cost control and context discipline;
- why model routing needs business policy;
- why provider dashboards lack project and record context;
- how user, agent, and project attribution changes AI FinOps;
- why universal connectors require a normalized business-context envelope;
- and what the Salesforce proof demonstrates.

## 33. Suggested podcast structure

### Opening

Describe the modern company with AI spread across CRM, service management,
support, internal tools, and autonomous agents. Ask who can explain the bill.

### The visibility gap

Explain why provider usage reports do not know the account, case, project,
matter, or employee behind a request.

### The CostPilot control path

Walk through policy, pruning, routing, model selection, budgets, and audit.

### From tokens to business context

Introduce projects, linked records, users, agents, and the Business Context
Engine.

### The Salesforce proof

Explain the Agentforce workflow and Salesforce-native Load Generator.

### Universal implementation

Show how the same normalized contract can support ServiceNow, HubSpot, and
custom systems.

### Honest boundaries

Distinguish working proof capabilities from packaging and enterprise-hardening
work still ahead.

### Closing

Return to the central message: AI accountability requires connecting every
model call to business purpose.

## 34. Podcast-ready sound bites

- "The AI bill tells you what you bought. CostPilot tells you why you bought
  it."
- "Pruning is not just compression. It is deciding what context the model
  actually needs."
- "A token without business context is only a charge. A token linked to a user,
  agent, and project becomes accountable business activity."
- "The cheapest model is not always the right model, and the most powerful
  model is not always necessary."
- "Provider dashboards understand models. Business applications understand
  work. CostPilot connects the two."
- "Simulation lets a company test governance and attribution without turning
  every demonstration into a provider bill."
- "CostPilot does not decide whether an employee is productive. It provides the
  evidence of how AI resources were used."
- "The universal part is not pretending Salesforce and ServiceNow are the same.
  It is translating both into the same governed business-context contract."

## 35. Final takeaway

CostPilot began with AI cost control, routing, governance, pruning, and agent
coordination. Work attribution and the Business Context Engine do not replace
those foundations. They explain what the controls are protecting and what the
spending is supporting.

The product's unifying idea is:

> Every AI request should be optimized, governed, attributable, and
> explainable before it becomes an untraceable line on a provider bill.
