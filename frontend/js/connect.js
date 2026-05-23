/**
 * connect.js — FAGE Platform Connector UI
 *
 * 3-screen flow:
 *   1. Pick platform (Salesforce, ServiceNow, HubSpot, Dynamics, Zendesk, Other)
 *   2. Configure agent (name, department, object, collision policy)
 *   3. Get webhook URL + platform-specific setup guide
 */

let selectedPlatform  = null;
let selectedObject    = null;
let selectedPolicy    = "lock";
let registeredAgentId = null;

// ── Platform metadata ─────────────────────────────────────────────────────────
// Each platform has:
//   oneWay  — the simple, no-code webhook path (data goes TO FAGE only)
//   biDir   — the full integration path (FAGE response writes BACK to the record)
//             biDir is null for "Other/Custom" since there's no universal code path

const PLATFORMS = {

  // ── Salesforce ──────────────────────────────────────────────────────────────
  salesforce: {
    label:   "Salesforce",
    objects: ["Case", "Lead", "Contact", "Account", "Opportunity"],
    oneWay: {
      cardLabel:   "Flow Builder",
      cardIcon:    "⚡",
      badge:       "Quick Setup · No Code",
      pros:        ["No code required", "Live in under 5 minutes"],
      cons:        ["One-way only — AI responses stay in your FAGE dashboard and are not written back to the Salesforce record"],
      toggleLabel: "Show Setup Steps",
      guide: `
        <div class="conn-guide-title">Salesforce Flow Builder — One-Way Setup</div>
        <ol class="conn-guide-steps">
          <li>Go to <strong>Setup → Flows → New Flow</strong> and choose <em>Record-Triggered Flow</em></li>
          <li>Set the trigger object (e.g. <strong>Case</strong>) and trigger condition: <em>A record is created or updated</em></li>
          <li>Add an <strong>Action</strong> element → <em>HTTP Callout</em></li>
          <li>Paste your FAGE Webhook URL as the endpoint. Method: <strong>POST</strong>, Content-Type: <strong>application/json</strong></li>
          <li>Paste the Request Body above, replacing the placeholder with your Flow variable (e.g. <code>{!$Record.Description}</code>)</li>
          <li>Save and <strong>Activate</strong> the Flow</li>
        </ol>
        <div class="conn-guide-note">💡 AI responses are visible in your FAGE dashboard under each department. To write responses back to the record, use the <strong>Apex Class</strong> path instead.</div>
      `,
    },
    biDir: {
      cardLabel:   "Apex Class",
      cardIcon:    "🔁",
      badge:       "Full Integration · Bidirectional",
      pros:        ["AI response written directly to the Salesforce record", "Model used, routing decision, and cost tracked inline", "Works on any Salesforce edition — no extra license needed"],
      tierNote:    null,
      toggleLabel: "Show Apex Class",
      hasCode:     true,
      codeLabel:   "FAGECallout.cls — paste into Setup → Apex Classes → New",
      buildCode:   (obj, dept, agent, url) => `public class FAGECallout {
    @InvocableMethod(label='Send to FAGE')
    public static void sendToFAGE(List<FAGERequest> requests) {
        if (System.isFuture() || System.isBatch()) return;
        sendAsync(requests[0].recordId, requests[0].recordText,
                  requests[0].department, requests[0].agentName);
    }

    @future(callout=true)
    public static void sendAsync(String recordId, String recordText,
                                 String department, String agentName) {
        HttpRequest httpReq = new HttpRequest();
        httpReq.setEndpoint('${url}/api/route');
        httpReq.setMethod('POST');
        httpReq.setHeader('Content-Type', 'application/json');
        Map<String, Object> body = new Map<String, Object>{
            'text'            => recordText,
            'department'      => department,
            'auto_prune'      => true,
            'agent_name'      => agentName,
            'source_platform' => 'Salesforce'
        };
        httpReq.setBody(JSON.serialize(body));
        httpReq.setTimeout(30000);

        HttpResponse res = new Http().send(httpReq);
        if (res.getStatusCode() == 200 && recordId != null) {
            Map<String, Object> r =
                (Map<String, Object>) JSON.deserializeUntyped(res.getBody());
            ${obj} record = new ${obj}(Id = recordId);
            record.FAGE_AI_Response__c      = (String)  r.get('simulated_response');
            record.FAGE_Model_Used__c       = (String)  r.get('model_name');
            record.FAGE_Routing_Decision__c = (String)  r.get('routing_decision');
            record.FAGE_Cost_USD__c         = Decimal.valueOf(
                                               String.valueOf(r.get('cost_usd')));
            update record;
        }
    }

    public class FAGERequest {
        @InvocableVariable(required=true  label='Record ID')   public String recordId;
        @InvocableVariable(required=true  label='Record Text') public String recordText;
        @InvocableVariable(required=true  label='Department')  public String department;
        @InvocableVariable(required=false label='Agent Name')  public String agentName;
    }
}`,
      fields: [
        { label: "FAGE AI Response",      api: "FAGE_AI_Response__c",      type: "Long Text Area", setting: "32,768 chars" },
        { label: "FAGE Model Used",       api: "FAGE_Model_Used__c",       type: "Text",           setting: "255 chars" },
        { label: "FAGE Routing Decision", api: "FAGE_Routing_Decision__c", type: "Text",           setting: "50 chars" },
        { label: "FAGE Cost USD",         api: "FAGE_Cost_USD__c",         type: "Currency",       setting: "Length 12, Decimals 6" },
      ],
      buildSetupSteps: (obj, field, dept, agent) => `
        <div class="conn-guide-title">Wire the Apex Class to a Flow</div>
        <ol class="conn-guide-steps">
          <li>Go to <strong>Setup → Flows → New Flow</strong><br/>
              Type: <em>Record-Triggered Flow</em> · Object: <strong>${obj}</strong> · Trigger: <em>A record is created or updated</em></li>
          <li>Add an <strong>Action</strong> element · Category: <em>Apex</em> · Action: <em>Send to FAGE</em></li>
          <li>Set input values:<br/>
              <code>Record ID</code> → <em>${obj} ID</em><br/>
              <code>Record Text</code> → <em>${obj} · ${field}</em><br/>
              <code>Department</code> → <em>${dept}</em><br/>
              <code>Agent Name</code> → <em>${agent}</em></li>
          <li><strong>Save &amp; Activate</strong> — the next time a <strong>${obj}</strong> is saved, FAGE processes it and writes the response back automatically</li>
        </ol>
      `,
    },
  },

  // ── ServiceNow ──────────────────────────────────────────────────────────────
  servicenow: {
    label:   "ServiceNow",
    objects: ["incident", "change_request", "sc_request", "problem", "task"],
    oneWay: {
      cardLabel:   "Flow Designer",
      cardIcon:    "⚡",
      badge:       "Quick Setup · No Code",
      pros:        ["No code required", "Built-in REST Step in Flow Designer"],
      cons:        ["One-way only — AI responses appear in FAGE dashboard but are not written back to the ServiceNow record"],
      toggleLabel: "Show Setup Steps",
      guide: `
        <div class="conn-guide-title">ServiceNow Flow Designer — One-Way Setup</div>
        <ol class="conn-guide-steps">
          <li>Go to <strong>Flow Designer → New → Flow</strong></li>
          <li>Add a trigger: <em>Record · Created · Incident</em> (or your chosen table)</li>
          <li>Add a <strong>REST Step</strong> action</li>
          <li>Set the Base URL to your FAGE Webhook URL. Method: <strong>POST</strong></li>
          <li>In the Request Body, paste the JSON above — replace the text placeholder with <code>trigger.current.description</code></li>
          <li>Save and <strong>Activate</strong> the flow</li>
        </ol>
        <div class="conn-guide-note">💡 AI responses are visible in your FAGE dashboard. To write them back to the incident record automatically, use the <strong>Script Include</strong> path instead.</div>
      `,
    },
    biDir: {
      cardLabel:   "Script Include",
      cardIcon:    "🔁",
      badge:       "Full Integration · Bidirectional",
      pros:        ["AI response written directly to the ServiceNow record", "Works via Business Rule or Flow Designer action", "Available on all ServiceNow editions — no extra license needed"],
      tierNote:    null,
      toggleLabel: "Show Script Include",
      hasCode:     true,
      codeLabel:   "FAGECallout.js — create in System Definition → Script Includes",
      buildCode:   (obj, dept, agent, url) => `var FAGECallout = Class.create();
FAGECallout.prototype = {

    /**
     * Call FAGE and write the AI response back to a record.
     * @param {string} recordId   - sys_id of the record to update
     * @param {string} recordText - the field text to send (e.g. description)
     * @param {string} department - FAGE department name
     * @param {string} agentName  - registered agent name
     * @param {string} tableName  - ServiceNow table (e.g. 'incident')
     */
    call: function(recordId, recordText, department, agentName, tableName) {
        var rm = new sn_ws.RESTMessageV2();
        rm.setEndpoint('${url}/api/route');
        rm.setHttpMethod('POST');
        rm.setRequestHeader('Content-Type', 'application/json');
        rm.setRequestBody(JSON.stringify({
            text:            recordText,
            department:      department,
            auto_prune:      true,
            agent_name:      agentName,
            source_platform: 'ServiceNow'
        }));
        rm.setHttpTimeout(30000);

        var response   = rm.execute();
        var statusCode = response.getStatusCode();

        if (statusCode == 200 && recordId) {
            var body = JSON.parse(response.getBody());
            var gr   = new GlideRecord(tableName || '${obj}');
            if (gr.get(recordId)) {
                gr.u_fage_ai_response      = body.simulated_response;
                gr.u_fage_model_used       = body.model_name;
                gr.u_fage_routing_decision = body.routing_decision;
                gr.u_fage_cost_usd         = body.cost_usd;
                gr.update();
            }
        }
        return response;
    },

    type: 'FAGECallout'
};`,
      fields: [
        { label: "FAGE AI Response",      api: "u_fage_ai_response",      type: "String (4000)",  setting: "Add to your table" },
        { label: "FAGE Model Used",       api: "u_fage_model_used",       type: "String (100)",   setting: "Add to your table" },
        { label: "FAGE Routing Decision", api: "u_fage_routing_decision", type: "String (50)",    setting: "Add to your table" },
        { label: "FAGE Cost USD",         api: "u_fage_cost_usd",         type: "Decimal",        setting: "Add to your table" },
      ],
      buildSetupSteps: (obj, field, dept, agent) => `
        <div class="conn-guide-title">Trigger the Script Include via a Business Rule</div>
        <ol class="conn-guide-steps">
          <li>Go to <strong>System Definition → Business Rules → New</strong></li>
          <li>Table: <strong>${obj}</strong> · When: <em>after</em> · Insert: ✓ · Update: ✓</li>
          <li>Check <strong>Advanced</strong> and add this to the Script field:
            <br/><code>(new FAGECallout()).call(current.sys_id, current.${field}, '${dept}', '${agent}', '${obj}');</code>
          </li>
          <li><strong>Submit</strong> — the next time a <strong>${obj}</strong> record is saved, FAGE processes it and writes the response back</li>
        </ol>
        <div class="conn-guide-note">💡 Alternatively, call the Script Include from a <strong>Flow Designer</strong> action for a no-code trigger with the same bidirectional write-back.</div>
      `,
    },
  },

  // ── HubSpot ──────────────────────────────────────────────────────────────────
  hubspot: {
    label:   "HubSpot",
    objects: ["Tickets", "Contacts", "Deals", "Companies"],
    oneWay: {
      cardLabel:   "Workflows Webhook",
      cardIcon:    "⚡",
      badge:       "Quick Setup · No Code",
      pros:        ["No code required", "Available on all HubSpot plans", "Live in minutes"],
      cons:        ["One-way only — AI responses appear in FAGE dashboard but are not written back to the HubSpot record"],
      toggleLabel: "Show Setup Steps",
      guide: `
        <div class="conn-guide-title">HubSpot Workflows — One-Way Setup</div>
        <ol class="conn-guide-steps">
          <li>Go to <strong>Automation → Workflows → Create Workflow</strong></li>
          <li>Choose <em>Ticket-based</em> (or Contact / Deal depending on your object)</li>
          <li>Set trigger: <em>Ticket is created</em></li>
          <li>Add action: <strong>Send a webhook</strong></li>
          <li>Paste your FAGE Webhook URL. Method: <strong>POST</strong></li>
          <li>In the Request Body, paste the JSON above and map ticket properties to the fields</li>
          <li>Save and <strong>Turn On</strong> the workflow</li>
        </ol>
        <div class="conn-guide-note">💡 AI responses are visible in your FAGE dashboard. To write them back to the HubSpot record, upgrade to <strong>Operations Hub Professional</strong> and use the Custom Coded Action path.</div>
      `,
    },
    biDir: {
      cardLabel:   "Custom Coded Action",
      cardIcon:    "🔁",
      badge:       "Full Integration · Bidirectional",
      pros:        ["AI response written directly back to the HubSpot record", "Runs inside a Workflow — no external server needed", "Output fields can update any HubSpot property"],
      tierNote:    "Requires Operations Hub Professional or Enterprise. This is a HubSpot licensing requirement — not a FAGE limitation.",
      toggleLabel: "Show Coded Action",
      hasCode:     true,
      codeLabel:   "Add inside Workflows → Custom Coded Action (Node.js)",
      buildCode:   (obj, dept, agent, url) => `// HubSpot Custom Coded Action — Node.js
// Input fields to configure in HubSpot:
//   description  (string) — map to your ticket/contact description property
//   department   (string) — hardcode or pass as a workflow variable
//   agent_name   (string) — optional, defaults to '${agent}'

const axios = require('axios');

exports.main = async (event, callback) => {
  try {
    const res = await axios.post('${url}/api/route', {
      text:            event.inputFields['description'],
      department:      event.inputFields['department'] || '${dept}',
      auto_prune:      true,
      agent_name:      event.inputFields['agent_name'] || '${agent}',
      source_platform: 'HubSpot'
    });

    // These output fields feed the next workflow step
    // Add a "Set property value" action after this to write them to the record
    callback({
      outputFields: {
        fage_ai_response:      res.data.simulated_response,
        fage_model_used:       res.data.model_name,
        fage_routing_decision: res.data.routing_decision,
        fage_cost_usd:         String(res.data.cost_usd)
      }
    });

  } catch (err) {
    callback({
      outputFields: { fage_ai_response: 'FAGE Error: ' + err.message }
    });
  }
};`,
      fields: [
        { label: "FAGE AI Response",      api: "fage_ai_response",      type: "Single-line text", setting: "Create in Properties" },
        { label: "FAGE Model Used",       api: "fage_model_used",       type: "Single-line text", setting: "Create in Properties" },
        { label: "FAGE Routing Decision", api: "fage_routing_decision", type: "Single-line text", setting: "Create in Properties" },
        { label: "FAGE Cost USD",         api: "fage_cost_usd",         type: "Single-line text", setting: "Create in Properties" },
      ],
      buildSetupSteps: (obj, field, dept, agent) => `
        <div class="conn-guide-title">Wire the Coded Action in a Workflow</div>
        <ol class="conn-guide-steps">
          <li>In your Workflow, add a <strong>Custom Coded Action</strong> step after your trigger</li>
          <li>Paste the code above into the code editor</li>
          <li>Define input fields: <code>description</code> → map to your <strong>${obj} · ${field}</strong> property</li>
          <li>After the coded action, add a <strong>Set property value</strong> action for each output field:<br/>
              Map <code>fage_ai_response</code> → your FAGE AI Response property, etc.</li>
          <li><strong>Save and Turn On</strong> the workflow</li>
        </ol>
        <div class="conn-guide-note">💡 The output fields from the coded action are available as workflow tokens — no separate API call needed to write back to the record.</div>
      `,
    },
  },

  // ── Dynamics 365 ────────────────────────────────────────────────────────────
  dynamics: {
    label:   "Dynamics 365",
    objects: ["Cases", "Leads", "Opportunities", "Contacts", "Accounts"],
    oneWay: {
      cardLabel:   "Power Automate (Send Only)",
      cardIcon:    "⚡",
      badge:       "Quick Setup · No Code",
      pros:        ["No code required", "Available with any Power Automate license", "Visual flow builder"],
      cons:        ["One-way only — AI responses appear in FAGE dashboard but are not written back to the Dynamics record"],
      toggleLabel: "Show Setup Steps",
      guide: `
        <div class="conn-guide-title">Power Automate — One-Way Setup</div>
        <ol class="conn-guide-steps">
          <li>Go to <strong>Power Automate → Create → Automated Cloud Flow</strong></li>
          <li>Trigger: <em>When a row is added, modified or deleted (Dataverse)</em> → Table: <strong>Cases</strong></li>
          <li>Add step: <strong>HTTP</strong></li>
          <li>Method: <strong>POST</strong> · URI: paste your FAGE Webhook URL</li>
          <li>Headers: <code>Content-Type: application/json</code></li>
          <li>Body: paste the Request Body above, mapping Dynamics field values</li>
          <li>Save and <strong>Turn On</strong> the flow</li>
        </ol>
        <div class="conn-guide-note">💡 AI responses go to your FAGE dashboard. To have them write back to the record automatically, use the <strong>Full Power Automate</strong> path — no extra license required.</div>
      `,
    },
    biDir: {
      cardLabel:   "Power Automate (Full)",
      cardIcon:    "🔁",
      badge:       "Full Integration · Bidirectional · No Code",
      pros:        ["No code required — entirely visual in Power Automate", "AI response written back to the Dynamics record automatically", "Available on any Power Automate plan — no extra license needed"],
      tierNote:    null,
      toggleLabel: "Show Full Flow Steps",
      hasCode:     false,
      buildSetupSteps: (obj, field, dept, agent) => `
        <div class="conn-guide-title">Power Automate — Full Bidirectional Flow</div>
        <div class="conn-guide-note" style="margin-bottom:16px">
          ✓ Dynamics 365 is the only platform on this list where the full bidirectional integration requires <strong>no code at all</strong> — Power Automate handles everything visually.
        </div>
        <ol class="conn-guide-steps">
          <li><strong>Trigger:</strong> When a row is added, modified or deleted (Dataverse) → Table: <strong>${obj}</strong></li>
          <li><strong>Step 1 — Call FAGE:</strong> Add an <strong>HTTP</strong> action<br/>
              Method: POST · URI: your FAGE Webhook URL<br/>
              Body: paste the Request Body JSON, mapping <code>text</code> to <em>${obj} · ${field}</em></li>
          <li><strong>Step 2 — Parse the response:</strong> Add a <strong>Parse JSON</strong> action<br/>
              Content: <em>Body</em> from the previous HTTP step<br/>
              Use this schema:
              <pre class="conn-inline-code">{"simulated_response":"","model_name":"","routing_decision":"","cost_usd":0}</pre>
          </li>
          <li><strong>Step 3 — Write back:</strong> Add <strong>Update a row (Dataverse)</strong><br/>
              Table: <strong>${obj}</strong> · Row ID: trigger row ID<br/>
              Map each parsed field to your custom Dataverse columns</li>
          <li><strong>Save and Turn On</strong></li>
        </ol>
      `,
      fields: [
        { label: "FAGE AI Response",      api: "fage_ai_response",      type: "Multiple Lines of Text", setting: "Add to table" },
        { label: "FAGE Model Used",       api: "fage_model_used",       type: "Single Line of Text",    setting: "Add to table" },
        { label: "FAGE Routing Decision", api: "fage_routing_decision", type: "Single Line of Text",    setting: "Add to table" },
        { label: "FAGE Cost USD",         api: "fage_cost_usd",         type: "Currency",               setting: "Add to table" },
      ],
    },
  },

  // ── Zendesk ──────────────────────────────────────────────────────────────────
  zendesk: {
    label:   "Zendesk",
    objects: ["Tickets", "Users", "Organizations"],
    oneWay: {
      cardLabel:   "Triggers + Webhooks",
      cardIcon:    "⚡",
      badge:       "Quick Setup · No Code",
      pros:        ["No code required", "Available on all Zendesk plans", "Live in minutes"],
      cons:        ["One-way only — AI responses appear in FAGE dashboard but are not written back to the Zendesk ticket"],
      toggleLabel: "Show Setup Steps",
      guide: `
        <div class="conn-guide-title">Zendesk — One-Way Setup (Triggers + Webhooks)</div>
        <ol class="conn-guide-steps">
          <li>Go to <strong>Admin → Objects and Rules → Webhooks → Create Webhook</strong></li>
          <li>Paste your FAGE Webhook URL as the Endpoint URL. Method: <strong>POST</strong> · Format: <strong>JSON</strong></li>
          <li>Go to <strong>Triggers → Add Trigger</strong></li>
          <li>Condition: <em>Ticket is created</em></li>
          <li>Action: <em>Notify webhook</em> → select your FAGE webhook</li>
          <li>Paste the Request Body above in the JSON body field. Map <code>{{ticket.description}}</code> to the text field</li>
          <li>Save the trigger</li>
        </ol>
        <div class="conn-guide-note">💡 AI responses are visible in your FAGE dashboard. To write them back to the ticket automatically, use the <strong>Sunshine Function</strong> path — requires Zendesk Suite or Sunshine Platform.</div>
      `,
    },
    biDir: {
      cardLabel:   "Sunshine Function",
      cardIcon:    "🔁",
      badge:       "Full Integration · Bidirectional",
      pros:        ["AI response written directly back to the Zendesk ticket", "Serverless — no server to manage", "Runs inside Zendesk's own infrastructure"],
      tierNote:    "Requires Zendesk Suite Professional (or higher) or the Sunshine Platform add-on. This is a Zendesk licensing requirement — not a FAGE limitation.",
      toggleLabel: "Show Sunshine Function",
      hasCode:     true,
      codeLabel:   "Deploy via Zendesk CLI (zaf app:create) or Developer Tools",
      buildCode:   (obj, dept, agent, url) => `// Zendesk Sunshine Platform Function
// Deploy with: zaf app:create → choose "Serverless Function"
// Trigger this function from a Zendesk Trigger or Automation

const fetch = require('node-fetch');

module.exports = async (event, callback) => {
  try {
    const res = await fetch('${url}/api/route', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text:            event.ticket.comment.body || event.ticket.description,
        department:      '${dept}',
        auto_prune:      true,
        agent_name:      '${agent}',
        source_platform: 'Zendesk'
      })
    });

    const data = await res.json();

    // Write AI response back to the ticket using Zendesk's API
    // Replace the field IDs below with your actual custom field IDs
    // (Admin → Objects and Rules → Tickets → Fields)
    callback(null, {
      ticket: {
        custom_fields: [
          { id: 'FAGE_RESPONSE_FIELD_ID',  value: data.simulated_response },
          { id: 'FAGE_MODEL_FIELD_ID',     value: data.model_name },
          { id: 'FAGE_ROUTING_FIELD_ID',   value: data.routing_decision },
          { id: 'FAGE_COST_FIELD_ID',      value: String(data.cost_usd) }
        ]
      }
    });

  } catch (err) {
    callback(err);
  }
};`,
      fields: [
        { label: "FAGE AI Response",      api: "FAGE_RESPONSE_FIELD_ID",  type: "Multi-line text", setting: "Note the numeric field ID" },
        { label: "FAGE Model Used",       api: "FAGE_MODEL_FIELD_ID",     type: "Text",            setting: "Note the numeric field ID" },
        { label: "FAGE Routing Decision", api: "FAGE_ROUTING_FIELD_ID",   type: "Text",            setting: "Note the numeric field ID" },
        { label: "FAGE Cost USD",         api: "FAGE_COST_FIELD_ID",      type: "Decimal",         setting: "Note the numeric field ID" },
      ],
      buildSetupSteps: (obj, field, dept, agent) => `
        <div class="conn-guide-title">Deploy the Sunshine Function</div>
        <ol class="conn-guide-steps">
          <li>Install the Zendesk CLI: <code>npm install -g @zendesk/zaf-cli</code></li>
          <li>Run <code>zaf app:create</code> and choose <em>Serverless Function</em>. Paste the code above.</li>
          <li>In your Zendesk Admin, go to <strong>Objects and Rules → Tickets → Fields</strong> and create the four custom fields. Note each numeric field ID and replace the placeholder IDs in the code.</li>
          <li>Deploy the function: <code>zaf app:upload</code></li>
          <li>Create a <strong>Trigger</strong> (Ticket created) with the action <em>Invoke Sunshine Function → your FAGE function</em></li>
          <li>Save and test by creating a ticket</li>
        </ol>
        <div class="conn-guide-note">💡 If you don't have Zendesk Suite Pro, you can achieve the same write-back using a middleware service (Make, Zapier, or n8n) between the one-way webhook and HubSpot's API.</div>
      `,
    },
  },

  // ── Other / Custom ───────────────────────────────────────────────────────────
  other: {
    label:   "Custom / Other",
    objects: ["Records", "Events", "Messages", "Documents", "Other"],
    oneWay: {
      cardLabel:   "HTTP Webhook",
      cardIcon:    "⚡",
      badge:       "Universal · Works Anywhere",
      pros:        ["Any platform that can send an HTTP POST can connect to FAGE", "No SDK or library required"],
      cons:        ["One-way by default — write-back depends on your platform's capabilities"],
      toggleLabel: "Show Setup Steps",
      guide: `
        <div class="conn-guide-title">Generic Webhook Setup</div>
        <ol class="conn-guide-steps">
          <li>In your platform's automation or integration builder, find the <strong>HTTP / Webhook</strong> action</li>
          <li>Set the URL to your FAGE Webhook URL. Method: <strong>POST</strong> · Content-Type: <strong>application/json</strong></li>
          <li>Paste the Request Body above, replacing placeholders with your platform's field variables</li>
          <li>To write the FAGE response back to your record, add a second step that reads the HTTP response body and maps <code>simulated_response</code>, <code>model_name</code>, <code>routing_decision</code>, and <code>cost_usd</code> to your record fields</li>
        </ol>
        <div class="conn-guide-note">💡 Need a write-back but your platform doesn't support reading HTTP responses? Use a middleware tool like Make, Zapier, or n8n to bridge the gap.</div>
      `,
    },
    biDir: null, // No universal code path — each custom platform is different
  },

};

