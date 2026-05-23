/**
 * onboarding.js — FAGE Client Onboarding Wizard
 *
 * 4-screen wizard:
 *   1. Company setup (name, industry, provider, total budget)
 *   2. Department breakdown (allocate budget per dept)
 *   3. Review
 *   4. Launch (API calls to create budgets, redirect to dashboard)
 */

let selectedProvider = "openai";

const defaultDepartments = [
  { name: "Support",    cap: 0 },
  { name: "Sales",      cap: 0 },
  { name: "Marketing",  cap: 0 },
  { name: "Operations", cap: 0 },
];

let departments = defaultDepartments.map(d => ({ ...d }));

// ── Provider selection ────────────────────────────────────────────────────────

function selectProvider(provider) {
  selectedProvider = provider;
  document.getElementById("prov-openai").classList.toggle("selected",    provider === "openai");
  document.getElementById("prov-anthropic").classList.toggle("selected", provider === "anthropic");
}

// ── Screen navigation ─────────────────────────────────────────────────────────

function goToScreen(n) {
  // Validate before advancing
  if (n === 2 && !validateScreen1()) return;
  if (n === 3 && !validateScreen2()) return;

  // Hide all screens
  document.querySelectorAll(".ob-screen").forEach(s => s.classList.remove("active"));
  document.querySelectorAll(".ob-step").forEach(s => s.classList.remove("active", "done"));

  // Show target screen
  document.getElementById(`screen-${n}`).classList.add("active");

  // Update progress
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById(`prog-${i}`);
    if (i < n)      el.classList.add("done");
    else if (i === n) el.classList.add("active");
  }

  if (n === 2) renderDeptScreen();
  if (n === 3) renderReview();
}

// ── Validation ────────────────────────────────────────────────────────────────

function validateScreen1() {
  const name   = document.getElementById("companyName").value.trim();
  const budget = parseFloat(document.getElementById("totalBudget").value);
  const err    = document.getElementById("error-1");

  if (!name) {
    err.textContent = "Please enter your company name.";
    return false;
  }
  if (!budget || budget < 10) {
    err.textContent = "Please enter a total monthly budget of at least $10.";
    return false;
  }
  err.textContent = "";
  return true;
}

function validateScreen2() {
  const err = document.getElementById("error-2");
  const valid = departments.every(d => d.name.trim() && d.cap >= 0);
  if (!valid) {
    err.textContent = "Each department needs a name and a budget cap.";
    return false;
  }
  if (departments.length === 0) {
    err.textContent = "Add at least one department.";
    return false;
  }
  err.textContent = "";
  return true;
}

// ── Screen 2: Department setup ────────────────────────────────────────────────

function renderDeptScreen() {
  const total = parseFloat(document.getElementById("totalBudget").value) || 0;
  document.getElementById("budgetLabel").textContent = "$" + total.toLocaleString();
  document.getElementById("totalAmt").textContent    = "$" + total.toLocaleString();

  // Auto-distribute evenly if all caps are 0
  if (departments.every(d => d.cap === 0) && total > 0) {
    const even = Math.floor((total / departments.length) * 100) / 100;
    departments.forEach(d => d.cap = even);
  }

  renderDeptList();
}

function renderDeptList() {
  const total     = parseFloat(document.getElementById("totalBudget").value) || 0;
  const container = document.getElementById("deptList");

  container.innerHTML = departments.map((d, i) => `
    <div class="ob-dept-row">
      <input type="text" class="ob-input ob-dept-name" value="${d.name}"
             oninput="updateDept(${i}, 'name', this.value)" placeholder="Department name" />
      <div class="ob-dept-cap-wrap">
        <span class="ob-currency">$</span>
        <input type="number" class="ob-input ob-dept-cap" value="${d.cap}"
               oninput="updateDept(${i}, 'cap', parseFloat(this.value) || 0)"
               min="0" step="50" />
        <span class="ob-currency-label">/ mo</span>
      </div>
      <button class="ob-dept-remove" onclick="removeDept(${i})" title="Remove">✕</button>
    </div>
  `).join("");

  updateBudgetSummary(total);
}

function updateDept(index, field, value) {
  departments[index][field] = value;
  const total = parseFloat(document.getElementById("totalBudget").value) || 0;
  updateBudgetSummary(total);
}

function addDepartment() {
  departments.push({ name: "", cap: 0 });
  renderDeptList();
}

function removeDept(index) {
  if (departments.length <= 1) return;
  departments.splice(index, 1);
  renderDeptList();
}

function updateBudgetSummary(total) {
  const allocated  = departments.reduce((sum, d) => sum + (d.cap || 0), 0);
  const remaining  = total - allocated;
  const remEl      = document.getElementById("remainingAmt");

  document.getElementById("allocatedAmt").textContent = "$" + allocated.toLocaleString();
  remEl.textContent = "$" + remaining.toLocaleString();
  remEl.style.color = remaining < 0 ? "var(--accent-red)" : "var(--accent-green)";
}

// ── Screen 3: Review ──────────────────────────────────────────────────────────

