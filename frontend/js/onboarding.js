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
  window.location.href = IS_TRIAL ? "/" : "/";
}

// Pre-fill known trial fields on page load
document.addEventListener("DOMContentLoaded", () => {
  if (!IS_TRIAL) return;

  // Pre-fill Screen 1 fields so validation passes when Review is rendered
  const companyEl = document.getElementById("companyName");
  const budgetEl  = document.getElementById("totalBudget");
  const trialCompany = localStorage.getItem("cp_trial_company") || (TRIAL_NAME ? TRIAL_NAME + "'s Company" : "My Company");
  if (companyEl) companyEl.value = trialCompany;
  if (budgetEl && !budgetEl.value) budgetEl.value = "550"; // matches trial default dept caps
  if (TRIAL_PRV) selectProvider(TRIAL_PRV);

  // Skip Screen 1 — go straight to Departments
  goToScreen(2);
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
  { name: "Sales",       cap: 0 },
  { name: "Support",     cap: 0 },
  { name: "Engineering", cap: 0 },
  { name: "Marketing",   cap: 0 },
  { name: "Operations",  cap: 0 },
];

// Trial users get curated defaults with sensible caps already filled in
const trialDefaultDepartments = [
  { name: "Sales",       cap: 100 },
  { name: "Support",     cap: 100 },
  { name: "Engineering", cap: 200 },
  { name: "Marketing",   cap: 50  },
  { name: "Operations",  cap: 100 },
];

let departments = (IS_TRIAL ? trialDefaultDepartments : defaultDepartments).map(d => ({ ...d }));

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
  const cfg = OB_PLATFORMS[platform];
  document.getElementById("dashboardBtn").textContent = `Connect ${cfg?.label || platform} →`;
}

