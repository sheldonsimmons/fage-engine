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

const PLATFORMS = {
  salesforce: {
    label:   "Salesforce",
    objects: ["Cases", "Leads", "Contacts", "Accounts", "Opportunities"],
    guide:   `
      <div class="conn-guide-title">Salesforce Setup (Flow Builder)</div>
      <ol class="conn-guide-steps">
        <li>Go to <strong>Setup → Flows → New Flow</strong></li>
        <li>Choose <strong>Record-Triggered Flow</strong></li>
        <li>Set trigger: <em>Case Created or Updated</em></li>
        <li>Add an <strong>Action</strong> element → <em>HTTP Callout</em></li>
        <li>Paste your FAGE Webhook URL as the endpoint</li>
        <li>Set method to <strong>POST</strong>, content type <strong>application/json</strong></li>
        <li>Paste the Request Body above, replacing field references with your Flow variables</li>
        <li>Add a second action to write the AI response back to the Case field of your choice</li>
        <li>Activate the Flow</li>
      </ol>
      <div class="conn-guide-note">💡 Tip: Map <code>{!Case.Description}</code> to the <code>text</code> field for automatic case summarization.</div>
    `,
  },
  servicenow: {
    label:   "ServiceNow",
    objects: ["Incidents", "Changes", "Requests", "Problems", "Tasks"],
    guide:   `
      <div class="conn-guide-title">ServiceNow Setup (Flow Designer)</div>
      <ol class="conn-guide-steps">
        <li>Go to <strong>Flow Designer → New Flow</strong></li>
        <li>Add a trigger: <em>Record Created → Incident</em></li>
        <li>Add a <strong>REST Step</strong> action</li>
        <li>Set Connection URL to your FAGE Webhook URL</li>
        <li>Method: <strong>POST</strong>, paste the Request Body above</li>
        <li>Map the response <code>simulated_response</code> field back to your Incident's Work Notes</li>
        <li>Activate the flow</li>
      </ol>
      <div class="conn-guide-note">💡 Tip: Use the <code>routing_reason</code> field to auto-escalate COMPLEX incidents to a senior queue.</div>
    `,
  },
  hubspot: {
    label:   "HubSpot",
    objects: ["Tickets", "Contacts", "Deals", "Companies"],
    guide:   `
      <div class="conn-guide-title">HubSpot Setup (Workflows)</div>
      <ol class="conn-guide-steps">
        <li>Go to <strong>Automation → Workflows → Create Workflow</strong></li>
        <li>Choose <em>Ticket-based</em> workflow</li>
        <li>Set trigger: <em>Ticket is created</em></li>
        <li>Add action: <strong>Send a webhook</strong></li>
        <li>Paste your FAGE Webhook URL</li>
        <li>Method: <strong>POST</strong>, paste the Request Body above</li>
        <li>Add a second action to update the ticket with the AI response</li>
        <li>Save and turn on</li>
      </ol>
      <div class="conn-guide-note">💡 Tip: HubSpot webhooks fire in seconds — your support tickets will have AI responses before an agent even opens them.</div>
    `,
  },
  dynamics: {
    label:   "Dynamics 365",
    objects: ["Cases", "Leads", "Opportunities", "Contacts", "Accounts"],
    guide:   `
      <div class="conn-guide-title">Dynamics 365 Setup (Power Automate)</div>
      <ol class="conn-guide-steps">
        <li>Go to <strong>Power Automate → Create → Automated Cloud Flow</strong></li>
        <li>Trigger: <em>When a record is created (Dataverse)</em> → select Cases</li>
        <li>Add step: <strong>HTTP</strong></li>
        <li>Method: <strong>POST</strong>, URI: paste your FAGE Webhook URL</li>
        <li>Headers: <code>Content-Type: application/json</code></li>
        <li>Body: paste the Request Body above, map Dynamics fields</li>
        <li>Add step to update the Case with the AI response</li>
        <li>Save and test</li>
      </ol>
      <div class="conn-guide-note">💡 Tip: Use the <code>budget_used_pct</code> field to trigger a Teams notification when a department approaches its cap.</div>
    `,
  },
  zendesk: {
    label:   "Zendesk",
    objects: ["Tickets", "Users", "Organizations"],
    guide:   `
      <div class="conn-guide-title">Zendesk Setup (Triggers + Webhooks)</div>
      <ol class="conn-guide-steps">
        <li>Go to <strong>Admin → Objects and Rules → Webhooks → Create Webhook</strong></li>
        <li>Paste your FAGE Webhook URL as the Endpoint URL</li>
        <li>Method: <strong>POST</strong>, format: <strong>JSON</strong></li>
        <li>Go to <strong>Triggers → Add Trigger</strong></li>
        <li>Condition: <em>Ticket is created</em></li>
        <li>Action: <em>Notify webhook</em> → select your FAGE webhook</li>
        <li>Paste the Request Body above in the JSON body field</li>
        <li>Save trigger</li>
      </ol>
      <div class="conn-guide-note">💡 Tip: Map <code>{{ticket.description}}</code> to the text field to automatically triage incoming tickets.</div>
    `,
  },
  other: {
    label:   "Custom / Other",
    objects: ["Records", "Events", "Messages", "Documents", "Other"],
    guide:   `
      <div class="conn-guide-title">Generic Webhook Setup</div>
      <ol class="conn-guide-steps">
        <li>In your platform's automation or integration builder, find the <strong>HTTP / Webhook</strong> action</li>
        <li>Set the URL to your FAGE Webhook URL</li>
        <li>Method: <strong>POST</strong>, Content-Type: <strong>application/json</strong></li>
        <li>Paste the Request Body above, replacing placeholder values with your platform's field variables</li>
        <li>Map the <code>simulated_response</code> from the FAGE response back to your record</li>
      </ol>
      <div class="conn-guide-note">💡 Any platform that can send an HTTP POST can connect to FAGE. No SDK required.</div>
    `,
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
      throw new Error("No budgets");
    }
  } catch {
    ["Support","Sales","Marketing","Operations"].forEach(d => {
      const opt = document.createElement("option");
      opt.value = d; opt.textContent = d;
      select.appendChild(opt);
    });
  }
  // Auto-select first real option
  if (select.options.length > 1) select.selectedIndex = 1;

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

