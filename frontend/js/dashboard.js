/**
 * dashboard.js — CostPilot Executive Dashboard  [Step 7]
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
function scopedApiPath(path) {
  if (document.body?.dataset?.dashboardScope === "all") return path;
  const wsId = localStorage.getItem("cp_workspace_id") || "";
  if (!wsId) return path;
  const joiner = path.includes("?") ? "&" : "?";
  return `${path}${joiner}workspace_id=${encodeURIComponent(wsId)}`;
}

function localDayKey(value) {
  if (!value) return "";
  const text = String(value);
  const date = new Date(text.endsWith("Z") ? text : `${text}Z`);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date).reduce((acc, part) => {
    acc[part.type] = part.value;
    return acc;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function normalizeTierName(tier) {
  const value = String(tier || "").toLowerCase();
  if (value === "micro" || value === "scout") return "Scout";
  if (value === "analyst") return "Analyst";
  if (value === "advisor" || value === "flagship") return "Advisor";
  if (value === "strategist") return "Strategist";
  return "";
}

async function loadDashboard() {
  try {
    const d = await apiGet(scopedApiPath("/api/dashboard"));
    renderKpis(d);
    renderStatBar(d);
    renderCeoBanner(d);
    renderDeptHealth(d);
    renderTodayTierSplit(d);
    renderAgentEfficiency();
    renderInsights();
  } catch (err) {
    console.warn("Dashboard load failed:", err.message);
  }
}

// ── Department Health Strip ───────────────────────────────────────────────────

function displayDeptName(name) {
  if (!name) return "—";
  const text = String(name);
  const colonIndex = text.indexOf(":");
  return colonIndex >= 0 ? text.slice(colonIndex + 1).trim() || text : text;
}

function mergedDeptBudgets(rows = []) {
  const merged = new Map();
  rows.forEach(b => {
    const label = displayDeptName(b.display_department || b.department || b.name);
    const key = label.toLowerCase();
    const current = merged.get(key);
    if (!current) {
      merged.set(key, { ...b, display_department: label });
      return;
    }
    current.current_spend_usd = (current.current_spend_usd || 0) + (b.current_spend_usd || 0);
    current.monthly_cap_usd = Math.max(current.monthly_cap_usd || 0, b.monthly_cap_usd || 0);
    current.throttled = current.throttled || b.throttled;
    const pct = current.monthly_cap_usd ? (current.current_spend_usd / current.monthly_cap_usd) * 100 : 0;
    current.budget_used_pct = Math.round(pct * 10) / 10;
    current.used_pct = current.budget_used_pct;
  });
  return Array.from(merged.values()).sort((a, b) =>
    String(a.display_department).localeCompare(String(b.display_department))
  );
}

function renderDeptHealth(d) {
  // Department pills — read from budget bars data
  const pills = document.getElementById("deptHealthPills");
  if (!pills) return;
  const budgets = mergedDeptBudgets(d.department_budgets || d.budget_summaries || []);
  if (!budgets.length) {
    pills.innerHTML = `<span style="color:var(--text-muted);font-size:11px">No departments configured</span>`;
    return;
  }
  pills.innerHTML = budgets.map(b => {
    const pct     = b.budget_used_pct ?? b.used_pct ?? 0;
    const color   = b.throttled ? "var(--accent-red)" : pct >= 70 ? "var(--accent-yellow)" : "var(--accent-green)";
    const bg      = b.throttled ? "rgba(248,81,73,.12)" : pct >= 70 ? "rgba(210,153,34,.12)" : "rgba(63,185,80,.12)";
    const icon    = b.throttled ? "⛔" : pct >= 70 ? "⚠" : "✓";
    const label   = displayDeptName(b.department || b.name);
    return `<span title="${label}: ${pct}% of $${(b.monthly_cap_usd||0).toFixed(0)}/mo cap used"
      style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:12px;
             font-size:11px;font-weight:600;background:${bg};color:${color};border:1px solid ${color}33;
             cursor:default">
      ${icon} ${label} <span style="font-weight:400;font-size:10px">${pct}%</span>
    </span>`;
  }).join("");
}

async function renderTodayTierSplit(d = {}) {
  const setEl = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  const label = document.getElementById("dhTierSplitLabel");
  const split = d.tier_split_today;
  if (split) {
    const total = split.total || 0;
    if (label) label.textContent = total ? "Tier split today:" : "Tier split today: no routed calls";
    setEl("dhScout", `${split.scout_pct || 0}%`);
    setEl("dhAnalyst", `${split.analyst_pct || 0}%`);
    setEl("dhAdvisor", `${split.advisor_pct || 0}%`);
    setEl("dhStrategist", `${split.strategist_pct || 0}%`);
    return;
  }

  try {
    const events = await apiGet(scopedApiPath("/api/audit?limit=1000"));
    const today = localDayKey(new Date().toISOString());
    const counts = { Scout: 0, Analyst: 0, Advisor: 0, Strategist: 0 };

    events.forEach(event => {
      if (localDayKey(event.timestamp) !== today) return;
      const tier = normalizeTierName(event.model_tier);
      if (tier && Object.prototype.hasOwnProperty.call(counts, tier)) counts[tier] += 1;
    });

    const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
    if (label) label.textContent = total ? "Tier split today:" : "Tier split today: no routed calls";

    ["Scout", "Analyst", "Advisor", "Strategist"].forEach(tier => {
      const pct = total ? Math.round((counts[tier] / total) * 1000) / 10 : 0;
      setEl(`dh${tier}`, `${pct}%`);
    });
  } catch (err) {
    if (label) label.textContent = "Tier split current view:";
    console.warn("Today tier split failed:", err.message);
  }
}

// ── Agent Efficiency Rank ─────────────────────────────────────────────────────

async function renderAgentEfficiency() {
  const leaderboard = document.getElementById("agentEffBody2");
  if (!leaderboard) return;
  try {
    const now  = new Date();
    const from = new Date(now.getFullYear(), now.getMonth() - 1, now.getDate()).toISOString();
    const data = await apiGet(`/api/reports/agent-activity?date_from=${from}&date_to=${now.toISOString()}`);
    const agents = (data.agents || [])
      .map(agent => ({
        ...agent,
        economy_pct: Math.max(0, Math.min(100, 100 - (agent.flagship_pct || 0))),
      }))
      .sort((a, b) =>
        b.economy_pct - a.economy_pct ||
        (b.tokens_saved || 0) - (a.tokens_saved || 0) ||
        (b.calls || 0) - (a.calls || 0)
      );
    if (!agents.length) {
      leaderboard.innerHTML = `<div class="placeholder">No agent activity yet.</div>`;
      return;
    }
    leaderboard.innerHTML = agents.map((a, index) => {
      const dept   = displayDeptName(a.display_department || a.department || "—");
      const econ   = a.economy_pct;
      const econColor = econ >= 70 ? "var(--accent-green)" : econ < 40 ? "var(--accent-red)" : "var(--accent-yellow)";
      const review = econ < 40 ? `<span class="efficiency-review">Needs review</span>` : "";
      return `<article class="efficiency-rank-card">
        <div class="efficiency-rank-number">${index + 1}</div>
        <div class="efficiency-rank-main">
          <div class="efficiency-rank-title">
            <div>
              <strong>${a.display_name || a.name}</strong>
              <span>${dept} · ${(a.platform || "—").toUpperCase()}</span>
            </div>
            ${review}
          </div>
          <div class="efficiency-progress-row">
            <div class="efficiency-progress" aria-label="${econ.toFixed(0)} percent economy routing">
              <span style="width:${econ}%;background:${econColor}"></span>
            </div>
            <strong style="color:${econColor}">${econ.toFixed(0)}%</strong>
          </div>
        </div>
        <div class="efficiency-rank-stats">
          <div><strong>${(a.calls || 0).toLocaleString()}</strong><span>Calls</span></div>
          <div><strong>$${(a.cost_usd || 0).toFixed(2)}</strong><span>Total cost</span></div>
          <div><strong>$${(a.avg_cost_usd || 0).toFixed(4)}</strong><span>Avg / call</span></div>
          <div><strong>${(a.tokens_saved || 0).toLocaleString()}</strong><span>Tokens pruned</span></div>
        </div>
      </article>`;
    }).join("");
  } catch(e) {
    leaderboard.innerHTML = `<div class="placeholder" style="color:var(--accent-red)">${e.message}</div>`;
  }
}

// ── Routing Insights: Top Keywords + Compliance Breakdown ─────────────────────

async function renderInsights() {
  try {
    const d = await apiGet(scopedApiPath("/api/dashboard"));

    // Compliance breakdown
    const compEl = document.getElementById("complianceBreakdown");
    if (compEl) {
      const items = [
        { label: "Requests Blocked",          val: d.blocked_count || 0,     color: "var(--accent-red)",    icon: "🚫" },
        { label: "PII Detected",              val: d.pii_count || 0,          color: "var(--accent-yellow)", icon: "🔒" },
        { label: "Flagged in Audit Log",      val: d.flagged_count || 0,      color: "var(--accent)",        icon: "🔍" },
        { label: "Escalated to Flagship",     val: d.escalated_count || 0,    color: "var(--accent-yellow)", icon: "⚠️" },
        { label: "Budget Overruns Prevented", val: d.throttle_prevented || 0, color: "var(--accent-green)",  icon: "💰" },
        { label: "Agent Collisions Resolved", val: d.collision_count || 0,    color: "var(--text-muted)",    icon: "⚡" },
      ];
      compEl.innerHTML = items.map(i => `
        <div style="display:flex;align-items:center;justify-content:space-between;
          padding:8px 0;border-bottom:1px solid var(--border);font-size:12px">
          <span style="color:var(--text-muted)">${i.icon} ${i.label}</span>
          <span style="font-weight:700;font-family:var(--font-mono);color:${i.color}">${i.val.toLocaleString()}</span>
        </div>`).join("");
    }

    // Top keywords — from structured keyword_stats in dashboard API response
    const kwEl = document.getElementById("topKeywordsList");
    if (kwEl) {
      const stats = d.keyword_stats || [];
      if (!stats.length) {
        kwEl.innerHTML = `<div style="color:var(--text-muted);font-size:12px;padding:8px 0">No keyword data yet — appears once calls route through CostPilot.</div>`;
      } else {
        const max = stats[0].count;
        kwEl.innerHTML = stats.map(({kw, count}) => `
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
            <div style="flex:1;font-size:12px;color:var(--text-primary);font-weight:600;min-width:80px">${kw}</div>
            <div style="flex:3;background:var(--border);border-radius:4px;height:6px;overflow:hidden">
              <div style="width:${Math.round(count/max*100)}%;height:100%;background:var(--accent);border-radius:4px"></div>
            </div>
            <div style="font-size:11px;color:var(--text-muted);font-family:var(--font-mono);min-width:24px;text-align:right">${count}</div>
          </div>`).join("");
      }
    }
  } catch(e) { /* silent */ }
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
  document.getElementById("kpiBlockedRequests").textContent  = (d.blocked_count || 0).toLocaleString();
  document.getElementById("statScoutCalls").textContent      = `${d.scout_calls} (${d.scout_pct}%)`;
  document.getElementById("statAnalystCalls").textContent    = `${d.analyst_calls} (${d.analyst_pct}%)`;
  document.getElementById("statAdvisorCalls").textContent    = `${d.advisor_calls} (${d.advisor_pct}%)`;
  document.getElementById("statStrategistCalls").textContent = `${d.strategist_calls} (${d.strategist_pct}%)`;
  document.getElementById("statBudgetPct").textContent       = d.overall_budget_pct + "%";
  document.getElementById("statPruningSaved").textContent    = "$" + d.pruning_savings_usd.toFixed(4);
  document.getElementById("statMonthSpend").textContent      = "$" + d.spend_month_usd.toFixed(2);
}