function renderReview() {
  const company  = document.getElementById("companyName").value.trim();
  const budget   = parseFloat(document.getElementById("totalBudget").value);
  const provider = selectedProvider === "openai" ? "OpenAI (gpt-3.5-turbo · gpt-4o)" : "Anthropic (claude-haiku · claude-sonnet)";

  document.getElementById("reviewCompany").textContent  = company;
  document.getElementById("reviewProvider").textContent = provider;
  document.getElementById("reviewBudget").textContent   = "$" + budget.toLocaleString() + " / month";

  document.getElementById("reviewDepts").innerHTML = departments.map(d => `
    <div class="ob-review-dept-row">
      <span class="ob-review-dept-name">${d.name}</span>
      <span class="ob-review-dept-cap">$${(d.cap || 0).toLocaleString()} / mo</span>
    </div>
  `).join("");
}

// ── Screen 4: Launch ──────────────────────────────────────────────────────────

async function launchFage() {
  goToScreen(4);

  const steps    = document.getElementById("launchSteps");
  const spinner  = document.getElementById("launchSpinner");
  const title    = document.getElementById("launchTitle");
  const sub      = document.getElementById("launchSub");
  const doneBtn  = document.getElementById("dashboardBtn");

  const log = (msg, success = null) => {
    const icon = success === null ? "⏳" : success ? "✓" : "✗";
    const color = success === false ? "var(--accent-red)" : success ? "var(--accent-green)" : "var(--text-muted)";
    steps.innerHTML += `<div class="ob-launch-step" style="color:${color}">${icon} ${msg}</div>`;
  };

  try {
    // Step 1 — Create department budgets
    log("Creating department budgets...");
    for (const dept of departments) {
      if (!dept.name.trim()) continue;
      try {
        await apiPost(`/api/budget/${encodeURIComponent(dept.name)}/cap`, {
          new_cap_usd: dept.cap || 0,
        });
        log(`${dept.name} — $${dept.cap}/mo`, true);
      } catch (e) {
        log(`${dept.name} — ${e.message}`, false);
      }
    }

    // Step 2 — Done
    spinner.style.display = "none";
    title.textContent = "FAGE is ready.";
    sub.textContent   = "Your AI governance layer is live. Next, connect your Salesforce org.";
    log("Setup complete!", true);
    doneBtn.style.display      = "inline-block";
    document.getElementById("skipConnectBtn").style.display = "inline-block";

  } catch (err) {
    spinner.style.display = "none";
    title.textContent = "Setup failed.";
    sub.textContent   = err.message;
  }
}

// ── Salesforce Integration (Screen 5) ────────────────────────────────────────

const OBJECT_FIELDS = {
  Case:        ["Description", "Subject", "Body", "Internal_Comments__c", "Custom Field..."],
  Lead:        ["Description", "Notes__c", "Custom Field..."],
  Opportunity: ["Description", "Next_Step__c", "Custom Field..."],
  Contact:     ["Description", "Custom Field..."],
  Account:     ["Description", "Notes__c", "Custom Field..."],
  Task:        ["Description", "Subject", "Custom Field..."],
  __custom__:  ["Custom Field..."],
};

function onObjectChange() {
  const obj     = document.getElementById("sfObject").value;
  const fields  = OBJECT_FIELDS[obj] || ["Description", "Custom Field..."];
  const select  = document.getElementById("sfField");
  const customObjRow = document.getElementById("customObjectField");

  customObjRow.style.display = obj === "__custom__" ? "" : "none";

  select.innerHTML = fields.map(f =>
    `<option value="${f}">${f}</option>`
  ).join("");

  onFieldChange();

  // Auto-suggest agent name
  const agentInput = document.getElementById("sfAgentName");
  if (!agentInput.value) {
    const label = obj === "__custom__" ? "Custom" : obj;
    agentInput.value = `SF-${label}Bot`;
  }
}

function onFieldChange() {
  const field = document.getElementById("sfField").value;
  document.getElementById("customFieldRow").style.display =
    field === "Custom Field..." ? "" : "none";
}

function getEffectiveObject() {
  const obj = document.getElementById("sfObject").value;
  if (obj === "__custom__") {
    return document.getElementById("sfCustomObject").value.trim() || "MyObject__c";
  }
  return obj;
}

function getEffectiveField() {
  const field = document.getElementById("sfField").value;
  if (field === "Custom Field...") {
    return document.getElementById("sfCustomField").value.trim() || "My_Field__c";
  }
  return field;
}