// ── Step navigation ───────────────────────────────────────────────────────────

function goStep(n) {
  if (n === 2 && !validateStep1()) return;

  document.querySelectorAll(".ob-screen").forEach(s => s.classList.remove("active"));
  document.querySelectorAll(".ob-step").forEach(s => s.classList.remove("active", "done"));

  document.getElementById(`screen-${n}`).classList.add("active");

  for (let i = 1; i <= 3; i++) {
    const el = document.getElementById(`prog-${i}`);
    if (i < n)       el.classList.add("done");
    else if (i === n) el.classList.add("active");
  }

  if (n === 2) initConfigScreen();
}

// ── Step 1: Platform selection ────────────────────────────────────────────────

function selectPlatform(id) {
  selectedPlatform = id;
  document.querySelectorAll(".conn-platform").forEach(el => el.classList.remove("selected"));
  document.getElementById(`plat-${id}`).classList.add("selected");
  document.getElementById("error-1").textContent = "";
}

function validateStep1() {
  if (!selectedPlatform) {
    document.getElementById("error-1").textContent = "Please select a platform to continue.";
    return false;
  }
  return true;
}

// ── Step 2: Agent configuration ───────────────────────────────────────────────

async function initConfigScreen() {
  const plat = PLATFORMS[selectedPlatform];

  // Set title
  document.getElementById("configTitle").textContent = `Configure Your ${plat.label} Agent`;

  // Auto-fill agent name
  const names = {
    salesforce: "SF-CaseBot",
    servicenow: "SN-IncidentBot",
    hubspot:    "HS-TicketBot",
    dynamics:   "D365-CaseBot",
    zendesk:    "ZD-TicketBot",
    other:      "CustomBot-1",
  };
  document.getElementById("agentName").value = names[selectedPlatform] || "";

  // Load departments
  await loadDeptDropdown();

  // Render object buttons + custom input
  renderObjectGrid(plat.objects);
}