function goToPlatformScreen() {
  goToScreen(5);
  if (selectedLaunchPlatform) {
    // Hide tile grid — platform already chosen on Screen 4
    document.querySelectorAll("#screen-5 .ob-platform-group").forEach(group => { group.style.display = "none"; });
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
  document.querySelectorAll("#screen-5 .ob-platform-group").forEach(group => { group.style.display = ""; });
  document.getElementById("obPlatConfig").style.display  = "none";
  document.getElementById("obPlatBackOnly").style.display = "";
  document.getElementById("obPlatOutput").style.display  = "none";
  const selectedSummary = document.getElementById("obSelectedPlatformSummary");
  if (selectedSummary) selectedSummary.style.display = "none";
  selectedLaunchPlatform = null;
  obSelectedPlatform = null;
  _obLastPlatform = null;
}

// ── Platform Integration (Screen 5) ──────────────────────────────────────────

const CostPilot_URL = "https://fage-engine-21cb49fe4806.herokuapp.com";

const OB_PLATFORMS = {
  salesforce: { label: "Salesforce",    kind: "business", objects: ["Case","Lead","Opportunity","Contact","Account","Task"],    agentDefault: "SF-CaseBot"    },
  servicenow: { label: "ServiceNow",    kind: "business", objects: ["incident","sc_request","problem","change_request","task"], agentDefault: "SN-IncidentBot" },
  hubspot:    { label: "HubSpot",       kind: "business", objects: ["contacts","deals","tickets","companies","tasks"],          agentDefault: "HS-TicketBot"  },
  dynamics:   { label: "Dynamics 365",  kind: "business", objects: ["incident","lead","opportunity","contact","account"],       agentDefault: "D365-CaseBot"  },
  zendesk:    { label: "Zendesk",       kind: "business", objects: ["ticket","user","organization","request"],                  agentDefault: "ZD-TicketBot"  },
  python:     { label: "Python",        kind: "code",     objects: ["function","api_route","worker","script"],                  agentDefault: "CostPilot-Python" },
  nodejs:     { label: "Node.js",       kind: "code",     objects: ["function","api_route","worker","script"],                  agentDefault: "CostPilot-Node" },
  java:       { label: "Java",          kind: "code",     objects: ["service","controller","worker","job"],                     agentDefault: "CostPilot-Java" },
  ruby:       { label: "Ruby",          kind: "code",     objects: ["service","controller","worker","job"],                     agentDefault: "CostPilot-Ruby" },
  rest:       { label: "REST / cURL",   kind: "code",     objects: ["request","webhook","worker","script"],                     agentDefault: "CostPilot-API" },
};

let obSelectedPlatform = null;
let _obLastPlatform = null;

const OB_PLATFORM_COPY = {
  salesforce: { objectLabel: "Salesforce Object API Name", fieldsLabel: "Salesforce Fields Routed Through CostPilot", fieldHint: "Prompt label · Salesforce field API name, standard or custom", fieldHeader: "Salesforce Field API Name" },
  servicenow: { objectLabel: "ServiceNow Table Name", fieldsLabel: "ServiceNow Fields Routed Through CostPilot", fieldHint: "Prompt label · ServiceNow column sys_name", fieldHeader: "ServiceNow Column" },
  hubspot:    { objectLabel: "HubSpot Object Type", fieldsLabel: "HubSpot Properties Routed Through CostPilot", fieldHint: "Prompt label · HubSpot property internal name", fieldHeader: "HubSpot Property" },
  dynamics:   { objectLabel: "Dynamics Table Name", fieldsLabel: "Dynamics Fields Routed Through CostPilot", fieldHint: "Prompt label · Dataverse field schema/logical name", fieldHeader: "Dynamics Field" },
  zendesk:    { objectLabel: "Zendesk Record Type", fieldsLabel: "Zendesk Fields Routed Through CostPilot", fieldHint: "Prompt label · Zendesk field name or custom field identifier", fieldHeader: "Zendesk Field" },
  code:       { objectLabel: "Code Context", fieldsLabel: "Data Routed Through CostPilot", fieldHint: "Prompt label · variable, JSON key, or request property from your code", fieldHeader: "Input Key" },
};

function selectObPlatform(platform) {
  obSelectedPlatform = platform;
  Object.keys(OB_PLATFORMS).forEach(p => {
    const el = document.getElementById("ob-plat-" + p);
    if (el) el.classList.toggle("selected", p === platform);
  });

  const cfg = OB_PLATFORMS[platform];
  const isCode = cfg.kind === "code";
  const copy = OB_PLATFORM_COPY[platform] || (isCode ? OB_PLATFORM_COPY.code : OB_PLATFORM_COPY.code);
  const objectLabel = document.getElementById("obObjectLabel");
  const fieldsLabel = document.getElementById("obFieldsLabel");
  const fieldsLabelHint = document.getElementById("obFieldsLabelHint");
  const selectedSummary = document.getElementById("obSelectedPlatformSummary");
  const selectedName = document.getElementById("obSelectedPlatformName");
  document.querySelectorAll("#screen-5 .ob-platform-group").forEach(group => { group.style.display = "none"; });
  if (selectedSummary) selectedSummary.style.display = "flex";
  if (selectedName) selectedName.textContent = cfg.label;
  if (objectLabel) objectLabel.textContent = copy.objectLabel;
  if (fieldsLabel) fieldsLabel.firstChild.textContent = copy.fieldsLabel;
  if (fieldsLabelHint) fieldsLabelHint.textContent = copy.fieldHint;

  const objInput = document.getElementById("obPlatObject");
  const objOptions = document.getElementById("obPlatObjectOptions");
  if (objOptions) objOptions.innerHTML = cfg.objects.map(o => `<option value="${o}"></option>`).join("");
  if (objInput && (platform !== _obLastPlatform || !objInput.value)) {
    objInput.value = cfg.objects[0] || "";
  }
  _obLastPlatform = platform;
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

  // Set default fields and hint for this platform
  _initObFields(platform);
}

// ── Field entry management ────────────────────────────────────────────────────

const OB_FIELD_DEFAULTS = {
  salesforce: {
    hint: "Use exact Salesforce API names. Standard fields look like Subject or Description. Custom objects and fields usually end in __c.",
    fields: [
      { label: "Subject",     name: "Subject"      },
      { label: "Description", name: "Description"  },
    ]
  },
  servicenow: {
    hint: "Use the column sys_name from the table schema (e.g. u_contract_text). Custom fields start with u_.",
    fields: [
      { label: "Short Description", name: "short_description" },
      { label: "Description",       name: "description"       },
    ]
  },
  hubspot: {
    hint: "Use the property internal name from HubSpot (e.g. hs_note_body). Find it in Settings → Properties.",
    fields: [
      { label: "Subject",     name: "subject"   },
      { label: "Description", name: "hs_note_body" },
    ]
  },
  dynamics: {
    hint: "Use the field schema name from Dynamics (e.g. new_contracttext). Custom fields use your publisher prefix.",
    fields: [
      { label: "Title",       name: "title"       },
      { label: "Description", name: "description" },
    ]
  },
  zendesk: {
    hint: "Use Zendesk field names (e.g. subject, description, comment). Custom fields use ID numbers.",
    fields: [
      { label: "Subject",     name: "subject"     },
      { label: "Description", name: "description" },
    ]
  },
  custom: {
    hint: "Use variable or dict key names from your code (e.g. contract_text, vendor_name).",
    fields: [
      { label: "Content",     name: "content" },
      { label: "Customer",    name: "customer_name" },
    ]
  },
};
["python","nodejs","java","ruby","rest"].forEach(p => OB_FIELD_DEFAULTS[p] = OB_FIELD_DEFAULTS.custom);

function _initObFields(platform) {
  const defaults = (OB_FIELD_DEFAULTS[platform] || OB_FIELD_DEFAULTS.custom);
  const hint  = document.getElementById("obFieldsHint");
  if (hint) hint.textContent = defaults.hint;
  _renderObFields(defaults.fields.map(f => ({ ...f })));
}

let _obFieldData = [];

function _renderObFields(fields) {
  _obFieldData = fields;
  const list = document.getElementById("obFieldsList");
  if (!list) return;
  list.innerHTML = fields.map((f, i) => `
    <div style="display:flex;gap:8px;align-items:center">
      <input type="text" value="${_obEsc(f.label)}" placeholder="Label (e.g. Contract Body)"
        onchange="_obFieldData[${i}].label=this.value"
        style="flex:1;background:var(--bg-base,#0d1117);border:1px solid var(--border,#30363d);
               border-radius:6px;padding:7px 10px;color:var(--text-primary,#e6edf3);font-size:12px" />
      <span style="color:var(--text-muted,#8b949e);font-size:12px;flex-shrink:0">→</span>
      <input type="text" value="${_obEsc(f.name)}" placeholder="Field name (e.g. Contract_Text__c)"
        onchange="_obFieldData[${i}].name=this.value"
        style="flex:2;background:var(--bg-base,#0d1117);border:1px solid var(--border,#30363d);
               border-radius:6px;padding:7px 10px;color:var(--text-primary,#e6edf3);
               font-size:12px;font-family:monospace" />
      <button type="button" onclick="removeObField(${i})"
        style="background:none;border:none;color:var(--text-muted,#8b949e);cursor:pointer;font-size:16px;padding:0 4px;line-height:1">×</button>
    </div>`).join("");
}

function addObField() {
  _syncObFieldDataFromDom(true);
  _obFieldData.push({ label: "", name: "" });
  _renderObFields(_obFieldData);
  // Focus the new label input
  const inputs = document.querySelectorAll("#obFieldsList input");
  if (inputs.length) inputs[inputs.length - 2].focus();
}

function removeObField(i) {
  _syncObFieldDataFromDom(true);
  if (_obFieldData.length <= 1) return; // keep at least one
  _obFieldData.splice(i, 1);
  _renderObFields(_obFieldData);
}

function _syncObFieldDataFromDom(keepEmpty = false) {
  const inputs = document.querySelectorAll("#obFieldsList input");
  const result = [];
  for (let i = 0; i < inputs.length - 1; i += 2) {
    const label = (inputs[i]?.value || "").trim();
    const name  = (inputs[i+1]?.value || "").trim();
    if (keepEmpty || label || name) result.push({ label: label || name, name });
  }
  _obFieldData = result;
  return result;
}

function getObFields() {
  // Sync from DOM before reading (handles active inputs that have not blurred yet)
  const result = _syncObFieldDataFromDom(false).filter(f => f.name.trim());
  return result.length ? result : _obFieldData.filter(f => f.name.trim());
}

function _obEsc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function _codeStr(s) {
  return String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function _doubleCodeStr(s) {
  return String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n");
}

function _jsonStr(s) {
  return JSON.stringify(String(s));
}

function _safeVar(name, fallback = "field") {
  const cleaned = String(name || "")
    .replace(/__c$/i, "")
    .replace(/[^a-zA-Z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
  const safe = cleaned || fallback;
  return /^[a-zA-Z_]/.test(safe) ? safe : `${fallback}_${safe}`;
}

function _apexVarName(field, i) {
  const base = _safeVar(field.name || field.label || "", `field${i + 1}`)
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!base || base === "field") return "field" + (i + 1);
  return /^[a-zA-Z]/.test(base) ? base : "field" + (i + 1);
}

function _apexVarNames(fields) {
  const seen = {};
  return fields.map((field, i) => {
    const base = _apexVarName(field, i);
    seen[base] = (seen[base] || 0) + 1;
    return seen[base] === 1 ? base : `${base}${seen[base]}`;
  });
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

function _platformMappingHtml(platform, obj, fields, extraRows = "") {
  const cfg = OB_PLATFORMS[platform] || {};
  const copy = OB_PLATFORM_COPY[platform] || (cfg.kind === "code" ? OB_PLATFORM_COPY.code : OB_PLATFORM_COPY.code);
  const label = cfg.label || platform;
  const mappingRows = fields.map(f =>
    `<div class="ob-field-row"><span>${_obEsc(f.label || f.name)}</span><span class="mono">${_obEsc(f.name)}</span><span>Prompt payload</span><span>Routed to CostPilot</span></div>`
  ).join("");
  return `<div class="ob-code-section" style="margin-top:20px">
    <div class="ob-code-header">
      <span class="ob-code-label">${_obEsc(label)} Mapping</span>
      <span class="ob-code-hint">No model-provider key is collected here. This step only maps source data into CostPilot.</span>
    </div>
    <div class="ob-field-table">
      <div class="ob-field-row ob-field-row-header"><span>Prompt Label</span><span>${_obEsc(copy.fieldHeader)}</span><span>Used As</span><span>Behavior</span></div>
      <div class="ob-field-row"><span>Source</span><span class="mono">${_obEsc(obj)}</span><span>Trigger context</span><span>Starts route</span></div>
      ${mappingRows}
      ${extraRows}
    </div>
  </div>`;
}

// Build prompt string for Apex (Java-style string concat)
function _apexPrompt(fields, varForField) {
  if (!fields.length) return "''";
  return fields.map((f, i) => {
    const label = f.label ? `'${_codeStr(f.label)}:\\\\n' + ` : '';
    const value = `String.valueOf(${varForField(f, i)})`;
    const join  = i < fields.length - 1 ? ` + '\\\\n\\\\n' +\n        ` : '';
    return `${label}${value}${join}`;
  }).join("");
}

// Build prompt string for JS/Python/etc
function _jsPrompt(fields, recordVar, accessor = (v,f) => `${v}.${f}`) {
  if (!fields.length) return "''";
  return fields.map(f => {
    const label = f.label ? `'${_codeStr(f.label)}:\\\\n' + ` : '';
    return `${label}${accessor(recordVar, f.name)}`;
  }).join(" + '\\\\n\\\\n' + ");
}

function _pythonPrompt(fields) {
  return fields.map(f => {
    const variable = _safeVar(f.name);
    return `${_codeStr(f.label || f.name)}:\\n{${variable}}`;
  }).join("\\n\\n");
}

function _plainPromptTemplate(fields, tokenForField) {
  return fields.map(f => `${f.label || f.name}:\\n${tokenForField(f)}`).join("\\n\\n");
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
  const obj    = document.getElementById("obPlatObject").value.trim();
  const dept   = document.getElementById("obPlatDept").value;
  const agent  = document.getElementById("obPlatAgent").value.trim() || OB_PLATFORMS[obSelectedPlatform].agentDefault;
  const fields = getObFields();
  if (!obj) { err.textContent = "Enter an object or record type."; return; }
  if (!fields.length) { err.textContent = "Add at least one field or data input."; return; }
  if (obSelectedPlatform === "salesforce") {
    const badObject = !_isSalesforceApiName(obj);
    const badField = fields.find(f => !_isSalesforceApiName(f.name));
    if (badObject) { err.textContent = "Enter a valid Salesforce object API name, like Case or Custom_Request__c."; return; }
    if (badField) { err.textContent = `Check the Salesforce field API name: ${badField.name}`; return; }
  }
  const fns    = {
    salesforce:_genSalesforce,
    servicenow:_genServiceNow,
    hubspot:_genHubSpot,
    dynamics:_genDynamics,
    zendesk:_genZendesk,
    python:_genPython,
    nodejs:_genNode,
    java:_genJava,
    ruby:_genRuby,
    rest:_genRest,
  };
  const html   = (fns[obSelectedPlatform] || _genRest)(obj, dept, agent, fields);
  const out   = document.getElementById("obPlatOutput");
  out.innerHTML = html;
  out.style.display = "block";
  out.scrollIntoView({ behavior: "smooth" });
}

function _isSalesforceApiName(name) {
  return /^[A-Za-z][A-Za-z0-9_]*(?:__c|__mdt|__e|__x)?$/.test(String(name || ""));
}

// ── Code generators ───────────────────────────────────────────────────────────

function _genSalesforce(obj, dept, agent, fields) {
  const sfFields = fields && fields.length ? fields : [{label:"Subject",name:"Subject"},{label:"Description",name:"Description"}];
  const apexVars = _apexVarNames(sfFields);
  const requestVars = sfFields.map((f, i) =>
    `        @InvocableVariable(required=${i === 0 ? "true " : "false"} label='${_codeStr(f.label || f.name)} (${_codeStr(f.name)})') public String ${apexVars[i]};`
  ).join("\n");
  const reqPrompt = _apexPrompt(sfFields, (f, i) => `req.${apexVars[i]}`);
  const mappingRows = sfFields.map((f, i) =>
    `<div class="ob-field-row"><span>${_obEsc(f.label || f.name)}</span><span class="mono">${_obEsc(f.name)}</span><span class="mono">${_obEsc(apexVars[i])}</span><span>Routed to CostPilot</span></div>`
  ).join("");
  const mappingHtml = `<div class="ob-field-table">
    <div class="ob-field-row ob-field-row-header"><span>Prompt Label</span><span>Salesforce Field API Name</span><span>Apex Input</span><span>Behavior</span></div>
    <div class="ob-field-row"><span>Source Object</span><span class="mono">${_obEsc(obj)}</span><span class="mono">recordId</span><span>Record-triggered Flow</span></div>
    ${mappingRows}
  </div>`;

  // ── Trial version: proxy endpoint, no custom fields required ─────────────
  if (IS_TRIAL) {
    const proxyEndpoint = TRIAL_PROXY + "/chat/completions";
    const isOpenAI      = (TRIAL_PRV || "openai") === "openai";
    const modelDefault  = isOpenAI ? "gpt-4o" : "claude-sonnet-4-6";
    const apex =
`public class CostPilotCallout {
    // Pre-filled — no provider credential needed in Salesforce
    private static final String ENDPOINT = '${proxyEndpoint}';
    private static final String CP_KEY   = '${TRIAL_SK}';

    @InvocableMethod(label='Send to CostPilot')
    public static void sendToCostPilot(List<CostPilotRequest> requests) {
        if (System.isFuture() || System.isBatch()) return;
        CostPilotRequest req = requests[0];
        String prompt = ${reqPrompt};
        sendAsync(prompt, req.department);
    }

    @future(callout=true)
    public static void sendAsync(String prompt, String department) {
        Http http = new Http();
        HttpRequest httpReq = new HttpRequest();
        httpReq.setEndpoint(ENDPOINT);
        httpReq.setMethod('POST');
        httpReq.setHeader('Content-Type',    'application/json');
        httpReq.setHeader('X-CostPilot-Key', CP_KEY);
        httpReq.setHeader('X-Department',    department);
        httpReq.setHeader('X-Platform',      'salesforce');
        httpReq.setBody(JSON.serialize(new Map<String, Object>{
            'model'    => '${modelDefault}',
            'messages' => new List<Object>{
                new Map<String, Object>{ 'role' => 'user', 'content' => prompt }
            }
        }));
        httpReq.setTimeout(30000);
        HttpResponse res = http.send(httpReq);

        // ── Write back to record fields (auto-populates once fields are created)
        if (res.getStatusCode() == 200) {
            try {
                Map<String,Object> responseMap = (Map<String,Object>) JSON.deserializeUntyped(res.getBody());
                List<Object> choices = (List<Object>) responseMap.get('choices');
                if (choices != null && !choices.isEmpty()) {
                    Map<String,Object> msg = (Map<String,Object>)((Map<String,Object>)choices[0]).get('message');
                    String aiResponse = (String) msg.get('content');
                    System.debug('CostPilot AI Response: ' + aiResponse);
                    // Uncomment once fields are created on your object:
                    // SObject rec = department.getSObjectType().newSObject();
                    // rec.put('CostPilot_AI_Response__c', aiResponse);
                    // rec.put('CostPilot_Model_Used__c',  res.getHeader('X-CostPilot-Tier'));
                    // rec.put('CostPilot_Routing__c',     res.getHeader('X-CostPilot-Routing'));
                    // rec.put('CostPilot_Cost_USD__c',    Decimal.valueOf(res.getHeader('X-CostPilot-Cost')));
                }
            } catch(Exception e) {
                System.debug('CostPilot field write skipped: ' + e.getMessage());
            }
        }
    }

    public class CostPilotRequest {
        @InvocableVariable(required=true  label='Department')         public String department;
${requestVars}
    }
}`;

    const flowHtml = `<div class="ob-flow-steps">
      <div class="ob-flow-step"><span class="ob-flow-num">1</span>
        <div><strong>Developer Console → File → New → Apex Class</strong><br/>Name it <code>CostPilotCallout</code>, paste the class above exactly as shown. Save. No keys to fill in.</div></div>
      <div class="ob-flow-step"><span class="ob-flow-num">2</span>
        <div><strong>Setup → Remote Site Settings → New</strong><br/>Name: <code>CostPilot</code> · URL: <code>https://fage-engine-21cb49fe4806.herokuapp.com</code> · Active: ✓<br/><em style="color:var(--text-muted,#8b949e);font-size:11px">Required by Salesforce for any external HTTP callout — this is the only manual setup step.</em></div></div>
      <div class="ob-flow-step"><span class="ob-flow-num">3</span>
        <div><strong>Setup → Flows → New Flow → Record-Triggered</strong><br/>Object: <strong>${_obEsc(obj)}</strong> · Trigger: Created or Updated<br/>Add Action → Apex → Send to CostPilot<br/>Map: ${sfFields.map((f, i) => `${_obEsc(f.name)} → ${_obEsc(apexVars[i])}`).join(" · ")} · Department → <strong>"${_obEsc(dept)}"</strong></div></div>
      <div class="ob-flow-step"><span class="ob-flow-num">4</span>
        <div><strong>Save &amp; Activate</strong> — next time a ${_obEsc(obj)} is created or updated, CostPilot routes the call automatically. Your first call appears on your dashboard within seconds.</div></div>
    </div>`;

    return `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Salesforce Mapping</span><span class="ob-code-hint">These are the exact object and field API names this setup will route.</span></div>${mappingHtml}</div>`
      + _obCodeSection("Apex Class — paste into Developer Console", "No provider credential needed; map each Flow input to the field names you configured", apex)
      + `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Setup Steps</span></div>${flowHtml}</div>`
      + _obBanner("salesforce", obj, dept, agent) + _obActions();
  }

  // ── Full version: internal routing + custom fields ────────────────────────
  const apex =
`public class CostPilotCallout {
    @InvocableMethod(label='Send to CostPilot')
    public static void sendToCostPilot(List<CostPilotRequest> requests) {
        if (System.isFuture() || System.isBatch()) return;
        CostPilotRequest req = requests[0];
        String payload = ${reqPrompt};
        String agent = String.isBlank(req.agentName) ? '${_codeStr(agent)}' : req.agentName;
        sendAsync(req.recordId, payload, req.department, agent);
    }

    @future(callout=true)
    public static void sendAsync(String recordId, String payload, String department, String agentName) {
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
        System.debug('CostPilot request endpoint: ' + httpReq.getEndpoint());
        System.debug('CostPilot request department=' + department + ', agent=' + agentName);
        HttpResponse res = http.send(httpReq);
        System.debug('CostPilot response status=' + res.getStatusCode() + ', body=' + res.getBody());
        if (res.getStatusCode() < 200 || res.getStatusCode() >= 300) {
            throw new CalloutException('CostPilot callout failed: HTTP ' + res.getStatusCode() + ' — ' + res.getBody());
        }
        if (res.getStatusCode() == 200 && recordId != null) {
            Map<String,Object> r = (Map<String,Object>) JSON.deserializeUntyped(res.getBody());
            SObjectType objType = Id.valueOf(recordId).getSObjectType();
            Map<String, Schema.SObjectField> fieldMap = objType.getDescribe().fields.getMap();
            SObject rec = objType.newSObject(recordId);
            Boolean hasUpdates = false;

            if (fieldMap.containsKey('CostPilot_AI_Response__c')) {
                rec.put('CostPilot_AI_Response__c', (String) r.get('simulated_response'));
                hasUpdates = true;
            }
            if (fieldMap.containsKey('CostPilot_Model_Used__c')) {
                rec.put('CostPilot_Model_Used__c', (String) r.get('model_name'));
                hasUpdates = true;
            }
            if (fieldMap.containsKey('CostPilot_Routing_Decision__c')) {
                rec.put('CostPilot_Routing_Decision__c', (String) r.get('routing_decision'));
                hasUpdates = true;
            }
            if (fieldMap.containsKey('CostPilot_Cost_USD__c') && r.get('cost_usd') != null) {
                rec.put('CostPilot_Cost_USD__c', Decimal.valueOf(String.valueOf(r.get('cost_usd'))));
                hasUpdates = true;
            }
            if (hasUpdates) update rec;
        }
    }

    public class CostPilotRequest {
        @InvocableVariable(required=true  label='Record ID')          public String recordId;
        @InvocableVariable(required=true  label='Department')         public String department;
        @InvocableVariable(required=false label='Agent Name')         public String agentName;
${requestVars}
    }
}`;

  const flowHtml = `<div class="ob-flow-steps">
    <div class="ob-flow-step"><span class="ob-flow-num">1</span>
      <div><strong>Setup → Flows → New Flow</strong><br/>Type: <em>Record-Triggered</em> · Object: <strong>${_obEsc(obj)}</strong> · Trigger: <em>Created or updated</em> · Optimize for: <em>Actions and Related Records</em></div></div>
    <div class="ob-flow-step"><span class="ob-flow-num">2</span>
      <div><strong>Add Action → Apex → Send to CostPilot</strong><br/>Map: Record ID · ${sfFields.map((f, i) => `${_obEsc(f.name)} → ${_obEsc(apexVars[i])}`).join(" · ")} · Department → ${_obEsc(dept)} · Agent Name → ${_obEsc(agent)}</div></div>
    <div class="ob-flow-step"><span class="ob-flow-num">3</span>
      <div><strong>Save &amp; Activate</strong><br/>If CostPilot does not show an event, check Setup → Apex Jobs and Setup → Paused and Failed Flow Interviews.</div></div>
  </div>`;

  const testApex =
`// Developer Console → Debug → Open Execute Anonymous Window
// Replace the sample record ID with a real ${obj} ID from your org.
CostPilotCallout.CostPilotRequest req = new CostPilotCallout.CostPilotRequest();
req.recordId = 'REPLACE_WITH_${obj.toUpperCase()}_ID';
req.department = '${_codeStr(dept)}';
req.agentName = '${_codeStr(agent)}';
${sfFields.map((f, i) => `req.${apexVars[i]} = 'Test value for ${_codeStr(f.label || f.name)}';`).join("\n")}
CostPilotCallout.sendToCostPilot(new List<CostPilotCallout.CostPilotRequest>{ req });`;

  const fieldHtml = `<div class="ob-field-table">
    <div class="ob-field-row ob-field-row-header"><span>Label</span><span>API Name</span><span>Type</span><span>Settings</span></div>
    <div class="ob-field-row"><span>CostPilot AI Response</span><span class="mono">CostPilot_AI_Response__c</span><span>Long Text</span><span>32,768 chars</span></div>
    <div class="ob-field-row"><span>CostPilot Model Used</span><span class="mono">CostPilot_Model_Used__c</span><span>Text</span><span>255 chars</span></div>
    <div class="ob-field-row"><span>CostPilot Routing</span><span class="mono">CostPilot_Routing_Decision__c</span><span>Text</span><span>50 chars</span></div>
    <div class="ob-field-row"><span>CostPilot Cost USD</span><span class="mono">CostPilot_Cost_USD__c</span><span>Currency</span><span>12,6</span></div>
  </div>`;

  return `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Salesforce Mapping</span><span class="ob-code-hint">These are the exact object and field API names this setup will route.</span></div>${mappingHtml}</div>`
    + _obCodeSection("Step 1 — Apex Class", "Developer Console → File → Open → CostPilotCallout → Replace all → Save", apex)
    + `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Step 2 — Salesforce Flow</span></div>${flowHtml}</div>`
    + `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Step 3 — Custom Fields on ${_obEsc(obj)}</span><span class="ob-code-hint">Setup → Object Manager → ${_obEsc(obj)} → Fields &amp; Relationships → New</span></div>${fieldHtml}</div>`
    + _obCodeSection("Debug Test — Execute Anonymous", "Runs the Apex action without waiting on your Flow trigger", testApex)
    + _obBanner("salesforce", obj, dept, agent) + _obActions();
}

function _genServiceNow(obj, dept, agent, fields) {
  const prompt = _jsPrompt(fields, "current", (v, f) => `(${v}.getValue('${_codeStr(f)}') || '')`);
  const code =
`// ServiceNow Business Rule — Table: ${obj}
// When: after | Insert: true | Update: true

(function executeRule(current, previous) {
    var prompt = ${prompt};

    var rm = new sn_ws.RESTMessageV2();
    rm.setEndpoint('${CostPilot_URL}/api/route');
    rm.setHttpMethod('POST');
    rm.setRequestHeader('Content-Type', 'application/json');
    rm.setRequestBody(JSON.stringify({
        text:            prompt,
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

  return _platformMappingHtml("servicenow", obj, fields)
    + _obCodeSection(
      "Business Rule Script",
      "System Definition → Business Rules → New · Table: " + obj + " · When: after · Insert + Update",
      code
    ) + _obBanner("servicenow", obj, dept, agent) + _obActions();
}

function _genHubSpot(obj, dept, agent, fields) {
  const prompt = _jsPrompt(fields, "event.inputFields", (v, f) => `(${v}['${_codeStr(f)}'] || '')`);
  const code =
`// HubSpot Custom Code Action (Node.js)
// Operations Hub → Workflows → Add Action → Custom Code
const axios = require('axios');

exports.main = async (event, callback) => {
  const text = ${prompt};
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

  return _platformMappingHtml("hubspot", obj, fields)
    + _obCodeSection(
    "Custom Code Action",
    "Operations Hub → Workflows → Add Action → Custom Code → Paste",
    code
  ) + _obBanner("hubspot", obj, dept, agent) + _obActions();
}

function _genDynamics(obj, dept, agent, fields) {
  const dynPrompt = _plainPromptTemplate(fields, f => `@{triggerOutputs()?['body/${f.name}']}`);
  const code =
`// Power Automate — HTTP Action Configuration
// Trigger: When a row is added, modified or deleted · Table: ${obj}

Method:  POST
URI:     ${CostPilot_URL}/api/route
Headers: { "Content-Type": "application/json" }
Body:
{
  "text":            ${_jsonStr(dynPrompt)},
  "department":      "${dept}",
  "auto_prune":      true,
  "agent_name":      "${agent}",
  "source_platform": "Dynamics365"
}

// After HTTP — add "Update a row" action:
// fage_ai_response:      @{body('HTTP')?['simulated_response']}
// fage_model_used:       @{body('HTTP')?['model_name']}
// fage_routing_decision: @{body('HTTP')?['routing_decision']}`;

  return _platformMappingHtml("dynamics", obj, fields)
    + _obCodeSection(
    "Power Automate HTTP Action",
    "Power Automate → New Flow → Automated → Dataverse trigger → Add HTTP action",
    code
  ) + _obBanner("dynamics", obj, dept, agent) + _obActions();
}

function _genZendesk(obj, dept, agent, fields) {
  const prompt = _jsPrompt(fields, "event.payload", (v, f) =>
    `((${v}.ticket && ${v}.ticket['${_codeStr(f)}']) || ${v}['${_codeStr(f)}'] || '')`
  );
  const code =
`// Zendesk Sunshine Function (Node.js 18)
// Admin Center → Apps and integrations → Sunshine Functions → Create
const fetch = require('node-fetch');

module.exports = async (event) => {
  const body     = ${prompt};

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

  console.log('[CostPilot]', data.simulated_response);
  return { status: 200, model: data.model_name, routing: data.routing_decision };
};`;

  return _platformMappingHtml("zendesk", obj, fields)
    + _obCodeSection(
    "Sunshine Function",
    "Admin Center → Apps and integrations → Sunshine Functions → Create → Node.js 18 · no Zendesk token required for first route",
    code
  ) + _obBanner("zendesk", obj, dept, agent) + _obActions();
}

function _genPython(obj, dept, agent, fields) {
  const params = fields.map(f => `${_safeVar(f.name)}: str`).join(", ");
  const prompt = _pythonPrompt(fields);
  const python =
`import requests

CostPilot_URL = "${CostPilot_URL}"

def route_to_costpilot(${params}) -> dict:
    prompt = f"""${prompt}"""
    resp = requests.post(CostPilot_URL + "/api/route", json={
        "text":            prompt,
        "department":      "${dept}",
        "auto_prune":      True,
        "agent_name":      "${agent}",
        "source_platform": "Python",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()

# result = route_to_costpilot(${fields.map(f => `${_safeVar(f.name)}="..."`).join(", ")})
# print(result["simulated_response"])`;

  return _platformMappingHtml("python", obj, fields)
    + _obCodeSection("Python", "pip install requests · call this function from your app or workflow", python)
    + _obBanner("python", obj, dept, agent) + _obActions();
}

function _genNode(obj, dept, agent, fields) {
  const prompt = _jsPrompt(fields, "input", (v, f) => `(${v}.${_safeVar(f)} || '')`);
  const exampleObj = fields.map(f => `//   ${_safeVar(f.name)}: '...'`).join(",\n");
  const code =
`// Node.js 18+
const CostPilot_URL = '${CostPilot_URL}';

async function routeToCostPilot(input) {
  const text = ${prompt};

  const res = await fetch(CostPilot_URL + '/api/route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      department: '${dept}',
      auto_prune: true,
      agent_name: '${agent}',
      source_platform: 'Node.js',
    }),
  });

  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// const result = await routeToCostPilot({
${exampleObj}
// });`;

  return _platformMappingHtml("nodejs", obj, fields)
    + _obCodeSection("Node.js", "Uses built-in fetch in Node 18+", code)
    + _obBanner("nodejs", obj, dept, agent) + _obActions();
}

function _genJava(obj, dept, agent, fields) {
  const params = fields.map(f => `String ${_safeVar(f.name)}`).join(", ");
  const textExpr = fields.map((f, i) => {
    const join = i < fields.length - 1 ? ' + "\\n\\n" +\n            ' : "";
    return `"${_doubleCodeStr(f.label || f.name)}:\\n" + ${_safeVar(f.name)}${join}`;
  }).join("");
  const code =
`import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class CostPilotClient {
    private static final String COSTPILOT_URL = "${CostPilot_URL}";
    private static final HttpClient CLIENT = HttpClient.newHttpClient();

    public static String routeToCostPilot(${params}) throws Exception {
        String text = ${textExpr};
        String body = "{"
            + "\\"text\\":\\"" + escapeJson(text) + "\\","
            + "\\"department\\":\\"${_doubleCodeStr(dept)}\\","
            + "\\"auto_prune\\":true,"
            + "\\"agent_name\\":\\"${_doubleCodeStr(agent)}\\","
            + "\\"source_platform\\":\\"Java\\""
            + "}";

        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(COSTPILOT_URL + "/api/route"))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(body))
            .build();

        return CLIENT.send(request, HttpResponse.BodyHandlers.ofString()).body();
    }

    private static String escapeJson(String value) {
        return value == null ? "" : value.replace("\\\\", "\\\\\\\\").replace("\\"", "\\\\\\"").replace("\\n", "\\\\n");
    }
}`;

  return _platformMappingHtml("java", obj, fields)
    + _obCodeSection("Java", "Java 11+ HttpClient", code)
    + _obBanner("java", obj, dept, agent) + _obActions();
}

function _genRuby(obj, dept, agent, fields) {
  const params = fields.map(f => `${_safeVar(f.name)}:`).join(", ");
  const prompt = fields.map(f => `#{${_safeVar(f.name)}}`).map((token, i) =>
    `${_doubleCodeStr(fields[i].label || fields[i].name)}:\\n${token}`
  ).join("\\n\\n");
  const code =
`require "json"
require "net/http"
require "uri"

COSTPILOT_URL = "${CostPilot_URL}"

def route_to_costpilot(${params})
  text = "${prompt}"
  uri = URI("#{COSTPILOT_URL}/api/route")

  req = Net::HTTP::Post.new(uri)
  req["Content-Type"] = "application/json"
  req.body = {
    text: text,
    department: "${_codeStr(dept)}",
    auto_prune: true,
    agent_name: "${_codeStr(agent)}",
    source_platform: "Ruby"
  }.to_json

  Net::HTTP.start(uri.hostname, uri.port, use_ssl: uri.scheme == "https") do |http|
    http.request(req)
  end
end

# response = route_to_costpilot(${fields.map(f => `${_safeVar(f.name)}: "..."`).join(", ")})`;

  return _platformMappingHtml("ruby", obj, fields)
    + _obCodeSection("Ruby", "Uses Net::HTTP from the Ruby standard library", code)
    + _obBanner("ruby", obj, dept, agent) + _obActions();
}

function _genRest(obj, dept, agent, fields) {
  const prompt = _plainPromptTemplate(fields, f => `\${${_safeVar(f.name)}}`);
  const curl =
`curl -X POST ${CostPilot_URL}/api/route \\
  -H "Content-Type: application/json" \\
  -d '{
    "text":            ${_jsonStr(prompt)},
    "department":      "${dept}",
    "auto_prune":      true,
    "agent_name":      "${agent}",
    "source_platform": "REST"
  }'`;

  return _platformMappingHtml("rest", obj, fields)
    + _obCodeSection("REST / cURL", "Replace ${...} placeholders with values from your app or shell", curl)
    + _obBanner("rest", obj, dept, agent) + _obActions();
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
