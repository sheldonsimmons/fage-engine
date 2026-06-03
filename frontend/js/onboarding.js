/**
 * onboarding.js — CostPilot Client Onboarding Wizard
 *
 * 4-screen wizard:
 *   1. Company setup (name, industry, provider, total budget)
 *   2. Department breakdown (allocate budget per dept)
 *   3. Review
 *   4. Launch (API calls to create budgets, redirect to dashboard)
 */

let selectedProvider  = "anthropic";
let voiceGuardEnabled = false;

// ── Trial detection ────────────────────────────────────────────────────────────
const TRIAL_WS  = localStorage.getItem("cp_workspace_id") || "";
const TRIAL_SK  = localStorage.getItem("cp_secret_key")   || "";
const TRIAL_PRV = localStorage.getItem("cp_provider")     || "";
const TRIAL_NAME= localStorage.getItem("cp_trial_name")   || "";
const IS_TRIAL  = !!TRIAL_WS && !!TRIAL_SK;
const TRIAL_PROXY = IS_TRIAL
  ? `https://fage-engine-21cb49fe4806.herokuapp.com/v1/ws-${TRIAL_WS}`
  : "";

function goToDashboard() {
  window.location.href = IS_TRIAL ? "/workspace.html" : "/";
}

// Pre-fill known trial fields on page load
document.addEventListener("DOMContentLoaded", () => {
  if (!IS_TRIAL) return;

  // Pre-fill company name from trial registration
  const companyEl = document.getElementById("companyName");
  if (companyEl && TRIAL_NAME) companyEl.value = TRIAL_NAME.split(" ")[0] + "'s Company";

  // Pre-select provider
  if (TRIAL_PRV) selectProvider(TRIAL_PRV);
});

function toggleVoiceGuard() {
  voiceGuardEnabled = !voiceGuardEnabled;
  const track    = document.getElementById("vgToggleTrack");
  const thumb    = document.getElementById("vgToggleThumb");
  const label    = document.getElementById("vgToggleLabel");
  const progLine = document.getElementById("prog-line-vg");
  const prog6    = document.getElementById("prog-6");
  const progLbl  = document.getElementById("prog-label-vg");

  track.style.background = voiceGuardEnabled ? "var(--accent-green,#3fb950)" : "var(--border,#30363d)";
  thumb.style.transform  = voiceGuardEnabled ? "translateX(18px)" : "translateX(0)";
  label.textContent      = voiceGuardEnabled ? "Enabled — Voice Guard step included" : "Disabled — skip Voice Guard setup";
  label.style.color      = voiceGuardEnabled ? "var(--accent-green,#3fb950)" : "var(--text-muted,#8b949e)";

  const d = voiceGuardEnabled ? "" : "none";
  if (progLine) progLine.style.display = d;
  if (prog6)    prog6.style.display    = d;
  if (progLbl)  progLbl.style.display  = d;
}