// ── Step 2 → Register agent + go to step 3 ───────────────────────────────────

async function registerAndConnect() {
  const name  = document.getElementById("agentName").value.trim();
  const dept  = document.getElementById("agentDept").value;
  const err   = document.getElementById("error-2");

  if (!name) {
    err.textContent = "Please enter an agent name.";
    return;
  }
  if (!dept) {
    err.textContent = "Please select a department.";
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
    err.textContent = "Error: " + err2.message;
    err.style.color = "var(--accent-red)";
  }
}

// ── Step 3: Build result screen ───────────────────────────────────────────────

function buildResultScreen(agent) {
  const baseUrl = window.location.origin;
  const dept    = agent.department;

  document.getElementById("finalAgentName").textContent = agent.name;

  // Webhook URL
  const webhookUrl = `${baseUrl}/api/route`;
  document.getElementById("webhookUrl").textContent = webhookUrl;

  // Request body
  const body = JSON.stringify({
    text:       `{Your ${selectedObject || "record"} description field}`,
    department: dept,
    auto_prune: true,
    agent_id:   agent.id,
  }, null, 2);
  document.getElementById("requestBody").textContent = body;

  // Platform guide
  const plat = PLATFORMS[selectedPlatform];
  document.getElementById("platformGuide").innerHTML = plat ? plat.guide : "";
}

// ── Utility ───────────────────────────────────────────────────────────────────

function copyUrl() {
  const url = document.getElementById("webhookUrl").textContent;
  navigator.clipboard.writeText(url).then(() => {
    const btn = event.target;
    btn.textContent = "Copied!";
    setTimeout(() => btn.textContent = "Copy", 2000);
  });
}

function copyBody() {
  const body = document.getElementById("requestBody").textContent;
  navigator.clipboard.writeText(body).then(() => {
    const btn = event.target;
    btn.textContent = "Copied!";
    setTimeout(() => btn.textContent = "Copy Body", 2000);
  });
}

function connectAnother() {
  selectedPlatform  = null;
  selectedObject    = null;
  selectedPolicy    = "lock";
  registeredAgentId = null;
  document.querySelectorAll(".conn-platform").forEach(el => el.classList.remove("selected"));
  goStep(1);
}
