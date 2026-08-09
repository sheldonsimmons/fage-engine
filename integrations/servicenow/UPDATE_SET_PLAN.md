# CostPilot ServiceNow Update Set Plan

This is the packaging boundary for the proof-of-concept connector. It is
designed to be installed in a second ServiceNow PDI without copying credentials
or instance-specific record identifiers.

## Include

1. Flow Designer Action: **CostPilot Governed AI Request**
2. Script-step inputs and outputs for the Action
3. UI Action: **Ask CostPilot AI** on `change_request`
4. System properties:
   - `costpilot.api_base_url`
   - `costpilot.servicenow.default_agent`
   - `costpilot.servicenow.default_department`

## Exclude

- The retired record-triggered Business Rule
- OAuth application client secrets
- OAuth tokens
- PDI URLs or tenant identifiers
- Test Change Requests
- CostPilot audit or attribution data

## Create the candidate Update Set

1. Create a Local Update Set named **CostPilot ServiceNow Connector 0.1**.
2. Make it the current Update Set.
3. Open and save the Flow Designer Action without changing its behavior.
4. Open and save the UI Action without changing its behavior.
5. Create the three System Properties listed above.
6. Review the Update Set's Customer Updates and confirm that no OAuth secrets,
   tokens, test records, or unrelated changes are present.
7. Mark the Update Set Complete and export it as XML.

## Clean-PDI acceptance test

1. Import and preview the Update Set in a second PDI.
2. Resolve only expected missing dependencies; reject unrelated changes.
3. Commit the Update Set.
4. Set `costpilot.api_base_url`.
5. Connect the PDI to CostPilot with OAuth.
6. Open a Change Request and invoke **Ask CostPilot AI**.
7. Confirm the response returns to ServiceNow.
8. Confirm CostPilot records the source instance, user, record, agent, model,
   tokens, cost, routing, pruning, and audit event.
9. Save the record without invoking AI and confirm no CostPilot event is
   created.

The connector is package-ready only when all ten Action outputs are populated
and the non-AI record-update test produces no CostPilot traffic.
