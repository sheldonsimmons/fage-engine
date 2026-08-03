# CostPilot Salesforce Connector

This package connects Agentforce and Salesforce Flow actions to CostPilot. It resolves or
creates a CostPilot project, runs the existing governance and model-routing
pipeline, attributes the resulting transaction to that project, and returns a
structured result to Agentforce.

The Salesforce DX package also creates:

- `CostPilot_Project__c`, the Salesforce project or matter record;
- `CostPilot_Project_Member__c`, the project-to-Salesforce-user membership;
- all project, attribution, and optional CostPilot response fields;
- tabs for projects and project members; and
- the `CostPilot_Connector_User` permission set; and
- a guided **CostPilot Setup** tab for OAuth, AI entry-point selection,
  relationship approval, verification, and activation.

## Proof scenario

From an Opportunity, ask Agentforce:

> Summarize this opportunity and recommend the next action.

The action sends the Opportunity and project context to CostPilot. Agentforce
receives:

- allow or block decision;
- CostPilot-selected model and tier;
- estimated request cost;
- project budget remaining;
- CostPilot transaction tracking ID.

The project and request then appear in CostPilot Work Attribution.

## 1. Install and assign access

Assign `CostPilot_Connector_User` to the Salesforce administrators and users
who will configure or invoke CostPilot. The package supplies the Named
Credential and External Credential definitions; no key belongs in Apex.

## 2. Connect and activate

1. Open **App Launcher → CostPilot Setup**.
2. Choose **Connect Salesforce** and approve Salesforce OAuth.
3. Select the Agentforce agents and Flows CostPilot should govern.
4. Approve the parent and related-record mapping used for attribution.
5. Run verification, then activate the connection.

The action resolves the CostPilot workspace from the connected Salesforce
organization. It does not contain a customer or demo workspace ID.

## 3. Validate package source during development

From this `agentforce-poc` directory, authenticate the sandbox and deploy:

```bash
sf org login web --instance-url https://test.salesforce.com --alias costpilot-sandbox
sf project deploy start --source-dir package-source --target-org costpilot-sandbox
sf apex run test --target-org costpilot-sandbox \
  --tests CostPilotAgentforceActionTest,CostPilotGatewayTest,CostPilotSetupControllerTest \
  --result-format human --wait 10
sf org assign permset --target-org costpilot-sandbox \
  --name CostPilot_Connector_User
```

That single `sf project deploy start` command creates the objects and fields as
well as deploying the Apex action. There is no manual Object Manager field
creation.

## 4. Create the first project and membership

1. Open **App Launcher → CostPilot Projects → New**.
2. Enter a project name, Project ID, status, department, and monthly AI budget.
3. Save the project.
4. Open **CostPilot Project Members → New**.
5. Select the project and Salesforce user, choose a role, leave Status as
   `Active`, and keep `Can Use AI` checked.

The membership object supports multiple Salesforce users per project. The Apex
action automatically sends `UserInfo.getUserId()` on every governed call so
CostPilot can attribute the transaction to the person who initiated it.

## 5. Add the action to Agentforce

1. Open **Setup → Agentforce Agents** and open the proof agent in Builder.
2. Create or open a topic named **CostPilot Project Governance**.
3. Add an action based on the Apex method
   **Govern AI Work with CostPilot**.
4. Allow Agentforce to populate these inputs:
   - Salesforce Record ID
   - Task Description
   - Project External ID
   - Project Name
   - Project Owner
   - Project Status
   - Monthly AI Budget
   - Department
   - Agent Name
5. Make the result fields available to the agent conversation.

Suggested topic instruction:

> Before completing project-related AI work, call Govern AI Work with
> CostPilot. Use the current Salesforce record ID. When a project code is
> available, use it as Project External ID; otherwise leave it blank and
> CostPilot will use the Salesforce record ID. Do not continue when Allowed is
> false. Tell the user the selected project, model, estimated cost, and
> CostPilot tracking ID.

## 6. Validate the proof

Use an Opportunity with a name, owner, active status, and optional project code.
In Agentforce Preview, request a summary. Confirm:

1. Agentforce calls the CostPilot action.
2. The response contains `allowed`, project, model, cost, and tracking ID.
3. The CostPilot Projects page contains the Salesforce project.
4. Its request count and spend increase.
5. The CostPilot audit log contains the governed routing decision.

Set Project Status to `On Hold` and repeat. CostPilot should return
`allowed = false` without creating an AI transaction.

## 7. Generate attributed Salesforce activity (internal demo source only)

The unpackaged `force-app` demo source includes a **CostPilot Load Generator**
Lightning tab for repeatable proof data. It is intentionally excluded from the
customer connector package. It uses Salesforce records and users rather than
anonymous browser traffic.

1. Deploy the CostPilot backend change that accepts `simulation_mode`.
2. Deploy the `force-app` Salesforce DX source and assign
   `CostPilot_Agentforce_User` to the test user.
3. Open **App Launcher → CostPilot Load Generator**.
4. Select an Account, Opportunity, or CostPilot Project.
5. Select the Salesforce user and enter the visible agent and department.
6. Choose a routing mix:
   - **Balanced** exercises Scout, Analyst, Advisor, and Strategist evenly.
   - **Natural** lets CostPilot classify the generated request normally.
   - **Economy** favors Scout and Analyst and avoids Strategist.
   - **Complex** alternates Advisor and Strategist.
7. Choose the operating mode:
   - **Simulation** permits 1–25 calls. Each call runs attribution,
     governance, pruning, routing, budget logic, and reporting, but does not
     purchase an LLM response.
   - **Live** permits 1–3 calls and uses the normal CostPilot-to-model path.
8. Start the run and watch completed, failed, and remaining call counts.

Every generated call sends the Salesforce record ID as the permanent work key,
the record name as its visible label, the selected Salesforce user, agent name,
department, source system, and source object. The resulting activity therefore
appears in CostPilot under the same project, person, agent, and model reporting
used for real Agentforce work.
