/**
 * models.js — CostPilot Model Registry UI
 *
 * Renders tier reference cards, model table, and add/edit modal.
 */

// ── Known model presets (loaded from API) ─────────────────────────────────────

async function renderPresetOptions() {
  const sel = document.getElementById("fPreset");
  if (!sel) return;
  try {
    const data = await apiGet("/api/models/known");
    let html = '<option value="">— or choose a preset to auto-fill —</option>';
    (data.groups || []).forEach(group => {
      html += `<optgroup label="${group.label}">`;
      group.models.forEach(m => {
        html += `<option value="${m.id}">${m.display_name} — ${m.model_id}</option>`;
      });
      html += `</optgroup>`;
    });
    sel.innerHTML = html;
  } catch (e) {
    sel.innerHTML = '<option value="">— presets unavailable —</option>';
  }
}

function applyPreset() {
  const sel   = document.getElementById("fPreset");
  const id    = sel.value;
  if (!id) return;
  // Find the selected model from the optgroup options
  const opt   = sel.querySelector(`option[value="${id}"]`);
  if (!opt) return;
  // Re-fetch to get full data for this id
  apiGet("/api/models/known/all").then(models => {
    const p = models.find(m => String(m.id) === String(id));
    if (!p) return;
    document.getElementById("fDisplayName").value = p.display_name;
    document.getElementById("fProvider").value    = p.provider;
    document.getElementById("fModelId").value     = p.model_id;
    document.getElementById("fCostIn").value      = p.cost_input_per_1m.toFixed(2);
    document.getElementById("fCostOut").value     = p.cost_output_per_1m.toFixed(2);
    selectTier(p.tier);
  }).catch(() => {});
}

// ── Known Models Admin Panel ──────────────────────────────────────────────────

function openKnownModelsPanel() {
  document.getElementById("knownModelsPanel").style.display = "block";
  loadKnownModelsTable();
}

function closeKnownModelsPanel() {
  document.getElementById("knownModelsPanel").style.display = "none";
  // Refresh preset dropdown in case admin made changes
  renderPresetOptions();
}

function km_syncGroup() {
  const provider = document.getElementById("km_provider").value;
  const groupEl  = document.getElementById("km_provider_group");
  if (!groupEl.value && provider) groupEl.value = provider;
}

async function loadKnownModelsTable() {
  const tbody = document.getElementById("knownModelsTableBody");
  tbody.innerHTML = '<tr><td colspan="7" style="padding:20px; text-align:center; color:var(--text-muted);">Loading...</td></tr>';
  try {
    const models = await apiGet("/api/models/known/all");
    if (!models.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="padding:20px; text-align:center; color:var(--text-muted);">No known models yet. Add one above.</td></tr>';
      return;
    }
    tbody.innerHTML = models.map(m => `
      <tr style="border-bottom:1px solid var(--border); opacity:${m.is_active ? 1 : 0.45};">
        <td style="padding:10px; color:var(--text-primary); font-weight:500;">
          ${m.display_name}
          ${m.notes ? `<div style="font-size:10px; color:var(--text-muted); margin-top:2px;">${m.notes}</div>` : ""}
        </td>
        <td style="padding:10px;"><code style="font-size:11px; color:var(--accent); background:var(--bg-base); padding:2px 6px; border-radius:3px;">${m.model_id}</code></td>
        <td style="padding:10px; color:var(--text-muted); font-size:11px;">${m.provider_group}</td>
        <td style="padding:10px; color:var(--text-muted); font-size:11px;">${getTierIcon(m.tier)} ${getTierName(m.tier)}</td>
        <td style="padding:10px; color:var(--text-muted); font-size:11px;">
          $${m.cost_input_per_1m.toFixed(2)} / $${m.cost_output_per_1m.toFixed(2)}
          ${(() => {
            if (!m.updated_at) return "";
            const days = Math.floor((Date.now() - new Date(m.updated_at)) / 86400000);
            if (days >= 30) return `<div style="margin-top:3px;"><span title="Pricing last updated ${days} days ago — verify with your provider" style="font-size:10px; color:var(--accent-yellow); border:1px solid var(--accent-yellow); border-radius:3px; padding:1px 5px; cursor:default;">⚠ ${days}d old — verify pricing</span></div>`;
            return "";
          })()}
        </td>
        <td style="padding:10px;">
          <button onclick="toggleKnownModel(${m.id})"
            style="font-size:11px; padding:3px 10px; border-radius:4px; cursor:pointer; font-weight:600;
                   border:1px solid ${m.is_active ? "var(--accent-green)" : "var(--border)"};
                   color:${m.is_active ? "var(--accent-green)" : "var(--text-muted)"};
                   background:transparent;">
            ${m.is_active ? "Visible" : "Hidden"}
          </button>
        </td>
        <td style="padding:10px; display:flex; gap:6px;">
          <button onclick="editKnownModel(${m.id})"
            style="font-size:11px; padding:3px 10px; border-radius:4px; cursor:pointer;
                   border:1px solid var(--accent); color:var(--accent); background:transparent;">
            Edit
          </button>
          <button onclick="deleteKnownModel(${m.id}, '${m.display_name.replace(/'/g,"\\'")}', event)"
            style="font-size:11px; padding:3px 10px; border-radius:4px; cursor:pointer;
                   border:1px solid var(--accent-red); color:var(--accent-red); background:transparent;">
            Delete
          </button>
        </td>
      </tr>
    `).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" style="padding:20px; text-align:center; color:var(--accent-red);">Error loading: ${e.message}</td></tr>`;
  }
}

