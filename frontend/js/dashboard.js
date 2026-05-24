/**
 * dashboard.js — FAGE Executive Dashboard  [Step 7]
 *
 * Loads GET /api/dashboard on page load and every 15 seconds.
 * Populates all KPI cards, stat bar, and the health indicator.
 */

// ── Health check ──────────────────────────────────────────────────────────────
async function checkHealth() {
  const dot   = document.querySelector(".status-dot");
  const label = document.getElementById("statusLabel");
  const badge = document.getElementById("modeBadge");
  try {
    const data = await apiGet("/health");
    dot.classList.remove("offline");
    dot.classList.add("online");
    label.textContent = `Backend online — ${data.version}`;

    // Show model mode badge
    const mode = data.model_mode || "simulated";
    const provider = data.provider || "simulated";
    badge.textContent   = mode === "live" ? `LIVE · ${provider.toUpperCase()}` : "SIMULATED";
    badge.className     = `mode-badge ${mode}`;
    badge.style.display = "inline-block";
  } catch {
    dot.classList.remove("online");
    dot.classList.add("offline");
    label.textContent   = "Backend offline — run: uvicorn main:app --reload --port 8001";
    badge.style.display = "none";
  }
}

// ── Main dashboard loader ──────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const d = await apiGet("/api/dashboard");
    renderKpis(d);
    renderStatBar(d);
  } catch (err) {
    console.warn("Dashboard load failed:", err.message);
  }
}

function renderKpis(d) {
  // Total Spend Today
  document.getElementById("kpiSpend").textContent =
    "$" + d.spend_today_usd.toFixed(4);
  document.getElementById("kpiSpendSub").textContent =
    `$${d.spend_month_usd.toFixed(2)} this month · ${d.calls_today} calls today`;

  // Tokens Saved
  document.getElementById("kpiTokensSaved").textContent =
    d.tokens_saved_total.toLocaleString();
  document.getElementById("kpiTokensSub").textContent =
    `${d.tokens_saved_today.toLocaleString()} today · est. $${d.pruning_savings_usd.toFixed(4)} saved`;

  // Active Agents
  document.getElementById("kpiAgents").textContent = d.agents_total;
  document.getElementById("kpiAgentsSub").textContent =
    `${d.agents_active} active · ${d.agents_locked} locked · ${d.agents_idle} idle`;

  // Throttled Departments (also updated by budget.js — this is the authoritative source)
  document.getElementById("kpiThrottled").textContent = d.throttled_count;
  document.getElementById("kpiThrottleSub").textContent =
    d.throttled_count > 0
      ? "⚠ Supervisor action required"
      : `${d.overall_budget_pct}% of total budget used`;
  document.getElementById("kpiThrottleCard").className =
    "kpi-card" + (d.throttled_count > 0 ? " alert" : "");
}

function renderStatBar(d) {
  document.getElementById("statTotalCalls").textContent    = d.total_calls.toLocaleString();
  document.getElementById("statMicroCalls").textContent    = `${d.micro_calls} (${d.micro_pct}%)`;
  document.getElementById("statFlagshipCalls").textContent = `${d.flagship_calls} (${d.flagship_pct}%)`;
  document.getElementById("statBudgetPct").textContent     = d.overall_budget_pct + "%";
  document.getElementById("statPruningSaved").textContent  = "$" + d.pruning_savings_usd.toFixed(4);
  document.getElementById("statMonthSpend").textContent    = "$" + d.spend_month_usd.toFixed(2);
}

// ── Enterprise Demo loader ────────────────────────────────────────────────────
async function loadEnterpriseDemo() {
  if (!confirm("Load enterprise demo data?\n\nThis will replace all current data with Meridian Financial Group's 1-year enterprise dataset (12 agents, 4 departments, 128K calls, Marketing throttled). Dashboard will refresh in ~30 seconds.")) return;
  try {
    const btn = document.querySelector('[onclick="loadEnterpriseDemo()"]');
    if (btn) btn.textContent = "★ Loading...";
    await apiPost("/api/admin/populate-enterprise-demo", {});
    // Wait for background task to complete, then refresh
    setTimeout(() => {
      loadDashboard();
      if (typeof loadBudgets   === "function") loadBudgets();
      if (typeof loadAgents    === "function") loadAgents();
      if (typeof loadAuditLog  === "function") loadAuditLog();
      if (typeof loadKeywords  === "function") loadKeywords();
      if (btn) btn.textContent = "★ Enterprise Demo";
    }, 8000);
  } catch (e) {
    alert("Enterprise demo failed: " + e.message);
  }
}

