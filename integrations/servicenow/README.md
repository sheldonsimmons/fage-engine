# CostPilot for ServiceNow

CostPilot should be invoked only when ServiceNow requests AI work. Creating or
updating a ServiceNow record is not, by itself, an AI request and must not be
measured as one.

## Install the reusable Flow Designer action

1. Open **Flow Designer** and create an Action named
   **CostPilot Governed AI Request** in the Global application.
2. Add the inputs listed below.
3. Add a **Script** step, map the Action inputs to matching Script inputs, and
   paste `costpilot_governed_ai_request.js`.
4. Add the outputs listed below and map them from the Script step.
5. Publish the Action.

### Inputs

| Name | Type | Required |
| --- | --- | --- |
| `prompt` | String | Yes |
| `record_table` | String | Yes |
| `record_sys_id` | String | Yes |
| `task` | String | No |
| `agent_name` | String | No |
| `department` | String | No |

### Outputs

| Name | Type |
| --- | --- |
| `ai_response` | String |
| `model_tier` | String |
| `model_name` | String |
| `routing_decision` | String |
| `cost_usd` | Decimal |
| `input_tokens` | Integer |
| `output_tokens` | Integer |
| `tokens_pruned` | Integer |
| `work_item_id` | String |
| `work_item_name` | String |

Invoke this Action from a user-triggered Flow, UI Action, Virtual Agent, or
Now Assist workflow. Do not invoke it from a generic insert/update Business
Rule. Every invocation represents one governed and measured AI request.

The existing proof-of-concept Business Rule should be disabled after the new
Action has been tested successfully.
