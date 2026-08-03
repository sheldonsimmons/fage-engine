# CostPilot Executive Analytics Agent Gap Matrix

**Assessment target:** Ask CostPilot

**Standard:** Executive Analytics Agent Standard v1

**Assessment date:** 2026-08-03
**Purpose:** Identify what CostPilot already has, what is incomplete, and the safest implementation sequence.

## 1. Executive assessment

Ask CostPilot has a strong Level 1 foundation. It already separates bounded language interpretation from deterministic reporting, exposes evidence and provenance, correlates governed requests with audit and cost records, and fails closed for selected buyer-critical contracts.

It is not yet a complete Level 2 executive analytics agent. The most important missing capabilities are a centralized metric registry, a general temporal/comparison engine, formal data-coverage and comparability reporting, systematic ambiguity handling, and a broader certification suite.

Current estimated conformance:

| Level | Status | Assessment |
|---|---|---|
| Level 0: Conversational prototype | Met | Curated product knowledge and contextual explanations exist |
| Level 1: Grounded reporting | Substantially met | Deterministic reporting, bounded intents, evidence, and contract checks exist |
| Level 2: Executive analysis | Partial | Comparison, coverage, ambiguity, permissions, and driver analysis need expansion |
| Level 3: Decision support | Early | Some optimization and budget language exists; governed forecasting and scenarios are not standardized |

## 2. Detailed gap matrix

| Capability | Current state | Gap | Priority | Acceptance condition |
|---|---|---|---|---|
| Product knowledge | Curated topics in `backend/core/costpilot_knowledge.py` | Knowledge schema lacks owner, version, effective date, and claim classification | P1 | Every topic has ownership, versioning, supporting product route, and review date |
| Canonical intent contracts | Buyer-critical patterns and validation in `backend/core/ask_costpilot_contracts.py` | Coverage is limited and intent contracts are embedded in code | P0 | Registry covers all certified demo questions and mutation tests reject wrong entities/metrics |
| Bounded LLM planning | Tool schema limits intent, entity, metric, period, and filters | Schema supports one primary period and a small metric vocabulary | P0 | Planner emits a versioned semantic query plan with two periods and ambiguity output |
| Deterministic reporting | `project_activity_reporting` computes facts and breakdowns | Metric logic is not exposed as a centralized registry | P0 | Every supported metric resolves through one versioned definition and validation contract |
| Date parsing | Calendar periods and rolling days are resolved server-side | Uses UTC directly; lacks workspace timezone, fiscal calendar, same-period-last-year, and explicit paired periods | P0 | Temporal service passes timezone, leap-year, fiscal, rolling, YTD, and YoY tests |
| Period comparisons | Equal-length immediately preceding comparison exists | Cannot reliably express same period last year or arbitrary paired windows | P0 | Same filters run over explicit Period A and Period B; exact ranges and comparability are returned |
| Driver analysis | Rankings can show high-usage entities | No reusable contribution/decomposition engine | P1 | Agent can decompose change into request volume, tokens/request, model mix, and entity contributors without causal overclaiming |
| Scope handling | Request includes workspace and reporting filters | Precedence across question, screen, conversation, and defaults is not formalized | P0 | Scope object records source and precedence for every filter and never silently overrides explicit user intent |
| Live versus simulator | Counts and provenance distinguish live, simulator, mixed, or no activity | Compatibility policy for comparisons is not formalized | P0 | Every answer declares traffic scope; comparisons use identical scope or explain the difference |
| Governed request evidence | Correlation fields connect transaction and audit records | Historical records may have missing correlation and this needs explicit coverage | P1 | Request-level answer reports evidence completeness and fails closed if the requested explanation is unsupported |
| Aggregate evidence | Answers include evidence rows and drill filters | Evidence identity and calculation version are not first-class | P1 | Every material claim has an answer ID, calculation ID, metric version, row count, and drill target |
| Data provenance | Payload reports source scope and measurement notes | No comprehensive coverage object or missing-day detection | P0 | Response includes earliest/latest data, latest complete boundary, missing periods, attribution percentages, and comparability |
| Attribution | People, agents, organizations, work items, source records, and confidence fields exist | Cross-system entity-resolution policy and effective dating are incomplete | P1 | Canonical mapping preserves source ID, confidence, mapping method, and validity dates |
| Agent adoption | Never, recently inactive, low, and active states are separated | Lifetime source and registration scope must remain covered by contracts | P0 | “Never used” always means registered plus zero lifetime governed requests under the authorized workspace |
| Product explanation versus analytics | Product responses and deterministic analytics are separate paths | Some wording can still blur configured behavior and observed behavior | P1 | Every response is classified as product knowledge, recorded analysis, estimate, projection, or scenario |
| Clarification behavior | Contract failure can withhold unsafe output | Material ambiguity is not modeled systematically | P0 | Planner returns ambiguity candidates; server asks a targeted clarification before executing materially different interpretations |
| Follow-up conversation | Prior messages and screen context are accepted | Confirmed analytical state is not stored as a structured query plan | P1 | Follow-ups operate on a prior validated plan and display changed fields |
| Permissions | Workspace scope is present in reporting calls | Role- and dimension-level analytics authorization is not part of the agent contract | P0 before customer production | Person, project, payload, and audit-detail access is checked independently of the LLM |
| Forecasting and scenarios | Budget and optimization narratives exist | No versioned forecast/scenario engine with assumptions and backtesting | P2 | Forecasts expose model, assumptions, confidence interval, data horizon, and fact/projection distinction |
| Recommendations | Deterministic paths can return recommendations | Recommendation eligibility and outcome tracking are not standardized | P2 | Every recommendation links to evidence, projected effect, risk, owner, and later measured outcome |
| Evaluation | Ask CostPilot tests include canonical, paraphrase, rejection, provenance, and governed-request cases | Missing a complete question catalog, temporal matrix, permission suite, and production certification report | P0 | Certification thresholds in the standard pass and produce a versioned report |
| Observability | Application logs planner/narration failures | No dedicated analytical quality telemetry | P1 | Track clarification rate, contract failures, unsupported questions, zero-result rate, evidence coverage, and reconciliation errors |