// ── Pruner panel (wired to POST /api/prune) ───────────────────────────────────
async function runPruner() {
  const input  = document.getElementById("prunerInput").value.trim();
  const btn    = document.getElementById("prunerRunBtn");
  const stats  = document.getElementById("prunerStats");
  const output = document.getElementById("prunerOutput");

  if (!input) {
    stats.innerHTML = '<span style="color:var(--accent-red)">Paste a payload first.</span>';
    return;
  }

  btn.disabled    = true;
  btn.textContent = "Running...";
  stats.innerHTML = "";
  output.value    = "";

  try {
    const result = await apiPost("/api/prune", { text: input, department: "Support" });
    output.value = result.cleaned_text;

    const savingsColor = result.compression_pct >= 50
      ? "var(--accent-green)"
      : result.compression_pct >= 20
        ? "var(--accent-yellow)"
        : "var(--text-muted)";

    const microSaved    = result.micro_cost_saved_usd.toFixed(6);
    const flagshipSaved = result.flagship_cost_saved_usd.toFixed(6);

    stats.innerHTML = `
      <span>Tokens before: <strong>${result.raw_tokens.toLocaleString()}</strong></span>
      &nbsp;→&nbsp;
      <span>Tokens after: <strong>${result.clean_tokens.toLocaleString()}</strong></span>
      &nbsp;|&nbsp;
      <span style="color:${savingsColor}; font-weight:600;">
        ${result.compression_pct}% compressed (${result.tokens_saved.toLocaleString()} tokens saved)
      </span>
      <br style="margin:4px 0"/>
      <span style="color:var(--text-muted)">Cost avoided &rarr;</span>
      <span style="color:var(--accent-green); font-weight:600;">&nbsp;Micro: $${microSaved}</span>
      &nbsp;&nbsp;
      <span style="color:var(--accent-purple); font-weight:600;">Flagship: $${flagshipSaved}</span>
      &nbsp;&nbsp;
      <span style="color:var(--text-muted); font-size:11px;">| Filters: ${result.filters_applied.join(", ")}</span>
    `;

    // Refresh KPIs to reflect any new pruning data
    loadDashboard();

  } catch (err) {
    stats.innerHTML = `<span style="color:var(--accent-red)">Error: ${err.message}</span>`;
  } finally {
    btn.disabled    = false;
    btn.textContent = "Run Sweeper";
  }
}

// ── Demo reset ────────────────────────────────────────────────────────────────
async function resetDemoData() {
  if (!confirm("Full reset — clears all transactions, audit events, and agents.\n\nDepartment budget caps and sensitive terms are preserved.")) return;
  try {
    const result = await apiPost("/api/admin/reset-demo", {});
    alert(`✓ Reset complete.\n${result.transactions_cleared} transactions cleared.\n${result.audit_events_cleared} audit events cleared.\n${result.agents_cleared} agents removed.`);
    loadDashboard();
    if (typeof loadAgents   === "function") loadAgents();
    loadBudgets();
  } catch (e) {
    alert("Reset failed: " + e.message);
  }
}

// ── Collapsible panels (with localStorage persistence) ────────────────────────
function togglePanel(bodyId, chevronId) {
  const body    = document.getElementById(bodyId);
  const chevron = document.getElementById(chevronId);
  const open    = body.style.display !== "none";
  body.style.display  = open ? "none" : "";
  chevron.textContent = open ? "▸" : "▾";
  try { localStorage.setItem("panel_" + bodyId, open ? "closed" : "open"); } catch(e) {}
}

// Restore panel states from localStorage on load
function restorePanelStates() {
  const panels = [
    ["prunerBody",      "prunerChevron"],
    ["routerBody",      "routerChevron"],
    ["keywordsBody",    "keywordsChevron"],
    ["auditBody",       "auditChevron"],
    ["voiceGuardBody",  "voiceGuardChevron"],
    ["midRowBody",      "midRowChevron"],
  ];
  panels.forEach(([bodyId, chevronId]) => {
    const body    = document.getElementById(bodyId);
    const chevron = document.getElementById(chevronId);
    if (!body || !chevron) return;
    try {
      const saved = localStorage.getItem("panel_" + bodyId);
      // Default: all closed unless localStorage says open
      const shouldOpen = saved === "open";
      body.style.display  = shouldOpen ? "" : "none";
      chevron.textContent = shouldOpen ? "▾" : "▸";
    } catch(e) {
      body.style.display  = "none";
      chevron.textContent = "▸";
    }
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────
restorePanelStates();
checkHealth();
loadDashboard();

setInterval(checkHealth,    10000);
setInterval(loadDashboard,  15000);