const defaultDepartments = [
  { name: "Support",    cap: 0 },
  { name: "Sales",      cap: 0 },
  { name: "Marketing",  cap: 0 },
  { name: "Operations", cap: 0 },
  { name: "Trips Team", cap: 0 },
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
  for (let i = 1; i <= 6; i++) {
    const el = document.getElementById(`prog-${i}`);
    if (!el) continue;
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
  // Redistribute total budget evenly across remaining departments
  const total = parseFloat(document.getElementById("totalBudget").value) || 0;
  if (total > 0 && departments.length > 0) {
    const perDept = Math.floor(total / departments.length);
    const remainder = total - perDept * departments.length;
    departments.forEach((d, i) => { d.cap = perDept + (i === 0 ? remainder : 0); });
  }
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
  const provider = selectedProvider === "openai" ? "OpenAI (GPT-4o Mini · GPT-4o)" : "Anthropic (Claude Haiku 4.5 · Claude Sonnet 4.6)";

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
    title.textContent = "CostPilot is ready.";
    sub.textContent   = "Your AI governance layer is live. Connect a platform or go to your dashboard.";
    log("Setup complete!", true);
    document.getElementById("launchPlatformPicker").style.display = "";
    doneBtn.style.display      = "inline-block";
    document.getElementById("skipConnectBtn").style.display = "inline-block";

    // Show trial credentials if this is a trial signup
    if (IS_TRIAL) {
      const credPanel = document.getElementById("trialCredentials");
      const proxyEl   = document.getElementById("trialProxyUrl");
      const keyEl     = document.getElementById("trialSecretKey");
      if (credPanel) credPanel.style.display = "block";
      if (proxyEl)   proxyEl.textContent   = TRIAL_PROXY;
      if (keyEl)     keyEl.textContent     = TRIAL_SK;
      document.getElementById("skipConnectBtn").textContent = "Open My Dashboard →";
    }

  } catch (err) {
    spinner.style.display = "none";
    title.textContent = "Setup failed.";
    sub.textContent   = err.message;
  }
}

// ── Launch platform picker ────────────────────────────────────────────────────

let selectedLaunchPlatform = null;

function selectLaunchPlatform(platform) {
  selectedLaunchPlatform = platform;
  // Highlight selected tile
  document.querySelectorAll("#launchPlatformPicker .ob-provider").forEach(el => el.classList.remove("selected"));
  document.getElementById(`lp-${platform}`).classList.add("selected");
  // Update connect button label
  const labels = { salesforce: "Salesforce", servicenow: "ServiceNow", hubspot: "HubSpot", dynamics: "Dynamics 365", zendesk: "Zendesk", custom: "Custom Platform" };
  document.getElementById("dashboardBtn").textContent = `Connect ${labels[platform] || platform} →`;
}

function goToPlatformScreen() {
  goToScreen(5);
  if (selectedLaunchPlatform) {
    // Hide tile grid — platform already chosen on Screen 4
    const grid = document.querySelector("#screen-5 .ob-provider-grid");
    if (grid) grid.style.display = "none";
    document.getElementById("obPlatBackOnly").style.display = "none";
    // Pre-fill and auto-generate code
    selectObPlatform(selectedLaunchPlatform);
    // Auto-pick first department from user's setup
    const userDepts = departments.filter(d => d.name.trim());
    if (userDepts.length > 0) {
      document.getElementById("obPlatDept").value = userDepts[0].name;
    }
    generateObCode();
  }
}

function resetObPlatformScreen() {
  // Restore tile grid visibility for next visit
  const grid = document.querySelector("#screen-5 .ob-provider-grid");
  if (grid) grid.style.display = "";
  document.getElementById("obPlatConfig").style.display  = "none";
  document.getElementById("obPlatBackOnly").style.display = "";
  document.getElementById("obPlatOutput").style.display  = "none";
  selectedLaunchPlatform = null;
}

// ── Platform Integration (Screen 5) ──────────────────────────────────────────

const CostPilot_URL = "https://fage-engine-21cb49fe4806.herokuapp.com";

const OB_PLATFORMS = {
  salesforce: { label: "Salesforce",    objects: ["Case","Lead","Opportunity","Contact","Account","Task"],              agentDefault: "SF-CaseBot"    },
  servicenow: { label: "ServiceNow",    objects: ["incident","sc_request","problem","change_request","task"],           agentDefault: "SN-IncidentBot" },
  hubspot:    { label: "HubSpot",       objects: ["contacts","deals","tickets","companies","tasks"],                    agentDefault: "HS-TicketBot"  },
  dynamics:   { label: "Dynamics 365", objects: ["incident","lead","opportunity","contact","account"],                  agentDefault: "D365-CaseBot"  },
  zendesk:    { label: "Zendesk",       objects: ["ticket","user","organization","request"],                            agentDefault: "ZD-TicketBot"  },
  custom:     { label: "Custom/Other",  objects: ["record","event","document","message"],                               agentDefault: "CostPilot-Bot"      },
};

