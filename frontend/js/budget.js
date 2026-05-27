/**
 * budget.js — Department Budget Allocator UI  [Step 4]
 *
 * Loads live budget data from GET /api/budget and renders:
 *   - Progress bars with colour-coded states (healthy / warning / throttled)
 *   - Per-department spend vs cap figures
 *   - Supervisor controls: update cap, grant override, reset period
 */

let budgetData = [];

/** Fetch all budgets and re-render the panel */
async function loadBudgets() {
  try {
    budgetData = await apiGet("/api/budget");
    renderBudgets();
    renderLiveBudgetBars();
    updateKpiThrottled();
  } catch (err) {
    document.getElementById("budgetList").innerHTML =
      `<p class="placeholder" style="color:var(--accent-red)">Failed to load budgets: ${err.message}</p>`;
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

function renderBudgets() {
  const container = document.getElementById("budgetList");
  if (!budgetData.length) {
    container.innerHTML = '<p class="placeholder">No budget data found.</p>';
    return;
  }

  container.innerHTML = budgetData.map(b => {
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

    const overrideBtn = b.throttled
      ? `<button class="btn-override" onclick="doOverride('${b.department}')">Grant Override</button>`
      : b.override_granted
        ? `<button class="btn-override btn-revoke" onclick="doRevoke('${b.department}')">Revoke Override</button>`
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

    return `
      <div class="budget-item" id="budget-${b.department}">
        <div class="budget-dept">
          <span class="dept-name">${b.department} ${stateTag}</span>
          <span class="dept-spend">
            ${fmtUsd(b.current_spend_usd)} / ${fmtUsd(b.monthly_cap_usd)}
            &nbsp;(${displayPct})
          </span>
        </div>
        <div class="budget-bar-track">
          <div class="budget-bar-fill ${fillClass}" style="width:${barPct}%"></div>
        </div>
        <div class="budget-actions">
          <input  type="number" class="cap-input" id="cap-${b.department}"
                  value="${b.monthly_cap_usd}" min="1" step="10" />
          <button class="btn-cap" onclick="doSetCap('${b.department}')">Set Cap</button>
          <button class="btn-cap btn-reset" onclick="doReset('${b.department}')">Reset Month</button>
          ${overrideBtn}
        </div>
        <div class="budget-throttle-row">
          <span class="budget-throttle-label">Throttle floor:</span>
          <select class="budget-throttle-select" id="throttle-tier-${b.department}"
                  onchange="doSetThrottleTier('${b.department}')">
            ${tierOpts}
          </select>
          <span class="budget-throttle-hint">Min tier when over budget</span>
        </div>
      </div>
    `;
  }).join("");
}

/** Render budget bars only (no cap controls) for the live ops strip */
function renderLiveBudgetBars() {
  const container = document.getElementById("liveBudgetBars");
  if (!container) return;
  if (!budgetData.length) {
    container.innerHTML = '<p class="placeholder">Loading...</p>';
    return;
  }
  container.innerHTML = budgetData.map(b => {
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
    const overrideBtn = b.throttled
      ? `<button class="btn-override" onclick="doOverride('${b.department}')">Grant Override</button>`
      : b.override_granted
        ? `<button class="btn-override btn-revoke" onclick="doRevoke('${b.department}')">Revoke Override</button>`
        : "";
    return `
      <div class="budget-item">
        <div class="budget-dept">
          <span class="dept-name">${b.department} ${stateTag}</span>
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

/** Supervisor: set the throttle floor tier for a department */
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
