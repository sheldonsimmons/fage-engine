/**
 * budget.js — Department Budget Allocator UI  [Step 4]
 *
 * Loads live budget data from GET /api/budget and renders:
 *   - Progress bars with colour-coded states (healthy / warning / throttled)
 *   - Per-department spend vs cap figures
 *   - Supervisor controls: update cap, grant override, reset period
 */

let budgetData = [];
let openBudgetKey = null;
let budgetSearchTerm = "";
let budgetStateFilter = "";
let budgetShowArchived = false;
const BUDGET_WARNING_PCT = 70;

/** Fetch all budgets and re-render the panel */
async function loadBudgets() {
  // Show cached data instantly while fresh data loads in background
  const cached = localStorage.getItem("fage_budgets");
  if (cached) {
    try {
      budgetData = JSON.parse(cached);
      renderBudgets();
      renderLiveBudgetBars();
      updateKpiThrottled();
    } catch {}
  }

  try {
    budgetData = await apiGet(scopedApiPath("/api/budget"));
    localStorage.setItem("fage_budgets", JSON.stringify(budgetData));
    renderBudgets();
    renderLiveBudgetBars();
    updateKpiThrottled();
  } catch (err) {
    if (!cached) {
      document.getElementById("budgetList").innerHTML =
        `<p class="placeholder" style="color:var(--accent-red)">Failed to load budgets: ${err.message}</p>`;
    }
  }
}

function fmtUsd(v) {
  if (v === 0)    return "$0.00";
  if (v < 0.0001) return "$" + v.toFixed(6);
  if (v < 0.01)   return "$" + v.toFixed(4);
  if (v < 1)      return "$" + v.toFixed(4);
  return "$" + v.toFixed(2);
}

function fmtPct(spend, cap) {
  if (!cap || cap === 0) return "0%";
  const pct = (spend / cap) * 100;
  if (pct === 0) return "0%";
  if (pct < 0.01) return "<0.01%";
  if (pct < 1)    return pct.toFixed(3) + "%";
  return pct.toFixed(1) + "%";
}

function cleanBudgetDeptName(department) {
  if (!department) return "—";
  const text = String(department).trim();
  const colonIndex = text.indexOf(":");
  if (colonIndex < 0) return text;
  const prefix = text.slice(0, colonIndex);
  const label = text.slice(colonIndex + 1).trim();
  return prefix.length >= 12 && /^[A-Za-z0-9-]+$/.test(prefix) && label ? label : text;
}

function budgetJsString(value) {
  return String(value || "").replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/\n/g, " ");
}