let obSelectedPlatform = null;

function selectObPlatform(platform) {
  obSelectedPlatform = platform;
  Object.keys(OB_PLATFORMS).forEach(p => {
    const el = document.getElementById("ob-plat-" + p);
    if (el) el.classList.toggle("selected", p === platform);
  });
  const cfg = OB_PLATFORMS[platform];
  const objSel = document.getElementById("obPlatObject");
  objSel.innerHTML = cfg.objects.map(o => `<option value="${o}">${o}</option>`).join("");
  // Populate department dropdown from the user's already-configured departments
  const deptSel = document.getElementById("obPlatDept");
  const userDepts = departments.filter(d => d.name.trim());
  if (userDepts.length > 0) {
    deptSel.innerHTML = userDepts.map(d => `<option value="${d.name}">${d.name}</option>`).join("");
  }
  const agentEl = document.getElementById("obPlatAgent");
  if (!agentEl.value || Object.values(OB_PLATFORMS).some(c => c.agentDefault === agentEl.value)) {
    agentEl.value = cfg.agentDefault;
  }
  document.getElementById("obPlatConfig").style.display  = "block";
  document.getElementById("obPlatBackOnly").style.display = "none";
  document.getElementById("obPlatOutput").style.display  = "none";
}

function _obEsc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function _obCodeSection(label, hint, code) {
  const id = "obc" + Math.random().toString(36).slice(2,8);
  return `
    <div class="ob-code-section" style="margin-top:20px">
      <div class="ob-code-header">
        <span class="ob-code-label">${_obEsc(label)}</span>
        ${hint ? `<span class="ob-code-hint">${_obEsc(hint)}</span>` : ""}
        <button class="ob-copy-btn" onclick="obCopyCode('${id}')">Copy</button>
      </div>
      <pre class="ob-code-block" id="${id}">${_obEsc(code)}</pre>
    </div>`;
}

function _obBanner(platform, obj, dept, agent) {
  const lbl = (OB_PLATFORMS[platform] || {}).label || platform;
  return `<div class="ob-success-banner">CostPilot configured for <strong>${_obEsc(lbl)} · ${_obEsc(obj)} → ${_obEsc(dept)}</strong>. Agent: <strong>${_obEsc(agent)}</strong>. Agents appear in the Agentlake Registry on first use.</div>`;
}

function _obActions() {
  const vg = voiceGuardEnabled
    ? `<button class="ob-btn-ghost" onclick="goToScreen(6)" style="margin-left:auto">🎙 Voice Guard →</button>` : "";
  return `<div class="ob-actions" style="margin-top:24px">
    <button class="ob-btn-ghost" onclick="generateObCode()">↺ Regenerate</button>
    ${vg}
    <button class="ob-btn-primary" onclick="goToDashboard()">Open Dashboard →</button>
  </div>`;
}

function obCopyCode(id) {
  const pre = document.getElementById(id);
  if (!pre) return;
  navigator.clipboard.writeText(pre.textContent).then(() => {
    const btn = event.target;
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = "Copy"; }, 2000);
  });
}

function generateObCode() {
  const err = document.getElementById("error-5");
  if (!obSelectedPlatform) { err.textContent = "Select a platform first."; return; }
  err.textContent = "";
  const obj   = document.getElementById("obPlatObject").value.trim();
  const dept  = document.getElementById("obPlatDept").value;
  const agent = document.getElementById("obPlatAgent").value.trim() || OB_PLATFORMS[obSelectedPlatform].agentDefault;
  const fns   = { salesforce:_genSalesforce, servicenow:_genServiceNow, hubspot:_genHubSpot, dynamics:_genDynamics, zendesk:_genZendesk, custom:_genCustom };
  const html  = (fns[obSelectedPlatform] || _genCustom)(obj, dept, agent);
  const out   = document.getElementById("obPlatOutput");
  out.innerHTML = html;
  out.style.display = "block";
  out.scrollIntoView({ behavior: "smooth" });
}

