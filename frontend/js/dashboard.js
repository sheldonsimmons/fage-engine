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
    renderComplianceGrid(d);
    renderExecSummary(d);
  } catch (err) {
    console.warn("Dashboard load failed:", err.message);
  }
}

function renderKpis(d) {
  // Total Spend Today
  document.getElementById("kpiSpend").textContent =
    "$" + d.spend_today_usd.toFixed(4);
  document.getElementById("kpiSpendSub").textContent =
    `$${d.spend_month_usd.toFixed(4)} this month · ${d.calls_today} calls today`;

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
  document.getElementById("statMonthSpend").textContent    = "$" + d.spend_month_usd.toFixed(4);
}

// ── Governance & Compliance Activity ─────────────────────────────────────────
function renderComplianceGrid(d) {
  const grid = document.getElementById("complianceGrid");
  if (!grid) return;
  const items = [
    { icon: "🚫", value: d.blocked_count,      label: "Requests Blocked",          sub: "Sensitive terms triggered block policy — request never reached AI model",                         cls: "critical" },
    { icon: "⚠️",  value: d.escalated_count,    label: "Escalated to Flagship",     sub: "Legal, NDA, contract language forced flagship review",                                           cls: "high"     },
    { icon: "🔍", value: d.flagged_count,       label: "Flagged in Audit Log",      sub: "High-risk keywords logged for compliance review",                                                cls: "medium"   },
    { icon: "🔒", value: d.pii_count,           label: "PII Detected",              sub: "Credit cards, SSNs, emails, phone numbers caught before AI processing",                          cls: "high"     },
    { icon: "💰", value: d.throttle_prevented,  label: "Budget Overruns Prevented", sub: "Auto-throttle engaged before department cap was breached",                                        cls: "low"      },
    { icon: "⚡", value: d.collision_count,     label: "Agent Collisions Resolved", sub: "Concurrent write conflicts detected and locked — zero data corruption",                           cls: "low"      },
  ];
  const hasData = items.some(i => i.value > 0);
  if (!hasData) {
    grid.innerHTML = '<p class="placeholder">No compliance events yet — run a route call or load demo data.</p>';
    return;
  }
  grid.innerHTML = items.map(i => `
    <div class="demo-compliance-card ${i.cls}">
      <div class="demo-compliance-icon">${i.icon}</div>
      <div class="demo-compliance-value">${i.value.toLocaleString()}</div>
      <div class="demo-compliance-label">${i.label}</div>
      <div class="demo-compliance-sub">${i.sub}</div>
    </div>
  `).join("");
}

// ── Executive Summary ROI ─────────────────────────────────────────────────────
function renderExecSummary(d) {
  const grid = document.getElementById("execGrid");
  if (!grid) return;
  const tokens = d.tokens_saved_total || 0;
  const tokensLabel = tokens >= 1_000_000
    ? (tokens / 1_000_000).toFixed(2) + "M"
    : tokens >= 1_000
      ? (tokens / 1_000).toFixed(1) + "K"
      : tokens.toLocaleString();
  const eventsLabel = (d.compliance_events_total || 0) >= 1000
    ? ((d.compliance_events_total) / 1000).toFixed(1) + "K"
    : (d.compliance_events_total || 0).toString();
  const savings = (d.projected_annual_savings || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const items = [
    { icon: "$", cls: "green",  color: "green",  title: "PROJECTED ANNUAL SAVINGS",  value: "$" + savings,                                     sub: "Based on 30-day actual performance extrapolated to 12 months" },
    { icon: "%", cls: "accent", color: "accent", title: "AI COST REDUCTION",         value: (d.cost_reduction_pct || 0) + "%",                  sub: "Achieved through smart routing, token pruning, and budget enforcement" },
    { icon: "◈", cls: "purple", color: "purple", title: "COMPLIANCE EVENTS LOGGED",  value: eventsLabel,                                        sub: "Immutable audit trail — every AI decision logged, timestamped, exportable" },
    { icon: "⚡", cls: "yellow", color: "yellow", title: "TIME TO DEPLOY",            value: "15 min",                                           sub: "One Apex class · one Flow action · 4 custom fields · no IT project required" },
    { icon: "↑", cls: "green",  color: "green",  title: "TOKENS SAVED",              value: tokensLabel,                                        sub: "Context pruned before every AI call — savings start from day one" },
    { icon: "0", cls: "red",    color: "",        title: "DATA CORRUPTION EVENTS",    value: "Zero",                                             sub: (d.collision_count || 0) + " agent collisions detected and locked — no silent overwrites" },
  ];
  const hasData = (d.total_calls || 0) > 0 || (d.flagged_count || 0) > 0;
  if (!hasData) {
    grid.innerHTML = '<p class="placeholder">No data yet — run a route call or load demo data.</p>';
    return;
  }
  grid.innerHTML = items.map(i => `
    <div class="demo-exec-card">
      <div class="demo-exec-icon ${i.cls}">${i.icon}</div>
      <div class="demo-exec-content">
        <div class="demo-exec-title">${i.title}</div>
        <div class="demo-exec-value ${i.color}">${i.value}</div>
        <div class="demo-exec-sub">${i.sub}</div>
      </div>
    </div>
  `).join("");
}

// ── Enterprise Demo loader ────────────────────────────────────────────────────
async function loadEnterpriseDemo() {
  if (!confirm("Load enterprise demo data?\n\nThis will replace all current data with Meridian Financial Group's 30-day enterprise dataset (12 agents, 4 departments, Marketing throttled).")) return;
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

// ── Collapsible panels ────────────────────────────────────────────────────────
function togglePanel(bodyId, chevronId) {
  const body    = document.getElementById(bodyId);
  const chevron = document.getElementById(chevronId);
  const open    = body.style.display !== "none";
  body.style.display  = open ? "none" : "";
  chevron.textContent = open ? "▸" : "▾";
}

// ── Boot ──────────────────────────────────────────────────────────────────────
checkHealth();
loadDashboard();

setInterval(checkHealth,    10000);
setInterval(loadDashboard,  15000);