function renderObjectGrid(objects) {
  const grid = document.getElementById("objectGrid");
  grid.innerHTML = objects.map(obj => `
    <button class="conn-object-btn" onclick="selectObject('${obj}', this)">${obj}</button>
  `).join("") + `
    <button class="conn-object-btn" onclick="showCustomObject(this)">+ Custom</button>
  `;
  // Select first object by default
  selectedObject = objects[0];
  grid.querySelector(".conn-object-btn").classList.add("selected");
}

function showCustomObject(btn) {
  document.querySelectorAll(".conn-object-btn").forEach(b => b.classList.remove("selected"));
  btn.classList.add("selected");
  selectedObject = null;

  // Show custom input if not already visible
  if (!document.getElementById("customObjectInput")) {
    const wrap = document.createElement("div");
    wrap.style.marginTop = "10px";
    wrap.innerHTML = `
      <input id="customObjectInput" type="text" class="ob-input"
             placeholder="e.g. WorkOrders, ServiceRequests, CustomObject__c"
             oninput="selectedObject = this.value.trim()" />
      <span class="conn-field-hint" style="display:block; margin-top:4px">
        Enter the exact object name from your platform.
      </span>
    `;
    document.getElementById("objectGrid").after(wrap);
  }
  document.getElementById("customObjectInput").focus();
}