// ── Code generators ───────────────────────────────────────────────────────────

function _genSalesforce(obj, dept, agent) {
  const apex =
`public class CostPilotCallout {
    @InvocableMethod(label='Send to CostPilot')
    public static void sendToCostPilot(List<CostPilotRequest> requests) {
        if (System.isFuture() || System.isBatch()) return;
        CostPilotRequest req = requests[0];
        sendAsync(req.recordId, req.subject, req.recordText, req.department, req.agentName);
    }

    @future(callout=true)
    public static void sendAsync(String recordId, String subject,
                                 String recordText, String department, String agentName) {
        // Always combine Subject + Description so short cases (subject-only) reach CostPilot
        String subj = String.isBlank(subject)     ? '' : subject.trim();
        String body = String.isBlank(recordText)  ? '' : recordText.trim();
        String payload = String.isBlank(subj)
            ? (String.isBlank(body) ? '(no content)' : body)
            : (String.isBlank(body) ? subj : subj + '\\n\\n' + body);
        Http http = new Http();
        HttpRequest httpReq = new HttpRequest();
        httpReq.setEndpoint('${CostPilot_URL}/api/route');
        httpReq.setMethod('POST');
        httpReq.setHeader('Content-Type', 'application/json');
        httpReq.setBody(JSON.serialize(new Map<String, Object>{
            'text'            => payload,
            'department'      => department,
            'auto_prune'      => true,
            'agent_name'      => agentName,
            'source_platform' => 'Salesforce'
        }));
        httpReq.setTimeout(30000);
        HttpResponse res = http.send(httpReq);
        if (res.getStatusCode() == 200 && recordId != null) {
            Map<String,Object> r = (Map<String,Object>) JSON.deserializeUntyped(res.getBody());
            ${obj} rec = new ${obj}(Id = recordId);
            rec.CostPilot_AI_Response__c      = (String)  r.get('simulated_response');
            rec.CostPilot_Model_Used__c       = (String)  r.get('model_name');
            rec.CostPilot_Routing_Decision__c = (String)  r.get('routing_decision');
            rec.CostPilot_Cost_USD__c         = Decimal.valueOf(String.valueOf(r.get('cost_usd')));
            update rec;
        }
    }

    public class CostPilotRequest {
        @InvocableVariable(required=true  label='Record ID')          public String recordId;
        @InvocableVariable(required=true  label='Subject')            public String subject;
        @InvocableVariable(required=false label='Description / Body') public String recordText;
        @InvocableVariable(required=true  label='Department')         public String department;
        @InvocableVariable(required=false label='Agent Name')         public String agentName;
    }
}`;

  const flowHtml = `<div class="ob-flow-steps">
    <div class="ob-flow-step"><span class="ob-flow-num">1</span>
      <div><strong>Setup → Flows → New Flow</strong><br/>Type: <em>Record-Triggered</em> · Object: <strong>${_obEsc(obj)}</strong> · Trigger: <em>Created or updated</em></div></div>
    <div class="ob-flow-step"><span class="ob-flow-num">2</span>
      <div><strong>Add Action → Apex → Send to CostPilot</strong><br/>Map these fields:<br/>• Record ID → ${_obEsc(obj)} ID<br/>• <strong>Subject → Subject</strong> (required — short cases like "My email is down" route via Subject)<br/>• Description / Body → Description (optional)<br/>• Department → ${_obEsc(dept)}<br/>• Agent Name → ${_obEsc(agent)}</div></div>
    <div class="ob-flow-step"><span class="ob-flow-num">3</span>
      <div><strong>Save &amp; Activate</strong> — agents appear in the Agentlake Registry on first use.</div></div>
  </div>`;

  const fieldHtml = `<div class="ob-field-table">
    <div class="ob-field-row ob-field-row-header"><span>Label</span><span>API Name</span><span>Type</span><span>Settings</span></div>
    <div class="ob-field-row"><span>CostPilot AI Response</span><span class="mono">CostPilot_AI_Response__c</span><span>Long Text</span><span>32,768 chars</span></div>
    <div class="ob-field-row"><span>CostPilot Model Used</span><span class="mono">CostPilot_Model_Used__c</span><span>Text</span><span>255 chars</span></div>
    <div class="ob-field-row"><span>CostPilot Routing</span><span class="mono">CostPilot_Routing_Decision__c</span><span>Text</span><span>50 chars</span></div>
    <div class="ob-field-row"><span>CostPilot Cost USD</span><span class="mono">CostPilot_Cost_USD__c</span><span>Currency</span><span>12,6</span></div>
  </div>`;

  return _obCodeSection("Step 1 — Apex Class", "Developer Console → File → Open → CostPilotCallout → Replace all → Save", apex)
    + `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Step 2 — Salesforce Flow</span></div>${flowHtml}</div>`
    + `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Step 3 — Custom Fields on ${_obEsc(obj)}</span><span class="ob-code-hint">Setup → Object Manager → ${_obEsc(obj)} → Fields &amp; Relationships → New</span></div>${fieldHtml}</div>`
    + _obBanner("salesforce", obj, dept, agent) + _obActions();
}

