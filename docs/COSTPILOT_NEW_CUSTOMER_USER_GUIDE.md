# CostPilot New Customer User Guide

Private pilot / trial guide

This guide helps a new customer start a CostPilot trial, connect one platform path, send a first governed AI request, and understand the dashboard.

CostPilot is not an AI model. CostPilot sits between your business platform and your chosen AI provider so your team can route requests, reduce unnecessary context, track spend, enforce policy, and review audit activity.

## 1. What CostPilot Does

CostPilot helps companies manage AI usage across business systems.

It can:

- Route routine requests to lower-cost model tiers.
- Reserve stronger model tiers for complex or sensitive work.
- Remove unnecessary context before a request reaches an AI provider.
- Detect sensitive terms, PII patterns, and code secrets.
- Block, flag, or escalate requests based on policy.
- Track AI spend by department, agent, platform, and model tier.
- Show which AI agents are active in AgentLake.
- Keep an audit trail of routing decisions.
- Show executive savings and governance dashboards.

CostPilot does not replace Salesforce, ServiceNow, HubSpot, Zendesk, or your AI provider. It controls the request path between those systems and the provider.

## 2. Before You Start

Have these ready:

- Your work email.
- Your company or team name.
- One platform to test first.
- One department or team name.
- One AI workflow you want to govern.
- The fields or payload you want sent to AI.

Example first test:

- Platform: Salesforce.
- Object: Case.
- Department: Support.
- Agent name: SF-CaseBot.
- Fields: Subject, Description, Priority.

You do not need to connect every platform on day one. Start with one use case, send one request, then review the result.

## 3. Start a Trial

Open the CostPilot trial link provided to you.

During signup, CostPilot creates:

- A trial workspace.
- A workspace ID.
- A CostPilot key.
- A proxy URL.
- A 30-day trial window.

The workspace ID and key identify your trial workspace when requests are sent through CostPilot.

## 4. Follow the Trial Command Center

After signup, open the workspace dashboard.

The workspace command center walks you through:

1. Trial signup.
2. Setup.
3. Field mapping.
4. Test request.
5. Dashboard and reporting review.

Use this page as your home base during the first setup.

## 5. Choose Your Platform

CostPilot supports multiple setup paths.

Business platforms:

- Salesforce.
- ServiceNow.
- HubSpot.
- Zendesk.
- Dynamics.

Code/API platforms:

- Python.
- Node.js.
- Java.
- Ruby.
- REST/cURL.

Pick the platform that matches the first workflow you want to test.

## 6. Configure Your First Route

In onboarding, choose:

- Platform.
- Object or record type.
- Department.
- Agent name.
- Fields or properties to send to AI.

For Salesforce, fields should use Salesforce API names.

Examples:

- `Subject`
- `Description`
- `Priority`
- `Status`
- `Custom_Field__c`

For ServiceNow, use table column names such as `short_description` or `u_contract_text`.

For HubSpot, use property names.

For REST/API, use the request fields your application sends.

## 7. Generate Setup Code

CostPilot generates setup code based on your selected platform and fields.

The generated code is meant to route your AI request through CostPilot instead of sending it directly to the AI provider.

Depending on the platform, this may be:

- Salesforce Apex plus Flow action.
- ServiceNow Script Include or Flow Action.
- HubSpot custom code workflow.
- Zendesk/custom workflow code.
- Python, Node.js, Java, Ruby, or REST snippet.

The generated code includes CostPilot metadata such as:

- Workspace key.
- Department.
- Platform.
- Agent name.
- Selected fields.

This metadata lets CostPilot show the request in AgentLake, dashboards, reports, and audit logs.

## 8. Send a First Test Request

You have two ways to test.

Option 1: Use the Workspace test panel.

This is the fastest path. Choose a platform sample, review the payload, and click Send Test Request.

Sample options include:

- Salesforce Case.
- ServiceNow Incident.
- HubSpot Ticket.
- Zendesk Ticket.
- REST/API request.

Option 2: Send from your actual platform.

Paste or install the generated setup code, trigger the workflow, and let the request flow through CostPilot.

For Salesforce, this typically means:

- Save the Apex class.
- Create or update a Flow.
- Map the record fields.
- Trigger a test record update.

## 9. What Happens During a Request

When a request reaches CostPilot, the system:

1. Reads the department, platform, and agent identity.
2. Registers or updates the agent in AgentLake.
3. Checks sensitive terms and PII patterns.
4. Blocks the request if policy requires it.
5. Detects code-like payloads and avoids unsafe pruning.
6. Prunes unnecessary text when appropriate.
7. Scores request complexity.
8. Chooses a model tier.
9. Checks department budget status.
10. Sends the governed request to the provider path.
11. Records token usage and cost.
12. Writes an audit event.
13. Updates dashboard and report data.

If the request is blocked, it should not reach the AI provider.

If the request is escalated, CostPilot routes it to a higher model tier based on policy or complexity.

If the request is routine, CostPilot can route it to a lower-cost tier.

## 10. Confirm CostPilot Is Working

After sending a test request, check these places:

### Workspace Dashboard

Look for:

- Calls sent through CostPilot.
- Spend.
- Routing efficiency.
- Department bars.
- Recent calls.
- AgentLake preview.

### Main Dashboard

Look for:

- Total AI spend avoided.
- Economy routing.
- Requests governed.
- Context pruning saved.
- Projected annual savings.
- Department health.

### AgentLake

Confirm the agent appears with:

- Agent name.
- Department.
- Platform.
- Status.
- Last activity.

### Governance Event Stream

Confirm the event appears with:

