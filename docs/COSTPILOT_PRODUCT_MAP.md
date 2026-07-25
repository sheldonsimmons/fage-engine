# CostPilot Product and Information Architecture Map

## Product definition

> CostPilot is an AI control, optimization, and accountability layer that
> removes unnecessary tokens, routes requests to the right model, enforces
> policies and budgets, and connects AI spending to business work.

CostPilot has four product pillars:

| Pillar | Customer outcome | Core capabilities |
| --- | --- | --- |
| Optimize | Reduce AI cost before it happens | Context pruning, model routing, spend avoided |
| Govern | Control what AI is permitted to do | Policies, budgets, sensitive-data controls, allow/block/queue/skip |
| Attribute | Connect technical usage to business ownership | Projects, matters, cases, users, agents, customers, departments |
| Observe | Understand performance and investigate decisions | Executive dashboard, AgentLake, audit log, reports |

These pillars are a product boundary. New functionality should support at least
one of them. Workflow management, project scheduling, general agent building,
and task-quality evaluation are not current CostPilot responsibilities.

## Page ownership rule

Every main page owns one primary user question. A page may contain several
components, but each component should help answer that question. Detailed
information can be summarized on another page only when it links back to the
owning page.

## Current production page map

| Current page | Primary user question | Pillar | Owns |
| --- | --- | --- | --- |
| Executive (`index.html`) | Is our company’s AI usage healthy, optimized, and under control? | Optimize, Govern, Observe | Company-level spend, spend avoided, pruning impact, budget health, material risk, uncontrolled baseline, leading cost drivers |
| Operate / AgentLake (`operate.html`) | Which AI agents and live requests are being used, and which need attention? | Observe, Govern | Agent inventory, active/unused agents, adoption, agent cost, live activity, operational exceptions |
| Work (`work-items.html`) | Which business work is consuming AI resources? | Attribute | Projects/matters/cases, customers, users, assigned agents, work budgets, work-level spend and risk |
| Govern (`policy.html`) | What is CostPilot permitted to allow, change, route, queue, or block? | Govern, Optimize | Policy order, routing rules, sensitive terms, collision behavior, policy testing |
| Models (`models.html`) | Which models can CostPilot use, and what do they cost? | Optimize, Govern | Provider/model catalog, price, tier, availability, defaults, routing eligibility |
| Reports (`reports.html`) | What patterns have developed over time? | Observe, Optimize, Govern, Attribute | Historical trends, comparisons, exports, savings, risk, departments, agent activity |
| Administration (`admin.html`) | How is this CostPilot workspace configured and managed? | Govern | Workspace administration, departments, access, lifecycle controls, system tools |
| Connect & Setup (`onboarding.html`, `connect.html`) | How does CostPilot understand our business and connect to our systems? | Attribute, Govern | Business Context template, platform connection, credentials, generated integration setup |

## Supporting experiences

These are useful supporting tools, not top-level product destinations:

| Experience | Purpose | Recommended location |
| --- | --- | --- |
| Sandbox | Test routing and policy safely | Tools |
| Savings Calculator | Estimate potential value before or during adoption | Tools |
| Live Demo | Demonstrate the end-to-end CostPilot story | Tools |
| Live Monitor | Diagnose service health and demo traffic | Admin/System Tools or status link |
| Getting Started | Guided education and help | Help |
| Trial and Upgrade | Account conversion | Account/Workspace |
| ROI Calculator | Analyze or estimate value | Reports, with calculator linked from Tools |

Experimental and duplicate pages such as `index2.html`, `live.html`,
`live-reports.html`, and `live-landing.html` should remain available for demos
or testing but should not define the normal production navigation.

## Component ownership

### Executive

The Executive page owns company-level signals:

- AI spend for the selected period
- spend avoided
- tokens removed through pruning
- governed requests
- budget health
- risk requiring executive attention
- actual spend versus uncontrolled baseline
- spend concentration by department or business work
- top optimization opportunities

It may show a short risk or agent signal, but detailed rows belong in Govern,
AgentLake, or the Audit Log.

### Operate / AgentLake

AgentLake is CostPilot's AI-agent inventory and operations view. It owns:

- registered agents
- active, inactive, and never-used agents
- agent ownership, department, and platform
- request volume, spend, pruning, and last activity by agent
- agents needing review
- agent-to-project participation
- live operational request activity

Agent creation, archival, permissions, and other administrative mutations should
live in Administration. AgentLake can link to those controls.

### Work

The Work page owns business attribution:

- configurable work terminology from the Business Context template
- customer/account/client association
- work owner and participants
- assigned AgentLake agents
- work budget, spend, tokens, pruning, and risk
- attribution sources and rules
- recent attributed activity

CostPilot does not own project schedules, files, milestones, or general task
management.

### Govern

Govern owns the complete pre-execution decision:

- context-pruning policy
- model-routing rules
- sensitive terms
- budget behavior
- collision behavior
- allow, block, queue, and skip outcomes
- policy exceptions
- safe policy testing

The page should explain the order in which controls are evaluated.

### Models

Models owns the model catalog:

- provider and API model ID
- input, cached-input, and output pricing
- model tier and capability
- enabled/disabled state
- default and fallback eligibility
- supported execution settings when applicable

Govern references models when defining rules; it does not duplicate model
administration.

### Reports

Reports owns historical analysis and export:

- spend and savings trends
- pruning trends and savings sources
- risk and compliance trends
- department comparisons
- work/customer comparisons
- agent adoption and efficiency trends
- CSV/PDF export

Reports should not duplicate the Executive page's action strip or pretend to be
a second executive dashboard.

### Administration

Administration owns configuration mutations:

- workspace settings
- department creation and budget configuration
- user access and roles
- agent registration, lifecycle, and permissions
- routing-tier naming
- system tools and maintenance actions

Administration should not present a second operational AgentLake experience.

### Connect & Setup

Connect & Setup owns:

- what the customer calls its work
- where the work lives
- customer terminology
- what CostPilot should measure
- platform credentials
- generated integration code
- connection validation

Advanced field mapping remains optional. A custom value must be available for
business terminology and source systems.

## Confirmed overlaps and resolution

| Overlap today | Owning page | Other page behavior |
| --- | --- | --- |
| AgentLake appears in Operate and Admin | Operate / AgentLake | Admin keeps registration and lifecycle controls, then links to AgentLake for activity |
| Department budgets appear in Operate and Admin | Admin for configuration; Executive/Operate for status | Executive and Operate show utilization and exceptions without duplicating editing |
| Agent efficiency appears in Operate and Reports | Operate for current action; Reports for history | Operate shows most/least/unused now; Reports shows trends and comparisons |
| Audit activity appears in Operate and governance streams | Operate for live exceptions; Audit detail for investigation | Summary cards link to one detailed event history |
| Executive ROI appears in Executive and Reports | Executive for current headline; Reports for historical analysis | Reports removes duplicate action language and retains detailed trends/export |
| Routing configuration appears in Policy, Admin, and Models | Govern for rules; Models for catalog; Admin for naming/system settings | Cross-links replace duplicated controls |
| Connection guidance appears in Onboarding, Connect, and Work | Connect & Setup | Work shows attribution source status and links back to connection setup |
| Projects appear in Work and AgentLake | Work owns projects; AgentLake owns agents | AgentLake's Projects view answers which agents participate, then links to Work details |

## Proposed navigation

Primary navigation:

1. **Executive** — company health and value
2. **Operate** — AgentLake and live operations
3. **Work** — projects, matters, cases, or the configured custom label
4. **Govern** — policies, routing, budgets, and controls
5. **Reports** — historical analysis and exports

Manage menu:

- Connect & Setup
- Models
- Administration

Tools menu:

- Sandbox
- Savings Calculator
- Live Demo

Utilities:

- System status
- Help

The Work label should use the Business Context template when practical. Until
that template is loaded, `Work` is the safe universal label.

## Cross-page decision rule

When deciding where a component belongs:

1. Identify the decision the user is trying to make.
2. Place the full component on the page that owns that decision.
3. Show only a compact signal on other pages.
4. Link the signal to the owning page with the relevant filter applied.
5. Do not duplicate editing controls across pages.

Example:

> Executive shows "3 high-risk events need attention." Selecting it opens
> Operate or the Audit Log filtered to those events. Executive does not render
> the complete event stream.

## First implementation slice

The first implementation slice should be the Executive page because it defines
the CostPilot story for every other page.

Its initial hierarchy should be:

1. Action required
2. Spend, spend avoided, tokens pruned, governed requests, and budget/risk health
3. Actual spend versus uncontrolled baseline
4. Savings mix showing pruning and routing contributions
5. Spend concentration by business work or department
6. Top cost and optimization drivers
7. Links to AgentLake, Work, Govern, and Reports for detail

This slice should initially reorganize existing data and components. It should
not introduce task-completion rules, attempt tracking, or another new product
domain.