function _genServiceNow(obj, dept, agent) {
  const code =
`// ServiceNow Business Rule — Table: ${obj}
// When: after | Insert: true | Update: true

(function executeRule(current, previous) {
    var rm = new sn_ws.RESTMessageV2();
    rm.setEndpoint('${CostPilot_URL}/api/route');
    rm.setHttpMethod('POST');
    rm.setRequestHeader('Content-Type', 'application/json');
    rm.setRequestBody(JSON.stringify({
        text:            current.description.toString(),
        department:      '${dept}',
        auto_prune:      true,
        agent_name:      '${agent}',
        source_platform: 'ServiceNow'
    }));
    var response = rm.execute();
    if (response.getStatusCode() == 200) {
        var r = JSON.parse(response.getBody());
        current.work_notes = '[CostPilot] ' + r.simulated_response +
            '\\nModel: ' + r.model_name + ' | Cost: $' + r.cost_usd;
    }
})(current, previous);`;

  return _obCodeSection(
    "Business Rule Script",
    "System Definition → Business Rules → New · Table: " + obj + " · When: after · Insert + Update",
    code
  ) + _obBanner("servicenow", obj, dept, agent) + _obActions();
}

function _genHubSpot(obj, dept, agent) {
  const code =
`// HubSpot Custom Code Action (Node.js)
// Operations Hub → Workflows → Add Action → Custom Code
const axios = require('axios');

exports.main = async (event, callback) => {
  const text = event.inputFields['description'] || event.inputFields['content'] || '';
  const res  = await axios.post('${CostPilot_URL}/api/route', {
    text,
    department:      '${dept}',
    auto_prune:      true,
    agent_name:      '${agent}',
    source_platform: 'HubSpot',
  });
  callback({
    outputFields: {
      fage_response: res.data.simulated_response,
      fage_model:    res.data.model_name,
      fage_routing:  res.data.routing_decision,
      fage_cost:     String(res.data.cost_usd),
    }
  });
};`;

  return _obCodeSection(
    "Custom Code Action",
    "Operations Hub → Workflows → Add Action → Custom Code → Paste",
    code
  ) + _obBanner("hubspot", obj, dept, agent) + _obActions();
}