function selectObject(obj, el) {
  selectedObject = obj;
  document.querySelectorAll(".conn-object-btn").forEach(b => b.classList.remove("selected"));
  el.classList.add("selected");
}

function selectPolicy(policy) {
  selectedPolicy = policy;
  document.querySelectorAll(".conn-policy").forEach(el => el.classList.remove("selected"));
  document.getElementById(`policy-${policy}`).classList.add("selected");
}

// ── Department management ─────────────────────────────────────────────────────

async function loadDeptDropdown(selectValue) {
  const select = document.getElementById("agentDept");
  select.innerHTML = `<option value="">Select a department...</option>`;
  try {
    const budgets = await apiGet("/api/budget");
    if (budgets && budgets.length) {
      budgets.forEach(b => {
        const opt = document.createElement("option");
        opt.value       = b.department;
        opt.textContent = b.department;
        select.appendChild(opt);
      });
    } else {
      throw new Error("empty");
    }
  } catch {
    ["Support","Sales","Marketing","Operations"].forEach(d => {
      const opt = document.createElement("option");
      opt.value = d; opt.textContent = d;
      select.appendChild(opt);
    });
  }
  // Add "new department" option at the bottom
  const addOpt = document.createElement("option");
  addOpt.value       = "__new__";
  addOpt.textContent = "+ Add New Department";
  select.appendChild(addOpt);

  // Select the right value
  if (selectValue) {
    select.value = selectValue;
  } else if (select.options.length > 1) {
    select.selectedIndex = 1;
  }
}

