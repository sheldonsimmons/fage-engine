# FAGE — Product Notes & Known Limitations

Personal reference for design decisions, known gaps, and future considerations.
Updated as features are built.

---

## Architecture Decisions

### Routing Tiers
- **Scout (1)** → routine, fast, cheap
- **Analyst (2)** → moderate complexity (keyword OR over token threshold)
- **Advisor (3)** → complex (keyword AND over token threshold)
- **Strategist (4)** → sensitive term escalation or `[STRATEGIST]` tag override
- Analyst (Tier 2) cascades **UP** to Advisor (Tier 3) if no Tier 2 model is registered — this is intentional (better to over-serve than under-serve mid-tier requests)

### Tier Prefix Tags
Users can force a specific tier by starting their payload with:
`[SCOUT]`, `[ANALYST]`, `[ADVISOR]`, `[STRATEGIST]`
- Tag detection looks within the first 100 characters to handle Salesforce `DESCRIPTION:\n[TAG]` field-label prefixes
- Prefix must be the first non-whitespace content after a simple field label (no full sentences before the tag)
- Tags are stripped before the payload is sent to the model

### Sandbox Mode
- All calls from `/sandbox.html` pass `is_test: true` to the API
- Backend skips transaction recording, budget updates, and audit writes entirely
- Results are session-only — nothing persists
- `is_test` is an honor-system flag — there is no authentication enforcing it

### Budget Throttle
- Auto-throttle triggers when `current_spend_usd >= monthly_cap_usd`
- Override requires a manual flag (`override_granted = True`) set in the DB or budget panel
- Throttled departments are forced to Scout (Tier 1) regardless of payload complexity or tier tags

---

## Current Limitations

### Routing Config is Global
- The token threshold and complexity keywords apply to **all departments equally**
- There is no per-department routing sensitivity (e.g., Legal can't have a lower escalation threshold than Support)
- **Future:** Per-department routing config override table

### Sensitive Terms: UI Scope Gap
- The `SensitiveTerm` DB model has a `department` column (supports per-department terms)
- The UI (Policy & Rules page) does not expose a department scope field when adding terms
- All terms added through the UI are global
- **Future:** Add department scope selector to the Add Term form

### Audit Events: ROUTINE Calls Not Logged
- Audit events are only written for COMPLEX, MODERATE, THROTTLED, and OVERRIDE decisions
- ROUTINE Scout calls are not audited (by design — reduces noise)
- Voice Guard calls are logged separately in the `voice_events` table
- **Future:** Optional full-audit mode (configurable flag per department)

### Voice Guard: No Per-Department PII Rules
- Voice Guard applies the same PII detection rules to all departments
- No way to configure department-specific PII sensitivity or additional patterns
- **Future:** Department-specific Voice Guard policy

### Voice Guard: Stats Query Cap
- The PII type breakdown in `/api/voice/stats` is capped at 500 rows to prevent full-table scans
- On very high-volume deployments the breakdown may not reflect the full dataset
- **Future:** Pre-aggregate PII type counts into a summary table

### Model Registry: Default Clearing is Tier-Scoped Only
- When setting a model as default, it clears other defaults for that tier globally
- With department-specific models, clearing defaults should be scoped to `tier + department`
- This was addressed when department assignment was added (v142+)

### Reports: Model Downgrade Savings Calculation
- "Model Downgrade Saved" calculates savings only for Scout-tier calls vs Advisor rates
- Does not account for MODERATE (Analyst) calls — those are treated as savings too but not included
- Conservative estimate intentional

### Agent Scale
- Agent cards in the Agentlake panel switch to table view at 16+ agents (planned)
- Currently: cards only, no auto-switch — large agent counts cause visual overflow
- **Future:** Auto-switch to compact table at 16+ agents with search/filter

### Demo Data Scripts
- `populate_demo.py` and `populate_enterprise.py` seed transaction data
- If department-specific models are configured, demo data does not reflect them — all demo transactions use global model tier labels
- **Future:** Update demo scripts to respect department model assignments

---

## Deployment Notes

### Heroku
- App: `fage-engine` on Heroku Basic dyno
- Postgres: Basic plan — limited connections (~25 max)
- **Warning:** Loading too many polling JS scripts simultaneously can exhaust the DB connection pool and crash the dyno (occurred during v140 deploy — resolved by removing hidden panel scripts)
- Static files served by FastAPI `StaticFiles` mount — must be registered LAST in `main.py` or it intercepts API routes

### Migrations
- No Alembic — schema changes use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `_run_migrations()` in `main.py`
- Safe to re-run on every startup
- Must add new migration blocks manually when new columns are added

### Salesforce Integration
- Salesforce Flows send case description with `DESCRIPTION:\n` prefix before the payload
- FAGE tag detection handles this by searching within the first 100 characters
- Flows may fire on record create before the description field is populated — `min_tokens=20` filter catches these empty payloads

---

## Feature Log

| Version | Feature |
|---------|---------|
| v135 | Tier prefix tags (`[ANALYST]`, `[ADVISOR]`, etc.) |
| v136 | Debug tag endpoint + Salesforce DESCRIPTION prefix fix |
| v137 | Tag detection extended to 100 chars for field-label prefixes |
| v138 | Timeseries chart refreshes on Reset All |
| v139 | Sandbox page — isolated test environment, is_test flag |
| v140 | Policy & Rules page, mic in Sandbox, routing/keywords moved out of dashboard |
| v141 | Removed hidden panel scripts — fixed dyno crash from voice/stats polling |
| v142 | Department-specific model assignment in Model Registry |
