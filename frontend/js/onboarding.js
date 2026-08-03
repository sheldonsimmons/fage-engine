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
  window.location.href = "/operate.html";
}

// Pre-fill known trial fields and restore the business-first onboarding choices.
document.addEventListener("DOMContentLoaded", () => {
  if (IS_TRIAL) {
    // Legacy setup fields remain available to existing helper functions, but
    // they no longer appear in the primary onboarding experience.
    const companyEl = document.getElementById("companyName");
    const budgetEl  = document.getElementById("totalBudget");
    const trialCompany = localStorage.getItem("cp_trial_company") || (TRIAL_NAME ? TRIAL_NAME + "'s Company" : "My Company");
    if (companyEl) companyEl.value = trialCompany;
    if (budgetEl && !budgetEl.value) budgetEl.value = "550";
    if (TRIAL_PRV) selectProvider(TRIAL_PRV);
  }
  restoreBusinessContextOnboarding();
  restoreOAuthDiscovery();
  restoreServerOnboardingProgress();
});

const businessFirstState = {
  workType: "",
  source: "",
  sourceLabel: "",
  customerLabel: "",
  built: false,
};

function setUniversalSetupStage(stage) {
  document.querySelectorAll(".ob-universal-stage").forEach(item => {
    const itemStage = Number(item.dataset.stage);
    item.classList.toggle("active", itemStage === stage);
    item.classList.toggle("done", itemStage < stage);
  });
}

function _selectBusinessChoice(containerId, selected) {
  document.querySelectorAll(`#${containerId} .ob-choice`).forEach(button => {
    button.classList.toggle("selected", button === selected);
  });
}