function handleDeptChange(select) {
  const form = document.getElementById("newDeptForm");
  if (select.value === "__new__") {
    form.style.display = "block";
    document.getElementById("newDeptName").focus();
  } else {
    form.style.display = "none";
  }
}

async function createDepartment() {
  const name  = document.getElementById("newDeptName").value.trim();
  const capRaw = document.getElementById("newDeptCap").value;
  const cap    = capRaw === "" ? 0 : parseFloat(capRaw) || 0;
  const err   = document.getElementById("newDeptError");

  if (!name) {
    err.textContent = "Please enter a department name.";
    return;
  }

  err.textContent = "Creating...";
  err.style.color = "var(--text-muted)";

  try {
    await apiPost(`/api/budget/${encodeURIComponent(name)}/cap`, { new_cap_usd: cap });
    // Reload dropdown and select the new department
    await loadDeptDropdown(name);
    document.getElementById("newDeptForm").style.display = "none";
    document.getElementById("newDeptName").value = "";
    document.getElementById("newDeptCap").value  = "";
    err.textContent = "";
  } catch (e) {
    err.textContent = "Error: " + e.message;
    err.style.color = "var(--accent-red)";
  }
}

// ── Step 2 → Register agent + go to step 3 ───────────────────────────────────

async function registerAndConnect() {
  const name  = document.getElementById("agentName").value.trim();
  const dept  = document.getElementById("agentDept").value;
  const err   = document.getElementById("error-2");

  // Capture custom object value at submit time in case oninput was missed
  const customInput = document.getElementById("customObjectInput");
  if (customInput && customInput.value.trim()) {
    selectedObject = customInput.value.trim();
  }

  if (!name) {
    err.textContent = "Please enter an agent name.";
    return;
  }
  if (!dept || dept === "__new__") {
    err.textContent = "Please select or create a department first.";
    return;
  }
  if (!selectedObject) {
    err.textContent = "Please select or enter what this agent monitors.";
    return;
  }

  err.textContent = "Registering agent...";
  err.style.color = "var(--text-muted)";

  try {
    const agent = await apiPost("/api/agents/register", {
      name:             name,
      department:       dept,
      permissions:      "read,write",
      target_table:     selectedObject ? selectedObject.toLowerCase() : "tickets",
      collision_policy: selectedPolicy,
    });

    registeredAgentId = agent.id;
    buildResultScreen(agent);
    goStep(3);

  } catch (err2) {
    if (err2.message.includes("409")) {
      err.textContent = `An agent named "${name}" already exists. Change the Agent Name above and try again.`;
    } else {
      err.textContent = "Error: " + err2.message;
    }
    err.style.color = "var(--accent-red)";
    document.getElementById("agentName").focus();
    document.getElementById("agentName").select();
  }
}