let _editingKnownModelId = null;

async function editKnownModel(id) {
  try {
    const models = await apiGet("/api/models/known/all");
    const m = models.find(m => m.id === id);
    if (!m) return;

    _editingKnownModelId = id;

    // Pre-fill the form
    document.getElementById("km_display_name").value   = m.display_name;
    document.getElementById("km_model_id").value       = m.model_id;
    document.getElementById("km_provider").value       = m.provider;
    document.getElementById("km_provider_group").value = m.provider_group;
    document.getElementById("km_tier").value           = m.tier;
    document.getElementById("km_cost_in").value        = m.cost_input_per_1m.toFixed(2);
    document.getElementById("km_cost_out").value       = m.cost_output_per_1m.toFixed(2);
    document.getElementById("km_notes").value          = m.notes || "";

    // Switch form into edit mode
    document.getElementById("km_form_title").textContent   = "✏️ Edit Model";
    document.getElementById("km_save_btn").textContent     = "Save Changes";
    document.getElementById("km_save_btn").style.background = "var(--accent)";
    document.getElementById("km_cancel_edit").style.display = "inline";

    // Show model ID warning
    document.getElementById("km_model_id_warning").style.display = "block";

    // Scroll form into view
    document.getElementById("km_display_name").scrollIntoView({ behavior: "smooth", block: "center" });
    document.getElementById("km_display_name").focus();

  } catch (e) {
    alert("Could not load model for editing: " + e.message);
  }
}