function _genDynamics(obj, dept, agent) {
  const code =
`// Power Automate — HTTP Action Configuration
// Trigger: When a row is added, modified or deleted · Table: ${obj}

Method:  POST
URI:     ${CostPilot_URL}/api/route
Headers: { "Content-Type": "application/json" }
Body:
{
  "text":            "@{triggerOutputs()?['body/description']}",
  "department":      "${dept}",
  "auto_prune":      true,
  "agent_name":      "${agent}",
  "source_platform": "Dynamics365"
}

// After HTTP — add "Update a row" action:
// fage_ai_response:      @{body('HTTP')?['simulated_response']}
// fage_model_used:       @{body('HTTP')?['model_name']}
// fage_routing_decision: @{body('HTTP')?['routing_decision']}`;

  return _obCodeSection(
    "Power Automate HTTP Action",
    "Power Automate → New Flow → Automated → Dataverse trigger → Add HTTP action",
    code
  ) + _obBanner("dynamics", obj, dept, agent) + _obActions();
}

function _genZendesk(obj, dept, agent) {
  const code =
`// Zendesk Sunshine Function (Node.js 18)
// Admin Center → Apps and integrations → Sunshine Functions → Create
const fetch = require('node-fetch');

module.exports = async (event) => {
  const ticketId = event.payload.ticket_id;
  const body     = event.payload.comment.body || '';

  const res  = await fetch('${CostPilot_URL}/api/route', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text: body, department: '${dept}',
      auto_prune: true, agent_name: '${agent}',
      source_platform: 'Zendesk',
    }),
  });
  const data = await res.json();

  await fetch(\`https://YOUR_SUBDOMAIN.zendesk.com/api/v2/tickets/\${ticketId}.json\`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer YOUR_TOKEN' },
    body: JSON.stringify({
      ticket: { comment: { body: '[CostPilot] ' + data.simulated_response, public: false } }
    }),
  });
  return { status: 200, model: data.model_name };
};`;

  return _obCodeSection(
    "Sunshine Function",
    "Admin Center → Apps and integrations → Sunshine Functions → Create → Node.js 18",
    code
  ) + _obBanner("zendesk", obj, dept, agent) + _obActions();
}

function _genCustom(obj, dept, agent) {
  const python =
`import requests

CostPilot_URL = "${CostPilot_URL}"

def send_to_fage(text: str) -> dict:
    resp = requests.post(CostPilot_URL + "/api/route", json={
        "text":            text,
        "department":      "${dept}",
        "auto_prune":      True,
        "agent_name":      "${agent}",
        "source_platform": "Custom",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()

# result = send_to_fage("Customer issue text here...")
# print(result["simulated_response"])`;

  const curl =
`curl -X POST ${CostPilot_URL}/api/route \\
  -H "Content-Type: application/json" \\
  -d '{
    "text":            "Your ${obj} text here",
    "department":      "${dept}",
    "auto_prune":      true,
    "agent_name":      "${agent}",
    "source_platform": "Custom"
  }'`;

  return _obCodeSection("Python", "pip install requests · works with any platform that can make HTTP calls", python)
    + _obCodeSection("cURL", "Test from your terminal", curl)
    + _obBanner("custom", obj, dept, agent) + _obActions();
}

// ── Screen 6: Voice Guard Demo ────────────────────────────────────────────────

const OB_VG_EXAMPLES = [
  "my card number is 4532 uh 0157 let me check 0119 8484 and my name is John Smith",
  "sure my social security is one two three, hold on, forty five, six seven eight nine",
  "you can reach me at j dot smith at acme dot com and my date of birth is March 15 1982",
];
let _obVgExIdx = 0;

function loadObExample() {
  document.getElementById("obTranscript").value = OB_VG_EXAMPLES[_obVgExIdx % OB_VG_EXAMPLES.length];
  _obVgExIdx++;
  document.getElementById("obVgResult").style.display = "none";
  document.getElementById("obVgStatus").textContent   = "";
}