function renderCeoBanner(d) {
  // Use backend pre-computed values so all pages show the same numbers
  const routedCalls    = d.requests_routed ?? ((d.scout_calls || 0) + (d.analyst_calls || 0) + (d.advisor_calls || 0) + (d.strategist_calls || 0));
  const governedCalls  = d.requests_governed ?? routedCalls;
  const economyCalls   = (d.scout_calls || 0) + (d.analyst_calls || 0);
  const routingPct     = d.routing_efficiency_pct != null ? Math.round(d.routing_efficiency_pct) : (routedCalls > 0 ? Math.round((economyCalls / routedCalls) * 100) : 0);

  const routingSaved   = d.routing_savings_usd  != null ? d.routing_savings_usd  : 0;
  const pruningSaved   = d.pruning_savings_usd   || 0;
  const totalSaved     = d.total_savings_usd     != null ? d.total_savings_usd    : (routingSaved + pruningSaved);
  const annualSavings  = d.projected_annual_savings != null ? d.projected_annual_savings : totalSaved * 12;
  const FLAGSHIP_AVG   = 0.030;
  const fullCost       = routedCalls * FLAGSHIP_AVG;
  const wasteBlocked   = fullCost > 0 ? Math.round((routingSaved / fullCost) * 100) : 0;

  function fmtBig(v) {
    if (v >= 1000000) return "$" + (v / 1000000).toFixed(2) + "M";
    if (v >= 1000)    return "$" + (v / 1000).toFixed(1) + "K";
    return "$" + v.toFixed(2);
  }

  document.getElementById("ceoTotalSaved").textContent    = fmtBig(totalSaved);
  document.getElementById("ceoRoutingPct").textContent    = routingPct + "%";
  document.getElementById("ceoTotalCalls").textContent    = governedCalls >= 1000
    ? (governedCalls / 1000).toFixed(1) + "K" : governedCalls.toLocaleString();
  document.getElementById("ceoPruningSaved").textContent  = fmtBig(pruningSaved);
  document.getElementById("ceoPruningTokens").textContent = (d.tokens_saved_total || 0).toLocaleString() + " tokens stripped";
  document.getElementById("ceoAnnualSavings").textContent = fmtBig(annualSavings);
  document.getElementById("ceoWastePercent").textContent  = wasteBlocked + "%";
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
      <span style="color:var(--tier-advisor); font-weight:600;">Flagship: $${flagshipSaved}</span>
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
    if (typeof loadTimeSeries    === "function") loadTimeSeries();

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

  // Re-render timeseries charts when section becomes visible
  // (Chart.js can't size itself when container is display:none)
  if (!open && bodyId === "timeseriesBody") {
    requestAnimationFrame(() => {
      if (typeof loadTimeSeries === "function") loadTimeSeries();
    });
  }
}