## 3. Required data additions

The current transaction and audit records provide a useful base: timestamps, token counts, costs, models, tiers, routing rationale, execution status, risk, workspace, organizational attribution, live/simulator state, and governed-request correlation.

The following metadata should be added or formalized before Level 2 certification.

### Workspace analytical settings

- `timezone_name`, for example `America/Chicago`.
- `week_starts_on`.
- `calendar_type`, such as calendar or fiscal.
- `fiscal_year_start_month` and optional start day.
- `default_analysis_window_days`.
- `data_collection_started_at`.

### Ingestion and coverage

- Source connection identifier on analytical facts.
- Provider event timestamp and CostPilot ingestion timestamp.
- Source-reported usage versus CostPilot estimate.
- Batch or ingestion-run identifier.
- Latest successful synchronization time.
- Expected source cadence.
- Missing or partial period indicators.

### Metric and calculation provenance

- Metric contract ID and version.
- Calculation/query ID.
- Query-plan version.
- Temporal-rule version.
- Coverage snapshot ID.
- Answer contract ID and version.

These additions do not require storing sensitive prompts. They describe the analytical record and how the result was produced.

## 4. Certified CostPilot question catalog

The first Level 2 certification pack should include these buyer-critical questions and paraphrases.

### Spend and usage

- How much did we spend this month?
- How many tokens did we use?
- Which agents cost the most?
- Which departments used the most tokens?
- What is our average cost per request?

### Dates and comparisons

- Compare this month with last month.
- What was token usage around this time last year?
- Compare the last 30 days with the same period last year.
- Compare year to date with the same point last year.
- What changed quarter over quarter?

### Adoption

- Which agents have never been used?
- Which agents have gone inactive?
- Which agents have low usage?
- Which registered agents have no owner?

### Governance and decisions

- Why were requests blocked?
- Why did CostPilot route this request to this model?
- Show high-risk requests from this month.
- Which routing decisions lack complete evidence?

### Savings and optimization

- How much did CostPilot save?
- How much came from routing versus pruning?
- Which agents have the largest measured optimization opportunity?
- Did savings improve without increasing failures?

### Confidence and coverage

- Is this live or simulator data?
- When did CostPilot start collecting data?
- How complete is the attribution?
- Can this period be fairly compared with last year?

## 5. Implementation roadmap

### Phase 1: Contracts and temporal foundation

1. Introduce a versioned metric registry for the existing supported metrics.
2. Define a semantic query-plan schema separate from the HTTP request model.
3. Build the workspace-aware temporal service.
4. Add explicit Period A and Period B objects.
5. Add a coverage and comparability object.
6. Expand answer-contract validation to comparison answers.

**Exit criterion:** CostPilot can correctly answer and prove “What was token usage around this time last year and compare the two?” across timezone, no-data, partial-data, and mixed-source cases.

### Phase 2: Executive analysis

1. Add contribution and change decomposition.
2. Formalize filter/context precedence.
3. Persist a structured validated query plan for follow-ups.
4. Add answer and calculation identifiers.
5. Add ambiguity detection and clarification responses.
6. Add role-aware analytical authorization.

**Exit criterion:** CostPilot can explain measured changes without unsupported causal claims and maintain scope through follow-ups.

### Phase 3: Certification and observability

1. Build the full golden-question catalog.
2. Generate paraphrase and adversarial suites.
3. Add reconciliation, mutation, tenant, and permission tests.
4. Capture analytical-quality telemetry.
5. Produce a versioned demo certification report.

**Exit criterion:** All Level 2 release thresholds pass locally before deployment.

### Phase 4: Governed decision support

1. Add forecast and scenario contracts.
2. Add explicit assumptions and confidence intervals.
3. Add recommendation eligibility rules.
4. Track recommendation acceptance and measured outcomes.

**Exit criterion:** Predictions and recommendations remain clearly separated from recorded facts and can be backtested.

## 6. Immediate next build slice

The first implementation slice should be temporal comparison because it exercises the core standard without requiring speculative AI behavior.

Deliverables:

- `MetricDefinition` registry for current Ask CostPilot metrics.
- `AnalyticalPeriod` and `ComparisonPlan` contracts.
- Workspace-aware half-open date resolution.
- Same-period-last-year, prior-period, MoM, QoQ, and YTD comparison modes.
- Coverage and comparability calculation.
- Paired deterministic report execution with identical non-date filters.
- Exact period labels in the answer.
- Contract validation for values, scope, periods, and evidence.
- Regression cases for the certified comparison questions.

No production deployment should occur until the complete local Ask CostPilot suite and backend suite pass.

## 7. Demo stop conditions

The agent should not present a polished analytical answer during a buyer demo when:

- The interpreted entity differs from the question.
- The metric contract is missing.
- Period A or Period B is ambiguous.
- Historical coverage is absent or materially incomplete.
- Live and simulator scope changed between periods.
- The answer cannot identify its source record population.
- The narrative introduces a number or cause outside the deterministic payload.
- A contract validator reports any violation.

In those cases, CostPilot should display the interpretation and ask for clarification or explain the coverage limitation. A transparent limitation protects buyer confidence better than a fluent but incorrect answer.