// ── Step 3: Build result screen ───────────────────────────────────────────────

let _currentMethod = null;

function buildResultScreen(agent) {
  const url  = window.location.origin;
  const dept = agent.department;
  const plat = PLATFORMS[selectedPlatform];

  document.getElementById("finalAgentName").textContent = agent.name;
  document.getElementById("webhookUrl").textContent = url + "/api/route";

  const body = JSON.stringify({
    text:            "{Your " + (selectedObject || "record") + " description field}",
    department:      dept,
    auto_prune:      true,
    agent_id:        agent.id,
    source_platform: plat ? plat.label : "Custom",
  }, null, 2);
  document.getElementById("requestBody").textContent = body;

  const chooser = document.getElementById("integrationChooser");
  const notice  = document.getElementById("onewayNotice");
  const guideEl = document.getElementById("platformGuide");

  if (plat && plat.biDir) {
    _currentMethod = null;
    document.getElementById("methodContent").innerHTML = "";
    document.getElementById("methodGrid").innerHTML = renderMethodCards(plat);
    chooser.style.display = "block";
    notice.style.display  = "none";
    guideEl.innerHTML     = "";
  } else {
    chooser.style.display = "none";
    notice.style.display  = plat ? "block" : "none";
    guideEl.innerHTML     = plat ? plat.oneWay.guide : "";
  }
}