function chooseBusinessWork(button) {
  _selectBusinessChoice("obWorkChoices", button);
  businessFirstState.workType = button.dataset.value;
  businessFirstState.built = false;
  document.getElementById("obCustomWorkWrap").hidden = button.dataset.value !== "custom";
  document.getElementById("obSourceQuestion").hidden = false;
  refreshBusinessTemplatePreview();
  document.getElementById("obSourceQuestion").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function chooseBusinessSource(button) {
  _selectBusinessChoice("obSourceChoices", button);
  businessFirstState.source = button.dataset.value;
  businessFirstState.sourceLabel = button.dataset.label || "";
  businessFirstState.built = false;
  document.getElementById("obCustomSourceWrap").hidden = button.dataset.value !== "custom";
  document.getElementById("obDetailsQuestion").hidden = false;
  setUniversalSetupStage(2);
  refreshBusinessTemplatePreview();
  document.getElementById("obDetailsQuestion").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function chooseBusinessCustomer(button) {
  _selectBusinessChoice("obCustomerChoices", button);
  businessFirstState.customerLabel = button.dataset.value;
  businessFirstState.built = false;
  document.getElementById("obCustomCustomerWrap").hidden = button.dataset.value !== "custom";
  refreshBusinessTemplatePreview();
}

function _businessWorkLabel() {
  if (businessFirstState.workType === "custom") {
    return document.getElementById("obCustomWork").value.trim();
  }
  const labels = {
    project: "Project",
    matter: "Matter",
    engagement: "Engagement",
    case: "Case",
    ticket: "Ticket",
    opportunity: "Opportunity",
    account: "Account",
    customer: "Customer",
  };
  return labels[businessFirstState.workType] || "";
}

function _businessSourceLabel() {
  if (businessFirstState.source === "custom") {
    return document.getElementById("obCustomSource").value.trim();
  }
  return businessFirstState.sourceLabel;
}

function _businessCustomerLabel() {
  if (businessFirstState.customerLabel === "custom") {
    return document.getElementById("obCustomCustomer").value.trim();
  }
  return businessFirstState.customerLabel;
}

function _businessMeasures() {
  return [...document.querySelectorAll(".ob-business-measures input:checked")]
    .map(input => input.value);
}

function _businessTemplateKey() {
  if (businessFirstState.source === "salesforce") return "salesforce_project";
  if (businessFirstState.source === "servicenow") return "servicenow_case";
  return "universal_context";
}

function refreshBusinessTemplatePreview() {
  const workLabel = _businessWorkLabel();
  const sourceLabel = _businessSourceLabel();
  const customerLabel = _businessCustomerLabel();
  const ready = Boolean(workLabel && sourceLabel && customerLabel);
  const button = document.getElementById("obBuildTemplateBtn");
  button.disabled = !ready;
  if (!businessFirstState.built) {
    button.textContent = "Build My Template →";
    document.getElementById("obTemplatePreview").hidden = true;
    return;
  }
  const measures = _businessMeasures();
  document.getElementById("obTemplateTitle").textContent = `${workLabel} Business Context`;
  document.getElementById("obTemplateDescription").textContent =
    `CostPilot will understand ${workLabel.toLowerCase()} activity from ${sourceLabel} using your business language.`;
  const parts = [
    workLabel,
    customerLabel === "None" ? null : customerLabel,
    "User",
    "AI agent",
    ...measures.map(value => value === "profitability" ? "Margin impact" : value.charAt(0).toUpperCase() + value.slice(1)),
  ].filter(Boolean);
  document.getElementById("obTemplateFlow").innerHTML = parts
    .map((part, index) => `${index ? '<span class="ob-flow-arrow">→</span>' : ""}<span>${_obEsc(part)}</span>`)
    .join("");
  document.getElementById("obTemplatePreview").hidden = false;
  button.textContent = businessFirstState.source === "not_sure"
    ? "Finish Setup →"
    : `Connect ${sourceLabel} →`;
}

async function buildBusinessContextTemplate() {
  if (businessFirstState.built) {
    openBusinessContextConnection();
    return;
  }
  const workLabel = _businessWorkLabel();
  const sourceLabel = _businessSourceLabel();
  const customerLabel = _businessCustomerLabel();
  const error = document.getElementById("obBusinessError");
  if (!workLabel || !sourceLabel || !customerLabel) {
    error.textContent = "Complete the visible custom value before building your template.";
    return;
  }
  error.textContent = "";
  const canonicalWorkType = businessFirstState.workType === "custom"
    ? "custom"
    : businessFirstState.workType;
  const templateKey = _businessTemplateKey();
  const templateNames = {
    salesforce_project: "Salesforce Business Context",
    servicenow_case: "ServiceNow Business Context",
    universal_context: `${sourceLabel} Business Context`,
  };
  obBusinessContext = {
    template: templateKey,
    template_name: templateNames[templateKey],
    platform: businessFirstState.source,
    platform_label: sourceLabel,
    work_type: canonicalWorkType,
    work_label: workLabel,
    customer_label: customerLabel,
    measures: _businessMeasures(),
  };
  localStorage.setItem("cp_business_context", JSON.stringify(obBusinessContext));
  try {
    await persistObBusinessContext();
  } catch (persistError) {
    error.textContent = `Your template is saved on this device, but CostPilot could not sync it yet: ${persistError.message}`;
  }
  businessFirstState.built = true;
  refreshBusinessTemplatePreview();
  document.getElementById("obTemplatePreview").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function openBusinessContextConnection() {
  if (businessFirstState.source === "not_sure" || businessFirstState.source === "costpilot") {
    goToDashboard();
    return;
  }
  let platform = businessFirstState.source;
  if (platform === "custom") {
    OB_PLATFORMS.custom = {
      label: _businessSourceLabel(),
      kind: "business",
      objects: ["request", "record", "work_item"],
      agentDefault: "CostPilot Integration",
    };
  }
  document.body.classList.add("ob-connection-mode");
  document.querySelectorAll(".ob-screen").forEach(screen => screen.classList.remove("active"));
  document.getElementById("screen-5").classList.add("active");
  selectObPlatform(platform);
  setUniversalSetupStage(3);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function returnToBusinessContextOnboarding() {
  resetObPlatformScreen();
  document.body.classList.remove("ob-connection-mode");
  document.querySelectorAll(".ob-screen").forEach(screen => screen.classList.remove("active"));
  document.getElementById("screen-1").classList.add("active");
  refreshBusinessTemplatePreview();
  setUniversalSetupStage(businessFirstState.source ? 2 : 1);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function restoreBusinessContextOnboarding() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem("cp_business_context") || "null");
  } catch (_) {
    return;
  }
  if (!saved?.work_label) return;
  const standardWork = ["project", "matter", "engagement", "case", "ticket", "opportunity", "account", "customer"];
  const workValue = standardWork.includes(saved.work_type) ? saved.work_type : "custom";
  const workButton = document.querySelector(`#obWorkChoices [data-value="${workValue}"]`);
  if (workButton) chooseBusinessWork(workButton);
  if (workValue === "custom") document.getElementById("obCustomWork").value = saved.work_label;

  const standardSources = ["salesforce", "servicenow", "hubspot", "dynamics", "zendesk", "costpilot", "not_sure"];
  const sourceValue = standardSources.includes(saved.platform) ? saved.platform : "custom";
  const sourceButton = document.querySelector(`#obSourceChoices [data-value="${sourceValue}"]`);
  if (sourceButton) chooseBusinessSource(sourceButton);
  if (sourceValue === "custom") {
    document.getElementById("obCustomSource").value = saved.platform_label || saved.platform;
  }

  const standardCustomers = ["Customer", "Account", "Client", "Organization", "None"];
  const customerValue = standardCustomers.includes(saved.customer_label) ? saved.customer_label : "custom";
  const customerButton = document.querySelector(`#obCustomerChoices [data-value="${customerValue}"]`);
  if (customerButton) chooseBusinessCustomer(customerButton);
  if (customerValue === "custom") document.getElementById("obCustomCustomer").value = saved.customer_label;

  document.querySelectorAll(".ob-business-measures input").forEach(input => {
    input.checked = (saved.measures || []).includes(input.value);
  });
  refreshBusinessTemplatePreview();
}

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
    if (IS_TRIAL) {
      const validDepartments = departments
        .filter(dept => dept.name.trim())
        .map(dept => ({ name: dept.name.trim(), cap_usd: dept.cap || 0 }));
      const setup = await apiPost("/api/trial/setup-departments", {
        workspace_id: TRIAL_WS,
        secret_key: TRIAL_SK,
        departments: validDepartments,
        platform: selectedLaunchPlatform || obSelectedPlatform || "other",
      });
      localStorage.setItem("cp_setup_complete", setup.setup_complete ? "true" : "false");
      validDepartments.forEach(dept => log(`${dept.name} — $${dept.cap_usd}/mo`, true));
    } else {
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
    // Pre-fill the recommended setup. The user confirms the business context
    // before CostPilot generates platform-specific instructions.
    selectObPlatform(selectedLaunchPlatform);
    // Auto-pick first department from user's setup
    const userDepts = departments.filter(d => d.name.trim());
    if (userDepts.length > 0) {
      document.getElementById("obPlatDept").value = userDepts[0].name;
    }
  }
}

function resetObPlatformScreen() {
  // Restore tile grid visibility for next visit
  document.querySelectorAll("#screen-5 .ob-platform-group").forEach(group => { group.style.display = ""; });
  document.getElementById("obPlatConfig").style.display  = "none";
  document.getElementById("obPlatBackOnly").style.display = "";
  document.getElementById("obPlatOutput").style.display  = "none";
  document.getElementById("obBusinessContextCard").hidden = true;
  const selectedSummary = document.getElementById("obSelectedPlatformSummary");
  if (selectedSummary) selectedSummary.style.display = "none";
  selectedLaunchPlatform = null;
  obSelectedPlatform = null;
  _obLastPlatform = null;
}

// ── Platform Integration (Screen 5) ──────────────────────────────────────────

const CostPilot_URL = "https://fage-engine-21cb49fe4806.herokuapp.com";

const OB_PLATFORMS = {
  salesforce: { label: "Salesforce",    kind: "business", objects: ["CostPilot_Project__c","Case","Opportunity","Lead","Contact","Account","Task"], agentDefault: "Salesforce Agentforce" },
  servicenow: { label: "ServiceNow",    kind: "business", objects: ["sn_customerservice_case","incident","pm_project","sc_request","problem","change_request","task"], agentDefault: "SN-IncidentBot" },
  hubspot:    { label: "HubSpot",       kind: "business", objects: ["contacts","deals","tickets","companies","tasks"],          agentDefault: "HS-TicketBot"  },
  dynamics:   { label: "Dynamics 365",  kind: "business", objects: ["incident","lead","opportunity","contact","account"],       agentDefault: "D365-CaseBot"  },
  zendesk:    { label: "Zendesk",       kind: "business", objects: ["ticket","user","organization","request"],                  agentDefault: "ZD-TicketBot"  },
  python:     { label: "Python",        kind: "code",     objects: ["function","api_route","worker","script"],                  agentDefault: "CostPilot-Python" },
  nodejs:     { label: "Node.js",       kind: "code",     objects: ["function","api_route","worker","script"],                  agentDefault: "CostPilot-Node" },
  java:       { label: "Java",          kind: "code",     objects: ["service","controller","worker","job"],                     agentDefault: "CostPilot-Java" },
  ruby:       { label: "Ruby",          kind: "code",     objects: ["service","controller","worker","job"],                     agentDefault: "CostPilot-Ruby" },
  rest:       { label: "REST / cURL",   kind: "code",     objects: ["request","webhook","worker","script"],                     agentDefault: "CostPilot-API" },
};

const OB_CONNECTOR_PLANS = {
  salesforce: {
    authenticate: "Named Credential",
    install: "Agentforce action or record-triggered Flow",
    identity: "Organization ID · User ID · Salesforce record ID",
  },
  servicenow: {
    authenticate: "OAuth REST Message",
    install: "Flow Designer action or Business Rule",
    identity: "Instance name · User sys_id · Record sys_id",
  },
  hubspot: {
    authenticate: "OAuth or private app",
    install: "Workflow custom code action",
    identity: "Portal ID · User ID · Object ID",
  },
};

let obSalesforceAiMode = localStorage.getItem("cp_salesforce_ai_mode") || "agentforce";

function selectSalesforceAiMode(mode) {
  if (!["agentforce", "automation"].includes(mode)) return;
  obSalesforceAiMode = mode;
  localStorage.setItem("cp_salesforce_ai_mode", mode);
  const generateButton = document.getElementById("obGenerateSetupBtn");
  if (generateButton) {
    generateButton.textContent = "Show Activation Steps →";
  }
  renderSalesforceAiPath("salesforce");
  renderObConnectionPlan("salesforce");
}

function getSalesforceGovernedObjects() {
  const relationshipMapping = _getApprovedRelationshipMapping();
  if (relationshipMapping?.parent_object) {
    return [
      relationshipMapping.parent_object,
      ...(relationshipMapping.children || [])
        .filter(child => child.behavior !== "ignore")
        .map(child => child.object),
    ];
  }
  const selectedObject = document.getElementById("obDiscoveredObject")?.value
    || document.getElementById("obPlatObject")?.value;
  return selectedObject ? [selectedObject] : [];
}

function renderSalesforceAiPath(platform) {
  const section = document.getElementById("obSalesforceAiPath");
  if (!section) return;
  section.hidden = platform !== "salesforce";
  if (platform !== "salesforce") return;

  const detail = document.getElementById("obSalesforceAiPathDetails");
  if (!detail) return;
  detail.innerHTML = `<strong>One universal action: CostPilot Governed AI Work.</strong>
    Add it only to the Agentforce agents and Flows whose AI requests should be governed. Connecting OAuth alone discovers metadata; it does not count ordinary Salesforce activity as AI usage.`;
}

function renderObConnectionPlan(platform) {
  const plan = OB_CONNECTOR_PLANS[platform];
  const container = document.getElementById("obConnectionPlan");
  if (!container) return;
  if (!plan) {
    container.innerHTML = `
      <div class="ob-connection-plan-item"><strong>Authenticate</strong>CostPilot API credential</div>
      <div class="ob-connection-plan-item"><strong>Install</strong>Add the generated request to your application</div>
      <div class="ob-connection-plan-item"><strong>Attribute</strong>Workspace · User · Work record</div>`;
    return;
  }
  const install = platform === "salesforce"
    ? (obSalesforceAiMode === "agentforce"
      ? "Add CostPilot Governed AI Work to Agentforce"
      : "Invoke CostPilot from Flow, Apex, or custom AI")
    : plan.install;
  container.innerHTML = `
    <div class="ob-connection-plan-item"><strong>1 · Authenticate</strong>${_obEsc(plan.authenticate)}</div>
    <div class="ob-connection-plan-item"><strong>2 · Install</strong>${_obEsc(install)}</div>
    <div class="ob-connection-plan-item"><strong>3 · Attribute</strong>${_obEsc(plan.identity)}</div>`;
}

const obStoredDiscoveryConnectionId = Number(localStorage.getItem("cp_discovery_connection_id") || 0);
let obDiscoveryConnectionId = Number.isInteger(obStoredDiscoveryConnectionId) && obStoredDiscoveryConnectionId > 0
  ? obStoredDiscoveryConnectionId
  : null;
let obDiscoveryPlatform = localStorage.getItem("cp_discovery_platform") || null;
let obDiscoveredFields = [];
let obDiscoveredRelationships = [];
let obDiscoveredObjectName = null;
let obSalesforceEntryPoints = { agents: [], flows: [] };

function renderObDiscoveryCard(platform) {
  const card = document.getElementById("obDiscoveryCard");
  if (!card) return;
  const label = OB_PLATFORMS[platform]?.label || platform;
  if (!["salesforce", "servicenow", "hubspot"].includes(platform)) {
    card.innerHTML = "";
    return;
  }
  const available = ["salesforce", "servicenow"].includes(platform);
  obDiscoveryPlatform = platform;
  card.innerHTML = `<div class="ob-discovery-head">
      <div><h3>Find related objects</h3>
      <p>Connect ${_obEsc(label)} so CostPilot can suggest which record is the parent and which related records should roll up to it. CostPilot reads metadata—not customer record contents.</p></div>
      <span class="ob-context-eyebrow">${available ? "Available" : "Adapter next"}</span>
    </div>
    ${platform === "salesforce" ? `<div class="ob-discovery-actions">
      <button type="button" class="ob-btn-primary" onclick="connectSalesforceDiscovery('https://login.salesforce.com')">Connect Salesforce</button>
      <button type="button" class="ob-btn-ghost" onclick="connectSalesforceDiscovery('https://test.salesforce.com')">Use a Sandbox</button>
    </div>` : platform === "servicenow" ? `<div class="ob-field">
      <label class="ob-label" for="obServiceNowInstance">ServiceNow instance URL</label>
      <input class="ob-input" id="obServiceNowInstance" type="url"
             placeholder="https://your-instance.service-now.com"
             autocomplete="url">
      <div class="ob-discovery-actions">
        <button type="button" class="ob-btn-primary" onclick="connectServiceNowDiscovery()">Connect ServiceNow</button>
      </div>
      <div class="ob-field-help">CostPilot reads table and field definitions only. The ServiceNow user still controls access through roles and ACLs.</div>
    </div>` : `<div class="ob-discovery-status">${_obEsc(label)} uses the same registry and mapping format. Its OAuth discovery adapter is not available yet.</div>`}
    <div id="obDiscoveryStatus"></div>
    <div id="obDiscoveryResults"></div>`;
}

async function connectSalesforceDiscovery(authBaseUrl) {
  return connectPlatformDiscovery("salesforce", authBaseUrl);
}

async function connectServiceNowDiscovery() {
  const instanceInput = document.getElementById("obServiceNowInstance");
  const instanceUrl = (instanceInput?.value || "").trim();
  if (!instanceUrl) {
    document.getElementById("obDiscoveryStatus").innerHTML =
      `<div class="ob-error">Enter your ServiceNow instance URL first.</div>`;
    return;
  }
  return connectPlatformDiscovery("servicenow", instanceUrl);
}

async function connectPlatformDiscovery(platform, authBaseUrl) {
  const status = document.getElementById("obDiscoveryStatus");
  const label = OB_PLATFORMS[platform]?.label || platform;
  status.innerHTML = `<div class="ob-discovery-status">Creating a secure ${_obEsc(label)} connection…</div>`;
  try {
    const workspaceId = TRIAL_WS || localStorage.getItem("cp_workspace_id") || "default";
    const createdResponse = await fetch(`${CostPilot_URL}/api/integrations/connections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace_id: workspaceId,
        platform,
        display_name: `${label} ${new Date().toISOString()}`,
        auth_base_url: authBaseUrl,
      }),
    });
    if (!createdResponse.ok) throw new Error(`Could not create connection (${createdResponse.status}).`);
    const connection = await createdResponse.json();
    obDiscoveryConnectionId = connection.id;
    obDiscoveryPlatform = platform;
    localStorage.setItem("cp_discovery_connection_id", String(connection.id));
    localStorage.setItem("cp_discovery_platform", platform);
    const authResponse = await fetch(`${CostPilot_URL}/api/integrations/connections/${connection.id}/authorize`, {
      method: "POST",
    });
    const auth = await authResponse.json();
    if (!authResponse.ok) throw new Error(auth.detail || `Could not start ${label} authorization.`);
    if (!auth.configured) {
      status.innerHTML = `<div class="ob-discovery-status">${_obEsc(auth.detail)} You can continue with the recommended manual mapping below.</div>`;
      return;
    }
    window.location.href = auth.authorization_url;
  } catch (error) {
    status.innerHTML = `<div class="ob-error">${_obEsc(error.message)}</div>`;
  }
}

function restoreOAuthDiscovery() {
  const params = new URLSearchParams(window.location.search || "");
  if (params.get("oauth") !== "success" || !params.get("connection_id")) return;
  obDiscoveryConnectionId = Number(params.get("connection_id"));
  obDiscoveryPlatform = params.get("platform") || localStorage.getItem("cp_discovery_platform") || "salesforce";
  localStorage.setItem("cp_discovery_connection_id", String(obDiscoveryConnectionId));
  localStorage.setItem("cp_discovery_platform", obDiscoveryPlatform);
  document.body.classList.add("ob-connection-mode");
  document.querySelectorAll(".ob-screen").forEach(screen => screen.classList.remove("active"));
  document.getElementById("screen-5")?.classList.add("active");
  selectObPlatform(obDiscoveryPlatform);
  setUniversalSetupStage(3);
  loadDiscoveryObjects();
  window.history.replaceState({}, "", "/onboarding.html");
}

async function restoreServerOnboardingProgress() {
  const params = new URLSearchParams(window.location.search || "");
  if (params.get("oauth") === "success" || !obDiscoveryConnectionId || obDiscoveryPlatform !== "salesforce") return;
  try {
    const response = await fetch(
      `${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/package-setup`
    );
    if (!response.ok) return;
    const setup = await response.json();
    if (!setup.relationships?.approved && !setup.active) return;

    document.body.classList.add("ob-connection-mode");
    document.querySelectorAll(".ob-screen").forEach(screen => screen.classList.remove("active"));
    document.getElementById("screen-5")?.classList.add("active");
    selectObPlatform("salesforce");
    const objectInput = document.getElementById("obPlatObject");
    const agentInput = document.getElementById("obPlatAgent");
    if (objectInput) objectInput.value = setup.relationships?.parent_object || "Account";
    if (agentInput) agentInput.value = setup.selected?.[0]?.name || "CostPilot Agent";

    const out = document.getElementById("obPlatOutput");
    if (!out) return;
    out.style.display = "block";
    if (setup.active) {
      setUniversalSetupStage(5);
      out.innerHTML = `<section class="ob-verification" id="obUniversalVerification">
        <div class="ob-context-eyebrow">Connection active</div>
        <h3>Salesforce is ready for CostPilot</h3>
        <p>Org ${_obEsc(setup.org?.organization_id || "verified")} is connected to workspace ${_obEsc(setup.workspace_id)}.</p>
        <div class="ob-actions"><button class="ob-btn-primary" onclick="goToDashboard()">Open Dashboard →</button></div>
      </section>`;
      return;
    }

    setUniversalSetupStage(4);
    out.innerHTML = _businessContextSummaryHtml() + _universalVerificationHtml();
    if (setup.checklist?.org_verified && setup.checklist?.workspace_bound) {
      _markVerificationRow(
        "obVerifySalesforceOrg",
        `${setup.org?.organization_id || "Org verified"} · ${setup.workspace_id}`,
      );
    }
    if (setup.verification?.parent_verified) {
      _markVerificationRow("obVerifyParentRequest", setup.verification.parent_record_name || "Received");
    }
    if (setup.verification?.child_verified) {
      _markVerificationRow("obVerifyChildRequest", setup.verification.child_record_name || "Received and rolled up");
    }
    if (setup.verification?.verified) {
      const button = document.getElementById("obRunTestBtn");
      if (button) button.textContent = "Live Requests Verified ✓";
    }
    refreshActivationButton();
  } catch (_) {
    // Keep the normal first step available when durable progress cannot load.
  }
}

async function loadSalesforceObjects() {
  return loadDiscoveryObjects();
}

async function loadDiscoveryObjects() {
  const status = document.getElementById("obDiscoveryStatus");
  const results = document.getElementById("obDiscoveryResults");
  const label = OB_PLATFORMS[obDiscoveryPlatform]?.label || "platform";
  status.innerHTML = `<div class="ob-discovery-status">Connected. Reading accessible ${_obEsc(label)} metadata…</div>`;
  try {
    const response = await fetch(`${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/objects`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Object discovery failed.");
    const preferred = payload.objects.filter(obj => obj.recommended || obj.custom ||
      ["Case", "Opportunity", "Account", "Contact", "Lead", "Task"].includes(obj.name));
    const objects = preferred.length ? preferred : payload.objects;
    results.innerHTML = `<div class="ob-field ob-parent-object-step" style="margin-top:14px">
      <div class="ob-context-eyebrow">Step 1 of 2</div>
      <label class="ob-label">Choose the primary business context</label>
      <p class="ob-field-help">This is the parent record where related AI cost, tokens, users, and agents will roll up.</p>
      <select class="ob-input" id="obDiscoveredObject">
        ${objects.map(obj => `<option value="${_obEsc(obj.name)}">${_obEsc(obj.label)} · ${_obEsc(obj.name)}</option>`).join("")}
      </select>
      <div class="ob-discovery-actions"><button type="button" class="ob-btn-primary" onclick="discoverPlatformFields()">Find related objects →</button></div>
    </div>`;
    status.innerHTML = `<div class="ob-discovery-status"><strong>${_obEsc(label)} connected.</strong> Choose the parent record below, then CostPilot will find its related objects.</div>`;
    results.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    status.innerHTML = `<div class="ob-error">${_obEsc(error.message)}</div>`;
  }
}

async function discoverSalesforceFields() {
  return discoverPlatformFields();
}

async function discoverPlatformFields() {
  const objectName = document.getElementById("obDiscoveredObject").value;
  obDiscoveredObjectName = objectName;
  const status = document.getElementById("obDiscoveryStatus");
  status.innerHTML = `<div class="ob-discovery-status">Finding objects related to ${_obEsc(objectName)}…</div>`;
  try {
    const response = await fetch(`${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/discover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ object_name: objectName }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Field discovery failed.");
    obDiscoveredFields = payload.fields;
    obDiscoveredRelationships = payload.child_relationships || [];
    renderDiscoveredMapping(payload);
    document.getElementById("obPlatObject").value = objectName;
    status.innerHTML = `<div class="ob-discovery-status"><strong>Relationship suggestions ready.</strong> Review which records should roll up to ${_obEsc(payload.object_label || objectName)}.</div>`;
  } catch (error) {
    status.innerHTML = `<div class="ob-error">${_obEsc(error.message)}</div>`;
  }
}

function renderDiscoveredMapping(payload) {
  obDiscoveredObjectName = payload.object || obDiscoveredObjectName;
  const targets = [
    ["work_id", "Work ID"],
    ["work_name", "Work name"],
    ["owner", "Owner / user"],
    ["customer", "Customer"],
    ["status", "Status"],
    ["content", "AI content"],
  ];
  const options = `<option value="">Not mapped</option>` + payload.fields.map(field =>
    `<option value="${_obEsc(field.name)}">${_obEsc(field.label)} · ${_obEsc(field.name)}</option>`
  ).join("");
  const children = (payload.child_relationships || []).slice(0, 30);
  document.getElementById("obDiscoveryResults").innerHTML = `<div class="ob-discovery-mapping">
    <div class="ob-discovery-section-head">
      <span class="ob-context-eyebrow">Step 2 of 2</span>
      <strong>Primary business context: ${_obEsc(payload.object_label || payload.object)}</strong>
      <span>Its permanent ID is the parent linking key. CostPilot keeps the original source record on every AI request.</span>
    </div>
    <details class="ob-advanced-mapping">
      <summary>Identity field details <span>Optional</span></summary>
      <div class="ob-advanced-mapping-body">${targets.map(([key, label]) => {
      const rec = payload.recommendations[key];
      return `<div class="ob-discovery-map-row">
        <label for="obMap-${key}">${label}</label>
        <select class="ob-input" id="obMap-${key}" data-map-key="${key}">${options}</select>
        <span class="ob-confidence">${rec ? `${rec.confidence} match` : "Choose"}</span>
      </div>`;
    }).join("")}</div>
    </details>
    <div class="ob-discovery-section-head">
      <strong>Related records to include</strong>
      <span>Choose how AI activity from each related object should appear beneath the parent.</span>
    </div>
    <div class="ob-relationship-list">
      ${children.length ? children.map((child, index) => `
        <div class="ob-relationship-row"
             data-child-index="${index}"
             data-child-object="${_obEsc(child.object)}"
             data-child-label="${_obEsc(child.label)}"
             data-parent-field="${_obEsc(child.parent_field)}"
             data-relationship-name="${_obEsc(child.relationship_name || "")}">
          <div>
            <strong>${_obEsc(child.label)}</strong>
            <span>${_obEsc(child.object)} → ${_obEsc(child.parent_field)} → ${_obEsc(payload.object)}</span>
          </div>
          <select class="ob-input ob-child-behavior" aria-label="Attribution behavior for ${_obEsc(child.label)}">
            <option value="track_and_rollup" ${child.confidence === "high" ? "selected" : ""}>Track separately + roll up</option>
            <option value="rollup_only">Roll up to parent only</option>
            <option value="separate">Track separately only</option>
            <option value="ignore" ${child.confidence !== "high" ? "selected" : ""}>Ignore for attribution</option>
          </select>
          <span class="ob-confidence">${_obEsc(child.confidence)} match</span>
        </div>`).join("") : '<div class="ob-discovery-status">No direct child relationships were found. You can continue with the parent object and add relationships later.</div>'}
    </div>
    <label class="ob-unmapped-choice">
      <span>Records without an approved relationship</span>
      <select class="ob-input" id="obUnmappedBehavior">
        <option value="separate">Track as separate work</option>
        <option value="ignore">Govern and audit, but do not attribute</option>
      </select>
    </label>
    <div class="ob-discovery-actions">
      <button type="button" class="ob-btn-primary" id="obApproveRelationshipBtn" onclick="approveDiscoveredMapping()">Approve relationship mapping →</button>
    </div>
  </div>`;
  targets.forEach(([key]) => {
    const value = payload.recommendations[key]?.field;
    const select = document.getElementById(`obMap-${key}`);
    if (select && value) select.value = value;
  });
}

async function approveDiscoveredMapping() {
  const button = document.getElementById("obApproveRelationshipBtn");
  const status = document.getElementById("obDiscoveryStatus");
  if (button) {
    button.disabled = true;
    button.textContent = "Saving relationship mapping…";
  }
  try {
    const mapping = {};
    document.querySelectorAll("[data-map-key]").forEach(select => {
      if (select.value) mapping[select.dataset.mapKey] = select.value;
    });
    const selectedObject = obDiscoveredObjectName;
    if (!selectedObject) throw new Error("The parent object was lost. Select the parent object and try discovery again.");
    if (!obDiscoveryConnectionId) throw new Error("The connection expired. Reconnect Salesforce and try again.");
    mapping.children = [...document.querySelectorAll(".ob-relationship-row")].map(row => ({
      object: row.dataset.childObject,
      label: row.dataset.childLabel,
      parent_field: row.dataset.parentField,
      relationship_name: row.dataset.relationshipName || null,
      behavior: row.querySelector(".ob-child-behavior").value,
    }));
    mapping.unmapped_behavior = document.getElementById("obUnmappedBehavior")?.value || "separate";
    mapping.preserve_origin_record = true;
    const response = await fetch(`${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/mapping`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_object: selectedObject, mapping }),
    });
    const responseText = await response.text();
    let payload = {};
    try {
      payload = responseText ? JSON.parse(responseText) : {};
    } catch (_) {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(payload.detail || `Could not save mapping (${response.status}).`);
    }
    if (obDiscoveryPlatform === "salesforce") {
      const packageResponse = await fetch(`${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/package-setup/relationships`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          parent_object: selectedObject,
          children: mapping.children.map(child => ({
            object_name: child.object,
            parent_field: child.parent_field,
            behavior: child.behavior,
          })),
        }),
      });
      const packagePayload = await packageResponse.json().catch(() => ({}));
      if (!packageResponse.ok) {
        throw new Error(packagePayload.detail || `Could not save Salesforce readiness mapping (${packageResponse.status}).`);
      }
    }
    const approvedRelationshipMapping = {
      platform: obDiscoveryPlatform,
      connection_id: obDiscoveryConnectionId,
      parent_object: selectedObject,
      children: mapping.children,
      unmapped_behavior: mapping.unmapped_behavior,
      preserve_origin_record: true,
      approved_at: new Date().toISOString(),
    };
    localStorage.setItem("cp_relationship_mapping", JSON.stringify(approvedRelationshipMapping));
    const contentField = mapping.content;
    if (contentField) _renderObFields([{ label: "Content", name: contentField }]);
    const includedChildren = mapping.children.filter(child => child.behavior !== "ignore");
    const relationshipSummary = includedChildren.length
      ? includedChildren.map(child => `${_obEsc(child.label)} (${_obEsc(child.behavior.replaceAll("_", " "))})`).join(", ")
      : "No related objects selected";
    const nextStep = obDiscoveryPlatform === "servicenow"
      ? `Next: install the generated Flow Designer action, run one request from a mapped record, and confirm the record sys_id, user sys_id, agent, tokens, and cost in Projects.`
      : `Next: install the generated action, run one request from a mapped record, and confirm its record, user, agent, tokens, and cost in Projects.`;
    document.getElementById("obDiscoveryStatus").innerHTML =
      `<div class="ob-discovery-status ob-relationship-approved"><strong>Relationship mapping approved.</strong>
        <span><b>Parent:</b> ${_obEsc(selectedObject)}</span>
        <span><b>Related:</b> ${relationshipSummary}</span>
        <span>${_obEsc(nextStep)}</span>
      </div>`;
    if (button) button.textContent = "Relationship mapping approved ✓";
    if (obDiscoveryPlatform === "salesforce") {
      await loadContextDiscoveryMonitor(true);
      await loadSalesforceAiEntryPoints();
    }
  } catch (error) {
    status.innerHTML = `<div class="ob-error"><strong>Relationship mapping was not saved.</strong> ${_obEsc(error.message)}</div>`;
    if (button) {
      button.disabled = false;
      button.textContent = "Try approval again →";
    }
    status.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function ensureContextDiscoveryMonitorHost() {
  let host = document.getElementById("obContextDiscoveryMonitor");
  if (host) return host;
  const results = document.getElementById("obDiscoveryResults");
  if (!results) return null;
  host = document.createElement("section");
  host.id = "obContextDiscoveryMonitor";
  host.className = "ob-context-monitor";
  results.appendChild(host);
  return host;
}

async function loadContextDiscoveryMonitor(scan = false) {
  if (!obDiscoveryConnectionId || obDiscoveryPlatform !== "salesforce") return;
  const host = ensureContextDiscoveryMonitorHost();
  if (!host) return;
  host.innerHTML = `<div class="ob-discovery-status">${scan ? "Scanning Salesforce metadata…" : "Loading context discovery…"}</div>`;
  try {
    const suffix = scan ? "/scan" : "";
    const response = await fetch(
      `${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/context-discovery${suffix}`,
      { method: scan ? "POST" : "GET" }
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Context discovery failed (${response.status}).`);
    renderContextDiscoveryMonitor(payload);
  } catch (error) {
    host.innerHTML = `<div class="ob-error"><strong>Context discovery could not run.</strong> ${_obEsc(error.message)}</div>`;
  }
}

function renderContextDiscoveryMonitor(payload) {
  const host = ensureContextDiscoveryMonitorHost();
  if (!host) return;
  const changes = payload.pending_changes || [];
  const baselineNote = payload.baseline_created
    ? "Baseline saved. Future scans will show only new objects and relationships."
    : payload.configured
      ? (changes.length ? `${changes.length} change${changes.length === 1 ? "" : "s"} need review.` : "Salesforce matches the approved context mapping.")
      : "Run the first scan to save the current Salesforce structure as the baseline.";
  const rows = changes.map(change => {
    const isRelationship = change.kind === "relationship_added";
    const title = isRelationship
      ? `${change.label || change.object} can roll up to ${change.parent_object}`
      : `${change.label || change.object} is a new Salesforce object`;
    const detail = isRelationship
      ? `${change.object}.${change.parent_field} references the approved parent.`
      : `${change.object} was not present in the previous metadata scan.`;
    return `<article class="ob-context-change">
      <div><span class="ob-context-change-kind">${isRelationship ? "New relationship" : "New object"}</span>
        <strong>${_obEsc(title)}</strong><small>${_obEsc(detail)}</small></div>
      <div class="ob-context-change-actions">
        ${isRelationship ? `<button type="button" class="ob-btn-secondary" onclick="reviewContextChange('${change.id}', 'approve', 'track_and_rollup')">Approve rollup</button>` : ""}
        <button type="button" class="ob-btn-secondary" onclick="reviewContextChange('${change.id}', 'approve', 'separate')">Track separately</button>
        <button type="button" class="ob-btn-ghost" onclick="reviewContextChange('${change.id}', 'ignore', 'ignore')">Ignore</button>
      </div>
    </article>`;
  }).join("");
  host.innerHTML = `<div class="ob-discovery-head">
      <div><div class="ob-context-eyebrow">Keep context current</div>
        <h3>Find new Salesforce objects and relationships</h3>
        <p>CostPilot watches for metadata changes. Nothing begins attribution until an administrator approves it.</p></div>
      <button type="button" class="ob-btn-secondary" onclick="loadContextDiscoveryMonitor(true)">Scan for changes</button>
    </div>
    <div class="ob-context-monitor-summary"><strong>${_obEsc(baselineNote)}</strong>
      ${payload.last_scan_at ? `<span>Last scan: ${_obEsc(new Date(payload.last_scan_at).toLocaleString())}</span>` : ""}</div>
    ${rows ? `<div class="ob-context-change-list">${rows}</div>` : ""}`;
}

async function reviewContextChange(changeId, decision, behavior) {
  if (!obDiscoveryConnectionId) return;
  const host = ensureContextDiscoveryMonitorHost();
  try {
    const response = await fetch(
      `${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/context-discovery/changes/${changeId}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, behavior }),
      }
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Review failed (${response.status}).`);
    await loadContextDiscoveryMonitor(false);
  } catch (error) {
    if (host) host.insertAdjacentHTML("afterbegin", `<div class="ob-error"><strong>Change was not saved.</strong> ${_obEsc(error.message)}</div>`);
  }
}

async function loadSalesforceAiEntryPoints() {
  const section = document.getElementById("obSalesforceEntryPoints");
  if (!section || !obDiscoveryConnectionId) return;
  section.hidden = false;
  section.innerHTML = `<div class="ob-discovery-status">Finding existing Agentforce agents and Salesforce Flows…</div>`;
  try {
    const response = await fetch(
      `${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/ai-entry-points`
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "AI entry-point discovery failed.");
    obSalesforceEntryPoints = {
      agents: payload.agents || [],
      flows: payload.flows || [],
    };
    renderSalesforceAiEntryPoints(payload);
  } catch (error) {
    renderSalesforceAiEntryPoints({
      agents: [],
      flows: [],
      warnings: [error.message || "CostPilot could not list Salesforce AI entry points."],
    });
  }
}

function renderSalesforceAiEntryPoints(payload) {
  const section = document.getElementById("obSalesforceEntryPoints");
  if (!section) return;
  const saved = _getSelectedSalesforceEntryPoints();
  const agents = payload.agents || [];
  const flows = payload.flows || [];
  const rows = (items, kind) => items.map(item => {
    const key = `${kind}:${item.id || item.name}`;
    const checked = saved.some(savedItem => savedItem.key === key);
    const detail = kind === "agent"
      ? "Existing Agentforce agent"
      : `${item.status === "active" ? "Active" : "Inactive"}${item.process_type ? ` · ${item.process_type}` : ""}`;
    return `<label class="ob-entry-point-row">
      <input type="checkbox" data-entry-kind="${kind}" data-entry-id="${_obEsc(item.id || "")}"
             data-entry-name="${_obEsc(item.name || "")}" data-entry-label="${_obEsc(item.label || item.name || "")}"
             ${checked ? "checked" : ""} onchange="saveSalesforceEntryPointSelection()" />
      <span><strong>${_obEsc(item.label || item.name)}</strong><small>${_obEsc(detail)}</small></span>
      <span class="ob-entry-status">Action required</span>
    </label>`;
  }).join("");
  const warnings = (payload.warnings || []).map(message =>
    `<div class="ob-entry-warning">${_obEsc(message)}</div>`
  ).join("");
  section.innerHTML = `<div class="ob-discovery-head">
      <div><div class="ob-context-eyebrow">Choose AI entry points</div>
      <h3>Which Salesforce AI should CostPilot govern?</h3>
      <p>Select existing Agentforce agents and Flows. CostPilot will give you one short activation checklist for each selection.</p></div>
      <span class="ob-context-eyebrow">Read-only discovery</span>
    </div>
    ${warnings}
    <div class="ob-entry-tabs" role="tablist">
      <button type="button" class="active" data-entry-tab="agents" onclick="showSalesforceEntryTab('agents')">Agentforce Agents <span>${agents.length}</span></button>
      <button type="button" data-entry-tab="flows" onclick="showSalesforceEntryTab('flows')">Salesforce Flows <span>${flows.length}</span></button>
    </div>
    <div class="ob-entry-panel" data-entry-panel="agents">
      ${rows(agents, "agent") || '<div class="ob-entry-empty">No agents were returned. Add the agent name below and continue.</div>'}
      <label class="ob-entry-manual"><span>Agent not listed?</span><input class="ob-input" id="obManualAgentName" placeholder="Enter existing Agentforce agent name" /></label>
      <button type="button" class="ob-btn-ghost" onclick="addManualSalesforceEntryPoint('agent')">+ Add agent</button>
    </div>
    <div class="ob-entry-panel" data-entry-panel="flows" hidden>
      ${rows(flows, "flow") || '<div class="ob-entry-empty">No flows were returned. Add the flow name below and continue.</div>'}
      <label class="ob-entry-manual"><span>Flow not listed?</span><input class="ob-input" id="obManualFlowName" placeholder="Enter existing Salesforce Flow name" /></label>
      <button type="button" class="ob-btn-ghost" onclick="addManualSalesforceEntryPoint('flow')">+ Add flow</button>
    </div>
    <div class="ob-entry-footer">
      <span id="obEntryPointCount">0 selected</span>
      <strong>Selection does not modify Salesforce.</strong> You will add the CostPilot action during activation.
    </div>`;
  saveSalesforceEntryPointSelection();
  section.scrollIntoView({ behavior: "smooth", block: "center" });
}

function showSalesforceEntryTab(tab) {
  document.querySelectorAll("[data-entry-tab]").forEach(button =>
    button.classList.toggle("active", button.dataset.entryTab === tab)
  );
  document.querySelectorAll("[data-entry-panel]").forEach(panel =>
    panel.hidden = panel.dataset.entryPanel !== tab
  );
}

function addManualSalesforceEntryPoint(kind) {
  const input = document.getElementById(kind === "agent" ? "obManualAgentName" : "obManualFlowName");
  const name = (input?.value || "").trim();
  if (!name) return;
  const selected = _getSelectedSalesforceEntryPoints();
  const key = `${kind}:manual:${name.toLowerCase()}`;
  if (!selected.some(item => item.key === key)) {
    selected.push({ key, kind, id: "", name, label: name, source: "manual" });
  }
  localStorage.setItem("cp_salesforce_entry_points", JSON.stringify(selected));
  renderSalesforceAiEntryPoints({
    agents: kind === "agent"
      ? [...obSalesforceEntryPoints.agents, { id: `manual:${name}`, name, label: name }]
      : obSalesforceEntryPoints.agents,
    flows: kind === "flow"
      ? [...obSalesforceEntryPoints.flows, { id: `manual:${name}`, name, label: name, status: "unknown" }]
      : obSalesforceEntryPoints.flows,
    warnings: [],
  });
}

async function saveSalesforceEntryPointSelection() {
  const existingManual = _getSelectedSalesforceEntryPoints().filter(item => item.source === "manual");
  const selected = [...document.querySelectorAll("[data-entry-kind]:checked")].map(input => ({
    key: `${input.dataset.entryKind}:${input.dataset.entryId || input.dataset.entryName}`,
    kind: input.dataset.entryKind,
    id: input.dataset.entryId || "",
    name: input.dataset.entryName,
    label: input.dataset.entryLabel || input.dataset.entryName,
    source: (input.dataset.entryId || "").startsWith("manual:") ? "manual" : "discovery",
    activation_status: "action_required",
  }));
  existingManual.forEach(item => {
    if (!selected.some(selectedItem => selectedItem.key === item.key)) selected.push(item);
  });
  const deduplicated = [];
  selected.forEach(item => {
    const existingIndex = deduplicated.findIndex(existing =>
      existing.kind === item.kind && existing.name.toLowerCase() === item.name.toLowerCase()
    );
    if (existingIndex < 0) {
      deduplicated.push(item);
      return;
    }
    const existingId = deduplicated[existingIndex].id || "";
    const itemId = item.id || "";
    if (itemId && !itemId.startsWith("manual:") && (!existingId || existingId.startsWith("manual:"))) {
      deduplicated[existingIndex] = item;
    }
  });
  localStorage.setItem("cp_salesforce_entry_points", JSON.stringify(deduplicated));
  const count = document.getElementById("obEntryPointCount");
  if (count) count.textContent = `${deduplicated.length} selected`;
  if (!obDiscoveryConnectionId) return;
  try {
    const response = await fetch(`${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/ai-entry-points/selection`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        entries: deduplicated.map(item => ({
          kind: item.kind,
          id: item.id || "",
          name: item.name,
          label: item.label || item.name,
        })),
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `Selection was not saved (${response.status}).`);
    if (count) count.textContent = `${deduplicated.length} selected · saved`;
  } catch (error) {
    if (count) count.textContent = `${deduplicated.length} selected · ${error.message}`;
  }
}

function _getSelectedSalesforceEntryPoints() {
  try {
    return JSON.parse(localStorage.getItem("cp_salesforce_entry_points") || "[]");
  } catch (_) {
    return [];
  }
}

function _getApprovedRelationshipMapping() {
  try {
    return JSON.parse(localStorage.getItem("cp_relationship_mapping") || "null");
  } catch (_) {
    return null;
  }
}

let obSelectedPlatform = null;
let _obLastPlatform = null;
let obBusinessContext = null;

const OB_CONTEXT_TEMPLATES = {
  salesforce: {
    key: "salesforce_project",
    name: "Salesforce Project",
    defaultWorkType: "project",
    defaultCustomerLabel: "Account",
  },
  servicenow: {
    key: "servicenow_case",
    name: "ServiceNow Case",
    defaultWorkType: "case",
    defaultCustomerLabel: "Account",
  },
};

const OB_CONTEXT_OBJECTS = {
  salesforce: {
    project: "CostPilot_Project__c",
    matter: "Matter__c",
    engagement: "Engagement__c",
    case: "Case",
    ticket: "Case",
    opportunity: "Opportunity",
    account: "Account",
    customer: "Account",
  },
  servicenow: {
    project: "pm_project",
    matter: "u_matter",
    engagement: "u_engagement",
    case: "sn_customerservice_case",
    ticket: "incident",
    opportunity: "u_opportunity",
    account: "customer_account",
    customer: "customer_account",
  },
};

function configureObBusinessContext(platform) {
  const card = document.getElementById("obBusinessContextCard");
  const cfg = OB_PLATFORMS[platform];
  if (!card || cfg?.kind !== "business") {
    if (card) card.hidden = true;
    obBusinessContext = null;
    return;
  }

  // The business-first onboarding already collected this language. Keep the
  // generated integration aligned with it instead of asking the questions a
  // second time or replacing custom terminology with platform defaults.
  if (obBusinessContext && obBusinessContext.platform === platform) {
    card.hidden = true;
    const objectName = OB_CONTEXT_OBJECTS[platform]?.[obBusinessContext.work_type];
    const objectInput = document.getElementById("obPlatObject");
    if (objectInput && objectName) objectInput.value = objectName;
    return;
  }

  card.hidden = false;
  const template = OB_CONTEXT_TEMPLATES[platform] || {
    key: "universal_context",
    name: `${cfg.label} Business Context`,
    defaultWorkType: "project",
    defaultCustomerLabel: "Customer",
  };
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem("cp_business_context") || "null");
  } catch (_) {
    saved = null;
  }
  const workType = saved?.platform === platform
    ? saved.work_type
    : template.defaultWorkType;
  const customerValue = saved?.platform === platform
    ? (saved.customer_type || (saved.customer_label_custom ? "Custom" : saved.customer_label))
    : template.defaultCustomerLabel;
  document.getElementById("obContextWorkType").value = workType;
  document.getElementById("obContextCustomerLabel").value = customerValue;
  if (workType === "custom") {
    document.getElementById("obContextCustomWork").value = saved?.work_label || "";
  }
  if (customerValue === "Custom") {
    document.getElementById("obContextCustomCustomer").value = saved?.customer_label_custom || saved?.customer_label || "";
  }
  updateObBusinessContext(true);
}

function updateObBusinessContext(setObjectDefault = false) {
  if (!obSelectedPlatform || OB_PLATFORMS[obSelectedPlatform]?.kind !== "business") return;
  const template = OB_CONTEXT_TEMPLATES[obSelectedPlatform] || {
    key: "universal_context",
    name: `${OB_PLATFORMS[obSelectedPlatform].label} Business Context`,
  };
  const workType = document.getElementById("obContextWorkType").value;
  const customerValue = document.getElementById("obContextCustomerLabel").value;
  const customWorkInput = document.getElementById("obContextCustomWork");
  const customCustomerInput = document.getElementById("obContextCustomCustomer");
  if (customWorkInput) customWorkInput.hidden = workType !== "custom";
  if (customCustomerInput) customCustomerInput.hidden = customerValue !== "Custom";
  const customWorkLabel = (customWorkInput?.value || "").trim();
  const customCustomerLabel = (customCustomerInput?.value || "").trim();
  const workLabel = workType === "custom"
    ? (customWorkLabel || "Custom work")
    : workType.charAt(0).toUpperCase() + workType.slice(1);
  const customerLabel = customerValue === "Custom"
    ? (customCustomerLabel || "Customer")
    : customerValue;
  const measures = [...document.querySelectorAll(".ob-context-measures input:checked")]
    .map(input => input.value);
  obBusinessContext = {
    template: template.key,
    template_name: template.name,
    platform: obSelectedPlatform,
    work_type: workType,
    work_label: workLabel,
    customer_type: customerValue,
    customer_label: customerLabel,
    customer_label_custom: customerValue === "Custom" ? customCustomerLabel : "",
    measures,
  };
  localStorage.setItem("cp_business_context", JSON.stringify(obBusinessContext));

  const objectName = OB_CONTEXT_OBJECTS[obSelectedPlatform]?.[workType];
  const objectInput = document.getElementById("obPlatObject");
  if (objectInput && objectName && (setObjectDefault || !objectInput.value)) {
    objectInput.value = objectName;
  }
  const result = document.getElementById("obContextResult");
  result.innerHTML = `<strong>${_obEsc(workLabel)} selected.</strong> CostPilot will connect each ${_obEsc(workLabel.toLowerCase())} to its ${_obEsc(customerLabel.toLowerCase())}, user, agent, ${_obEsc(measures.length ? measures.join(", ") : "business activity")}, and source record.`;
}

async function persistObBusinessContext() {
  if (!IS_TRIAL || !obBusinessContext) return;
  await apiPost("/api/trial/business-context", {
    workspace_id: TRIAL_WS,
    secret_key: TRIAL_SK,
    ...obBusinessContext,
  });
}

const OB_PLATFORM_COPY = {
  salesforce: {
    objectLabel: "Salesforce Object API Name",
    objectPlaceholder: "Case, Lead, Opportunity, Custom_Request__c",
    agentLabel: "Salesforce Agent Name",
    agentPlaceholder: "e.g. SF-CaseBot",
    fieldsLabel: "Salesforce Fields to Send to AI",
    fieldHint: "Prompt label · Salesforce field API name, standard or custom",
    fieldHeader: "Salesforce Field API Name",
    fieldPlaceholder: "Subject, Description, Custom_Text__c",
    returnLabel: "Salesforce Return Fields",
    returnHint: "Optional · Salesforce field API name to update after CostPilot responds",
    returnHeader: "Salesforce Field API Name",
    returnPlaceholder: "CostPilot_AI_Response__c",
    addFieldLabel: "+ Add Salesforce Field",
    emptyFieldsError: "Add at least one Salesforce field API name.",
    emptyObjectError: "Enter a Salesforce object API name."
  },
  servicenow: {
    objectLabel: "ServiceNow Table",
    objectPlaceholder: "incident, sc_request, u_ai_request",
    agentLabel: "ServiceNow Bot Name",
    agentPlaceholder: "e.g. SN-IncidentBot",
    fieldsLabel: "ServiceNow Columns to Send to AI",
    fieldHint: "Prompt label · ServiceNow column sys_name",
    fieldHeader: "ServiceNow Column",
    fieldPlaceholder: "short_description, description, u_contract_text",
    returnLabel: "ServiceNow Return Columns",
    returnHint: "Optional · ServiceNow column to update after CostPilot responds",
    returnHeader: "ServiceNow Column",
    returnPlaceholder: "u_costpilot_response",
    addFieldLabel: "+ Add ServiceNow Column",
    emptyFieldsError: "Add at least one ServiceNow column.",
    emptyObjectError: "Enter a ServiceNow table name."
  },
  hubspot: {
    objectLabel: "HubSpot Object Type",
    objectPlaceholder: "tickets, deals, contacts",
    agentLabel: "HubSpot Agent Name",
    agentPlaceholder: "e.g. HS-TicketBot",
    fieldsLabel: "HubSpot Properties to Send to AI",
    fieldHint: "Prompt label · HubSpot property internal name",
    fieldHeader: "HubSpot Property",
    fieldPlaceholder: "subject, hs_note_body, dealstage",
    returnLabel: "HubSpot Return Properties",
    returnHint: "Optional · HubSpot output property from your custom code action",
    returnHeader: "HubSpot Property",
    returnPlaceholder: "costpilot_ai_response",
    addFieldLabel: "+ Add HubSpot Property",
    emptyFieldsError: "Add at least one HubSpot property.",
    emptyObjectError: "Enter a HubSpot object type."
  },
  dynamics: {
    objectLabel: "Dynamics / Dataverse Table",
    objectPlaceholder: "incident, lead, opportunity, new_airequest",
    agentLabel: "Dynamics Agent Name",
    agentPlaceholder: "e.g. D365-CaseBot",
    fieldsLabel: "Dataverse Fields to Send to AI",
    fieldHint: "Prompt label · Dataverse field schema or logical name",
    fieldHeader: "Dataverse Field",
    fieldPlaceholder: "title, description, new_contracttext",
    returnLabel: "Dataverse Return Fields",
    returnHint: "Optional · Dataverse field to update after CostPilot responds",
    returnHeader: "Dataverse Field",
    returnPlaceholder: "new_costpilotresponse",
    addFieldLabel: "+ Add Dataverse Field",
    emptyFieldsError: "Add at least one Dataverse field.",
    emptyObjectError: "Enter a Dynamics table name."
  },
  zendesk: {
    objectLabel: "Zendesk Record Type",
    objectPlaceholder: "ticket, request, user",
    agentLabel: "Zendesk Bot Name",
    agentPlaceholder: "e.g. ZD-TicketBot",
    fieldsLabel: "Zendesk Fields to Send to AI",
    fieldHint: "Prompt label · Zendesk field name or custom field identifier",
    fieldHeader: "Zendesk Field",
    fieldPlaceholder: "subject, description, comment, custom_field_123",
    returnLabel: "Zendesk Return Fields",
    returnHint: "Optional · Zendesk custom ticket field or internal-note output key",
    returnHeader: "Zendesk Field",
    returnPlaceholder: "custom_field_123",
    addFieldLabel: "+ Add Zendesk Field",
    emptyFieldsError: "Add at least one Zendesk field.",
    emptyObjectError: "Enter a Zendesk record type."
  },
  code: {
    objectLabel: "Code Entry Point",
    objectPlaceholder: "api_route, worker, function, service",
    agentLabel: "Service / Agent Name",
    agentPlaceholder: "e.g. CostPilot-Agent",
    fieldsLabel: "Request Data to Send to AI",
    fieldHint: "Prompt label · variable, JSON key, or request property from your code",
    fieldHeader: "Request Property",
    fieldPlaceholder: "content, message, contract_text, customer_name",
    returnLabel: "Return Values",
    returnHint: "Optional · response keys your app should read or persist",
    returnHeader: "Response Key",
    returnPlaceholder: "costpilot_response",
    addFieldLabel: "+ Add Request Property",
    emptyFieldsError: "Add at least one request property or variable.",
    emptyObjectError: "Enter the function, route, worker, or request type."
  },
};

function selectObPlatform(platform) {
  obSelectedPlatform = platform;
  if (IS_TRIAL) localStorage.setItem("cp_platform", platform);
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
  const returnFieldsLabel = document.getElementById("obReturnFieldsLabel");
  const returnFieldsLabelHint = document.getElementById("obReturnFieldsLabelHint");
  const agentLabel = document.getElementById("obAgentLabel");
  const addFieldBtn = document.getElementById("obAddFieldBtn");
  const addReturnFieldBtn = document.getElementById("obAddReturnFieldBtn");
  const selectedSummary = document.getElementById("obSelectedPlatformSummary");
  const selectedName = document.getElementById("obSelectedPlatformName");
  document.querySelectorAll("#screen-5 .ob-platform-group").forEach(group => { group.style.display = "none"; });
  if (selectedSummary) selectedSummary.style.display = "flex";
  if (selectedName) selectedName.textContent = cfg.label;
  renderObConnectionPlan(platform);
  renderObDiscoveryCard(platform);
  renderSalesforceAiPath(platform);
  const generateButton = document.getElementById("obGenerateSetupBtn");
  if (generateButton) {
    generateButton.textContent = platform === "salesforce"
      ? "Show Activation Steps →"
      : "Generate Setup Code →";
  }
  if (objectLabel) objectLabel.textContent = copy.objectLabel;
  if (fieldsLabel) fieldsLabel.childNodes[0].textContent = copy.fieldsLabel + " ";
  if (fieldsLabelHint) fieldsLabelHint.textContent = copy.fieldHint;
  if (returnFieldsLabel) returnFieldsLabel.childNodes[0].textContent = copy.returnLabel + " ";
  if (returnFieldsLabelHint) returnFieldsLabelHint.textContent = copy.returnHint;
  if (agentLabel) agentLabel.textContent = copy.agentLabel;
  if (addFieldBtn) addFieldBtn.textContent = copy.addFieldLabel;
  if (addReturnFieldBtn) addReturnFieldBtn.textContent = "+ Add Return Field";

  const objInput = document.getElementById("obPlatObject");
  const objOptions = document.getElementById("obPlatObjectOptions");
  if (objOptions) objOptions.innerHTML = cfg.objects.map(o => `<option value="${o}"></option>`).join("");
  if (objInput) objInput.placeholder = copy.objectPlaceholder;
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
  if (agentEl) agentEl.placeholder = copy.agentPlaceholder;
  if (!agentEl.value || Object.values(OB_PLATFORMS).some(c => c.agentDefault === agentEl.value)) {
    agentEl.value = cfg.agentDefault;
  }
  document.getElementById("obPlatConfig").style.display  = "block";
  document.getElementById("obPlatBackOnly").style.display = "none";
  document.getElementById("obPlatOutput").style.display  = "none";

  // Set default fields and hint for this platform
  _initObFields(platform);
  _initObReturnFields(platform);
  configureObBusinessContext(platform);
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

const OB_RETURN_DEFAULTS = {
  salesforce: {
    hint: "Optional. Create these fields on the Salesforce object if you want AI output and routing metadata written back.",
    fields: [
      { label: "AI Response",      name: "CostPilot_AI_Response__c",        source: "response" },
      { label: "Model Tier",       name: "CostPilot_Model_Tier__c",         source: "tier" },
      { label: "Routing Decision", name: "CostPilot_Routing_Decision__c",  source: "routing" },
      { label: "Cost USD",         name: "CostPilot_Cost_USD__c",           source: "cost" },
    ]
  },
  servicenow: {
    hint: "Optional. Use existing or custom columns if you want CostPilot results written back to the record.",
    fields: [
      { label: "AI Response",      name: "u_costpilot_response",           source: "response" },
      { label: "Model Tier",       name: "u_costpilot_model_tier",         source: "tier" },
      { label: "Routing Decision", name: "u_costpilot_routing_decision",  source: "routing" },
      { label: "Cost USD",         name: "u_costpilot_cost_usd",           source: "cost" },
    ]
  },
  hubspot: {
    hint: "Optional. Add matching output fields/properties to your workflow if you want values saved in HubSpot.",
    fields: [
      { label: "AI Response",      name: "costpilot_ai_response",          source: "response" },
      { label: "Model Tier",       name: "costpilot_model_tier",           source: "tier" },
      { label: "Routing Decision", name: "costpilot_routing_decision",    source: "routing" },
      { label: "Cost USD",         name: "costpilot_cost_usd",             source: "cost" },
    ]
  },
  dynamics: {
    hint: "Optional. Use Dataverse fields you want the Power Automate update step to write back.",
    fields: [
      { label: "AI Response",      name: "new_costpilotresponse",          source: "response" },
      { label: "Model Tier",       name: "new_costpilotmodeltier",         source: "tier" },
      { label: "Routing Decision", name: "new_costpilotroutingdecision",  source: "routing" },
      { label: "Cost USD",         name: "new_costpilotcostusd",           source: "cost" },
    ]
  },
  zendesk: {
    hint: "Optional. Use ticket custom fields or return keys your Zendesk workflow will map after the function runs.",
    fields: [
      { label: "AI Response",      name: "costpilot_ai_response",          source: "response" },
      { label: "Model Tier",       name: "costpilot_model_tier",           source: "tier" },
      { label: "Routing Decision", name: "costpilot_routing_decision",    source: "routing" },
      { label: "Cost USD",         name: "costpilot_cost_usd",             source: "cost" },
    ]
  },
  custom: {
    hint: "Optional. These are response keys your app can read from the CostPilot result and save wherever it needs.",
    fields: [
      { label: "AI Response",      name: "costpilot_response",             source: "response" },
      { label: "Model Tier",       name: "costpilot_model_tier",           source: "tier" },
      { label: "Routing Decision", name: "costpilot_routing_decision",    source: "routing" },
      { label: "Cost USD",         name: "costpilot_cost_usd",             source: "cost" },
    ]
  },
};
["python","nodejs","java","ruby","rest"].forEach(p => OB_RETURN_DEFAULTS[p] = OB_RETURN_DEFAULTS.custom);

function _initObFields(platform) {
  const defaults = (OB_FIELD_DEFAULTS[platform] || OB_FIELD_DEFAULTS.custom);
  const hint  = document.getElementById("obFieldsHint");
  if (hint) hint.textContent = defaults.hint;
  _renderObFields(defaults.fields.map(f => ({ ...f })));
}

let _obFieldData = [];
let _obReturnFieldData = [];

function _renderObFields(fields) {
  _obFieldData = fields;
  const list = document.getElementById("obFieldsList");
  if (!list) return;
  const cfg = OB_PLATFORMS[obSelectedPlatform] || {};
  const copy = OB_PLATFORM_COPY[obSelectedPlatform] || (cfg.kind === "code" ? OB_PLATFORM_COPY.code : OB_PLATFORM_COPY.code);
  list.innerHTML = fields.map((f, i) => `
    <div class="ob-map-field-row">
      <input type="text" value="${_obEsc(f.label)}" placeholder="Prompt label"
        oninput="_obFieldData[${i}].label=this.value"
        class="ob-map-field-label" />
      <span style="color:var(--text-muted,#8b949e);font-size:12px;flex-shrink:0">→</span>
      <input type="text" value="${_obEsc(f.name)}" placeholder="${_obEsc(copy.fieldPlaceholder)}"
        oninput="_obFieldData[${i}].name=this.value"
        class="ob-map-field-name" />
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

function _initObReturnFields(platform) {
  const defaults = (OB_RETURN_DEFAULTS[platform] || OB_RETURN_DEFAULTS.custom);
  const hint  = document.getElementById("obReturnFieldsHint");
  if (hint) hint.textContent = defaults.hint;
  _renderObReturnFields(defaults.fields.map(f => ({ ...f })));
}

function _renderObReturnFields(fields) {
  _obReturnFieldData = fields;
  const list = document.getElementById("obReturnFieldsList");
  if (!list) return;
  const cfg = OB_PLATFORMS[obSelectedPlatform] || {};
  const copy = OB_PLATFORM_COPY[obSelectedPlatform] || (cfg.kind === "code" ? OB_PLATFORM_COPY.code : OB_PLATFORM_COPY.code);
  list.innerHTML = fields.map((f, i) => `
    <div class="ob-map-field-row">
      <select onchange="_obReturnFieldData[${i}].source=this.value"
        class="ob-map-field-label" style="min-width:150px">
        ${_returnSourceOptions(f.source)}
      </select>
      <span style="color:var(--text-muted,#8b949e);font-size:12px;flex-shrink:0">→</span>
      <input type="text" value="${_obEsc(f.name)}" placeholder="${_obEsc(copy.returnPlaceholder)}"
        oninput="_obReturnFieldData[${i}].name=this.value"
        class="ob-map-field-name" />
      <button type="button" onclick="removeObReturnField(${i})"
        style="background:none;border:none;color:var(--text-muted,#8b949e);cursor:pointer;font-size:16px;padding:0 4px;line-height:1">×</button>
    </div>`).join("");
}

function _returnSourceOptions(current) {
  const options = [
    ["response", "AI Response"],
    ["tier", "Model Tier"],
    ["model", "Model Name"],
    ["routing", "Routing Decision"],
    ["risk", "Risk Level"],
    ["cost", "Cost USD"],
    ["tokens_saved", "Tokens Saved"],
    ["audit_id", "Audit ID"],
  ];
  return options.map(([value, label]) =>
    `<option value="${value}" ${value === current ? "selected" : ""}>${label}</option>`
  ).join("");
}

function addObReturnField() {
  _syncObReturnFieldDataFromDom(true);
  _obReturnFieldData.push({ source: "response", name: "" });
  _renderObReturnFields(_obReturnFieldData);
  const inputs = document.querySelectorAll("#obReturnFieldsList input");
  if (inputs.length) inputs[inputs.length - 1].focus();
}

function removeObReturnField(i) {
  _syncObReturnFieldDataFromDom(true);
  _obReturnFieldData.splice(i, 1);
  _renderObReturnFields(_obReturnFieldData);
}

function _syncObReturnFieldDataFromDom(keepEmpty = false) {
  const rows = document.querySelectorAll("#obReturnFieldsList .ob-map-field-row");
  const result = [];
  rows.forEach(row => {
    const source = row.querySelector("select")?.value || "response";
    const name = (row.querySelector("input")?.value || "").trim();
    if (keepEmpty || name) result.push({ source, name });
  });
  _obReturnFieldData = result;
  return result;
}

function getObReturnFields() {
  return _syncObReturnFieldDataFromDom(false).filter(f => f.name.trim());
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

function _platformMappingHtml(platform, obj, fields, returnFields = [], extraRows = "") {
  const cfg = OB_PLATFORMS[platform] || {};
  const copy = OB_PLATFORM_COPY[platform] || (cfg.kind === "code" ? OB_PLATFORM_COPY.code : OB_PLATFORM_COPY.code);
  const label = cfg.label || platform;
  const mappingRows = fields.map(f =>
    `<div class="ob-field-row"><span>${_obEsc(f.label || f.name)}</span><span class="mono">${_obEsc(f.name)}</span><span>Prompt payload</span><span>Routed to CostPilot</span></div>`
  ).join("");
  const returnRows = returnFields.length ? returnFields.map(f =>
    `<div class="ob-field-row"><span>${_obEsc(_returnSourceLabel(f.source))}</span><span class="mono">${_obEsc(f.name)}</span><span>CostPilot result</span><span>Optional write-back</span></div>`
  ).join("") : `<div class="ob-field-row"><span>No return fields</span><span class="mono">—</span><span>CostPilot only</span><span>Routes and logs without write-back</span></div>`;
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
      <div class="ob-field-row ob-field-row-header"><span>Return Value</span><span>${_obEsc(copy.returnHeader)}</span><span>Used As</span><span>Behavior</span></div>
      ${returnRows}
    </div>
  </div>`;
}

function _businessContextSummaryHtml() {
  if (!obBusinessContext) return "";
  const measures = obBusinessContext.measures.length
    ? obBusinessContext.measures.join(", ")
    : "No measures selected";
  return `<div class="ob-code-section" style="margin-top:20px">
    <div class="ob-code-header">
      <span class="ob-code-label">Business Context Template</span>
      <span class="ob-code-hint">CostPilot uses this business definition across users, agents, routing, and reporting.</span>
    </div>
    <div class="ob-flow-steps">
      <div class="ob-flow-step"><span class="ob-flow-num">✓</span><div>
        <strong>${_obEsc(obBusinessContext.template_name)}</strong><br/>
        Work: ${_obEsc(obBusinessContext.work_label)} · Customer: ${_obEsc(obBusinessContext.customer_label)} · Measures: ${_obEsc(measures)}
      </div></div>
    </div>
  </div>`;
}

function _returnSourceLabel(source) {
  const labels = {
    response: "AI Response",
    tier: "Model Tier",
    model: "Model Name",
    routing: "Routing Decision",
    risk: "Risk Level",
    cost: "Cost USD",
    tokens_saved: "Tokens Saved",
    audit_id: "Audit ID",
  };
  return labels[source] || "AI Response";
}

function _routeResultExpr(source, lang = "js") {
  const key = _routeResultKey(source);
  if (lang === "python") return `result.get("${key}", "")`;
  if (lang === "ruby") return `result["${key}"]`;
  if (lang === "java") return key;
  return `data.${key} || ''`;
}

function _jsResultExpr(source, varName = "data") {
  return `${varName}.${_routeResultKey(source)} || ''`;
}

function _routeResultKey(source) {
  const map = {
    response: "simulated_response",
    tier: "model_tier",
    model: "model_name",
    routing: "routing_decision",
    risk: "risk_level",
    cost: "cost_usd",
    tokens_saved: "tokens_saved_by_pruning",
    audit_id: "audit_id",
  };
  return map[source] || "simulated_response";
}

function _serviceNowResultExpr(source) {
  const key = _routeResultKey(source);
  if (source === "cost" || source === "tokens_saved") return `String(r.${key} || '')`;
  return `r.${key} || ''`;
}

function _salesforceReturnWrites(returnFields) {
  return returnFields.map(f => {
    const field = _codeStr(f.name);
    const source = f.source || "response";
    if (source === "cost") {
      return `            if (fieldMap.containsKey('${field}') && r.get('cost_usd') != null) {
                rec.put('${field}', Decimal.valueOf(String.valueOf(r.get('cost_usd'))));
                hasUpdates = true;
            }`;
    }
    const keyMap = {
      response: "simulated_response",
      tier: "model_tier",
      model: "model_name",
      routing: "routing_decision",
      risk: "risk_level",
      tokens_saved: "tokens_saved_by_pruning",
      audit_id: "audit_id",
    };
    const key = keyMap[source] || "simulated_response";
    return `            if (fieldMap.containsKey('${field}') && r.get('${key}') != null) {
                rec.put('${field}', String.valueOf(r.get('${key}')));
                hasUpdates = true;
            }`;
  }).join("\n");
}

function _salesforceTrialReturnWrites(returnFields) {
  return returnFields.map(f => {
    const field = _codeStr(f.name);
    const source = f.source || "response";
    if (source === "cost") {
      return `                    String costHeader = res.getHeader('X-CostPilot-Cost');
                    if (fieldMap.containsKey('${field}') && String.isNotBlank(costHeader)) {
                        rec.put('${field}', Decimal.valueOf(costHeader));
                        hasUpdates = true;
                    }`;
    }
    const exprMap = {
      response: "aiResponse",
      tier: "res.getHeader('X-CostPilot-Tier')",
      model: "res.getHeader('X-CostPilot-Model')",
      routing: "res.getHeader('X-CostPilot-Routing')",
      risk: "(responseMap.get('risk_level') == null ? null : String.valueOf(responseMap.get('risk_level')))",
      tokens_saved: "res.getHeader('X-CostPilot-Tokens-Saved')",
      audit_id: "(responseMap.get('audit_id') == null ? null : String.valueOf(responseMap.get('audit_id')))",
    };
    const valueExpr = exprMap[source] || "aiResponse";
    return `                    String value_${_safeVar(field)} = ${valueExpr};
                    if (fieldMap.containsKey('${field}') && String.isNotBlank(value_${_safeVar(field)})) {
                        rec.put('${field}', value_${_safeVar(field)});
                        hasUpdates = true;
                    }`;
  }).join("\n");
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
    <button class="ob-btn-primary" onclick="goToUniversalVerification()">Continue to Verification ↓</button>
  </div>`;
}

function goToUniversalVerification() {
  document.getElementById("obUniversalVerification")?.scrollIntoView({ behavior: "smooth", block: "center" });
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

async function generateObCode() {
  const err = document.getElementById("error-5");
  if (!obSelectedPlatform) { err.textContent = "Select a platform first."; return; }
  err.textContent = "";
  const cfg = OB_PLATFORMS[obSelectedPlatform];
  const copy = OB_PLATFORM_COPY[obSelectedPlatform] || (cfg.kind === "code" ? OB_PLATFORM_COPY.code : OB_PLATFORM_COPY.code);
  const obj    = document.getElementById("obPlatObject").value.trim();
  const dept   = document.getElementById("obPlatDept").value;
  const agent  = document.getElementById("obPlatAgent").value.trim() || cfg.agentDefault;
  const fields = getObFields();
  const returnFields = getObReturnFields();
  if (!obj) { err.textContent = copy.emptyObjectError; return; }
  if (!fields.length) { err.textContent = copy.emptyFieldsError; return; }
  if (obSelectedPlatform === "salesforce") {
    const badObject = !_isSalesforceApiName(obj);
    const badField = fields.find(f => !_isSalesforceApiName(f.name));
    const badReturnField = returnFields.find(f => !_isSalesforceApiName(f.name));
    if (badObject) { err.textContent = "Enter a valid Salesforce object API name, like Case or Custom_Request__c."; return; }
    if (badField) { err.textContent = `Check the Salesforce field API name: ${badField.name}`; return; }
    if (badReturnField) { err.textContent = `Check the Salesforce return field API name: ${badReturnField.name}`; return; }
  }
  try {
    await persistObBusinessContext();
  } catch (error) {
    err.textContent = `Setup code is ready, but CostPilot could not save the Business Context template: ${error.message}`;
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
  const html   = _businessContextSummaryHtml()
    + (fns[obSelectedPlatform] || _genRest)(obj, dept, agent, fields, returnFields)
    + _universalVerificationHtml();
  const out   = document.getElementById("obPlatOutput");
  out.innerHTML = html;
  out.style.display = "block";
  setUniversalSetupStage(4);
  out.scrollIntoView({ behavior: "smooth" });
}

function _universalVerificationHtml() {
  const platform = OB_PLATFORMS[obSelectedPlatform]?.label || obSelectedPlatform;
  const selectedEntryPoints = obSelectedPlatform === "salesforce" ? _getSelectedSalesforceEntryPoints() : [];
  const entryPointLabel = selectedEntryPoints.length
    ? `${selectedEntryPoints.length} selected Salesforce AI entr${selectedEntryPoints.length === 1 ? "y point" : "y points"}`
    : "selected Salesforce AI entry points";
  const actionRow = obSelectedPlatform === "salesforce"
    ? `<div class="ob-verification-row" id="obVerifyAgentAction"><span>○</span> CostPilot action added to ${_obEsc(entryPointLabel)} <span class="status">Confirm below</span></div>`
    : "";
  const installText = obSelectedPlatform === "salesforce"
    ? `I added <strong>CostPilot Governed AI Work</strong> to every selected Agentforce agent and Salesforce Flow that I want governed.`
    : `I installed the generated setup in ${_obEsc(platform)}. This confirms the external platform step that CostPilot cannot inspect directly.`;
  const liveRows = obSelectedPlatform === "salesforce"
    ? `<div class="ob-verification-row" id="obVerifySalesforceOrg"><span>○</span> Correct Salesforce org and CostPilot workspace <span class="status">Checking</span></div>
       <div class="ob-verification-row" id="obVerifyParentRequest"><span>○</span> Live Account request received <span class="status">Waiting</span></div>
       <div class="ob-verification-row" id="obVerifyChildRequest"><span>○</span> Live related-record request received and rolled up <span class="status">Waiting</span></div>`
    : `<div class="ob-verification-row" id="obVerifyRoute"><span>○</span> Routing and pruning test <span class="status">Not tested</span></div>`;
  return `<section class="ob-verification" id="obUniversalVerification">
    <h3>Verify your CostPilot connection</h3>
    <p>This test validates CostPilot's live contract, routing, governance, pruning, and attribution envelope. It does not claim that an external agent is connected until you confirm that platform step.</p>
    <div class="ob-verification-list">
      <div class="ob-verification-row pass"><span>✓</span> Business context configured <span class="status">Ready</span></div>
      <div class="ob-verification-row pass"><span>✓</span> ${_obEsc(platform)} mapping generated <span class="status">Ready</span></div>
      <div class="ob-verification-row" id="obVerifyContract"><span>○</span> Universal contract available <span class="status">Not tested</span></div>
      ${liveRows}
      ${actionRow}
    </div>
    <button type="button" class="ob-btn-primary" id="obRunTestBtn" onclick="runUniversalSetupTest()">Run CostPilot Test →</button>
    <div class="ob-error" id="obVerificationError"></div>
    <label class="ob-activation-confirm">
      <input type="checkbox" id="obPlatformInstalled" onchange="confirmPlatformInstall(this.checked)" />
      <span>${installText}</span>
    </label>
    <button type="button" class="ob-btn-primary" id="obActivateBtn" onclick="activateUniversalConnection()" disabled>Activate Connection</button>
  </section>`;
}

function confirmPlatformInstall(confirmed) {
  if (obSelectedPlatform === "salesforce") {
    const row = document.getElementById("obVerifyAgentAction");
    if (row) {
      row.classList.toggle("pass", confirmed);
      row.querySelector("span").textContent = confirmed ? "✓" : "○";
      row.querySelector(".status").textContent = confirmed ? "Confirmed" : "Confirm below";
    }
  }
  refreshActivationButton();
}

function _markVerificationRow(id, label) {
  const row = document.getElementById(id);
  if (!row) return;
  row.classList.add("pass");
  row.querySelector("span").textContent = "✓";
  row.querySelector(".status").textContent = label;
}

async function runUniversalSetupTest() {
  const button = document.getElementById("obRunTestBtn");
  const error = document.getElementById("obVerificationError");
  button.disabled = true;
  button.textContent = "Testing…";
  error.textContent = "";
  try {
    const contractResponse = await fetch(`${CostPilot_URL}/api/integrations/contract`);
    if (!contractResponse.ok) throw new Error("Universal contract is unavailable.");
    const contract = await contractResponse.json();
    if (contract.contract_version !== "2026-07-26") throw new Error("Unexpected connector contract version.");
    _markVerificationRow("obVerifyContract", "Connected");

    if (obSelectedPlatform === "salesforce") {
      if (!obDiscoveryConnectionId) throw new Error("Reconnect Salesforce so CostPilot can verify the correct org and workspace.");
      const verifyResponse = await fetch(`${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/package-setup/verify`, {
        method: "POST",
      });
      const setup = await verifyResponse.json().catch(() => ({}));
      if (!verifyResponse.ok) throw new Error(setup.detail || `Salesforce verification failed (${verifyResponse.status}).`);
      if (setup.checklist?.org_verified && setup.checklist?.workspace_bound) {
        _markVerificationRow(
          "obVerifySalesforceOrg",
          `${setup.org?.organization_id || "Org verified"} · ${setup.workspace_id || "Workspace verified"}`,
        );
      }
      if (setup.verification?.parent_verified) {
        _markVerificationRow("obVerifyParentRequest", setup.verification.parent_record_name || "Received");
      }
      if (setup.verification?.child_verified) {
        _markVerificationRow("obVerifyChildRequest", setup.verification.child_record_name || "Received and rolled up");
      }
      if (!setup.verification?.verified) {
        throw new Error(setup.verification?.message || "Run one request from the Account and one from an approved related record, then check again.");
      }
      localStorage.setItem("cp_connection_test", JSON.stringify({
        platform: "salesforce",
        connection_id: obDiscoveryConnectionId,
        workspace_id: setup.workspace_id,
        organization_id: setup.org?.organization_id,
        tested_at: new Date().toISOString(),
        parent_audit_id: setup.verification.parent_audit_id,
        child_audit_id: setup.verification.child_audit_id,
      }));
      button.textContent = "Live Requests Verified ✓";
      refreshActivationButton();
      return;
    }

    const testResponse = await fetch(`${CostPilot_URL}/api/route`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contract_version: contract.contract_version,
        mode: "control",
        is_test: true,
        source: {
          platform: obSelectedPlatform,
          workspace_id: "onboarding-verification",
          agent_name: document.getElementById("obPlatAgent").value || "CostPilot Setup Test",
          department: document.getElementById("obPlatDept").value || "Operations",
        },
        request: {
          task: "Verify CostPilot connection",
          content: "Summarize this routine setup verification request.",
          payload_type: "text",
          auto_prune: true,
        },
      }),
    });
    if (!testResponse.ok) throw new Error(`Routing test failed (${testResponse.status}).`);
    const result = await testResponse.json();
    _markVerificationRow("obVerifyRoute", `${result.model_tier || "Model"} · ${result.routing_decision || "Routed"}`);
    localStorage.setItem("cp_connection_test", JSON.stringify({
      platform: obSelectedPlatform,
      tested_at: new Date().toISOString(),
      contract_version: contract.contract_version,
      model_tier: result.model_tier,
      routing_decision: result.routing_decision,
      tokens_saved: result.tokens_saved_by_pruning || 0,
    }));
    button.textContent = "Test Passed ✓";
    refreshActivationButton();
  } catch (testError) {
    error.textContent = testError.message;
    button.disabled = false;
    button.textContent = "Retry CostPilot Test →";
  }
}

function refreshActivationButton() {
  const tested = obSelectedPlatform === "salesforce"
    ? document.getElementById("obVerifyParentRequest")?.classList.contains("pass")
      && document.getElementById("obVerifyChildRequest")?.classList.contains("pass")
    : document.getElementById("obVerifyRoute")?.classList.contains("pass");
  const installed = document.getElementById("obPlatformInstalled")?.checked;
  const button = document.getElementById("obActivateBtn");
  if (button) button.disabled = !(tested && installed);
}

async function activateUniversalConnection() {
  const button = document.getElementById("obActivateBtn");
  const error = document.getElementById("obVerificationError");
  if (obSelectedPlatform === "salesforce") {
    button.disabled = true;
    button.textContent = "Activating…";
    try {
      const response = await fetch(`${CostPilot_URL}/api/integrations/connections/${obDiscoveryConnectionId}/package-setup/activate`, {
        method: "POST",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `Activation failed (${response.status}).`);
    } catch (activationError) {
      error.textContent = activationError.message;
      button.disabled = false;
      button.textContent = "Activate Connection";
      return;
    }
  }
  const record = {
    platform: obSelectedPlatform,
    platform_label: OB_PLATFORMS[obSelectedPlatform]?.label || obSelectedPlatform,
    object: document.getElementById("obPlatObject").value.trim(),
    department: document.getElementById("obPlatDept").value,
    agent_name: document.getElementById("obPlatAgent").value.trim(),
    salesforce_ai_mode: obSelectedPlatform === "salesforce" ? obSalesforceAiMode : null,
    salesforce_entry_points: obSelectedPlatform === "salesforce" ? _getSelectedSalesforceEntryPoints() : [],
    governed_record_types: obSelectedPlatform === "salesforce" ? getSalesforceGovernedObjects() : [],
    business_context: obBusinessContext,
    contract_version: "2026-07-26",
    status: "active",
    activated_at: new Date().toISOString(),
  };
  localStorage.setItem("cp_active_connection", JSON.stringify(record));
  setUniversalSetupStage(5);
  const section = document.getElementById("obUniversalVerification");
  const nextStep = record.platform === "salesforce"
    ? `Run one real request from a selected agent or Flow. When it invokes <strong>CostPilot Governed AI Work</strong>, the request will appear in Audit and reporting with its user, entry point, department, and Salesforce record.`
    : `Run one real request from ${_obEsc(record.platform_label)} to confirm live attribution.`;
  section.innerHTML = `<div class="ob-context-eyebrow">Connection active</div>
    <h3>${_obEsc(record.platform_label)} is ready for CostPilot</h3>
    <p>Requests can now be attributed to the user, ${_obEsc(obBusinessContext?.work_label || "work record")}, agent, department, and platform.</p>
    <p><strong>Final live check:</strong> ${nextStep}</p>
    <div class="ob-actions"><button class="ob-btn-primary" onclick="goToDashboard()">Open Dashboard →</button></div>`;
  section.scrollIntoView({ behavior: "smooth", block: "center" });
}

function _isSalesforceApiName(name) {
  return /^[A-Za-z][A-Za-z0-9_]*(?:__c|__mdt|__e|__x)?$/.test(String(name || ""));
}

// ── Code generators ───────────────────────────────────────────────────────────

function _genSalesforce(obj, dept, agent, fields, returnFields = []) {
  const sfFields = fields && fields.length ? fields : [{label:"Subject",name:"Subject"},{label:"Description",name:"Description"}];
  const apexVars = _apexVarNames(sfFields);
  const requestVars = sfFields.map((f, i) =>
    `        @InvocableVariable(required=${i === 0 ? "true " : "false"} label='${_codeStr(f.label || f.name)} (${_codeStr(f.name)})') public String ${apexVars[i]};`
  ).join("\n");
  const reqPrompt = _apexPrompt(sfFields, (f, i) => `req.${apexVars[i]}`);
  const mappingRows = sfFields.map((f, i) =>
    `<div class="ob-field-row"><span>${_obEsc(f.label || f.name)}</span><span class="mono">${_obEsc(f.name)}</span><span class="mono">${_obEsc(apexVars[i])}</span><span>Routed to CostPilot</span></div>`
  ).join("");
  const returnRows = returnFields.length ? returnFields.map(f =>
    `<div class="ob-field-row"><span>${_obEsc(_returnSourceLabel(f.source))}</span><span class="mono">${_obEsc(f.name)}</span><span class="mono">${_obEsc(f.source)}</span><span>Optional write-back</span></div>`
  ).join("") : `<div class="ob-field-row"><span>No return fields</span><span class="mono">—</span><span class="mono">—</span><span>Routes and logs without write-back</span></div>`;
  const mappingHtml = `<div class="ob-field-table">
    <div class="ob-field-row ob-field-row-header"><span>Prompt Label</span><span>Salesforce Field API Name</span><span>Apex Input</span><span>Behavior</span></div>
    <div class="ob-field-row"><span>Source Object</span><span class="mono">${_obEsc(obj)}</span><span class="mono">recordId</span><span>Record-triggered Flow</span></div>
    ${mappingRows}
    <div class="ob-field-row ob-field-row-header"><span>Return Value</span><span>Salesforce Field API Name</span><span>Source</span><span>Behavior</span></div>
    ${returnRows}
  </div>`;
  const sfReturnWrites = _salesforceReturnWrites(returnFields);
  const sfTrialReturnWrites = _salesforceTrialReturnWrites(returnFields);
  const governedObjects = getSalesforceGovernedObjects();
  const selectedEntryPoints = _getSelectedSalesforceEntryPoints();
  const selectedAgents = selectedEntryPoints.filter(item => item.kind === "agent");
  const selectedFlows = selectedEntryPoints.filter(item => item.kind === "flow");
  const selectedAgentNames = selectedAgents.map(item => item.label || item.name).join(", ");
  const selectedFlowNames = selectedFlows.map(item => item.label || item.name).join(", ");
  const agentforceGuide = obSalesforceAiMode === "agentforce"
    ? `<div class="ob-code-section" style="margin-top:20px">
        <div class="ob-code-header">
          <span class="ob-code-label">Agentforce — add the universal action</span>
          <span class="ob-code-hint">This is the step that places CostPilot in the AI request path.</span>
        </div>
        <div class="ob-flow-steps">
          <div class="ob-flow-step"><span class="ob-flow-num">1</span><div><strong>Open Agentforce Builder</strong><br/>Open the agent you want CostPilot to govern. Create or select a general AI-work topic.</div></div>
          <div class="ob-flow-step"><span class="ob-flow-num">2</span><div><strong>Add Action → Apex → Route Through CostPilot</strong><br/>Give the agent-facing action the label <code>CostPilot Governed AI Work</code>. This synchronous packaged action returns CostPilot's AI response to Agentforce and can be reused for summaries, drafting, analysis, recommendations, and other governed AI tasks.</div></div>
          <div class="ob-flow-step"><span class="ob-flow-num">3</span><div><strong>Map the context</strong><br/>Pass the current record ID, record name, user request, agent name, and department. Selected record types: <strong>${_obEsc(governedObjects.join(", ") || obj)}</strong>.</div></div>
          <div class="ob-flow-step"><span class="ob-flow-num">4</span><div><strong>Tell the topic when to use it</strong><br/>“For AI work involving a Salesforce record, call CostPilot Governed AI Work and return its AI response to the user.”</div></div>
          <div class="ob-flow-step"><span class="ob-flow-num">5</span><div><strong>Test one real request</strong><br/>A successful CostPilot setup test verifies the gateway. A real Agentforce request appearing in CostPilot Audit verifies the agent itself.</div></div>
        </div>
      </div>`
    : `<div class="ob-code-section" style="margin-top:20px">
        <div class="ob-code-header"><span class="ob-code-label">Flow, Apex, or custom AI</span></div>
        <p class="ob-code-hint">Invoke <code>Send to CostPilot</code> only when AI work occurs. Do not attach it to every record save, because ordinary CRM activity is not AI usage. For a conversational Agentforce response, use the packaged <code>Route Through CostPilot</code> action instead.</p>
      </div>`;

  // Agentforce customers should see the short packaged-action workflow, not
  // the legacy Apex implementation used by custom automation.
  if (obSalesforceAiMode === "agentforce" || selectedEntryPoints.length) {
    const approved = _getApprovedRelationshipMapping();
    const parentObject = approved?.parent_object || obj;
    const relatedObjects = (approved?.children || [])
      .filter(child => child.behavior !== "ignore");
    const relatedHtml = relatedObjects.length
      ? relatedObjects.map(child =>
          `<li><strong>${_obEsc(child.label || child.object)}</strong> — ${_obEsc(child.behavior.replaceAll("_", " "))}</li>`
        ).join("")
      : `<li>No related objects approved yet. Use <strong>Find related objects</strong> above before activation.</li>`;
    const relationshipPreview = `<div class="ob-code-section ob-agentforce-relationship">
      <div class="ob-code-header">
        <span class="ob-code-label">Business relationship</span>
        <span class="ob-code-hint">CostPilot uses this hierarchy for attribution and reporting.</span>
      </div>
      <div class="ob-relationship-preview">
        <div><span>Parent</span><strong>${_obEsc(parentObject)}</strong></div>
        <div><span>Related records</span><ul>${relatedHtml}</ul></div>
      </div>
    </div>`;
    const selectedSummary = `<div class="ob-entry-activation-summary">
      <strong>Selected for activation</strong>
      <span>Agentforce: ${_obEsc(selectedAgentNames || "None selected")}</span>
      <span>Flows: ${_obEsc(selectedFlowNames || "None selected")}</span>
    </div>`;
    const agentSteps = selectedAgents.length || (!selectedEntryPoints.length && obSalesforceAiMode === "agentforce")
      ? `<div class="ob-flow-step"><span class="ob-flow-num">2</span><div><strong>Add the action to Agentforce</strong><br/>Open each selected agent in Agentforce Builder, choose the topic that handles governed AI work, then select <code>Add Action → Apex → Route Through CostPilot</code>. Label it <code>CostPilot Governed AI Work</code>.</div></div>`
      : "";
    const flowSteps = selectedFlows.length
      ? `<div class="ob-flow-step"><span class="ob-flow-num">${selectedAgents.length ? "3" : "2"}</span><div><strong>Add the action to each selected Flow</strong><br/>Open the Flow, place <code>Route Through CostPilot</code> exactly where AI work should occur, and map the prompt, record ID, record name, flow name, and department. Do not attach it to every record save.</div></div>`
      : "";
    const contextStepNumber = 2 + (agentSteps ? 1 : 0) + (flowSteps ? 1 : 0);
    const setupSteps = `${selectedSummary}<div class="ob-flow-steps">
      <div class="ob-flow-step"><span class="ob-flow-num">1</span><div><strong>Confirm the CostPilot Salesforce components are installed</strong><br/>In Salesforce Setup, verify that the Apex action <code>Route Through CostPilot</code> is available. If it is missing, install the CostPilot pilot components first.</div></div>
      ${agentSteps}${flowSteps}
      <div class="ob-flow-step"><span class="ob-flow-num">${contextStepNumber}</span><div><strong>Map the request context</strong><br/>Pass the user request, current record ID, record name, agent or Flow name, and department. CostPilot uses the approved relationship above to connect related records to <strong>${_obEsc(parentObject)}</strong>.</div></div>
      ${agentSteps ? `<div class="ob-flow-step"><span class="ob-flow-num">${contextStepNumber + 1}</span><div><strong>Add one Agentforce instruction</strong><br/><code>For AI work involving a Salesforce record, call CostPilot Governed AI Work and return its AI response to the user.</code></div></div>` : ""}
      <div class="ob-flow-step"><span class="ob-flow-num">${contextStepNumber + (agentSteps ? 2 : 1)}</span><div><strong>Test each selected entry point</strong><br/>Run one real request from every selected agent and Flow. Verify each request appears in CostPilot with its user, entry-point name, record, tokens, cost, and parent context.</div></div>
    </div>`;
    return relationshipPreview
      + `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Agentforce setup</span><span class="ob-code-hint">No Apex code to copy for this path.</span></div>${setupSteps}</div>`
      + _obBanner("salesforce", parentObject, dept, agent)
      + _obActions();
  }

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
    private static final String CP_DEPARTMENT = '${_codeStr(dept)}';
    private static final String CP_AGENT      = '${_codeStr(agent)}';

    @InvocableMethod(label='Send to CostPilot')
    public static void sendToCostPilot(List<CostPilotRequest> requests) {
        if (System.isFuture() || System.isBatch()) return;
        CostPilotRequest req = requests[0];
        String prompt = ${reqPrompt};
        String department = String.isBlank(req.department) ? CP_DEPARTMENT : req.department;
        String agentName  = String.isBlank(req.agentName)  ? CP_AGENT      : req.agentName;
        sendAsync(req.recordId, prompt, department, agentName);
    }

    @future(callout=true)
    public static void sendAsync(String recordId, String prompt, String department, String agentName) {
        Http http = new Http();
        HttpRequest httpReq = new HttpRequest();
        httpReq.setEndpoint(ENDPOINT);
        httpReq.setMethod('POST');
        httpReq.setHeader('Content-Type',    'application/json');
        httpReq.setHeader('X-CostPilot-Key', CP_KEY);
        httpReq.setHeader('X-Department',    department);
        httpReq.setHeader('X-Agent-Name',    agentName);
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
                String aiResponse = null;
                List<Object> choices = (List<Object>) responseMap.get('choices');
                if (choices != null && !choices.isEmpty()) {
                    Map<String,Object> msg = (Map<String,Object>)((Map<String,Object>)choices[0]).get('message');
                    aiResponse = (String) msg.get('content');
                    System.debug('CostPilot AI Response: ' + aiResponse);
                }
                if (recordId != null) {
                    Schema.SObjectType objType = Id.valueOf(recordId).getSObjectType();
                    Map<String, Schema.SObjectField> fieldMap = objType.getDescribe().fields.getMap();
                    SObject rec = objType.newSObject(recordId);
                    Boolean hasUpdates = false;

${sfTrialReturnWrites || "                    // No return fields configured. CostPilot will still route, log, and report this request."}
                    if (hasUpdates) update rec;
                }
            } catch(Exception e) {
                System.debug('CostPilot field write skipped: ' + e.getMessage());
            }
        }
    }

    public class CostPilotRequest {
        @InvocableVariable(required=false label='agentName')  public String agentName;
        @InvocableVariable(required=false label='department') public String department;
        @InvocableVariable(required=false label='Record ID for optional write-back') public String recordId;
${requestVars}
    }
}`;

    const flowHtml = `<div class="ob-flow-steps">
      <div class="ob-flow-step"><span class="ob-flow-num">1</span>
        <div><strong>Developer Console → File → New → Apex Class</strong><br/>Name it <code>CostPilotCallout</code>, paste the class above exactly as shown. Save. No keys to fill in.</div></div>
      <div class="ob-flow-step"><span class="ob-flow-num">2</span>
        <div><strong>Setup → Remote Site Settings → New</strong><br/>Name: <code>CostPilot</code> · URL: <code>https://fage-engine-21cb49fe4806.herokuapp.com</code> · Active: ✓<br/><em style="color:var(--text-muted,#8b949e);font-size:11px">Required by Salesforce for any external HTTP callout — this is the only manual setup step.</em></div></div>
      <div class="ob-flow-step"><span class="ob-flow-num">3</span>
        <div><strong>Setup → Flows → New Flow → Record-Triggered</strong><br/>Object: <strong>${_obEsc(obj)}</strong> · Trigger: Created or Updated<br/>Add Action → Apex → Send to CostPilot<br/>Map: <code>agentName</code> → <strong>${_obEsc(agent)}</strong> · <code>department</code> → <strong>${_obEsc(dept)}</strong> · ${sfFields.map((f, i) => `${_obEsc(f.name)} → ${_obEsc(apexVars[i])}`).join(" · ")}<br/>You can also leave agentName/department blank to use the defaults built into the class.</div></div>
      <div class="ob-flow-step"><span class="ob-flow-num">4</span>
        <div><strong>Optional write-back</strong><br/>Create the return fields listed above, then map <code>$Record.Id</code> to <code>recordId</code>. If you skip this, CostPilot still routes and logs the request.</div></div>
      <div class="ob-flow-step"><span class="ob-flow-num">5</span>
        <div><strong>Save &amp; Activate</strong> — next time a ${_obEsc(obj)} is created or updated, CostPilot routes the call automatically. Your first call appears on your dashboard within seconds.</div></div>
    </div>`;

    return `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Salesforce Mapping</span><span class="ob-code-hint">These are the exact object and field API names this setup will route.</span></div>${mappingHtml}</div>`
      + _obCodeSection("Apex Class — paste into Developer Console", "No provider credential needed; map each Flow input to the field names you configured", apex)
      + agentforceGuide
      + `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Setup Steps</span></div>${flowHtml}</div>`
      + _obBanner("salesforce", obj, dept, agent) + _obActions();
  }

  // ── Full version: internal routing + custom fields ────────────────────────
  const apex =
`public class CostPilotCallout {
    private static final String CP_DEPARTMENT = '${_codeStr(dept)}';
    private static final String CP_AGENT      = '${_codeStr(agent)}';

    @InvocableMethod(label='Send to CostPilot')
    public static void sendToCostPilot(List<CostPilotRequest> requests) {
        if (System.isFuture() || System.isBatch()) return;
        CostPilotRequest req = requests[0];
        String payload = ${reqPrompt};
        String department = String.isBlank(req.department) ? CP_DEPARTMENT : req.department;
        String agentName  = String.isBlank(req.agentName)  ? CP_AGENT      : req.agentName;
        sendAsync(req.recordId, payload, department, agentName);
    }

    @future(callout=true)
    public static void sendAsync(String recordId, String payload, String department, String agentName) {
        Http http = new Http();
        HttpRequest httpReq = new HttpRequest();
        httpReq.setEndpoint('${CostPilot_URL}/api/route');
        httpReq.setMethod('POST');
        httpReq.setHeader('Content-Type', 'application/json');
        httpReq.setBody(JSON.serialize(new Map<String, Object>{
            'contract_version' => '2026-07-26',
            'mode'             => 'control',
            'source' => new Map<String, Object>{
                'platform'     => 'Salesforce',
                'workspace_id' => UserInfo.getOrganizationId(),
                'agent_name'   => agentName,
                'department'   => department
            },
            'actor' => new Map<String, Object>{
                'external_id' => UserInfo.getUserId(),
                'name'        => UserInfo.getName(),
                'email'       => UserInfo.getUserEmail(),
                'role'        => 'Member',
                'can_use_ai'  => true
            },
            'work' => new Map<String, Object>{
                'external_id'    => recordId,
                'type'           => '${_codeStr(obj)}',
                'name'           => '${_codeStr(obj)} ' + recordId,
                'sync_if_missing'=> true
            },
            'request' => new Map<String, Object>{
                'task'         => 'Process ${_codeStr(obj)} record',
                'content'      => payload,
                'payload_type' => 'text',
                'auto_prune'   => true
            }
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
            Schema.SObjectType objType = Id.valueOf(recordId).getSObjectType();
            Map<String, Schema.SObjectField> fieldMap = objType.getDescribe().fields.getMap();
            SObject rec = objType.newSObject(recordId);
            Boolean hasUpdates = false;

${sfReturnWrites || "            // No return fields configured. CostPilot will still route, log, and report this request."}
            if (hasUpdates) update rec;
        }
    }

    public class CostPilotRequest {
        @InvocableVariable(required=false label='agentName')  public String agentName;
        @InvocableVariable(required=false label='department') public String department;
        @InvocableVariable(required=true  label='Record ID')          public String recordId;
${requestVars}
    }
}`;

  const flowHtml = `<div class="ob-flow-steps">
    <div class="ob-flow-step"><span class="ob-flow-num">1</span>
      <div><strong>Setup → Flows → New Flow</strong><br/>Type: <em>Record-Triggered</em> · Object: <strong>${_obEsc(obj)}</strong> · Trigger: <em>Created or updated</em> · Optimize for: <em>Actions and Related Records</em></div></div>
    <div class="ob-flow-step"><span class="ob-flow-num">2</span>
      <div><strong>Add Action → Apex → Send to CostPilot</strong><br/>Map: <code>agentName</code> → <strong>${_obEsc(agent)}</strong> · <code>department</code> → <strong>${_obEsc(dept)}</strong> · Record ID · ${sfFields.map((f, i) => `${_obEsc(f.name)} → ${_obEsc(apexVars[i])}`).join(" · ")}<br/>Agent and department are optional Flow inputs; if blank, the class uses the onboarding defaults.</div></div>
    <div class="ob-flow-step"><span class="ob-flow-num">3</span>
      <div><strong>Save &amp; Activate</strong><br/>If CostPilot does not show an event, check Setup → Apex Jobs and Setup → Paused and Failed Flow Interviews.</div></div>
  </div>`;

  const testApex =
`// Developer Console → Debug → Open Execute Anonymous Window
// Replace the sample record ID with a real ${obj} ID from your org.
CostPilotCallout.CostPilotRequest req = new CostPilotCallout.CostPilotRequest();
req.recordId = 'REPLACE_WITH_${obj.toUpperCase()}_ID';
req.agentName = '${_codeStr(agent)}';
req.department = '${_codeStr(dept)}';
${sfFields.map((f, i) => `req.${apexVars[i]} = 'Test value for ${_codeStr(f.label || f.name)}';`).join("\n")}
CostPilotCallout.sendToCostPilot(new List<CostPilotCallout.CostPilotRequest>{ req });`;

  const fieldHtml = `<div class="ob-field-table">
    <div class="ob-field-row ob-field-row-header"><span>Label</span><span>API Name</span><span>Type</span><span>Settings</span></div>
    ${returnFields.length ? returnFields.map(f => `<div class="ob-field-row"><span>${_obEsc(_returnSourceLabel(f.source))}</span><span class="mono">${_obEsc(f.name)}</span><span>${f.source === "cost" ? "Currency" : f.source === "response" ? "Long Text" : "Text"}</span><span>${f.source === "cost" ? "12,6" : f.source === "response" ? "32,768 chars" : "255 chars"}</span></div>`).join("") : `<div class="ob-field-row"><span>No return fields configured</span><span class="mono">—</span><span>—</span><span>Add fields above and regenerate if you want write-back</span></div>`}
  </div>`;

  return `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Salesforce Mapping</span><span class="ob-code-hint">These are the exact object and field API names this setup will route.</span></div>${mappingHtml}</div>`
    + _obCodeSection("Step 1 — Apex Class", "Developer Console → File → Open → CostPilotCallout → Replace all → Save", apex)
    + agentforceGuide
    + `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Step 2 — Salesforce Flow</span></div>${flowHtml}</div>`
    + `<div class="ob-code-section" style="margin-top:20px"><div class="ob-code-header"><span class="ob-code-label">Step 3 — Custom Fields on ${_obEsc(obj)}</span><span class="ob-code-hint">Setup → Object Manager → ${_obEsc(obj)} → Fields &amp; Relationships → New</span></div>${fieldHtml}</div>`
    + _obCodeSection("Debug Test — Execute Anonymous", "Runs the Apex action without waiting on your Flow trigger", testApex)
    + _obBanner("salesforce", obj, dept, agent) + _obActions();
}

function _genServiceNow(obj, dept, agent, fields, returnFields = []) {
  const code =
`// ServiceNow Flow Designer custom Action — Script step
// Runs only when a Flow, UI Action, Virtual Agent, or Now Assist workflow
// explicitly invokes "CostPilot Governed AI Request".
(function execute(inputs, outputs) {
    var tableName = String(inputs.record_table || '${obj}').trim();
    var recordSysId = String(inputs.record_sys_id || '').trim();
    var prompt = String(inputs.prompt || '').trim();
    if (!tableName || !recordSysId || !prompt) {
        throw new Error('record_table, record_sys_id, and prompt are required.');
    }

    var record = new GlideRecordSecure(tableName);
    if (!record.get(recordSysId)) {
        throw new Error('The requested ServiceNow record was not found or is not accessible.');
    }

    var apiBaseUrl = String(gs.getProperty(
        'costpilot.api_base_url',
        '${CostPilot_URL}'
    )).replace(/\\/+$/, '');
    var rm = new sn_ws.RESTMessageV2();
    rm.setEndpoint(apiBaseUrl + '/api/route');
    rm.setHttpMethod('POST');
    rm.setRequestHeader('Content-Type', 'application/json');
    rm.setHttpTimeout(120000);
    rm.setRequestBody(JSON.stringify({
        contract_version: '2026-07-26',
        mode: 'control',
        source: {
            platform: 'ServiceNow',
            workspace_id: gs.getProperty('instance_name'),
            agent_name: String(inputs.agent_name || '${agent}'),
            department: String(inputs.department || '${dept}')
        },
        actor: {
            external_id: gs.getUserID(),
            name: gs.getUserDisplayName(),
            email: gs.getUser().getEmail()
        },
        work: {
            external_id: record.getUniqueValue(),
            type: tableName,
            name: record.getDisplayValue() || tableName + ' ' + record.getUniqueValue(),
            sync_if_missing: true
        },
        request: {
            task: String(inputs.task || 'ServiceNow AI request'),
            content: prompt,
            payload_type: 'text',
            auto_prune: true
        }
    }));

    var response = rm.execute();
    var status = response.getStatusCode();
    if (status < 200 || status >= 300) {
        throw new Error('CostPilot request failed (' + status + '): ' + response.getBody());
    }
    var result = JSON.parse(response.getBody());

    outputs.ai_response = result.simulated_response || '';
    outputs.model_tier = result.model_tier || '';
    outputs.model_name = result.model_name || '';
    outputs.routing_decision = result.routing_decision || '';
    outputs.cost_usd = Number(result.cost_usd || 0);
    outputs.input_tokens = Number(result.input_tokens || 0);
    outputs.output_tokens = Number(result.output_tokens || 0);
    outputs.tokens_pruned = Number(result.tokens_saved_by_pruning || 0);
    outputs.work_item_id = result.work_item_id || '';
    outputs.work_item_name = result.work_item_name || '';
})(inputs, outputs);`;

  const setupHtml = `<div class="ob-code-section" style="margin-top:20px">
    <div class="ob-code-header"><span class="ob-code-label">Flow Designer Action setup</span><span class="ob-code-hint">This action runs only when an AI workflow explicitly calls it.</span></div>
    <div class="ob-install-steps">
      <div><strong>1. Import the CostPilot Update Set or create the Action manually</strong><br/>Name: <strong>CostPilot Governed AI Request</strong> · Application: Global</div>
      <div><strong>2. Configure the endpoint</strong><br/>System Property: <code>costpilot.api_base_url</code> · Value: <code>${_obEsc(CostPilot_URL)}</code>. Credentials and tokens are configured separately and are never packaged.</div>
      <div><strong>3. Add Action Inputs</strong><br/><code>prompt</code>, <code>record_table</code>, and <code>record_sys_id</code> as required String inputs. Add optional String inputs <code>task</code>, <code>agent_name</code>, and <code>department</code>.</div>
      <div><strong>4. Add a Script step</strong><br/>Create matching Script inputs and paste the generated script below.</div>
      <div><strong>5. Add Action Outputs</strong><br/><code>ai_response</code>, <code>model_tier</code>, <code>model_name</code>, <code>routing_decision</code>, <code>work_item_id</code>, and <code>work_item_name</code> as String; <code>cost_usd</code> as Decimal; <code>input_tokens</code>, <code>output_tokens</code>, and <code>tokens_pruned</code> as Integer.</div>
      <div><strong>6. Invoke it only from AI experiences</strong><br/>Add this action to a user-triggered Flow, UI Action, Virtual Agent, or Now Assist workflow. Do not attach it to a record insert/update trigger.</div>
    </div>
  </div>`;

  return _platformMappingHtml("servicenow", obj, fields, returnFields)
    + _obCodeSection(
      "CostPilot Governed AI Request — Script step",
      "Flow Designer → Action → Add Script step · Explicit AI requests only",
      code
    ) + setupHtml + _obBanner("servicenow", obj, dept, agent) + _obActions();
}

function _genHubSpot(obj, dept, agent, fields, returnFields = []) {
  const prompt = _jsPrompt(fields, "event.inputFields", (v, f) => `(${v}['${_codeStr(f)}'] || '')`);
  const outputFields = returnFields.length
    ? returnFields.map(f => `      '${_codeStr(f.name)}': ${_jsResultExpr(f.source, "res.data")}`).join(",\n")
    : "      // No return properties configured. CostPilot still routes, logs, and reports this request.";
  const code =
`// HubSpot Custom Code Action (Node.js)
// Operations Hub → Workflows → Add Action → Custom Code
const axios = require('axios');

exports.main = async (event, callback) => {
  const text = ${prompt};
  const res  = await axios.post('${CostPilot_URL}/api/route', {
    contract_version: '2026-07-26',
    mode: 'control',
    source: {
      platform: 'HubSpot',
      workspace_id: String(event.origin?.portalId || 'hubspot'),
      agent_name: '${agent}',
      department: '${dept}',
    },
    actor: event.origin?.userId ? {
      external_id: String(event.origin.userId),
      name: String(event.origin.userId),
    } : null,
    work: {
      external_id: String(event.object?.objectId || event.callbackId),
      type: '${obj}',
      name: '${obj} ' + String(event.object?.objectId || event.callbackId),
      sync_if_missing: true,
    },
    request: {
      task: 'Process ${obj} record',
      content: text,
      payload_type: 'text',
      auto_prune: true,
    },
  });
  callback({
    outputFields: {
${outputFields}
    }
  });
};`;

  return _platformMappingHtml("hubspot", obj, fields, returnFields)
    + _obCodeSection(
    "Custom Code Action",
    "Operations Hub → Workflows → Add Action → Custom Code → Paste",
    code
  ) + _obBanner("hubspot", obj, dept, agent) + _obActions();
}

function _genDynamics(obj, dept, agent, fields, returnFields = []) {
  const dynPrompt = _plainPromptTemplate(fields, f => `@{triggerOutputs()?['body/${f.name}']}`);
  const dynReturns = returnFields.length
    ? returnFields.map(f => `${f.name}: @{body('HTTP')?['${_routeResultKey(f.source)}']}`).join("\n")
    : "No return fields configured. CostPilot still routes, logs, and reports this request.";
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
${dynReturns}`;

  return _platformMappingHtml("dynamics", obj, fields, returnFields)
    + _obCodeSection(
    "Power Automate HTTP Action",
    "Power Automate → New Flow → Automated → Dataverse trigger → Add HTTP action",
    code
  ) + _obBanner("dynamics", obj, dept, agent) + _obActions();
}

function _genZendesk(obj, dept, agent, fields, returnFields = []) {
  const prompt = _jsPrompt(fields, "event.payload", (v, f) =>
    `((${v}.ticket && ${v}.ticket['${_codeStr(f)}']) || ${v}['${_codeStr(f)}'] || '')`
  );
  const returnObject = returnFields.length
    ? returnFields.map(f => `    ${_safeVar(f.name)}: ${_routeResultExpr(f.source)}`).join(",\n")
    : "    message: 'No return fields configured; CostPilot routed and logged the request.'";
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
  return {
    status: 200,
${returnObject}
  };
};`;

  return _platformMappingHtml("zendesk", obj, fields, returnFields)
    + _obCodeSection(
    "Sunshine Function",
    "Admin Center → Apps and integrations → Sunshine Functions → Create → Node.js 18 · no Zendesk token required for first route",
    code
  ) + _obBanner("zendesk", obj, dept, agent) + _obActions();
}

function _genPython(obj, dept, agent, fields, returnFields = []) {
  const params = fields.map(f => `${_safeVar(f.name)}: str`).join(", ");
  const prompt = _pythonPrompt(fields);
  const returnMap = returnFields.length
    ? returnFields.map(f => `# ${f.name} = ${_routeResultExpr(f.source, "python")}`).join("\n")
    : "# No return fields configured. CostPilot still routes, logs, and reports this request.";
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
# print(result["simulated_response"])
${returnMap}`;

  return _platformMappingHtml("python", obj, fields, returnFields)
    + _obCodeSection("Python", "pip install requests · call this function from your app or workflow", python)
    + _obBanner("python", obj, dept, agent) + _obActions();
}

function _genNode(obj, dept, agent, fields, returnFields = []) {
  const prompt = _jsPrompt(fields, "input", (v, f) => `(${v}.${_safeVar(f)} || '')`);
  const exampleObj = fields.map(f => `//   ${_safeVar(f.name)}: '...'`).join(",\n");
  const returnMap = returnFields.length
    ? returnFields.map(f => `// const ${_safeVar(f.name)} = ${_jsResultExpr(f.source, "result")};`).join("\n")
    : "// No return fields configured. CostPilot still routes, logs, and reports this request.";
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
// });
${returnMap}`;

  return _platformMappingHtml("nodejs", obj, fields, returnFields)
    + _obCodeSection("Node.js", "Uses built-in fetch in Node 18+", code)
    + _obBanner("nodejs", obj, dept, agent) + _obActions();
}

function _genJava(obj, dept, agent, fields, returnFields = []) {
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

  const returnNote = returnFields.length
    ? `\n// Return fields to parse from the JSON response:\n${returnFields.map(f => `// ${f.name} <= ${_routeResultKey(f.source)} (${_returnSourceLabel(f.source)})`).join("\n")}`
    : "\n// No return fields configured. CostPilot still routes, logs, and reports this request.";

  return _platformMappingHtml("java", obj, fields, returnFields)
    + _obCodeSection("Java", "Java 11+ HttpClient", code)
    + _obCodeSection("Java Return Mapping", "Parse these response keys if you want write-back in your app", returnNote)
    + _obBanner("java", obj, dept, agent) + _obActions();
}

function _genRuby(obj, dept, agent, fields, returnFields = []) {
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

# response = route_to_costpilot(${fields.map(f => `${_safeVar(f.name)}: "..."`).join(", ")})
${returnFields.length ? returnFields.map(f => `# ${_safeVar(f.name)} = ${_routeResultExpr(f.source, "ruby")}`).join("\n") : "# No return fields configured. CostPilot still routes, logs, and reports this request."}`;

  return _platformMappingHtml("ruby", obj, fields, returnFields)
    + _obCodeSection("Ruby", "Uses Net::HTTP from the Ruby standard library", code)
    + _obBanner("ruby", obj, dept, agent) + _obActions();
}

function _genRest(obj, dept, agent, fields, returnFields = []) {
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
  const returnNote = returnFields.length
    ? returnFields.map(f => `${f.name} <= response.${_routeResultKey(f.source)} (${_returnSourceLabel(f.source)})`).join("\n")
    : "No return fields configured. CostPilot still routes, logs, and reports this request.";

  return _platformMappingHtml("rest", obj, fields, returnFields)
    + _obCodeSection("REST / cURL", "Replace ${...} placeholders with values from your app or shell", curl)
    + _obCodeSection("Return Mapping", "Read these response keys if you want write-back in your app", returnNote)
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
