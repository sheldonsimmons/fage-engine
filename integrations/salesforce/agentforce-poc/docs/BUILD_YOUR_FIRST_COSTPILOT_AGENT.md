# Build Your First CostPilot-Governed Salesforce Agent

**Estimated time:** 15–20 minutes

This guide walks a Salesforce administrator through creating an Agentforce
agent, connecting it to CostPilot, publishing the correct version, and proving
that a live request was governed and audited. You do not need to write Apex.

## What you will complete

1. Install the CostPilot Salesforce Connector.
2. Connect Salesforce to a CostPilot workspace.
3. Create or select an Agentforce agent.
4. Add the packaged Route Through CostPilot action.
5. Require every response to use the governed result.
6. Publish and activate the correct agent version.
7. Verify standalone and record-based requests.

## Before you begin

- Enable Einstein and Agentforce in the Salesforce organization.
- Use a Salesforce administrator who can create and publish agents.
- Install CostPilot Salesforce Connector 0.1.0.7 or later.
- Have a CostPilot workspace available to connect.
- Open **App Launcher → CostPilot Setup** and complete Steps 1–3.

## 1. Create or open the agent

1. In Salesforce Setup, search for **Agentforce Agents**.
2. Open an existing agent selected in CostPilot Setup, or select **New Agent**.
3. For a new sales agent, use these recommended values.

| Field | Recommended value |
| --- | --- |
| Label | CostPilot Sales Assistant |
| API Name | CostPilot_Sales_Assistant |
| Description | Helps sales users complete AI work governed, routed, and recorded by CostPilot. |
| Role | Assist sales users with research, summaries, follow-ups, and recommended actions. |
| Company | Use Salesforce business context while sending every AI request through CostPilot. |

4. Save the agent and open it in the current Agentforce Builder.

## 2. Create the governance topic

Add a topic named **CostPilot Governed AI Work**.

**Classification description**

> Handles every AI request that must be governed, routed, executed, and audited by CostPilot.

**Scope**

> Route the complete user request through CostPilot and return only the governed result.

**Instructions**

```text
For every user request, invoke Route Through CostPilot before responding.
Pass the user's complete request as User Prompt. When Salesforce record context is available, include its record ID and object name.
Return the CostPilot Response to the user. Never create an alternative answer if the action fails.
If CostPilot cannot complete the request, tell the user that governance could not be completed.
```

## 3. Add Route Through CostPilot

1. Inside the topic, select **Add Action**.
2. Choose **Apex**.
3. Select **Route Through CostPilot**.
4. Configure these inputs.

| Input | Salesforce value | Required |
| --- | --- | --- |
| User Prompt | Complete user request | Yes |
| Record ID | Current record ID when available | No |
| Object Name | Current Salesforce object API name | No |
| Agent Name | Current agent label or API name | Recommended |
| Department | Sales or the owning department | Recommended |
| Business Unit | Owning business unit | No |
| Context JSON | Additional grounded context | No |

Make these outputs available to the topic:

| Output | Use |
| --- | --- |
| CostPilot Response | Return this governed answer to the user. |
| Model Tier | Model tier selected by CostPilot. |
| Risk Level | Governance classification. |
| Outcome | Routing result. |
| Blocked | Stop instead of creating an alternative response. |
| Audit ID | Evidence that CostPilot completed the request. |

## 4. Configure the execution user

The Agentforce execution user is different from the administrator installing
the package. Assign both of these packaged permission sets to that execution
user:

- **CostPilot Connector User**
- **CostPilot Agent Credential User**

The second permission set provides read-only access to the execution user's
assigned external credential. It does not grant credential administration.

## 5. Publish and activate

1. Save the topic and resolve all Agentforce validation errors.
2. Publish the agent. Salesforce creates a new numbered version.
3. Open the agent version list.
4. Confirm that the newly published version is active.
5. If an older version remains active, activate the new version.

> **Important:** Publishing does not guarantee that the new version is active.
> Always confirm the active version before testing.

## 6. Run the verification challenges

### Challenge A: Standalone request

In Agentforce Preview, ask:

> Draft a two-sentence follow-up email after a successful sales discovery call.

### Challenge B: Record-based request

Open an Account or Opportunity and ask:

> Summarize this record and recommend the next sales action.

Return to **CostPilot Setup → Step 4** and select **Run verification**.

## Completion standard

CostPilot Setup must confirm all of the following before activation:

- The request originated from Salesforce Agentforce.
- Route Through CostPilot executed successfully.
- CostPilot selected a model tier.
- A governed answer returned to Salesforce.
- A new CostPilot audit ID exists.
- The record-based request is attributed to the correct Salesforce record.
- The agent did not generate an alternative answer outside CostPilot.

If verification does not pass, do not activate the connection. Review the
active agent version, execution-user permission sets, action mapping, and
credential access, then run the request again.