async function testObVoiceGuard() {
  const input    = document.getElementById("obTranscript").value.trim();
  const statusEl = document.getElementById("obVgStatus");
  if (!input) { statusEl.textContent = "Enter a transcript first."; return; }

  statusEl.textContent = "Scanning...";
  statusEl.style.color = "var(--text-muted, #8b949e)";

  try {
    const res  = await fetch("/api/voice/transcript", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript: input, platform: "Onboarding Demo", department: "Demo" }),
    });
    const data = await res.json();

    const resultEl = document.getElementById("obVgResult");
    resultEl.style.display = "block";

    // Highlight redactions
    document.getElementById("obVgCleanText").innerHTML = data.clean_transcript
      .replace(/\[REDACTED-([^\]]+)\]/g, (_, type) =>
        `<span style="background:#2d0a0a;color:#f85149;border:1px solid #5a1a1a;border-radius:4px;padding:1px 6px;font-weight:700;font-size:12px">[REDACTED-${type}]</span>`
      );

    const piiList = data.pii_types_found.length
      ? data.pii_types_found.map(t => t.replace(/_/g, " ")).join(", ")
      : "none detected";

    document.getElementById("obVgMeta").innerHTML = `
      <span>Redactions: <strong style="color:#f85149">${data.redactions_count}</strong></span>
      <span>PII types: <strong>${piiList}</strong></span>
      <span>Confidence: <strong>${data.redactions_count ? (data.confidence_score * 100).toFixed(1) + "%" : "—"}</strong></span>
      <span>Processed in: <strong>${data.processing_ms}ms</strong></span>
    `;

    statusEl.textContent = data.redactions_count
      ? `✓ ${data.redactions_count} PII item(s) redacted — transcript is safe to route`
      : "✓ No PII detected — transcript is clean";
    statusEl.style.color = "#3fb950";
  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
    statusEl.style.color = "#f85149";
  }
}

// Mic support for Screen 6
let _obRecognition = null;
let _obMicActive   = false;
let _obFinalText   = "";

function toggleObMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    document.getElementById("obVgStatus").textContent = "Speech recognition not supported — use Chrome or Edge.";
    return;
  }
  if (_obMicActive) { if (_obRecognition) _obRecognition.stop(); return; }

  _obRecognition               = new SR();
  _obRecognition.continuous     = true;
  _obRecognition.interimResults = true;
  _obRecognition.lang           = "en-US";
  _obFinalText                  = "";

  _obRecognition.onstart = () => {
    _obMicActive = true;
    document.getElementById("obMicBtn").textContent    = "⏹ Stop";
    document.getElementById("obMicBtn").style.background = "#f85149";
    document.getElementById("obMicBtn").style.color    = "#fff";
    document.getElementById("obMicBadge").style.display  = "flex";
    document.getElementById("obLivePreview").style.display = "block";
    document.getElementById("obVgResult").style.display  = "none";
    document.getElementById("obTranscript").value        = "";
  };

  _obRecognition.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      event.results[i].isFinal ? (_obFinalText += chunk + " ") : (interim = chunk);
    }
    document.getElementById("obLiveText").innerHTML =
      (_obFinalText ? `<span style="color:#e6edf3">${_obFinalText}</span>` : "") +
      (interim     ? `<span style="color:#8b949e;font-style:italic">${interim}</span>` : "");
  };

  _obRecognition.onend = () => {
    _obMicActive = false;
    document.getElementById("obMicBtn").textContent    = "🎙 Speak";
    document.getElementById("obMicBtn").style.background = "";
    document.getElementById("obMicBtn").style.color    = "#f85149";
    document.getElementById("obMicBadge").style.display  = "none";
    document.getElementById("obLivePreview").style.display = "none";
    const text = _obFinalText.trim();
    if (text) {
      document.getElementById("obTranscript").value = text;
      setTimeout(() => testObVoiceGuard(), 300);
    }
  };

  _obRecognition.onerror = (event) => {
    document.getElementById("obVgStatus").textContent = "Mic error: " + event.error;
    _obMicActive = false;
  };

  _obRecognition.start();
}

// ── Boot ──────────────────────────────────────────────────────────────────────
renderDeptList();