- Routing decision.
- Model tier.
- Matched keywords if any.
- Department.
- Agent context.
- Budget context.

### Audit Log

Open the audit detail to review:

- Plain-English rationale.
- Prompt payload preview.
- Matched keywords.
- Budget context.
- Agent context.
- Downloadable audit file.

## 11. Read the Main Dashboard

The main dashboard is designed for executives and operators.

Key areas:

### Total AI Spend Avoided

Shows CostPilot's calculated avoided spend based on logged usage, routing tiers, pruning estimates, and baseline assumptions.

### Economy Routing

Shows how many calls were served by lower-cost tiers such as Scout or Analyst.

### Requests Governed

Shows how many AI-related requests CostPilot managed, including routing and governance activity.

### Context Pruning Saved

Shows how many tokens were removed before reaching the model.

### Department Health

Shows each department's spend position and budget usage.

### AgentLake Registry

Shows AI agents connected to CostPilot and their current state.

### Governance Event Stream

Shows recent decisions, escalations, blocks, and routing events.

## 12. Review Reports

The Reports page gives deeper views.

Reports may include:

- Savings.
- Risk.
- Departments.
- Agent activity.
- Bot efficiency.
- ROI calculator.

Use reports to answer:

- Which departments are using AI most?
- Which agents cost the most?
- Which agents are routing efficiently?
- Where are sensitive terms appearing?
- How much context is being pruned?
- What would the cost look like under different assumptions?

## 13. Configure Policy and Routing

After the first test, review your policy settings.

You can configure:

- Sensitive terms.
- Keyword actions: flag, escalate, block.
- Complexity keywords.
- Model tier names.
- Routing threshold.
- Department budgets.
- Agent tier bounds.
- Agent pruning settings.

Examples:

- Add `legal` as an escalation term.
- Add `subpoena` as a block term.
- Set Support to a monthly budget cap.
- Limit a low-risk agent to Scout or Analyst.
- Disable pruning for a code-review agent.

## 14. Configure Models

The Model Registry controls which AI models CostPilot can route to.

For each model, CostPilot tracks:

- Display name.
- Provider.
- API model ID.
- Tier.
- Input price.
- Output price.
- Enabled state.
- Default state.
- Optional department scope.

Model pricing is currently maintained in the registry. If provider prices or customer contract rates change, the registry should be reviewed so reporting stays accurate.

## 15. Voice Guard

Voice Guard handles call transcripts.

It can:

- Process a transcript.
- Redact PII.
- Return a clean transcript.
- Track redaction count.
- Track PII types found.
- Store event details.
- Avoid storing raw transcript when PII is found.

Use Voice Guard when AI workflows involve support calls, sales calls, contact center transcripts, or dictated customer information.

## 16. Upgrade Request

When you are ready to continue beyond the trial, use the Upgrade page.

The upgrade request captures:

- Name.
- Email.
- Company.
- Requested plan.
- Notes.

Submitting the request records your upgrade interest for follow-up.

## 17. First-Use Checklist

Use this checklist for your first trial run:

- Start trial.
- Open workspace command center.
- Add or confirm departments.
- Choose one platform.
- Choose one object or record type.
- Enter one agent name.
- Add fields to send to AI.
- Generate setup code.
- Send one test request.
- Confirm the agent appears in AgentLake.
- Confirm the event appears in the Governance Event Stream.
- Open the audit detail.
- Review dashboard savings and routing.
- Review reports.
- Decide whether to continue testing or request upgrade.

## 18. Good First Use Cases

Salesforce Case triage:

- Send Case subject, description, priority, and account tier.
- Route routine cases to lower-cost tiers.
- Escalate legal, HIPAA, or fraud language.
- Log the decision for review.

ServiceNow incident summary:

- Send incident short description, work notes, impact, and urgency.
- Route routine incidents to lower-cost tiers.
- Escalate critical incidents.
- Track IT department spend.

HubSpot ticket or deal support:

- Send ticket content or deal notes.
- Summarize customer context.
- Track Sales or Support usage.

Zendesk ticket response:

- Send ticket subject, comment, priority, and customer context.
- Generate a support draft through a governed request path.

Custom API workflow:

- Replace a direct AI endpoint with the CostPilot proxy.
- Add department, platform, and agent headers.
- Review routing and cost in CostPilot.

## 19. Common Questions

### Is CostPilot an AI model?

No. CostPilot is not a foundation model. It routes, prunes, checks, logs, and reports on requests sent to existing AI providers.

### Do we need to connect every platform immediately?

No. Start with one platform and one use case.

### Can we add more fields later?

In the current pilot flow, adding more fields may require regenerating or editing setup code. The long-term connector direction is to support stable installed connectors with configurable mappings.

### Can CostPilot block sensitive requests?

Yes. The sensitive-term and PII policy layer can block, flag, or escalate depending on the configured action.

### Does CostPilot guarantee savings?

No. CostPilot calculates savings views based on logged usage, routing decisions, pruning estimates, rates, and baseline assumptions. Actual savings depend on usage patterns and configuration.

### Does CostPilot add latency?

CostPilot adds a governed request path between the platform and provider. Its local checks run before the provider call, but the total response time still depends heavily on the external AI provider and payload size. Do not treat dashboard refresh intervals as request latency.

## 20. What to Tell Your Internal Team

Use this short explanation:

CostPilot sits between our business systems and AI providers. It helps route each AI request to the right model tier, remove unnecessary context, check for sensitive data, track department spend, show which agents are active, and keep an audit trail. We are starting with one platform and one workflow so we can see the value before expanding.

