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
  document.getElementById("statTotalCalls").textContent      = d.total_calls.toLocaleString();
  document.getElementById("statScoutCalls").textContent      = `${d.scout_calls} (${d.scout_pct}%)`;
  document.getElementById("statAnalystCalls").textContent    = `${d.analyst_calls} (${d.analyst_pct}%)`;
  document.getElementById("statAdvisorCalls").textContent    = `${d.advisor_calls} (${d.advisor_pct}%)`;
  document.getElementById("statStrategistCalls").textContent = `${d.strategist_calls} (${d.strategist_pct}%)`;
  document.getElementById("statBudgetPct").textContent       = d.overall_budget_pct + "%";
  document.getElementById("statPruningSaved").textContent    = "$" + d.pruning_savings_usd.toFixed(4);
  document.getElementById("statMonthSpend").textContent      = "$" + d.spend_month_usd.toFixed(2);
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
  if (!confirm("Full reset — clears everything for a clean test run.\n\n• All transactions\n• All audit events\n• All registered agents\n• All Voice Guard events\n• Department spend reset to $0\n\nBudget caps and sensitive terms are preserved.")) return;

  const btn = document.getElementById("floatResetBtn");
  if (btn) { btn.textContent = "Resetting..."; btn.disabled = true; }

  try {
    const result = await apiPost("/api/admin/reset-demo", {});

    // Also wipe Voice Guard events
    try { await fetch("/api/voice/events", { method: "DELETE" }); } catch (_) {}

    // Refresh all panels
    loadDashboard();
    loadBudgets();
    if (typeof loadAgents        === "function") loadAgents();
    if (typeof loadVoiceStats    === "function") loadVoiceStats();
    if (typeof loadVoiceAuditLog === "function") loadVoiceAuditLog();

    if (btn) {
      btn.textContent = "✓ Done";
      setTimeout(() => { btn.textContent = "↺ Reset All"; btn.disabled = false; }, 2500);
    }

    _showResetToast(`Reset complete — ${result.transactions_cleared} transactions, ${result.audit_events_cleared} audit events, ${result.agents_cleared} agents cleared.`);
  } catch (e) {
    if (btn) { btn.textContent = "↺ Reset All"; btn.disabled = false; }
    alert("Reset failed: " + e.message);
  }
}

function _showResetToast(msg) {
  const toast = document.createElement("div");
  toast.textContent = msg;
  Object.assign(toast.style, {
    position: "fixed", bottom: "80px", right: "20px", zIndex: "9999",
    background: "#1a2f1a", border: "1px solid var(--accent-green)",
    color: "var(--accent-green)", borderRadius: "8px",
    padding: "10px 18px", fontSize: "12px", fontWeight: "600",
    boxShadow: "0 4px 16px rgba(0,0,0,0.4)", maxWidth: "360px",
  });
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
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

// ── Draggable panels ──────────────────────────────────────────────────────────
const PANEL_ORDER_KEY = "fage_panel_order";

function initDraggablePanels() {
  const container = document.getElementById("draggable-panels");
  if (!container || typeof Sortable === "undefined") return;

  Sortable.create(container, {
    animation:    150,
    handle:       ".drag-handle",
    ghostClass:   "sortable-ghost",
    chosenClass:  "sortable-chosen",
    onEnd() {
      const order = [...container.querySelectorAll(".dp-wrap")]
        .map(el => el.dataset.panelId);
      try { localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify(order)); } catch(e) {}
    },
  });

  // Restore saved order
  try {
    const saved = JSON.parse(localStorage.getItem(PANEL_ORDER_KEY) || "null");
    if (Array.isArray(saved)) {
      saved.forEach(id => {
        const el = container.querySelector(`.dp-wrap[data-panel-id="${id}"]`);
        if (el) container.appendChild(el);
      });
    }
  } catch(e) {}
}

function randomizePanels() {
  const container = document.getElementById("draggable-panels");
  if (!container) return;
  const panels = [...container.querySelectorAll(".dp-wrap")];
  // Fisher-Yates shuffle
  for (let i = panels.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [panels[i], panels[j]] = [panels[j], panels[i]];
  }
  panels.forEach(el => container.appendChild(el));
  const order = panels.map(el => el.dataset.panelId);
  try { localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify(order)); } catch(e) {}
}

// ── Boot ──────────────────────────────────────────────────────────────────────
initDraggablePanels();
restorePanelStates();
checkHealth();
loadDashboard();

setInterval(checkHealth,    10000);
setInterval(loadDashboard,   5000);