function cancelKnownModelEdit() {
  _editingKnownModelId = null;
  ["km_display_name","km_model_id","km_provider_group","km_cost_in","km_cost_out","km_notes"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("km_provider").value = "";
  document.getElementById("km_tier").value     = "";
  document.getElementById("km_form_title").textContent    = "+ Add a New Model to the Dropdown";
  document.getElementById("km_save_btn").textContent      = "+ Add to Dropdown";
  document.getElementById("km_save_btn").style.background = "var(--accent-green)";
  document.getElementById("km_cancel_edit").style.display = "none";
  document.getElementById("km_model_id_warning").style.display = "none";
  document.getElementById("km_error").style.display   = "none";
  document.getElementById("km_success").style.display = "none";
}

async function saveKnownModel() {
  const errEl  = document.getElementById("km_error");
  const okEl   = document.getElementById("km_success");
  const btn    = document.getElementById("km_save_btn");
  errEl.style.display = "none";
  okEl.style.display  = "none";

  const display_name   = document.getElementById("km_display_name").value.trim();
  const model_id       = document.getElementById("km_model_id").value.trim();
  const provider       = document.getElementById("km_provider").value;
  const provider_group = document.getElementById("km_provider_group").value.trim();
  const tier           = parseInt(document.getElementById("km_tier").value);
  const cost_in        = parseFloat(document.getElementById("km_cost_in").value) || 0;
  const cost_out       = parseFloat(document.getElementById("km_cost_out").value) || 0;
  const notes          = document.getElementById("km_notes").value.trim();

  if (!display_name)   { errEl.textContent = "Display Name is required.";   errEl.style.display = "inline"; return; }
  if (!model_id)       { errEl.textContent = "API Model ID is required.";   errEl.style.display = "inline"; return; }
  if (!provider)       { errEl.textContent = "Please select a provider.";   errEl.style.display = "inline"; return; }
  if (!provider_group) { errEl.textContent = "Group Label is required.";    errEl.style.display = "inline"; return; }
  if (!tier)           { errEl.textContent = "Please select a tier.";       errEl.style.display = "inline"; return; }

  const payload = { display_name, model_id, provider, provider_group, tier, cost_input_per_1m: cost_in, cost_output_per_1m: cost_out, notes: notes || null };

  btn.disabled = true; btn.textContent = "Saving...";
  try {
    if (_editingKnownModelId) {
      await apiPut(`/api/models/known/${_editingKnownModelId}`, payload);
      okEl.textContent = `✓ "${display_name}" updated.`;
    } else {
      await apiPost("/api/models/known", payload);
      okEl.textContent = `✓ "${display_name}" added to the dropdown.`;
    }
    okEl.style.display = "inline";
    cancelKnownModelEdit();
    loadKnownModelsTable();
  } catch (e) {
    errEl.textContent = e.message || "Save failed.";
    errEl.style.display = "inline";
  } finally {
    btn.disabled = false; btn.textContent = "+ Add to Dropdown";
  }
}

async function toggleKnownModel(id) {
  try {
    await apiPatch(`/api/models/known/${id}/toggle`, null);
    loadKnownModelsTable();
  } catch (e) {
    alert("Toggle failed: " + e.message);
  }
}

async function deleteKnownModel(id, name, e) {
  e.stopPropagation();
  if (!confirm(`Remove "${name}" from the Quick Select dropdown? This cannot be undone.`)) return;
  try {
    await apiDelete(`/api/models/known/${id}`);
    loadKnownModelsTable();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}

// TIERS — static metadata (tagline, best_for, examples, color stay fixed).
// The .name field is overridden at render time by getTierName() from tier-utils.js.
const TIERS = {
  1: { icon: "⚡", tagline: "Fast, affordable, handles routine tasks",              best_for: "FAQs, status lookups, simple summaries",           examples: "GPT-4o mini, Claude Haiku", color: "#3fb950" },
  2: { icon: "🔍", tagline: "Balanced reasoning for most business tasks",           best_for: "Customer emails, data summarization, drafting",     examples: "GPT-4o, Claude Sonnet",     color: "#58a6ff" },
  3: { icon: "💡", tagline: "Deep reasoning for complex or sensitive work",         best_for: "Contract review, escalations, multi-step analysis", examples: "GPT-4 Turbo, Claude Opus",  color: "#d29922" },
  4: { icon: "🎯", tagline: "Highest capability for mission-critical decisions",    best_for: "Legal, financial, compliance-heavy tasks",          examples: "o3, Claude Opus Max",       color: "#f85149" },
};

let _editingId  = null;
let _allModels  = [];
let _selectedTier = null;

// ── Boot ──────────────────────────────────────────────────────────────────────
// Scripts are at bottom of <body> so DOM is ready — call directly.
// Load custom tier names first, then render so cards show the right labels.
loadTierNames().then(() => {
  renderTierCards();
  renderTierOptions();
  if (_allModels.length) {
    updateTierCounts();
    renderModelHealth();
  }
});
renderPresetOptions();
loadModels();
loadRoutingOutcomes();

// ── Tier reference cards ──────────────────────────────────────────────────────

function renderTierCards() {
  const wrap = document.getElementById("tierCards");
  wrap.innerHTML = Object.entries(TIERS).map(([tier, t]) => {
    const count = _allModels.filter(m => m.tier === parseInt(tier)).length;
    return (
      '<div class="mdl-tier-card" style="--tier-color:' + t.color + '">' +
        '<div class="mdl-tier-icon">' + t.icon + '</div>' +
        '<div class="mdl-tier-name" style="color:' + t.color + '">' + getTierName(parseInt(tier)) + '</div>' +
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
    const models = _allModels.filter(m => m.tier === parseInt(tier));
    const eligible = models.filter(m => m.is_enabled);
    const globalDefault = eligible.find(m => m.is_default && !m.department);
    if (!models.length) {
      el.textContent = "No models yet";
    } else {
      el.textContent = eligible.length + " eligible · " +
        (globalDefault ? "Default: " + globalDefault.display_name : "No global default");
    }
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
            '<div class="mdl-tier-opt-name" style="color:' + t.color + '">' + getTierName(parseInt(tier)) + '</div>' +
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
  try {
    _allModels = await apiGet("/api/models");
    renderTable(getFilteredModels());
    updateTierCounts();
    renderModelHealth();
    renderPreviewDepartments();
    previewModelRouting();
  } catch (e) {
    document.getElementById("modelTableBody").innerHTML =
      '<tr><td colspan="9" class="mdl-placeholder" style="color:var(--accent-red)">Error loading models: ' + e.message + '</td></tr>';
  }
}

function getFilteredModels() {
  const tier = document.getElementById("filterTier").value;
  const provider = document.getElementById("filterProvider").value;
  const enabled = document.getElementById("filterEnabled").value;
  return _allModels.filter(m => {
    if (tier && String(m.tier) !== tier) return false;
    if (provider && m.provider !== provider) return false;
    if (enabled !== "" && String(Boolean(m.is_enabled)) !== enabled) return false;
    return true;
  });
}

function modelHtmlEscape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderModelHealth() {
  const enabled = _allModels.filter(m => m.is_enabled);
  const defaults = enabled.filter(m => m.is_default);
  const providers = new Set(_allModels.map(m => m.provider).filter(Boolean));
  const warnings = [];

  Object.keys(TIERS).forEach(rawTier => {
    const tier = Number(rawTier);
    const tierLabel = getTierName(tier);
    const globalModels = _allModels.filter(m => m.tier === tier && !m.department);
    const eligibleGlobal = globalModels.filter(m => m.is_enabled);
    const enabledDefaults = eligibleGlobal.filter(m => m.is_default);
    const disabledDefaults = globalModels.filter(m => m.is_default && !m.is_enabled);

    if (!eligibleGlobal.length) {
      warnings.push(`<strong>${modelHtmlEscape(tierLabel)}</strong> has no eligible global model. Requests may cascade to another tier or use the built-in fallback.`);
    } else if (!enabledDefaults.length) {
      warnings.push(`<strong>${modelHtmlEscape(tierLabel)}</strong> has no enabled global default. The router will use the first eligible global model.`);
    }
    if (enabledDefaults.length > 1) {
      warnings.push(`<strong>${modelHtmlEscape(tierLabel)}</strong> has ${enabledDefaults.length} enabled global defaults. Verify which one should lead routing.`);
    }
    if (disabledDefaults.length) {
      warnings.push(`<strong>${modelHtmlEscape(tierLabel)}</strong> has a disabled model still marked default. Disabled models are ignored by routing.`);
    }
  });

  const scopedGroups = new Map();
  _allModels.filter(m => m.department).forEach(m => {
    const key = `${m.department}::${m.tier}`;
    if (!scopedGroups.has(key)) scopedGroups.set(key, []);
    scopedGroups.get(key).push(m);
  });
  scopedGroups.forEach(models => {
    const eligible = models.filter(m => m.is_enabled);
    const activeDefaults = eligible.filter(m => m.is_default);
    const { department, tier } = models[0];
    if (eligible.length && !activeDefaults.length) {
      warnings.push(`<strong>${modelHtmlEscape(department)} · ${modelHtmlEscape(getTierName(tier))}</strong> has eligible models but no default. The first eligible department model will be used.`);
    }
    if (activeDefaults.length > 1) {
      warnings.push(`<strong>${modelHtmlEscape(department)} · ${modelHtmlEscape(getTierName(tier))}</strong> has ${activeDefaults.length} enabled defaults.`);
    }
  });

  const missingPricing = enabled.filter(m =>
    Number(m.cost_input_per_1m) <= 0 || Number(m.cost_output_per_1m) <= 0
  );
  if (missingPricing.length) {
    warnings.push(`<strong>${missingPricing.length} eligible model${missingPricing.length === 1 ? "" : "s"}</strong> have a zero input or output rate. Verify pricing before relying on cost forecasts.`);
  }

  document.getElementById("mdlHealthRegistered").textContent = _allModels.length;
  document.getElementById("mdlHealthEnabled").textContent = enabled.length;
  document.getElementById("mdlHealthDefaults").textContent = defaults.length;
  document.getElementById("mdlHealthProviders").textContent = providers.size;
  document.getElementById("mdlHealthWarnings").textContent = warnings.length;
  document.getElementById("mdlWarningList").innerHTML = warnings.length
    ? warnings.slice(0, 8).map(w => `<div class="mdl-warning">${w}</div>`).join("")
    : '<div class="mdl-warning ok"><strong>Catalog ready.</strong> Every tier has an eligible global default and enabled pricing is populated.</div>';
}

function renderPreviewDepartments() {
  const select = document.getElementById("mdlPreviewDepartment");
  const current = select.value;
  const departments = [...new Set(_allModels.map(m => m.department).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
  select.innerHTML = '<option value="">Global / any department</option>' +
    departments.map(d => `<option value="${modelHtmlEscape(d)}">${modelHtmlEscape(d)}</option>`).join("");
  if (departments.includes(current)) select.value = current;
}

async function previewModelRouting() {
  const result = document.getElementById("mdlPreviewResult");
  if (!result) return;
  const tier = document.getElementById("mdlPreviewTier").value;
  const department = document.getElementById("mdlPreviewDepartment").value;
  const params = new URLSearchParams({ tier });
  if (department) params.set("department", department);
  result.textContent = "Checking current routing selection…";
  try {
    const preview = await apiGet("/api/models/routing-preview?" + params.toString());
    const routeLabel = preview.cascaded
      ? `${getTierName(preview.requested_tier)} → ${getTierName(preview.resolved_tier)}`
      : getTierName(preview.resolved_tier);
    result.innerHTML =
      `<span class="mdl-preview-pill">${modelHtmlEscape(preview.source)}</span>` +
      `<span class="mdl-preview-pill">${modelHtmlEscape(preview.scope)}</span>` +
      `<span class="mdl-preview-pill">${modelHtmlEscape(routeLabel)}</span>` +
      `<strong>${modelHtmlEscape(preview.display_name)}</strong>` +
      `<code>${modelHtmlEscape(preview.model_id)}</code>` +
      `<span>$${Number(preview.cost_input_per_1m).toFixed(2)} input · $${Number(preview.cost_output_per_1m).toFixed(2)} output per 1M tokens</span>` +
      `<span>${modelHtmlEscape(preview.reason)}</span>`;
  } catch (e) {
    result.innerHTML = `<span style="color:var(--accent-red)">Preview unavailable: ${modelHtmlEscape(e.message)}</span>`;
  }
}

function formatModelCurrency(value) {
  const amount = Number(value || 0);
  if (amount === 0) return "$0.00";
  if (amount < 0.01) return "$" + amount.toFixed(4);
  return "$" + amount.toFixed(2);
}

function formatTelemetryCoverage(value, exact, total) {
  const pct = Number(value || 0);
  if (!total || !exact) return "0.0%";
  if (pct < 0.1) return "<0.1%";
  return `${pct.toFixed(1)}%`;
}

function handleRoutingAlertAction(code, modelKey) {
  if (modelKey) {
    openRoutingOutcomeDetail(modelKey);
    return;
  }
  const target = code === "routing_cascade"
    ? document.getElementById("mdlRoutingPreviewTitle")
    : document.getElementById("mdlCatalogHealthTitle");
  if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function loadRoutingOutcomes() {
  const list = document.getElementById("mdlOutcomeList");
  if (!list) return;
  const days = document.getElementById("mdlOutcomeDays").value;
  list.innerHTML = '<div class="mdl-placeholder">Loading routing outcomes…</div>';
  try {
    const data = await apiGet(`/api/models/routing-outcomes?days=${encodeURIComponent(days)}`);
    document.getElementById("mdlOutcomeCalls").textContent = Number(data.total_calls).toLocaleString();
    document.getElementById("mdlOutcomeSpend").textContent = formatModelCurrency(data.total_spend_usd);
    document.getElementById("mdlOutcomeAvg").textContent = formatModelCurrency(data.avg_cost_usd);
    document.getElementById("mdlOutcomeCascade").textContent = Number(data.cascaded_calls).toLocaleString();
    document.getElementById("mdlOutcomeFallback").textContent = Number(data.fallback_calls).toLocaleString();
    document.getElementById("mdlOutcomeUnused").textContent = Number(data.unused_eligible_count).toLocaleString();

    const exact = Number(data.recorded_calls);
    const inferred = Number(data.inferred_calls);
    const total = Number(data.total_calls);
    const preciseCoverage = Number(
      data.telemetry_coverage_pct_precise ?? data.telemetry_coverage_pct ?? 0
    );
    const unusedNames = (data.unused_eligible || []).slice(0, 4).map(m => m.display_name);
    let note = `${formatTelemetryCoverage(preciseCoverage, exact, total)} exact-model telemetry · ${exact.toLocaleString()} exact · ${inferred.toLocaleString()} tier-inferred.`;
    if (inferred) {
      note += " Inferred history is mapped to the current eligible tier selection and is not presented as provider-confirmed.";
    }
    if (unusedNames.length) {
      note += ` Eligible models with no attributed calls: ${unusedNames.join(", ")}${data.unused_eligible_count > unusedNames.length ? ", and more" : ""}.`;
    }
    document.getElementById("mdlTelemetryNote").textContent = note;

    const alerts = data.alerts || [];
    const alertWrap = document.getElementById("mdlRoutingAlerts");
    alertWrap.innerHTML = alerts.length
      ? alerts.map(alert =>
          '<div class="mdl-routing-alert ' + modelHtmlEscape(alert.severity || "info") + '">' +
            '<div class="mdl-routing-alert-icon" aria-hidden="true">' +
              (alert.severity === "critical" ? "!" : alert.severity === "warning" ? "△" : "i") +
            '</div>' +
            '<div class="mdl-routing-alert-copy">' +
              '<strong>' + modelHtmlEscape(alert.title) + '</strong>' +
              '<span>' + modelHtmlEscape(alert.detail) + '</span>' +
            '</div>' +
            '<button class="mdl-routing-alert-action" type="button" data-code="' +
              modelHtmlEscape(alert.code || "") + '" data-model-key="' +
              modelHtmlEscape(alert.model_key || "") + '" onclick="handleRoutingAlertAction(this.dataset.code,this.dataset.modelKey)">' +
              modelHtmlEscape(alert.action || "Review") +
            '</button>' +
          '</div>'
        ).join("")
      : '<div class="mdl-routing-alert good">' +
          '<div class="mdl-routing-alert-icon" aria-hidden="true">✓</div>' +
          '<div class="mdl-routing-alert-copy"><strong>No routing issues detected</strong>' +
          '<span>No fallback, cascade, unused-model, or high-concentration signal crossed the review threshold.</span></div>' +
        '</div>';

    if (!(data.models || []).length) {
      list.innerHTML = '<div class="mdl-placeholder">No AI calls were recorded in this period.</div>';
      return;
    }
    list.innerHTML = data.models.map(model => {
      const telemetry = model.telemetry === "exact" ? "Exact" :
        model.telemetry === "mixed" ? "Mixed telemetry" : "Tier-inferred";
      const badgeClass = model.telemetry === "exact" ? "" : " inferred";
      const departments = (model.top_departments || [])
        .map(item => `${item.department} ${Number(item.calls).toLocaleString()}`)
        .join(" · ");
      return (
        '<div class="mdl-outcome-row" role="button" tabindex="0" data-model-key="' + modelHtmlEscape(model.model_key) + '"' +
          ' onclick="openRoutingOutcomeDetail(this.dataset.modelKey)"' +
          ' onkeydown="handleRoutingOutcomeKey(event,this)">' +
          '<div class="mdl-outcome-model">' +
            '<strong>' + modelHtmlEscape(model.display_name) +
              '<span class="mdl-telemetry-badge' + badgeClass + '">' + telemetry + '</span>' +
            '</strong>' +
            '<span>' + modelHtmlEscape(model.provider || "Provider not recorded") +
              (departments ? ' · ' + modelHtmlEscape(departments) : '') +
            '</span>' +
          '</div>' +
          '<div class="mdl-outcome-stat"><strong>' + Number(model.calls).toLocaleString() + '</strong><span>Calls</span></div>' +
          '<div class="mdl-outcome-stat"><strong>' + formatModelCurrency(model.spend_usd) + '</strong><span>Spend</span></div>' +
          '<div class="mdl-outcome-stat"><strong>' + formatModelCurrency(model.avg_cost_usd) + '</strong><span>Average / call</span></div>' +
          '<div class="mdl-outcome-open" aria-hidden="true">›</div>' +
        '</div>'
      );
    }).join("");
  } catch (e) {
    const alertWrap = document.getElementById("mdlRoutingAlerts");
    if (alertWrap) alertWrap.innerHTML = '<div class="mdl-routing-alert critical">Routing signals unavailable.</div>';
    list.innerHTML = `<div class="mdl-placeholder" style="color:var(--accent-red)">Routing outcomes unavailable: ${modelHtmlEscape(e.message)}</div>`;
  }
}

function formatModelDetailTime(value) {
  if (!value) return "Time unavailable";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function handleRoutingOutcomeKey(event, element) {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  openRoutingOutcomeDetail(element.dataset.modelKey);
}

function closeRoutingOutcomeDetail(event) {
  if (event && event.target !== event.currentTarget) return;
  const overlay = document.getElementById("mdlOutcomeDetailOverlay");
  if (overlay) overlay.style.display = "none";
}

async function openRoutingOutcomeDetail(modelKey) {
  const overlay = document.getElementById("mdlOutcomeDetailOverlay");
  const body = document.getElementById("mdlOutcomeDetailBody");
  const days = document.getElementById("mdlOutcomeDays").value;
  overlay.style.display = "flex";
  document.getElementById("mdlDetailTitle").textContent = "Model usage";
  document.getElementById("mdlDetailSubtitle").textContent = "Loading routing evidence…";
  body.innerHTML = '<div class="mdl-placeholder">Loading model evidence…</div>';

  try {
    const data = await apiGet(
      `/api/models/routing-outcomes/detail?model_key=${encodeURIComponent(modelKey)}&days=${encodeURIComponent(days)}`
    );
    const model = data.model;
    document.getElementById("mdlDetailTitle").textContent = model.display_name;
    document.getElementById("mdlDetailSubtitle").textContent =
      `${model.provider || "Provider not recorded"} · ${model.tier_name || "Tier not recorded"} · Last ${data.days} days`;

    const departmentRows = (data.departments || []).map(item =>
      '<div class="mdl-detail-row">' +
        '<strong>' + modelHtmlEscape(item.department) + '</strong>' +
        '<span>' + Number(item.calls).toLocaleString() + ' calls</span>' +
        '<span>' + formatModelCurrency(item.spend_usd) + '</span>' +
      '</div>'
    ).join("") || '<div class="mdl-detail-empty">No department usage in this period.</div>';

    const agentRows = (data.agents || []).slice(0, 10).map(item =>
      '<div class="mdl-detail-row">' +
        '<strong>' + modelHtmlEscape(item.agent_name) +
          '<span>' + modelHtmlEscape(item.department || "Unassigned") + ' · ' + modelHtmlEscape(item.source_platform || "Platform unknown") + '</span>' +
        '</strong>' +
        '<span>' + Number(item.calls).toLocaleString() + ' calls</span>' +
        '<span>' + formatModelCurrency(item.spend_usd) + '</span>' +
      '</div>'
    ).join("") || '<div class="mdl-detail-empty">No agent attribution in this period.</div>';

    const recentRows = (data.recent_calls || []).map(item => {
      const route = item.routing_cascaded
        ? `${item.requested_tier || "Unknown"} → ${item.resolved_tier || "Unknown"}`
        : item.resolved_tier || item.requested_tier || "Unknown";
      const telemetry = item.telemetry === "exact" ? "Exact" : "Inferred";
      return '<div class="mdl-detail-row mdl-detail-recent">' +
        '<strong>' + modelHtmlEscape(formatModelDetailTime(item.timestamp)) +
          '<span>' + modelHtmlEscape(item.department) + ' · ' + modelHtmlEscape(item.agent_name) + '</span>' +
        '</strong>' +
        '<span>' + modelHtmlEscape(route) + (item.routing_cascaded ? " · cascaded" : "") + '</span>' +
        '<span>' + modelHtmlEscape(item.routing_reason || item.model_source || "Routed") + '</span>' +
        '<span>' + formatModelCurrency(item.cost_usd) + ' · ' + telemetry + '</span>' +
      '</div>';
    }).join("") || '<div class="mdl-detail-empty">No recent calls in this period.</div>';

    const auditRows = (data.audit_events || []).map(item =>
      '<div class="mdl-detail-row mdl-detail-recent">' +
        '<strong>#' + Number(item.id) + ' · ' + modelHtmlEscape(formatModelDetailTime(item.timestamp)) +
          '<span>' + modelHtmlEscape(item.department || "Unassigned") + '</span>' +
        '</strong>' +
        '<span>' + modelHtmlEscape(item.event_type || "Event") + '</span>' +
        '<span>' + modelHtmlEscape(item.decision_outcome || "Decision recorded") + '</span>' +
        '<span>' + (item.telemetry === "exact" ? "Exact model" : "Tier-related") + '</span>' +
      '</div>'
    ).join("") || '<div class="mdl-detail-empty">No matching audit evidence in this period.</div>';

    const optimization = data.optimization || {};
    const scenario = optimization.scenario;
    const reasonRows = (optimization.routing_reasons || []).slice(0, 5).map(item =>
      '<div class="mdl-opt-reason">' +
        '<strong>' + modelHtmlEscape(item.reason) + '</strong>' +
        '<span>' + Number(item.calls).toLocaleString() + ' calls · ' +
          Number(item.share_pct || 0).toFixed(1) + '% · ' + formatModelCurrency(item.spend_usd) +
        '</span>' +
      '</div>'
    ).join("") || '<div class="mdl-detail-empty">No routing reasons recorded.</div>';
    const driverParts = [];
    if (optimization.top_agent) {
      driverParts.push(
        '<div class="mdl-opt-driver"><span>Leading agent</span><strong>' +
        modelHtmlEscape(optimization.top_agent.agent_name) + '</strong><small>' +
        formatModelCurrency(optimization.top_agent.spend_usd) + ' · ' +
        Number(optimization.top_agent.spend_share_pct || 0).toFixed(1) + '% of this model’s spend</small></div>'
      );
    }
    if (optimization.top_department) {
      driverParts.push(
        '<div class="mdl-opt-driver"><span>Leading department</span><strong>' +
        modelHtmlEscape(optimization.top_department.department) + '</strong><small>' +
        formatModelCurrency(optimization.top_department.spend_usd) + ' · ' +
        Number(optimization.top_department.spend_share_pct || 0).toFixed(1) + '% of this model’s spend</small></div>'
      );
    }
    const scenarioHtml = scenario
      ? '<div class="mdl-opt-scenario">' +
          '<div><span>Compare with</span><strong>' + modelHtmlEscape(scenario.candidate_display_name) +
            ' · ' + modelHtmlEscape(scenario.candidate_tier_name) + '</strong></div>' +
          '<div><span>Current spend</span><strong>' + formatModelCurrency(scenario.current_spend_usd) + '</strong></div>' +
          '<div><span>Illustrative spend</span><strong>' + formatModelCurrency(scenario.estimated_spend_usd) + '</strong></div>' +
          '<div class="saving"><span>Potential period savings</span><strong>' +
            formatModelCurrency(scenario.estimated_savings_usd) + ' · ' +
            Number(scenario.estimated_savings_pct || 0).toFixed(1) + '%</strong></div>' +
          '<div class="saving"><span>Annualized at this pace</span><strong>' +
            formatModelCurrency(scenario.annualized_savings_usd) + '</strong></div>' +
          '<p>' + modelHtmlEscape(scenario.disclaimer) + '</p>' +
        '</div>'
      : '';
    const optimizationHtml =
      '<div class="mdl-optimization ' + modelHtmlEscape(optimization.status || "insufficient_data") + '">' +
        '<div class="mdl-opt-eyebrow">Optimization opportunity · ' +
          modelHtmlEscape(optimization.confidence || "none") + ' telemetry</div>' +
        '<h3>' + modelHtmlEscape(optimization.headline || "Optimization analysis unavailable.") + '</h3>' +
        '<p>' + modelHtmlEscape(optimization.guidance || "No routing change is recommended.") + '</p>' +
        (driverParts.length ? '<div class="mdl-opt-drivers">' + driverParts.join("") + '</div>' : '') +
        '<div class="mdl-opt-review"><strong>' +
          Number(optimization.review_candidate_calls || 0).toLocaleString() +
          ' calls flagged for first review</strong><span>Overrides, cascades, or built-in fallback · ' +
          formatModelCurrency(optimization.review_candidate_spend_usd) + '</span></div>' +
        scenarioHtml +
        '<div class="mdl-opt-reasons"><h4>Why requests reached this model</h4>' + reasonRows + '</div>' +
      '</div>';

    body.innerHTML =
      '<div class="mdl-detail-kpis">' +
        '<div class="mdl-detail-kpi"><strong>' + Number(data.total_calls).toLocaleString() + '</strong><span>Calls</span></div>' +
        '<div class="mdl-detail-kpi"><strong>' + formatModelCurrency(data.total_spend_usd) + '</strong><span>Spend</span></div>' +
        '<div class="mdl-detail-kpi"><strong>' + Number(data.exact_calls).toLocaleString() + '</strong><span>Exact</span></div>' +
        '<div class="mdl-detail-kpi"><strong>' + Number(data.inferred_calls).toLocaleString() + '</strong><span>Inferred</span></div>' +
        '<div class="mdl-detail-kpi"><strong>' + Number(data.cascaded_calls).toLocaleString() + '</strong><span>Cascaded</span></div>' +
        '<div class="mdl-detail-kpi"><strong>' + Number(data.fallback_calls).toLocaleString() + '</strong><span>Fallback</span></div>' +
        '<div class="mdl-detail-kpi"><strong>' + formatModelCurrency(data.avg_cost_usd) + '</strong><span>Average / call</span></div>' +
      '</div>' +
      optimizationHtml +
      '<div class="mdl-detail-section"><h3>Department usage</h3><div class="mdl-detail-table">' + departmentRows + '</div></div>' +
      '<div class="mdl-detail-section"><h3>Agent usage</h3><div class="mdl-detail-table">' + agentRows + '</div></div>' +
      '<div class="mdl-detail-section"><h3>Recent calls</h3>' +
        '<div class="mdl-detail-section-note">Exact identifies a recorded model name. Inferred maps legacy tier-only history to the current eligible tier selection.</div>' +
        '<div class="mdl-detail-table">' + recentRows + '</div></div>' +
      '<div class="mdl-detail-section"><h3>Audit evidence</h3>' +
        '<div class="mdl-detail-section-note">Tier-related events predate exact-model audit telemetry and should not be treated as provider-confirmed.</div>' +
        '<div class="mdl-detail-table">' + auditRows + '</div></div>' +
      '<div class="mdl-detail-actions">' +
        (model.id ? '<button class="mdl-add-btn" type="button" onclick="closeRoutingOutcomeDetail();openEditModal(' + Number(model.id) + ')">Adjust model configuration</button>' : '') +
        '<a class="mdl-action-btn" href="/operate.html#auditSection">Open full audit log</a>' +
      '</div>';
  } catch (e) {
    body.innerHTML = `<div class="mdl-placeholder" style="color:var(--accent-red)">Model evidence unavailable: ${modelHtmlEscape(e.message)}</div>`;
  }
}

document.addEventListener("keydown", event => {
  if (event.key === "Escape") closeRoutingOutcomeDetail();
});

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
    let routingRole = '<span class="mdl-ineligible-badge">Not eligible</span>';
    if (!m.is_enabled && m.is_default) {
      routingRole = '<span class="mdl-default-badge">⚠ Default disabled</span>';
    } else if (m.is_enabled && m.is_default) {
      routingRole = '<span class="mdl-default-badge">★ Default</span>';
    } else if (m.is_enabled) {
      routingRole = '<span class="mdl-eligible-badge">Eligible fallback</span>';
    }

    return (
      '<tr>' +
        '<td>' +
          '<div class="mdl-model-name">' + modelHtmlEscape(m.display_name) + '</div>' +
          (m.department ? '<div class="mdl-dept-scope">🏢 ' + modelHtmlEscape(m.department) + ' only</div>' : '') +
          (m.notes ? '<div class="mdl-model-notes">' + modelHtmlEscape(m.notes) + '</div>' : '') +
        '</td>' +
        '<td>' +
          '<span class="mdl-provider-badge">' + modelHtmlEscape(m.provider) + '</span>' +
        '</td>' +
        '<td>' +
          '<div class="mdl-tier-badge" style="color:' + (tierInfo.color || "#8b949e") + '; border-color:' + (tierInfo.color || "#8b949e") + '">' +
            (tierInfo.icon || "◈") + ' ' + modelHtmlEscape(m.tier_name) +
          '</div>' +
          '<div class="mdl-tier-tagline-sm">' + modelHtmlEscape(m.tier_tagline) + '</div>' +
        '</td>' +
        '<td><code class="mdl-model-id">' + modelHtmlEscape(m.model_id) + '</code></td>' +
        '<td class="mdl-cost">$' + m.cost_input_per_1m.toFixed(2) + '</td>' +
        '<td class="mdl-cost">$' + m.cost_output_per_1m.toFixed(2) + '</td>' +
        '<td>' + enabledBadge + '</td>' +
        '<td>' + routingRole + '</td>' +
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
  const presetSel = document.getElementById("fPreset");
  if (presetSel) presetSel.value = "";
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
