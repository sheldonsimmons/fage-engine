/**
 * models.js — CostPilot Model Registry UI
 *
 * Renders tier reference cards, model table, and add/edit modal.
 */

const TIERS = {
  1: { name: "Scout",      icon: "⚡", tagline: "Fast, affordable, handles routine tasks",              best_for: "FAQs, status lookups, simple summaries",           examples: "GPT-4o mini, Claude Haiku", color: "#3fb950" },
  2: { name: "Analyst",    icon: "🔍", tagline: "Balanced reasoning for most business tasks",           best_for: "Customer emails, data summarization, drafting",     examples: "GPT-4o, Claude Sonnet",     color: "#58a6ff" },
  3: { name: "Advisor",    icon: "💡", tagline: "Deep reasoning for complex or sensitive work",         best_for: "Contract review, escalations, multi-step analysis", examples: "GPT-4 Turbo, Claude Opus",  color: "#d29922" },
  4: { name: "Strategist", icon: "🎯", tagline: "Highest capability for mission-critical decisions",    best_for: "Legal, financial, compliance-heavy tasks",          examples: "o3, Claude Opus Max",       color: "#f85149" },
};

let _editingId  = null;
let _allModels  = [];
let _selectedTier = null;

// ── Boot ──────────────────────────────────────────────────────────────────────
// Scripts are at bottom of <body> so DOM is ready — call directly.

renderTierCards();
renderTierOptions();
loadModels();

// ── Tier reference cards ──────────────────────────────────────────────────────

function renderTierCards() {
  const wrap = document.getElementById("tierCards");
  wrap.innerHTML = Object.entries(TIERS).map(([tier, t]) => {
    const count = _allModels.filter(m => m.tier === parseInt(tier)).length;
    return (
      '<div class="mdl-tier-card" style="--tier-color:' + t.color + '">' +
        '<div class="mdl-tier-icon">' + t.icon + '</div>' +
        '<div class="mdl-tier-name" style="color:' + t.color + '">' + t.name + '</div>' +
        '<div class="mdl-tier-tagline">' + t.tagline + '</div>' +
        '<div class="mdl-tier-meta"><strong>Best for:</strong> ' + t.best_for + '</div>' +
        '<div class="mdl-tier-meta"><strong>Examples:</strong> ' + t.examples + '</div>' +
        '<div class="mdl-tier-count" id="tierCount' + tier + '">' +
          (count > 0 ? count + ' model' + (count !== 1 ? 's' : '') + ' registered' : 'No models yet') +
        '</div>' +
      '</div>'
    );
  }).join("");
}

function updateTierCounts() {
  Object.keys(TIERS).forEach(tier => {
    const el = document.getElementById("tierCount" + tier);
    if (!el) return;
    const count = _allModels.filter(m => m.tier === parseInt(tier)).length;
    el.textContent = count > 0 ? count + " model" + (count !== 1 ? "s" : "") + " registered" : "No models yet";
  });
}

// ── Tier option radio buttons (in modal) ──────────────────────────────────────

function renderTierOptions() {
  const wrap = document.getElementById("tierOptions");
  wrap.innerHTML = Object.entries(TIERS).map(([tier, t]) => {
    return (
      '<label class="mdl-tier-option" id="tierOpt' + tier + '" onclick="selectTier(' + tier + ')">' +
        '<div class="mdl-tier-opt-left">' +
          '<div class="mdl-tier-opt-icon" style="color:' + t.color + '">' + t.icon + '</div>' +
          '<div>' +
            '<div class="mdl-tier-opt-name" style="color:' + t.color + '">' + t.name + '</div>' +
            '<div class="mdl-tier-opt-desc">' + t.tagline + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="mdl-tier-opt-check" id="tierCheck' + tier + '"></div>' +
      '</label>'
    );
  }).join("");
}

function selectTier(tier) {
  _selectedTier = parseInt(tier);
  Object.keys(TIERS).forEach(t => {
    const opt   = document.getElementById("tierOpt"   + t);
    const check = document.getElementById("tierCheck" + t);
    if (parseInt(t) === _selectedTier) {
      opt.classList.add("selected");
      check.textContent = "✓";
    } else {
      opt.classList.remove("selected");
      check.textContent = "";
    }
  });
}