function generateCode() {
  const err = document.getElementById("error-5");
  const obj   = getEffectiveObject();
  const field = getEffectiveField();
  const dept  = document.getElementById("sfDept").value;
  const agent = document.getElementById("sfAgentName").value.trim() || `SF-${obj}Bot`;

  if (!obj)   { err.textContent = "Please select or enter an object."; return; }
  if (!field) { err.textContent = "Please select or enter a field.";   return; }
  err.textContent = "";

  // ── Generate Apex ──────────────────────────────────────────────────────────
  const apex = `public class FAGECallout {
    @InvocableMethod(label='Send to FAGE')
    public static void sendToFAGE(List<FAGERequest> requests) {
        if (System.isFuture() || System.isBatch()) return;
        FAGERequest req = requests[0];
        sendAsync(req.recordId, req.recordText, req.department, req.agentName);
    }

    @future(callout=true)
    public static void sendAsync(String recordId, String recordText, String department, String agentName) {
        Http http = new Http();
        HttpRequest httpReq = new HttpRequest();
        httpReq.setEndpoint('https://fage-engine-21cb49fe4806.herokuapp.com/api/route');
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

        HttpResponse res = http.send(httpReq);
        System.debug('FAGE Response: ' + res.getBody());

        if (res.getStatusCode() == 200 && recordId != null) {
            Map<String, Object> fageResp =
                (Map<String, Object>) JSON.deserializeUntyped(res.getBody());

            String aiResponse      = (String) fageResp.get('simulated_response');
            String modelUsed       = (String) fageResp.get('model_name');
            String routingDecision = (String) fageResp.get('routing_decision');
            Decimal costUsd        = Decimal.valueOf(String.valueOf(fageResp.get('cost_usd')));

            ${obj} record = new ${obj}(Id = recordId);
            record.FAGE_AI_Response__c      = aiResponse;
            record.FAGE_Model_Used__c       = modelUsed;
            record.FAGE_Routing_Decision__c = routingDecision;
            record.FAGE_Cost_USD__c         = costUsd;
            update record;
        }
    }

    public class FAGERequest {
        @InvocableVariable(required=true  label='Record ID')   public String recordId;
        @InvocableVariable(required=true  label='Record Text') public String recordText;
        @InvocableVariable(required=true  label='Department')  public String department;
        @InvocableVariable(required=false label='Agent Name')  public String agentName;
    }
}`;

  document.getElementById("apexCode").textContent = apex;

  // ── Flow instructions ──────────────────────────────────────────────────────
  document.getElementById("flowSteps").innerHTML = `
    <div class="ob-flow-step">
      <span class="ob-flow-num">1</span>
      <div><strong>Setup → Flows → New Flow</strong><br/>
      Type: <em>Record-Triggered Flow</em> · Object: <strong>${obj}</strong> · Trigger: <em>A record is created or updated</em></div>
    </div>
    <div class="ob-flow-step">
      <span class="ob-flow-num">2</span>
      <div><strong>Add an Action element</strong><br/>
      Category: <em>Apex</em> · Action: <em>Send to FAGE</em> · Label: <em>Route to FAGE</em></div>
    </div>
    <div class="ob-flow-step">
      <span class="ob-flow-num">3</span>
      <div><strong>Set Input Values</strong><br/>
      <code>Record ID</code> → <em>Triggering ${obj} &gt; ${obj} ID</em><br/>
      <code>Record Text</code> → <em>Triggering ${obj} &gt; ${field}</em><br/>
      <code>Department</code> → <em>${dept}</em><br/>
      <code>Agent Name</code> → <em>${agent}</em></div>
    </div>
    <div class="ob-flow-step">
      <span class="ob-flow-num">4</span>
      <div><strong>Save &amp; Activate the Flow</strong><br/>
      That's it. The next time a <strong>${obj}</strong> is saved with a <strong>${field}</strong> value, FAGE will process it and write the AI response back automatically.</div>
    </div>
  `;

  // ── Field table ────────────────────────────────────────────────────────────
  document.getElementById("fieldObjectLabel").textContent = obj;
  document.getElementById("fieldTable").innerHTML = `
    <div class="ob-field-row ob-field-row-header">
      <span>Field Label</span><span>API Name</span><span>Type</span><span>Settings</span>
    </div>
    <div class="ob-field-row">
      <span>FAGE AI Response</span>
      <span class="mono">FAGE_AI_Response__c</span>
      <span>Long Text Area</span>
      <span>32,768 chars</span>
    </div>
    <div class="ob-field-row">
      <span>FAGE Model Used</span>
      <span class="mono">FAGE_Model_Used__c</span>
      <span>Text</span>
      <span>255 chars</span>
    </div>
    <div class="ob-field-row">
      <span>FAGE Routing Decision</span>
      <span class="mono">FAGE_Routing_Decision__c</span>
      <span>Text</span>
      <span>50 chars</span>
    </div>
    <div class="ob-field-row">
      <span>FAGE Cost USD</span>
      <span class="mono">FAGE_Cost_USD__c</span>
      <span>Currency</span>
      <span>Length 12, Decimals 6</span>
    </div>
  `;

  document.getElementById("successSummary").textContent =
    `${obj}.${field} → ${dept} department · Agent: ${agent}`;

  document.getElementById("sfOutput").style.display = "block";
  document.getElementById("sfOutput").scrollIntoView({ behavior: "smooth" });
}

function copyCode(elementId) {
  const text = document.getElementById(elementId).textContent;
  navigator.clipboard.writeText(text).then(() => {
    const btn = event.target;
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = "Copy"; }, 2000);
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────
renderDeptList();
onObjectChange(); // populate field dropdown on load
