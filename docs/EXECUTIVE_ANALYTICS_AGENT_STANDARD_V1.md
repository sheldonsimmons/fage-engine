# Executive Analytics Agent Standard v1

**Status:** Proposed standard

**Version:** 1.0

**Reference implementation:** CostPilot Ask CostPilot
**Audience:** Product, engineering, data, security, compliance, and go-to-market teams

## 1. Purpose

This standard defines the minimum product, data, reasoning, evidence, safety, and evaluation requirements for an executive analytics agent. It is intended to be reusable across CostPilot and future enterprise programs.

An executive analytics agent is not a general chatbot connected to a database. It is a governed analytical interface that translates executive language into approved queries, calculates results through deterministic services, explains those results using verified evidence, and makes uncertainty visible.

The governing principle is:

> The language model interprets and explains. The analytics system defines, filters, calculates, validates, and proves.

## 2. Required outcomes

An agent conforming to this standard must help an executive:

- Understand current operating performance.
- Compare periods, business units, products, and programs consistently.
- Identify measured contributors to changes.
- Inspect budgets, risks, adoption, controls, and optimization opportunities.
- Move from a summary to the underlying evidence.
- Understand what data is missing, estimated, simulated, or incomplete.
- Trust that identical questions with identical scope produce reconcilable results.

The agent must not:

- Invent facts, rows, entities, causes, or calculations.
- Silently substitute a different metric, entity, scope, or date range.
- Treat correlation as causation.
- infer employee productivity, intent, or quality from usage alone.
- Present an estimate, projection, or scenario as a recorded fact.
- Hide incomplete coverage or incompatible comparison windows.
- use conversation context to override an explicit current filter.

## 3. Knowledge architecture

The agent must have access to six separately governed knowledge layers.

### 3.1 Product knowledge

Product knowledge explains what the program does, how its controls work, which screens own configuration, and which claims the product can support. It must be curated, versioned, and reviewable. Product documentation must never be treated as transaction evidence.

Required topics include:

- Product capabilities and boundaries.
- Feature definitions and workflows.
- Control and policy behavior.
- Calculation concepts.
- Configuration ownership.
- Known limitations.
- Links to the appropriate screen or record.

### 3.2 Business ontology

Every business term must resolve to one canonical entity or relationship. Each definition must state what the term includes, excludes, and how it is identified.

At minimum, an enterprise analytics ontology should cover:

- Workspace or tenant.
- Person, identity, and organizational unit.
- Agent, application, automation, or service.
- Account, customer, project, case, claim, ticket, matter, and work item.
- Source platform and provider.
- Request, execution, model call, and audit event.
- Requested model, selected model, provider model, and model tier.
- Budget, policy, control, exception, risk, and outcome.
- Live, test, simulator, imported, and estimated activity.

Terms that sound similar but have different analytical meaning must remain distinct. For example, `never used` means zero lifetime governed requests; `inactive` means historical usage but zero requests during the selected period.

### 3.3 Metric registry

Every supported metric must be declared in a versioned registry before the agent can answer questions about it.

Each metric contract must contain:

| Field | Requirement |
|---|---|
| Canonical name | Stable machine identifier |
| Display name | Executive-facing label |
| Definition | Plain-English meaning |
| Formula | Deterministic calculation |
| Source | Authoritative tables, events, and columns |
| Time field | Field used to place a record in a period |
| Grain | Request, model call, agent, day, project, or other unit |
| Supported dimensions | Approved groupings and filters |
| Aggregation behavior | Sum, count, distinct count, weighted average, ratio, or snapshot |
| Scope behavior | Workspace, live/simulator, department, project, and permission rules |
| Null behavior | How missing values affect the calculation |
| Classification | Recorded fact, derived fact, estimate, projection, or scenario |
| Freshness | Expected ingestion or calculation delay |
| Coverage requirements | Minimum completeness needed to answer |
| Validation rules | Invariants that cause an answer to fail closed |
| Owner and version | Accountable team and change history |

Example contract:

```yaml
id: total_tokens
label: Total token usage
definition: Sum of recorded input and output tokens for matching model calls
formula: SUM(input_tokens + output_tokens)
source: token_transactions
time_field: timestamp
grain: model_call
dimensions: [agent, person, department, model, platform, work_item]
classification: recorded_fact
scope_fields: [workspace_id, is_simulation]
validation:
  - input_tokens >= 0
  - output_tokens >= 0
```

### 3.4 Organizational and entity context

The agent must use a governed entity graph that connects customer-specific identities and structures:

```text
Workspace
  -> organizational units
  -> people and agents
  -> accounts and work items
  -> source-system records
  -> governed requests
  -> audit events and model calls
```

Cross-system identity resolution must preserve the source identifier, canonical identifier, mapping source, confidence, and effective dates. Unresolved attribution must appear as unknown or unattributed; it must never be guessed.

### 3.5 Temporal context

Date interpretation must be a deterministic service, not open-ended language-model arithmetic.

The workspace must define:

- Business timezone.
- Week start day.
- Calendar or fiscal year.
- Fiscal quarter boundaries when applicable.
- Data collection start and latest complete time.
- Rules for incomplete current periods.

The temporal service must support:

- Explicit custom ranges.
- Today, yesterday, this/last week, month, quarter, and year.
- Trailing and rolling windows.
- Month-over-month, quarter-over-quarter, and year-over-year.
- Same period last year.
- Year to date against the same point last year.
- Before-and-after comparisons.
- Comparable prior equal-length periods.
- Leap years, daylight-saving changes, and timezone boundaries.

All internal intervals should be half-open: `start <= timestamp < end`. The UI may present an inclusive date label, but the query contract must preserve the exact boundary timestamps.

When the user says “around this time last year,” the resolution order is:

1. Use the explicit active date range if one exists.
2. Otherwise use the most recently confirmed conversational date range.
3. Otherwise use the product's declared default window, normally trailing 30 days.
4. Shift the exact calendar boundaries back one year in the workspace timezone.
5. Display both interpreted ranges before presenting the comparison.

### 3.6 Interaction context

The agent may use:

- Current page and section.
- Visible metric or selected record.
- Active filters and date range.
- The prior confirmed query plan.
- A bounded conversation window.

Precedence must be explicit:

1. Current user statement.
2. Current explicit UI selections.
3. Confirmed conversation state.
4. Workspace defaults.

Context must be represented as structured fields. Raw page text or an entire conversation must not become an unbounded source of analytical truth.

## 4. Supported executive question classes

Conforming agents should implement question classes through registered query contracts.

### 4.1 Overview and totals

- How much did we spend this month?
- How many requests and tokens did we process?
- Where did our AI usage go?
- Give me the three issues that need attention.

### 4.2 Rankings and concentration

- Which agents cost the most?
- Which departments drove the most token usage?
- What percentage of spend came from the top five projects?
- Are we concentrated in one provider or model tier?

### 4.3 Period and cohort comparisons

- Compare this month with last month.
- What was token usage around this time last year?
- Compare live and simulator traffic.
- Compare Support with Legal using the same period.
- What changed after a policy or product launch?

### 4.4 Drivers and explanations

- Why did spend increase?
- Was the increase caused by request volume or cost per request?
- Which entities contributed most to the change?
- Why was this request routed, blocked, or throttled?

Driver analysis may report measured contribution. It may claim causation only when an approved causal method and required evidence exist.

### 4.5 Adoption and accountability

- Which agents have never been used?
- Which agents became inactive?
- Who owns the highest-cost agents?
- What activity is unattributed?
- Where is attribution confidence weak?

### 4.6 Budget, forecast, and scenario

- Which departments are near budget?
- What is projected month-end spend?
- When will the current budget be exhausted?
- What happens if volume increases by 20 percent?

Forecast and scenario answers must show assumptions, method, confidence, and classification.

### 4.7 Risk, policy, and audit

- Why were requests blocked?
- Which policies triggered most often?
- Which requests lack complete decision evidence?
- Did a model selection violate configured bounds?
- Explain one governed request from its audit record.

### 4.8 Data quality and confidence

- How complete is the data?
- When did collection begin?
- Which integrations are stale?
- Is this result live, simulated, estimated, or mixed?
- Can these periods be compared fairly?

## 5. Query-plan contract

The language model must select from a bounded schema. It must not generate executable SQL or arbitrary database instructions.

Minimum query-plan fields:

```json
{
  "intent": "period_comparison",
  "metric": "total_tokens",
  "entity": "workspace",
  "dimensions": [],
  "filters": {
    "workspace_id": "current",
    "traffic_scope": "all"
  },
  "period_a": {
    "kind": "trailing_days",
    "days": 30
  },
  "period_b": {
    "kind": "same_period_previous_year"
  },
  "ranking": null,
  "requested_analysis": ["absolute_change", "percent_change", "contributors"]
}
```

The planner must return a confidence and any material ambiguity. A material ambiguity is one that could change the entity, metric, population, period, or decision. The server must request clarification rather than pick silently.

## 6. Execution and calculation requirements

The analytical service must:

- Authorize the requested scope before querying.
- Resolve all entities to canonical identifiers.
- Resolve periods in the workspace timezone.
- Apply identical non-date filters to comparison windows unless the user explicitly requests otherwise.
- Execute registered metric logic only.
- Calculate absolute change and signed percentage change deterministically.
- Handle zero denominators without false percentages.
- Compute contribution using a declared method.
- Reconcile aggregates to their underlying records.
- Attach freshness and coverage metadata.
- Return a stable calculation identifier and metric-contract version.

## 7. Answer contract

Every analytical response must carry the following machine-readable fields, even if the visible presentation is shorter:

| Field | Purpose |
|---|---|
| `answer_id` | Traceable response identity |
| `interpretation` | What the system understood |
| `intent`, `metric`, `entity` | Contract identifiers |
| `scope` | Workspace, filters, and live/simulator state |
| `periods` | Exact timestamps and display labels |
| `result` | Deterministic values and units |
| `calculation` | Formula, row count, and metric version |
| `evidence` | Supporting rows and drill targets |
| `coverage` | Completeness, freshness, and comparability |
| `classification` | Fact, estimate, projection, or scenario |
| `confidence` | Evidence confidence, not stylistic confidence |
| `limitations` | Missing data or unsupported conclusions |
| `contract_status` | Passed, clarification required, or failed |

The narrative layer may improve readability but may not add a number, entity, cause, recommendation, or date that is absent from the validated result.

## 8. Evidence and provenance

Every material numerical claim must map to:

- A metric contract and version.
- A query/calculation identifier.
- Exact scope and period.
- Source record count.
- Evidence records or an aggregate drill-down.
- Data freshness and coverage status.

Request-level explanations must use the immutable audit record and correlated cost record when available. Aggregate answers must preserve a path to the matching governed requests.

## 9. Coverage and comparability

The coverage object must include:

- Earliest available event.
- Latest available event.
- Latest complete ingestion boundary.
- Expected and observed source coverage.
- Percentage of records with person, agent, department, and work attribution.
- Live and simulator record counts.
- Estimated and provider-reported usage counts.
- Missing days or source outages.
- Whether compared periods are methodologically compatible.

An answer must not describe a period as lower merely because CostPilot did not yet collect that period. It should state that the comparison is unavailable or partial.

## 10. Security, privacy, and permissions

The agent is subject to the same authorization rules as direct product screens and APIs.

Required controls:

- Tenant isolation before planning and querying.
- Row- and dimension-level permissions.
- Redaction of sensitive payloads and personal information.
- Role restrictions for person-level, project-level, and audit-detail answers.
- No permission expansion through follow-up questions.
- Prompt-injection isolation between source content and agent instructions.
- Audit logging of question, query plan, scope, result contract, and requesting identity.

## 11. Failure behavior

The agent must fail closed when:

- No metric contract exists.
- An entity cannot be resolved confidently.
- Required comparison coverage is missing.
- The requested scope is unauthorized.
- The deterministic result violates an invariant.
- Evidence belongs to the wrong entity or period.
- The narration adds unsupported claims.

The response must distinguish:

- **Clarification required:** More user intent is needed.
- **No matching activity:** A valid query returned zero rows.
- **Insufficient coverage:** The system cannot support the conclusion.
- **Unsupported analysis:** The product does not calculate the requested metric.
- **System failure:** An approved calculation could not be completed.

## 12. Evaluation standard

Each registered intent requires an evaluation pack containing:

- Canonical questions.
- At least ten natural paraphrases for buyer-critical intents.
- Follow-up and correction cases.
- Ambiguous cases requiring clarification.
- Zero, sparse, partial, and mixed-source data.
- Date boundary, timezone, leap-year, and fiscal-calendar cases.
- Wrong-entity and wrong-metric mutation tests.
- Permission and tenant-isolation tests.
- Reconciliation tests against source totals.
- Narrative-grounding tests.

Release certification requires:

- 100% correct interpretation for buyer-critical canonical questions.
- 100% answer-contract compliance.
- 100% period and scope disclosure.
- Zero unsupported numerical claims.
- Zero silent metric, entity, period, or scope substitutions.
- Clarification or refusal for every material ambiguity.
- Verified evidence links for every buyer-critical result.

An agent should also be tested for consistency across repeated runs. Language may vary; facts, scope, periods, and conclusions may not.

## 13. Operational governance

Every production agent must declare:

- Product owner.
- Data owner.
- Security owner.
- Supported intent and metric versions.
- Model and prompt versions.
- Evaluation-suite version.
- Last certification date.
- Known limitations.
- Rollback procedure.

Changes to a metric definition, temporal rule, or entity mapping are analytical contract changes and require regression testing even when no model or prompt changes.

## 14. Conformance levels

### Level 0: Conversational prototype

Can discuss product knowledge but is not approved for executive analytics.

### Level 1: Grounded reporting

Uses registered metrics, deterministic calculations, visible scope, and evidence for totals and rankings.

### Level 2: Executive analysis

Adds reliable comparisons, contribution analysis, coverage, follow-ups, permissions, and fail-closed answer contracts.

### Level 3: Decision support

Adds governed forecasts, scenarios, recommendations, outcome tracking, and explicit assumption management.

CostPilot should not market Ask CostPilot as a complete executive analytics agent until it meets Level 2 for its declared buyer-critical question set.

## 15. Definition of done for a new program

A future program conforms to this standard only when it has:

1. A reviewed ontology.
2. A versioned metric registry.
3. A deterministic temporal service.
4. A bounded query-plan schema.
5. Deterministic analytics implementations.
6. Machine-verifiable answer contracts.
7. Evidence, provenance, coverage, and confidence objects.
8. Role-aware authorization.
9. Golden and adversarial evaluation suites.
10. A release certification and monitoring process.