// ── Model table ───────────────────────────────────────────────────────────────

async function loadModels() {
  const tier     = document.getElementById("filterTier").value;
  const provider = document.getElementById("filterProvider").value;
  const enabled  = document.getElementById("filterEnabled").value;

  const params = new URLSearchParams();
  if (tier)     params.set("tier", tier);
  if (provider) params.set("provider", provider);
  if (enabled !== "") params.set("enabled", enabled);

  try {
    _allModels = await apiGet("/api/models?" + params.toString());
    renderTable(_allModels);
    updateTierCounts();
  } catch (e) {
    document.getElementById("modelTableBody").innerHTML =
      '<tr><td colspan="9" class="mdl-placeholder" style="color:var(--accent-red)">Error loading models: ' + e.message + '</td></tr>';
  }
}

function renderTable(models) {
  const tbody = document.getElementById("modelTableBody");
  const empty = document.getElementById("modelEmpty");
  const table = tbody ? tbody.closest("table") : null;

  if (!models.length) {
    if (table) table.style.display = "none";
    if (empty) empty.style.display = "flex";
    return;
  }

  if (table) table.style.display = "";
  if (empty) empty.style.display = "none";

  tbody.innerHTML = models.map(m => {
    const tierInfo = TIERS[m.tier] || {};
    const enabledBadge = m.is_enabled
      ? '<span class="mdl-status-badge mdl-status-on">Enabled</span>'
      : '<span class="mdl-status-badge mdl-status-off">Disabled</span>';
    const defaultBadge = m.is_default
      ? '<span class="mdl-default-badge">★ Default</span>'
      : '<span class="mdl-default-none">—</span>';

    return (
      '<tr>' +
        '<td>' +
          '<div class="mdl-model-name">' + m.display_name + '</div>' +
          (m.department ? '<div class="mdl-dept-scope">🏢 ' + m.department + ' only</div>' : '') +
          (m.notes ? '<div class="mdl-model-notes">' + m.notes + '</div>' : '') +
        '</td>' +
        '<td>' +
          '<span class="mdl-provider-badge">' + m.provider + '</span>' +
        '</td>' +
        '<td>' +
          '<div class="mdl-tier-badge" style="color:' + (tierInfo.color || "#8b949e") + '; border-color:' + (tierInfo.color || "#8b949e") + '">' +
            (tierInfo.icon || "◈") + ' ' + m.tier_name +
          '</div>' +
          '<div class="mdl-tier-tagline-sm">' + m.tier_tagline + '</div>' +
        '</td>' +
        '<td><code class="mdl-model-id">' + m.model_id + '</code></td>' +
        '<td class="mdl-cost">$' + m.cost_input_per_1m.toFixed(2) + '</td>' +
        '<td class="mdl-cost">$' + m.cost_output_per_1m.toFixed(2) + '</td>' +
        '<td>' + enabledBadge + '</td>' +
        '<td>' + defaultBadge + '</td>' +
        '<td class="mdl-actions">' +
          '<button class="mdl-action-btn" onclick="openEditModal(' + m.id + ')">Edit</button>' +
          '<button class="mdl-action-btn mdl-action-toggle" onclick="toggleModel(' + m.id + ', event)">' +
            (m.is_enabled ? 'Disable' : 'Enable') +
          '</button>' +
          '<button class="mdl-action-btn mdl-action-delete" onclick="deleteModel(' + m.id + ', event)">Delete</button>' +
        '</td>' +
      '</tr>'
    );
  }).join("");
}

// ── Toggle / Delete ───────────────────────────────────────────────────────────

async function toggleModel(id, e) {
  e.stopPropagation();
  try {
    await apiPatch("/api/models/" + id + "/toggle", null);
    loadModels();
  } catch (err) {
    alert("Toggle failed: " + err.message);
  }
}

async function deleteModel(id, e) {
  e.stopPropagation();
  const model = _allModels.find(m => m.id === id);
  if (!confirm("Delete " + (model ? model.display_name : "this model") + "? This cannot be undone.")) return;
  try {
    await apiDelete("/api/models/" + id);
    loadModels();
  } catch (err) {
    alert("Delete failed: " + err.message);
  }
}