// Restore panel states from localStorage on load
function restorePanelStates() {
  const panels = [
    ["prunerBody",      "prunerChevron"],
    ["routerBody",      "routerChevron"],
    ["keywordsBody",    "keywordsChevron"],
    ["auditBody",       "auditChevron"],
    ["voiceGuardBody",   "voiceGuardChevron"],
    ["midRowBody",       "midRowChevron"],
    ["routingRulesBody",  "routingRulesChevron"],
    ["timeseriesBody",    "timeseriesChevron"],
  ];
  // Panels that are always open regardless of saved state
  const alwaysOpen = new Set(["timeseriesBody"]);

  panels.forEach(([bodyId, chevronId]) => {
    const body    = document.getElementById(bodyId);
    const chevron = document.getElementById(chevronId);
    if (!body || !chevron) return;
    try {
      const saved = localStorage.getItem("panel_" + bodyId);
      const shouldOpen = alwaysOpen.has(bodyId) || saved === "open";
      body.style.display  = shouldOpen ? "" : "none";
      chevron.textContent = shouldOpen ? "▾" : "▸";
    } catch(e) {
      body.style.display  = "none";
      chevron.textContent = "▸";
    }
  });

  // Ensure timeseries localStorage preference doesn't override alwaysOpen
  try { localStorage.removeItem("panel_timeseriesBody"); } catch(e) {}

  // Re-render timeseries charts — container is now guaranteed visible
  setTimeout(() => {
    if (typeof loadTimeSeries === "function") loadTimeSeries();
  }, 100);
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
// Staggered 200ms, dashboard poll reduced from 5s → 15s to reduce API burst
setTimeout(checkHealth,   200);
setTimeout(loadDashboard, 200);

setInterval(checkHealth,   15000);
setInterval(loadDashboard, 15000);
