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

    return `
      <div class="budget-item" id="budget-${b.department}">
        <div class="budget-dept">
          <span class="dept-name">${b.department} ${stateTag}</span>
          <span class="dept-spend">
            $${b.current_spend_usd < 0.01 ? b.current_spend_usd.toFixed(4) : b.current_spend_usd.toFixed(2)} / $${b.monthly_cap_usd.toFixed(2)}
            &nbsp;(${b.used_pct}%)
          </span>
        </div>
        <div class="budget-bar-track">
          <div class="budget-bar-fill ${fillClass}" style="width:${Math.min(b.used_pct, 100)}%"></div>
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