function renderMethodCards(plat) {
  const ow = plat.oneWay;
  const bd = plat.biDir;

  const owPros = ow.pros.map(function(p) { return '<li class="conn-pro">' + p + "</li>"; }).join("");
  const owCons = ow.cons.map(function(c) { return '<li class="conn-con">' + c + "</li>"; }).join("");
  const bdPros = bd.pros.map(function(p) { return '<li class="conn-pro">' + p + "</li>"; }).join("");
  const tierChip = bd.tierNote
    ? '<div class="conn-tier-note">&#9888; ' + bd.tierNote + "</div>"
    : "";

  return (
    '<div class="conn-method-card" id="method-card-oneway" onclick="toggleMethod(\'oneway\')">' +
      '<div class="conn-method-icon">' + ow.cardIcon + "</div>" +
      '<div class="conn-method-name">' + ow.cardLabel + "</div>" +
      '<div class="conn-method-badge">' + ow.badge + "</div>" +
      '<ul class="conn-method-list">' + owPros + owCons + "</ul>" +
      '<div class="conn-method-toggle-label" id="oneway-toggle-label">' + ow.toggleLabel + " &#9660;</div>" +
    "</div>" +
    '<div class="conn-method-card" id="method-card-bidir" onclick="toggleMethod(\'bidir\')">' +
      '<div class="conn-method-icon">' + bd.cardIcon + "</div>" +
      '<div class="conn-method-name">' + bd.cardLabel + "</div>" +
      '<div class="conn-method-badge conn-method-badge-green">' + bd.badge + "</div>" +
      '<ul class="conn-method-list">' + bdPros + "</ul>" +
      tierChip +
      '<div class="conn-method-toggle-label" id="bidir-toggle-label">' + bd.toggleLabel + " &#9660;</div>" +
    "</div>"
  );
}

// ── Integration method toggle ─────────────────────────────────────────────────

