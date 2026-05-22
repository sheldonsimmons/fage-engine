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
      </div>
    `;
  }).join("");
}

function updateKpiThrottled() {
  const count = budgetData.filter(b => b.throttled).length;
  document.getElementById("kpiThrottled").textContent = count;
  document.getElementById("kpiThrottleCard").className =
    "kpi-card" + (count > 0 ? " alert" : "");
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

// Load on page ready, refresh every 15 seconds
loadBudgets();
setInterval(loadBudgets, 15000);