function budgetAttr(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function budgetStateFor(row) {
  if (row.throttled) return "throttled";
  return (row.used_pct || 0) >= BUDGET_WARNING_PCT ? "warning" : "healthy";
}

function displayBudgetRows(rows = []) {
  const merged = new Map();
  rows.forEach(b => {
    const label = cleanBudgetDeptName(b.display_department || b.department || b.name);
    const key = label.toLowerCase();
    const current = merged.get(key);
    if (!current) {
      merged.set(key, { ...b, budget_key: key, display_department: label, raw_departments: [b.department] });
      return;
    }
    current.current_spend_usd = (current.current_spend_usd || 0) + (b.current_spend_usd || 0);
    current.monthly_cap_usd = Math.max(current.monthly_cap_usd || 0, b.monthly_cap_usd || 0);
    current.remaining_usd = current.monthly_cap_usd - current.current_spend_usd;
    current.throttled = current.throttled || b.throttled;
    current.override_granted = current.override_granted || b.override_granted;
    current.raw_payload_logging_enabled = current.raw_payload_logging_enabled || b.raw_payload_logging_enabled;
    current.archived = current.archived && (b.archived || false);
    current.raw_departments.push(b.department);
  });
  return Array.from(merged.values())
    .map(row => {
      const usedPct = row.monthly_cap_usd ? (row.current_spend_usd / row.monthly_cap_usd) * 100 : 0;
      row.used_pct = Math.round(usedPct * 10) / 10;
      row.state = budgetStateFor(row);
      return row;
    })
    .sort((a, b) => String(a.display_department).localeCompare(String(b.display_department)));
}

function renderBudgets() {
  const container = document.getElementById("budgetList");
  const searchWasFocused = document.activeElement?.dataset?.budgetSearch === "true";
  const searchCursor = searchWasFocused ? document.activeElement.selectionStart : null;
  if (!budgetData.length) {
    container.innerHTML = '<p class="placeholder">No budget data found.</p>';
    return;
  }

  let rows = displayBudgetRows(budgetData);
  rows = rows.filter(b => budgetShowArchived ? b.archived : !b.archived);
  const search = budgetSearchTerm.trim().toLowerCase();
  if (search) rows = rows.filter(b => String(b.display_department || "").toLowerCase().includes(search));
  if (budgetStateFilter) {
    rows = rows.filter(b => {
      if (budgetStateFilter === "raw") return b.raw_payload_logging_enabled;
      return (b.state || "healthy") === budgetStateFilter;
    });
  }

  const tableRows = rows.map(b => {
    const fillClass = b.state === "throttled" ? "critical"
                    : b.state === "warning"   ? "warn"
                    : "";

    const stateTag = b.throttled
      ? `<span class="budget-tag">THROTTLED</span>`
      : b.override_granted
        ? `<span class="budget-tag" style="color:var(--accent-green)">OVERRIDE ACTIVE</span>`
        : b.state === "warning"
          ? `<span class="budget-tag" style="color:var(--accent-yellow)">WARNING</span>`
          : "";

    const displayPct = fmtPct(b.current_spend_usd, b.monthly_cap_usd);
    const barPct     = b.monthly_cap_usd > 0
      ? Math.min((b.current_spend_usd / b.monthly_cap_usd) * 100, 100)
      : 0;

    const throttleTier = b.throttle_tier || 1;
    const tierOpts = [
      { v: 1, label: "⚡ Scout — Tier 1 (cheapest)" },
      { v: 2, label: "🔍 Analyst — Tier 2" },
      { v: 3, label: "💡 Advisor — Tier 3" },
      { v: 4, label: "🎯 Strategist — Tier 4 (premium)" },
    ].map(o =>
      `<option value="${o.v}" ${o.v === throttleTier ? "selected" : ""}>${o.label}</option>`
    ).join("");
    const key = budgetJsString(b.budget_key);
    const departments = (b.raw_departments || [b.department]).filter(Boolean);
    const rawCount = departments.length > 1
      ? `<span style="color:var(--text-muted);font-size:10px"> ${departments.length} records</span>`
      : "";
    const editor = openBudgetKey === b.budget_key ? `
      <tr>
        <td colspan="6" style="padding:12px 10px;background:rgba(88,166,255,.04)">
          <div class="budget-actions" style="margin-top:0">
            <input type="number" class="cap-input" id="cap-${b.budget_key}"
                   value="${b.monthly_cap_usd}" min="1" step="10" />
            <button class="btn-cap" onclick="doSetCapGroup('${key}')">Set Cap</button>
            <button class="btn-cap btn-reset" onclick="doResetGroup('${key}')">Reset Month</button>
            <button class="btn-cap btn-reset" onclick="${b.archived ? `doRestoreGroup('${key}')` : `doArchiveGroup('${key}')`}">
              ${b.archived ? "Restore" : "Archive"}
            </button>
            ${b.throttled
              ? `<button class="btn-override" onclick="doOverrideGroup('${key}')">Grant Override</button>`
              : b.override_granted
                ? `<button class="btn-override btn-revoke" onclick="doRevokeGroup('${key}')">Revoke Override</button>`
                : ""}
          </div>
          <div class="budget-throttle-row">
            <span class="budget-throttle-label">Throttle floor:</span>
            <select class="budget-throttle-select" id="throttle-tier-${b.budget_key}">
              ${tierOpts}
            </select>
            <button class="btn-cap" onclick="doSetThrottleTierGroup('${key}')">Save Floor</button>
          </div>
          <div class="budget-throttle-row">
            <span class="budget-throttle-label">Raw payload log:</span>
            <label class="toggle-switch">
              <input type="checkbox" id="raw-log-${b.budget_key}" ${b.raw_payload_logging_enabled ? "checked" : ""}>
              <span class="toggle-slider"></span>
            </label>
            <select class="budget-throttle-select" id="raw-retention-${b.budget_key}">
              <option value="30"  ${(b.raw_retention_days ?? 30) === 30  ? "selected" : ""}>30 days</option>
              <option value="90"  ${(b.raw_retention_days ?? 30) === 90  ? "selected" : ""}>90 days</option>
              <option value="180" ${(b.raw_retention_days ?? 30) === 180 ? "selected" : ""}>180 days</option>
              <option value="365" ${(b.raw_retention_days ?? 30) === 365 ? "selected" : ""}>1 year</option>
              <option value="0"   ${(b.raw_retention_days ?? 30) === 0   ? "selected" : ""}>Indefinite</option>
            </select>
            <button class="btn-cap" onclick="doSetRawLoggingGroup('${key}')">Save Logging</button>
          </div>
          <div class="budget-throttle-hint">Applies to: ${departments.map(cleanBudgetDeptName).join(", ")}</div>
        </td>
      </tr>` : "";

    return `
      <tr>
        <td>
          <strong>${b.display_department}</strong>${rawCount}
          <div class="budget-bar-track" style="margin-top:6px">
            <div class="budget-bar-fill ${fillClass}" style="width:${barPct}%"></div>
          </div>
        </td>
        <td class="dept-spend">${fmtUsd(b.current_spend_usd)} / ${fmtUsd(b.monthly_cap_usd)}</td>
        <td>${displayPct}</td>
        <td>${stateTag || `<span style="color:var(--accent-green);font-weight:700">HEALTHY</span>`}</td>
        <td>${b.raw_payload_logging_enabled ? "On" : "Off"}</td>
        <td><button class="btn-cap" onclick="toggleBudgetEditor('${key}')">${openBudgetKey === b.budget_key ? "Close" : "Edit"}</button></td>
      </tr>
      ${editor}
    `;
  }).join("");

  container.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:12px;align-items:center;flex-wrap:wrap">
      <input class="agent-filter-search" style="flex:1;min-width:180px" placeholder="Search departments..."
             data-budget-search="true" value="${budgetAttr(budgetSearchTerm)}" oninput="budgetSearchTerm=this.value;renderBudgets()" />
      <select class="agent-filter-select" onchange="budgetStateFilter=this.value;renderBudgets()">
        <option value="" ${budgetStateFilter === "" ? "selected" : ""}>All Statuses</option>
        <option value="healthy" ${budgetStateFilter === "healthy" ? "selected" : ""}>Healthy</option>
        <option value="warning" ${budgetStateFilter === "warning" ? "selected" : ""}>Warning</option>
        <option value="throttled" ${budgetStateFilter === "throttled" ? "selected" : ""}>Throttled</option>
        <option value="raw" ${budgetStateFilter === "raw" ? "selected" : ""}>Raw Logging On</option>
      </select>
      <button class="btn-cap" onclick="budgetShowArchived=!budgetShowArchived;openBudgetKey=null;renderBudgets()">
        ${budgetShowArchived ? "Show Active" : "Show Archived"}
      </button>
    </div>
    <table class="agent-table">
      <thead>
        <tr>
          <th>Department</th><th>Spend / Cap</th><th>Used</th><th>Status</th><th>Raw Log</th><th>Action</th>
        </tr>
      </thead>
      <tbody>
        ${tableRows || `<tr><td colspan="6" class="placeholder">No ${budgetShowArchived ? "archived" : "active"} departments match this filter.</td></tr>`}
      </tbody>
    </table>`;
  if (searchWasFocused) {
    const input = container.querySelector("[data-budget-search='true']");
    if (input) {
      input.focus();
      input.setSelectionRange(searchCursor, searchCursor);
    }
  }
}

/** Render budget bars only (no cap controls) for the live ops strip */
function renderLiveBudgetBars() {
  const container = document.getElementById("liveBudgetBars");
  if (!container) return;
  if (!budgetData.length) {
    container.innerHTML = '<p class="placeholder">Loading...</p>';
    return;
  }
  container.innerHTML = displayBudgetRows(budgetData).filter(b => !b.archived).map(b => {
    const fillClass = b.state === "throttled" ? "critical"
                    : b.state === "warning"   ? "warn"
                    : "";
    const stateTag = b.throttled
      ? `<span class="budget-tag">THROTTLED</span>`
      : b.override_granted
        ? `<span class="budget-tag" style="color:var(--accent-green)">OVERRIDE ACTIVE</span>`
        : b.state === "warning"
          ? `<span class="budget-tag" style="color:var(--accent-yellow)">WARNING</span>`
          : "";
    const displayPct = fmtPct(b.current_spend_usd, b.monthly_cap_usd);
    const barPct     = b.monthly_cap_usd > 0
      ? Math.min((b.current_spend_usd / b.monthly_cap_usd) * 100, 100)
      : 0;
    const key = budgetJsString(b.budget_key);
    const overrideBtn = b.throttled
      ? `<button class="btn-override" onclick="doOverrideGroup('${key}')">Grant Override</button>`
      : b.override_granted
        ? `<button class="btn-override btn-revoke" onclick="doRevokeGroup('${key}')">Revoke Override</button>`
        : "";
    return `
      <div class="budget-item">
        <div class="budget-dept">
          <span class="dept-name">${b.display_department || cleanBudgetDeptName(b.department)} ${stateTag}</span>
          <span style="display:flex;align-items:center;gap:10px">
            ${overrideBtn}
            <span class="dept-spend">${fmtUsd(b.current_spend_usd)} / ${fmtUsd(b.monthly_cap_usd)} &nbsp;(${displayPct})</span>
          </span>
        </div>
        <div class="budget-bar-track">
          <div class="budget-bar-fill ${fillClass}" style="width:${barPct}%"></div>
        </div>
      </div>`;
  }).join("");
}

function updateKpiThrottled() {
  const count = budgetData.filter(b => b.throttled).length;
  const kpiEl   = document.getElementById("kpiThrottled");
  const kpiCard = document.getElementById("kpiThrottleCard");
  if (kpiEl)   kpiEl.textContent = count;
  if (kpiCard) kpiCard.className = "kpi-card" + (count > 0 ? " alert" : "");
}

function toggleBudgetEditor(key) {
  openBudgetKey = openBudgetKey === key ? null : key;
  renderBudgets();
}

function findBudgetGroup(key) {
  return displayBudgetRows(budgetData).find(b => b.budget_key === key);
}

async function forEachBudgetDepartment(key, fn) {
  const row = findBudgetGroup(key);
  const departments = row?.raw_departments?.filter(Boolean) || [];
  if (!departments.length) throw new Error("Department not found.");
  for (const department of departments) {
    await fn(department);
  }
}

async function doSetCapGroup(key) {
  const input = document.getElementById(`cap-${key}`);
  const newCap = parseFloat(input.value);
  if (!newCap || newCap <= 0) return;
  try {
    await forEachBudgetDepartment(key, department =>
      apiPost(`/api/budget/${encodeURIComponent(department)}/cap`, { new_cap_usd: newCap })
    );
    await loadBudgets();
  } catch (err) {
    alert(`Failed to set cap: ${err.message}`);
  }
}

async function doOverrideGroup(key) {
  try {
    await forEachBudgetDepartment(key, department =>
      apiPost(`/api/budget/${encodeURIComponent(department)}/override`, {})
    );
    await loadBudgets();
  } catch (err) {
    alert(`Failed to grant override: ${err.message}`);
  }
}

async function doRevokeGroup(key) {
  try {
    await forEachBudgetDepartment(key, department =>
      apiPost(`/api/budget/${encodeURIComponent(department)}/revoke`, {})
    );
    await loadBudgets();
  } catch (err) {
    alert(`Failed to revoke override: ${err.message}`);
  }
}

async function doSetThrottleTierGroup(key) {
  const sel = document.getElementById(`throttle-tier-${key}`);
  const tier = parseInt(sel.value, 10);
  try {
    await forEachBudgetDepartment(key, department =>
      apiPatch(`/api/budget/${encodeURIComponent(department)}/throttle-tier`, { tier })
    );
    await loadBudgets();
  } catch (err) {
    alert(`Failed to set throttle floor: ${err.message}`);
  }
}

async function doSetRawLoggingGroup(key) {
  const checkbox = document.getElementById(`raw-log-${key}`);
  const retentionSel = document.getElementById(`raw-retention-${key}`);
  const enabled = checkbox.checked;
  const retention_days = parseInt(retentionSel.value, 10);
  try {
    await forEachBudgetDepartment(key, department =>
      apiPatch(`/api/budget/${encodeURIComponent(department)}/raw-logging`, { enabled, retention_days })
    );
    await loadBudgets();
  } catch (err) {
    alert(`Failed to update raw logging: ${err.message}`);
  }
}

async function doResetGroup(key) {
  const row = findBudgetGroup(key);
  const label = row?.display_department || key;
  if (!confirm(`Reset ${label} spend to $0.00? This simulates a new billing period.`)) return;
  try {
    await forEachBudgetDepartment(key, department =>
      apiPost(`/api/budget/${encodeURIComponent(department)}/reset`, {})
    );
    await loadBudgets();
  } catch (err) {
    alert(`Failed to reset: ${err.message}`);
  }
}

async function doArchiveGroup(key) {
  const row = findBudgetGroup(key);
  const label = row?.display_department || key;
  if (!confirm(`Archive ${label}? It will be hidden from default budget views, but history stays available.`)) return;
  try {
    await forEachBudgetDepartment(key, department =>
      apiPatch(`/api/budget/${encodeURIComponent(department)}/archive`, {})
    );
    openBudgetKey = null;
    await loadBudgets();
  } catch (err) {
    alert(`Failed to archive department: ${err.message}`);
  }
}

async function doRestoreGroup(key) {
  const row = findBudgetGroup(key);
  const label = row?.display_department || key;
  try {
    await forEachBudgetDepartment(key, department =>
      apiPatch(`/api/budget/${encodeURIComponent(department)}/restore`, {})
    );
    openBudgetKey = null;
    await loadBudgets();
  } catch (err) {
    alert(`Failed to restore department: ${err.message}`);
  }
}

/** Supervisor: update a department's monthly cap */
async function doSetCap(department) {
  const input = document.getElementById(`cap-${department}`);
  const newCap = parseFloat(input.value);
  if (!newCap || newCap <= 0) return;
  try {
    await apiPost(`/api/budget/${department}/cap`, { new_cap_usd: newCap });
    await loadBudgets();
  } catch (err) {
    alert(`Failed to set cap: ${err.message}`);
  }
}

/** Supervisor: grant throttle override */
async function doOverride(department) {
  try {
    await apiPost(`/api/budget/${department}/override`, {});
    await loadBudgets();
  } catch (err) {
    alert(`Failed to grant override: ${err.message}`);
  }
}

/** Supervisor: revoke a previously granted override */
async function doRevoke(department) {
  try {
    await apiPost(`/api/budget/${department}/revoke`, {});
    await loadBudgets();
  } catch (err) {
    alert(`Failed to revoke override: ${err.message}`);
  }
}

/** Supervisor: set the throttle ceiling tier for a department */
async function doSetThrottleTier(department) {
  const sel  = document.getElementById(`throttle-tier-${department}`);
  const tier = parseInt(sel.value, 10);
  try {
    await apiPatch(`/api/budget/${department}/throttle-tier`, { tier });
    await loadBudgets();
  } catch (err) {
    alert(`Failed to set throttle floor: ${err.message}`);
  }
}

/** Supervisor: toggle raw payload logging and set retention period */
async function doSetRawLogging(department) {
  const checkbox     = document.getElementById(`raw-log-${department}`);
  const retentionSel = document.getElementById(`raw-retention-${department}`);
  const enabled        = checkbox.checked;
  const retention_days = parseInt(retentionSel.value, 10);
  try {
    await apiPatch(`/api/budget/${department}/raw-logging`, { enabled, retention_days });
    await loadBudgets();
  } catch (err) {
    alert(`Failed to update raw logging: ${err.message}`);
  }
}

/** Supervisor: reset spend to zero (new billing month) */
async function doReset(department) {
  if (!confirm(`Reset ${department} spend to $0.00? This simulates a new billing period.`)) return;
  try {
    await apiPost(`/api/budget/${department}/reset`, {});
    await loadBudgets();
  } catch (err) {
    alert(`Failed to reset: ${err.message}`);
  }
}

// Load on page ready, refresh every 15 seconds (staggered 0ms)
setTimeout(loadBudgets, 0);
setInterval(loadBudgets, 15000);