function toggleMethod(method) {
  var content    = document.getElementById("methodContent");
  var onewayCard = document.getElementById("method-card-oneway");
  var bidirCard  = document.getElementById("method-card-bidir");
  var plat       = PLATFORMS[selectedPlatform];
  var ow         = plat.oneWay;
  var bd         = plat.biDir;

  if (_currentMethod === method) {
    _currentMethod = null;
    onewayCard.classList.remove("active");
    bidirCard.classList.remove("active");
    document.getElementById("oneway-toggle-label").textContent = ow.toggleLabel + " \u25BC";
    document.getElementById("bidir-toggle-label").textContent  = bd.toggleLabel + " \u25BC";
    content.innerHTML = "";
    return;
  }

  _currentMethod = method;
  onewayCard.classList.toggle("active", method === "oneway");
  bidirCard.classList.toggle("active",  method === "bidir");
  document.getElementById("oneway-toggle-label").textContent =
    method === "oneway" ? ow.toggleLabel + " \u25B2" : ow.toggleLabel + " \u25BC";
  document.getElementById("bidir-toggle-label").textContent =
    method === "bidir"  ? bd.toggleLabel + " \u25B2" : bd.toggleLabel + " \u25BC";

  content.innerHTML = method === "oneway"
    ? buildOneWayContent(plat)
    : buildBiDirContent(plat);

  content.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function buildOneWayContent(plat) {
  var bd = plat.biDir;
  var biDirHint = bd
    ? " For automatic write-back, use the <strong>" + bd.cardLabel + "</strong> option."
    : "";
  return (
    '<div class="conn-method-content">' +
      '<div class="conn-oneway-inline">' +
        "&#9888; <strong>One-way integration:</strong> Data goes <em>to FAGE</em> for governance, " +
        "routing, and budget tracking. AI responses are visible in your FAGE dashboard but are " +
        "<strong>not</strong> written back to the record automatically." + biDirHint +
      "</div>" +
      '<div class="conn-guide">' + plat.oneWay.guide + "</div>" +
    "</div>"
  );
}

function buildBiDirContent(plat) {
  var obj   = selectedObject || "record";
  var dept  = (document.getElementById("agentDept") || {}).value || "Support";
  var agent = ((document.getElementById("agentName") || {}).value || "").trim()
              || (selectedPlatform ? selectedPlatform.toUpperCase() + "-Bot" : "Bot");
  var field = "Description";
  var url   = window.location.origin;
  var bd    = plat.biDir;

  var tierBanner = bd.tierNote
    ? '<div class="conn-oneway-inline" style="border-color:rgba(255,200,60,0.35);background:rgba(255,200,60,0.05)">' +
      "&#9888; <strong>License note:</strong> " + bd.tierNote + "</div>"
    : "";

  var codeBlock = "";
  if (bd.hasCode) {
    var rawCode = bd.buildCode(obj, dept, agent, url);
    codeBlock = (
      '<div class="conn-url-card" style="margin-bottom:16px">' +
        '<div class="conn-url-label">' + bd.codeLabel + "</div>" +
        '<pre class="conn-code" id="bidir-code">' + escHtml(rawCode) + "</pre>" +
        '<button class="conn-copy-btn" style="margin-top:8px" onclick="copyBiDirCode()">Copy Code</button>' +
      "</div>"
    );
  } else if (bd.buildCode) {
    // no-code path but has description content
    codeBlock = bd.buildCode(obj, dept, agent, url);
  }

  var fieldsBlock = "";
  if (bd.fields) {
    var rows = bd.fields.map(function(f) {
      return (
        '<div class="conn-field-row">' +
          "<span>" + f.label + "</span>" +
          '<span class="conn-mono">' + f.api + "</span>" +
          "<span>" + f.type + "</span>" +
          "<span>" + f.setting + "</span>" +
        "</div>"
      );
    }).join("");
    fieldsBlock = (
      '<div class="conn-url-card" style="margin-bottom:16px">' +
        '<div class="conn-url-label">CUSTOM FIELDS TO ADD IN ' + plat.label.toUpperCase() + "</div>" +
        '<div class="conn-field-table">' +
          '<div class="conn-field-row conn-field-header">' +
            "<span>Field Label</span><span>API / Column Name</span><span>Type</span><span>Notes</span>" +
          "</div>" +
          rows +
        "</div>" +
      "</div>"
    );
  }

  var stepsBlock = '<div class="conn-guide">' + bd.buildSetupSteps(obj, field, dept, agent) + "</div>";

  return (
    '<div class="conn-method-content">' +
      tierBanner + codeBlock + fieldsBlock + stepsBlock +
    "</div>"
  );
}

function escHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function copyBiDirCode() {
  var el = document.getElementById("bidir-code");
  if (!el) return;
  navigator.clipboard.writeText(el.textContent).then(function() {
    var btn = event.target;
    btn.textContent = "Copied!";
    setTimeout(function() { btn.textContent = "Copy Code"; }, 2000);
  });
}

// ── Utility ───────────────────────────────────────────────────────────────────

function copyUrl() {
  var url = document.getElementById("webhookUrl").textContent;
  navigator.clipboard.writeText(url).then(function() {
    var btn = event.target;
    btn.textContent = "Copied!";
    setTimeout(function() { btn.textContent = "Copy"; }, 2000);
  });
}

function copyBody() {
  var body = document.getElementById("requestBody").textContent;
  navigator.clipboard.writeText(body).then(function() {
    var btn = event.target;
    btn.textContent = "Copied!";
    setTimeout(function() { btn.textContent = "Copy Body"; }, 2000);
  });
}

function connectAnother() {
  selectedPlatform  = null;
  selectedObject    = null;
  selectedPolicy    = "lock";
  registeredAgentId = null;
  document.querySelectorAll(".conn-platform").forEach(function(el) { el.classList.remove("selected"); });
  goStep(1);
}