// ── Modal ─────────────────────────────────────────────────────────────────────

function openAddModal() {
  _editingId = null;
  document.getElementById("modalTitle").textContent = "Add Model";
  document.getElementById("saveBtn").textContent    = "Save Model";
  clearForm();
  selectTier(1);
  showModal();
}

function openEditModal(id) {
  const model = _allModels.find(m => m.id === id);
  if (!model) return;

  _editingId = id;
  document.getElementById("modalTitle").textContent = "Edit Model";
  document.getElementById("saveBtn").textContent    = "Save Changes";

  document.getElementById("fDisplayName").value = model.display_name;
  document.getElementById("fProvider").value    = model.provider;
  document.getElementById("fModelId").value     = model.model_id;
  document.getElementById("fCostIn").value      = model.cost_input_per_1m;
  document.getElementById("fCostOut").value     = model.cost_output_per_1m;
  document.getElementById("fEnabled").checked    = model.is_enabled;
  document.getElementById("fDefault").checked    = model.is_default;
  document.getElementById("fNotes").value        = model.notes || "";
  document.getElementById("fDepartment").value   = model.department || "";

  selectTier(model.tier);
  showModal();
}

function showModal() {
  document.getElementById("modalOverlay").style.display = "block";
  document.getElementById("modelModal").style.display   = "flex";
  document.getElementById("modalError").style.display   = "none";
  document.getElementById("fDisplayName").focus();
}

function closeModal() {
  document.getElementById("modalOverlay").style.display = "none";
  document.getElementById("modelModal").style.display   = "none";
}

function clearForm() {
  ["fDisplayName","fProvider","fModelId","fCostIn","fCostOut","fNotes","fDepartment"].forEach(id => {
    document.getElementById(id).value = "";
  });
  document.getElementById("fEnabled").checked = true;
  document.getElementById("fDefault").checked = false;
  _selectedTier = null;
  Object.keys(TIERS).forEach(t => {
    const opt = document.getElementById("tierOpt" + t);
    const chk = document.getElementById("tierCheck" + t);
    if (opt) opt.classList.remove("selected");
    if (chk) chk.textContent = "";
  });
}

async function saveModel() {
  const displayName = document.getElementById("fDisplayName").value.trim();
  const provider    = document.getElementById("fProvider").value;
  const modelId     = document.getElementById("fModelId").value.trim();
  const costIn      = parseFloat(document.getElementById("fCostIn").value)  || 0;
  const costOut     = parseFloat(document.getElementById("fCostOut").value) || 0;
  const isEnabled   = document.getElementById("fEnabled").checked;
  const isDefault   = document.getElementById("fDefault").checked;
  const notes       = document.getElementById("fNotes").value.trim();
  const department  = document.getElementById("fDepartment").value || null;

  const errEl = document.getElementById("modalError");
  errEl.style.display = "none";

  if (!displayName) { showError("Display Name is required."); return; }
  if (!provider)    { showError("Please select a provider."); return; }
  if (!modelId)     { showError("API Model ID is required."); return; }
  if (!_selectedTier) { showError("Please select a tier."); return; }

  const payload = {
    display_name:       displayName,
    provider:           provider,
    model_id:           modelId,
    tier:               _selectedTier,
    cost_input_per_1m:  costIn,
    cost_output_per_1m: costOut,
    is_enabled:         isEnabled,
    is_default:         isDefault,
    notes:              notes || null,
    department:         department,
  };

  const saveBtn = document.getElementById("saveBtn");
  saveBtn.disabled = true;
  saveBtn.textContent = "Saving...";

  try {
    if (_editingId) {
      await apiPut("/api/models/" + _editingId, payload);
    } else {
      await apiPost("/api/models", payload);
    }
    closeModal();
    loadModels();
  } catch (err) {
    showError(err.message || "Save failed.");
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = _editingId ? "Save Changes" : "Save Model";
  }
}

function showError(msg) {
  const el = document.getElementById("modalError");
  el.textContent = msg;
  el.style.display = "block";
}
