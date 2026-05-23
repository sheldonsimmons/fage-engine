/**
 * reports.js — FAGE Reporting Engine UI
 *
 * Three tabs: Savings, Risk & Compliance, Departments
 * Chart.js for all visualizations.
 * Date range selector: 7D / 30D / 90D / 1Y
 */

let activeTab  = "savings";
const charts   = {};

const COLORS = {
  micro:    "#58a6ff",
  flagship: "#bc8cff",
  green:    "#3fb950",
  yellow:   "#d29922",
  red:      "#f85149",
  muted:    "#8b949e",
  border:   "#30363d",
  panel:    "#161b22",
  dept: ["#58a6ff","#3fb950","#bc8cff","#d29922","#f85149","#79c0ff","#56d364","#d2a8ff"],
};

// ── Utilities ─────────────────────────────────────────────────────────────────

function fmtUsd(v) {
  if (v == null)  return "—";
  if (v === 0)    return "$0.00";
  if (v < 0.0001) return "$" + v.toFixed(6);
  if (v < 0.01)   return "$" + v.toFixed(4);
  if (v < 1)      return "$" + v.toFixed(4);
  return "$" + v.toFixed(2);
}

function fmtNum(v) {
  if (v == null) return "—";
  return v.toLocaleString();
}

function fmtTs(iso) {
  if (!iso) return "—";
  return new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("en-US", {
    timeZone: "America/Chicago",
    month: "numeric", day: "numeric",
    hour: "numeric", minute: "2-digit", hour12: true,
  });
}

function riskBadge(level) {
  const cls = { critical: "badge-critical", high: "badge-high", medium: "badge-medium", low: "badge-low" }[level] || "";
  return `<span class="rpt-badge ${cls}">${(level || "low").toUpperCase()}</span>`;
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function chartDefaults() {
  return {
    plugins: { legend: { labels: { color: COLORS.muted, font: { size: 11 } } } },
    scales: {
      x: { ticks: { color: COLORS.muted, font: { size: 10 }, maxTicksLimit: 10 },
           grid: { color: "rgba(48,54,61,0.5)" } },
      y: { ticks: { color: COLORS.muted, font: { size: 10 } },
           grid: { color: "rgba(48,54,61,0.5)" } },
    },
  };
}

// ── Tab switching ─────────────────────────────────────────────────────────────

document.querySelectorAll(".rpt-tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".rpt-tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".rpt-pane").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    activeTab = btn.dataset.tab;
    document.getElementById(`tab-${activeTab}`).classList.add("active");
    loadActiveTab();
  });
});

// ── Date preset picker ────────────────────────────────────────────────────────

function resolveDatePreset(preset) {
  const now   = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
  function addMonths(d, n) { const r = new Date(d); r.setMonth(r.getMonth() + n); return r; }

  let from, to;

  if (preset === "custom") {
    const f = document.getElementById("rptDateFrom").value;
    const t = document.getElementById("rptDateTo").value;
    from = f ? new Date(f) : addDays(today, -30);
    to   = t ? addDays(new Date(t), 1) : addDays(today, 1);
  } else if (preset === "today") {
    from = today; to = addDays(today, 1);
  } else if (preset === "yesterday") {
    from = addDays(today, -1); to = today;
  } else if (preset === "this_week") {
    from = addDays(today, -today.getDay()); to = addDays(today, 1);
  } else if (preset === "last_week") {
    const sun = addDays(today, -today.getDay());
    from = addDays(sun, -7); to = sun;
  } else if (preset === "this_month") {
    from = new Date(today.getFullYear(), today.getMonth(), 1); to = addDays(today, 1);
  } else if (preset === "last_month") {
    from = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    to   = new Date(today.getFullYear(), today.getMonth(), 1);
  } else if (preset === "this_quarter") {
    const qm = Math.floor(today.getMonth() / 3) * 3;
    from = new Date(today.getFullYear(), qm, 1); to = addDays(today, 1);
  } else if (preset === "last_quarter") {
    const qm = Math.floor(today.getMonth() / 3) * 3;
    from = new Date(today.getFullYear(), qm - 3, 1);
    to   = new Date(today.getFullYear(), qm, 1);
  } else if (preset === "last_2q") {
    const qm = Math.floor(today.getMonth() / 3) * 3;
    from = new Date(today.getFullYear(), qm - 6, 1);
    to   = new Date(today.getFullYear(), qm, 1);
  } else if (preset === "this_year") {
    from = new Date(today.getFullYear(), 0, 1); to = addDays(today, 1);
  } else if (preset === "last_year") {
    from = new Date(today.getFullYear() - 1, 0, 1);
    to   = new Date(today.getFullYear(), 0, 1);
  } else if (preset === "last_7")   { from = addDays(today, -7);   to = addDays(today, 1); }
  else if (preset === "last_14")    { from = addDays(today, -14);  to = addDays(today, 1); }
  else if (preset === "last_60")    { from = addDays(today, -60);  to = addDays(today, 1); }
  else if (preset === "last_90")    { from = addDays(today, -90);  to = addDays(today, 1); }
  else if (preset === "last_120")   { from = addDays(today, -120); to = addDays(today, 1); }
  else if (preset === "last_6m")    { from = addMonths(today, -6); to = addDays(today, 1); }
  else { // last_30 (default)
    from = addDays(today, -30); to = addDays(today, 1);
  }

  const days = Math.max(1, Math.ceil((to - from) / 86400000));
  return { date_from: from.toISOString(), date_to: to.toISOString(), days };
}

function getActiveDateRange() {
  const preset = (document.getElementById("rptDatePreset") || {}).value || "last_30";
  return resolveDatePreset(preset);
}

function onDatePresetChange() {
  const preset = document.getElementById("rptDatePreset").value;
  document.getElementById("rptCustomDates").style.display = preset === "custom" ? "flex" : "none";
  if (preset !== "custom") loadActiveTab();
}

function loadActiveTab() {
  if (activeTab === "savings")     loadSavings();
  if (activeTab === "risk")        loadRisk();
  if (activeTab === "departments") loadDepartments();
  if (activeTab === "activity")    loadAgentActivity();
  // efficiency tab is on-demand only — user clicks Generate Review
}

// ── TAB 1: SAVINGS ────────────────────────────────────────────────────────────

async function loadSavings() {
  const { days } = getActiveDateRange();
  const data = await apiGet(`/api/reports/savings?days=${days}`);

  document.getElementById("sv-total-saved").textContent  = fmtUsd(data.total_saved_usd);
  document.getElementById("sv-no-fage").textContent      = fmtUsd(data.cost_if_no_fage_usd);
  document.getElementById("sv-actual").textContent       = fmtUsd(data.total_cost_usd);
  document.getElementById("sv-pruning").textContent      = fmtUsd(data.pruning_saved_usd);
  document.getElementById("sv-tokens").textContent       = fmtNum(data.tokens_pruned) + " tokens removed";
  document.getElementById("sv-downgrade").textContent    = fmtUsd(data.downgrade_saved_usd);
  document.getElementById("sv-micro-pct").textContent    = data.micro_pct + "% routed to micro";
  document.getElementById("sv-calls").textContent        = fmtNum(data.total_calls);
  document.getElementById("sv-call-split").textContent   =
    fmtNum(data.micro_calls) + " micro / " + fmtNum(data.flagship_calls) + " flagship";

  const labels = data.timeline.map(d => d.date);

  // Daily spend line chart
  destroyChart("dailySpend");
  charts["dailySpend"] = new Chart(
    document.getElementById("chartDailySpend").getContext("2d"),
    {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Daily Spend ($)",
          data: data.timeline.map(d => d.cost),
          borderColor: COLORS.micro,
          backgroundColor: "rgba(88,166,255,0.08)",
          fill: true, tension: 0.3, pointRadius: 2,
        }],
      },
      options: { ...chartDefaults(), plugins: { legend: { display: false } } },
    }
  );

  // Model tier doughnut
  destroyChart("modelSplit");
  charts["modelSplit"] = new Chart(
    document.getElementById("chartModelSplit").getContext("2d"),
    {
      type: "doughnut",
      data: {
        labels: ["Micro", "Flagship"],
        datasets: [{ data: [data.micro_calls, data.flagship_calls],
          backgroundColor: [COLORS.micro, COLORS.flagship], borderWidth: 0 }],
      },
      options: { plugins: { legend: { labels: { color: COLORS.muted } } }, cutout: "65%" },
    }
  );

  // Daily tokens pruned bar
  destroyChart("tokensPruned");
  charts["tokensPruned"] = new Chart(
    document.getElementById("chartTokensPruned").getContext("2d"),
    {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Tokens Pruned",
          data: data.timeline.map(d => d.tokens_saved),
          backgroundColor: "rgba(63,185,80,0.5)",
          borderColor: COLORS.green, borderWidth: 1,
        }],
      },
      options: { ...chartDefaults(), plugins: { legend: { display: false } } },
    }
  );

  // Savings breakdown doughnut
  destroyChart("savingsBreakdown");
  charts["savingsBreakdown"] = new Chart(
    document.getElementById("chartSavingsBreakdown").getContext("2d"),
    {
      type: "doughnut",
      data: {
        labels: ["Pruning Savings", "Downgrade Savings", "Actual Cost"],
        datasets: [{ data: [data.pruning_saved_usd, data.downgrade_saved_usd, data.total_cost_usd],
          backgroundColor: [COLORS.green, COLORS.micro, COLORS.muted], borderWidth: 0 }],
      },
      options: { plugins: { legend: { labels: { color: COLORS.muted } } }, cutout: "60%" },
    }
  );
}

// ── TAB 2: RISK ───────────────────────────────────────────────────────────────

async function loadRisk() {
  const { days } = getActiveDateRange();
  const data = await apiGet(`/api/reports/risk?days=${days}`);

  document.getElementById("rk-total").textContent    = fmtNum(data.total_events);
  document.getElementById("rk-critical").textContent = fmtNum(data.critical);
  document.getElementById("rk-high").textContent     = fmtNum(data.high);
  document.getElementById("rk-blocked").textContent  = fmtNum(data.blocked);
  document.getElementById("rk-locks").textContent    = fmtNum(data.locks);
  document.getElementById("rk-terms").textContent    = fmtNum(data.term_library.total);
  document.getElementById("rk-terms-sub").textContent =
    data.term_library.block + " block / " + data.term_library.escalate + " escalate";

  const labels = data.timeline.map(d => d.date);

  // Risk timeline stacked bar
  destroyChart("riskTimeline");
  charts["riskTimeline"] = new Chart(
    document.getElementById("chartRiskTimeline").getContext("2d"),
    {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Critical", data: data.timeline.map(d => d.critical),
            backgroundColor: "rgba(248,81,73,0.7)", stack: "risk" },
          { label: "High",     data: data.timeline.map(d => d.high),
            backgroundColor: "rgba(210,153,34,0.7)", stack: "risk" },
          { label: "Medium",   data: data.timeline.map(d => d.medium),
            backgroundColor: "rgba(88,166,255,0.5)", stack: "risk" },
          { label: "Low",      data: data.timeline.map(d => d.low),
            backgroundColor: "rgba(139,148,158,0.3)", stack: "risk" },
        ],
      },
      options: chartDefaults(),
    }
  );

  // Risk level doughnut
  destroyChart("riskBreakdown");
  charts["riskBreakdown"] = new Chart(
    document.getElementById("chartRiskBreakdown").getContext("2d"),
    {
      type: "doughnut",
      data: {
        labels: ["Critical", "High", "Medium", "Low"],
        datasets: [{ data: [data.critical, data.high, data.medium, data.low],
          backgroundColor: [COLORS.red, COLORS.yellow, COLORS.micro, COLORS.muted],
          borderWidth: 0 }],
      },
      options: { plugins: { legend: { labels: { color: COLORS.muted } } }, cutout: "65%" },
    }
  );

  // Event table
  const tbody = document.getElementById("riskEventTable");
  if (!data.recent_events.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="placeholder">No events in this period.</td></tr>`;
  } else {
    tbody.innerHTML = data.recent_events.map(e => `
      <tr>
        <td style="font-size:11px; font-family:var(--font-mono)">${fmtTs(e.timestamp)}</td>
        <td><span class="rpt-badge badge-event">${e.event_type}</span></td>
        <td>${e.department}</td>
        <td>${riskBadge(e.risk_level)}</td>
        <td style="font-size:11px; color:var(--text-muted)">${e.decision_outcome}</td>
      </tr>
    `).join("");
  }

  // Load governance summary panels from dashboard API
  try {
    const d = await apiGet("/api/dashboard");
    renderComplianceGrid(d);
    renderExecSummary(d);
  } catch (e) {
    console.warn("Governance panels failed:", e.message);
  }
}

function renderComplianceGrid(d) {
  const grid = document.getElementById("complianceGrid");
  if (!grid) return;
  const items = [
    { icon: "🚫", value: d.blocked_count,     label: "Requests Blocked",          sub: "Sensitive terms triggered block policy — request never reached AI model",          cls: "critical" },
    { icon: "⚠️",  value: d.escalated_count,   label: "Escalated to Flagship",     sub: "Legal, NDA, contract language forced flagship model review",                       cls: "high"     },
    { icon: "🔍", value: d.flagged_count,      label: "Flagged in Audit Log",      sub: "High-risk keywords logged for compliance review",                                   cls: "medium"   },
    { icon: "🔒", value: d.pii_count,          label: "PII Detected",              sub: "Credit cards, SSNs, emails, phone numbers caught before AI processing",             cls: "high"     },
    { icon: "💰", value: d.throttle_prevented, label: "Budget Overruns Prevented", sub: "Auto-throttle engaged before department cap was breached",                          cls: "low"      },
    { icon: "⚡", value: d.collision_count,    label: "Agent Collisions Resolved", sub: "Concurrent write conflicts detected and locked — zero data corruption",             cls: "low"      },
  ];
  const hasData = items.some(i => i.value > 0);
  if (!hasData) {
    grid.innerHTML = '<p class="placeholder">No compliance events yet — load enterprise demo or route a call.</p>';
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

function renderExecSummary(d) {
  const grid = document.getElementById("execGrid");
  if (!grid) return;
  const tokens = d.tokens_saved_total || 0;
  const tokensLabel = tokens >= 1_000_000
    ? (tokens / 1_000_000).toFixed(2) + "M"
    : tokens >= 1_000 ? (tokens / 1_000).toFixed(1) + "K"
    : tokens.toLocaleString();
  const eventsLabel = (d.compliance_events_total || 0) >= 1000
    ? ((d.compliance_events_total) / 1000).toFixed(1) + "K"
    : (d.compliance_events_total || 0).toString();
  const savings = (d.projected_annual_savings || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const items = [
    { icon: "$", cls: "green",  color: "green",  title: "PROJECTED ANNUAL SAVINGS",  value: "$" + savings,                        sub: "Based on 30-day actual performance extrapolated to 12 months" },
    { icon: "%", cls: "accent", color: "accent", title: "AI COST REDUCTION",         value: (d.cost_reduction_pct || 0) + "%",    sub: "Achieved through smart routing, token pruning, and budget enforcement" },
    { icon: "◈", cls: "purple", color: "purple", title: "COMPLIANCE EVENTS LOGGED",  value: eventsLabel,                          sub: "Immutable audit trail — every AI decision logged, timestamped, exportable" },
    { icon: "⚡", cls: "yellow", color: "yellow", title: "TIME TO DEPLOY",            value: "15 min",                             sub: "One Apex class · one Flow action · 4 custom fields · no IT project required" },
    { icon: "↑", cls: "green",  color: "green",  title: "TOKENS SAVED",              value: tokensLabel,                          sub: "Context pruned before every AI call — savings start from day one" },
    { icon: "0", cls: "red",    color: "",        title: "DATA CORRUPTION EVENTS",    value: "Zero",                               sub: (d.collision_count || 0) + " agent collisions detected and locked — no silent overwrites" },
  ];
  const hasData = (d.total_calls || 0) > 0 || (d.flagged_count || 0) > 0;
  if (!hasData) {
    grid.innerHTML = '<p class="placeholder">No data yet — load enterprise demo or route a call.</p>';
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

// ── TAB 3: DEPARTMENTS ────────────────────────────────────────────────────────

async function loadDepartments() {
  const { days } = getActiveDateRange();
  const data = await apiGet(`/api/reports/departments?days=${days}`);

  // Scorecard table
  const tbody = document.getElementById("deptScorecardTable");
  if (!data.scorecards.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="placeholder">No data in this period.</td></tr>`;
  } else {
    tbody.innerHTML = data.scorecards.map(d => {
      const statusBadge = d.throttled
        ? `<span class="rpt-badge badge-critical">THROTTLED</span>`
        : d.override_granted
          ? `<span class="rpt-badge badge-event">OVERRIDE</span>`
          : `<span class="rpt-badge badge-low">OK</span>`;
      const barW = Math.min(d.budget_used_pct, 100);
      const barC = d.budget_used_pct >= 90 ? COLORS.red
                 : d.budget_used_pct >= 70 ? COLORS.yellow
                 : COLORS.green;
      return `
        <tr>
          <td><strong>${d.department}</strong></td>
          <td>${fmtNum(d.total_calls)}</td>
          <td>${d.micro_pct}%</td>
          <td>${fmtUsd(d.total_cost_usd)}</td>
          <td class="green">${fmtUsd(d.pruning_saved_usd)}</td>
          <td>
            <div style="display:flex;align-items:center;gap:8px">
              <div style="flex:1;height:6px;background:var(--border);border-radius:3px">
                <div style="width:${barW}%;height:100%;background:${barC};border-radius:3px"></div>
              </div>
              <span style="font-size:11px;color:var(--text-muted);white-space:nowrap">${d.budget_used_pct}%</span>
            </div>
          </td>
          <td>${fmtUsd(d.monthly_cap_usd)}</td>
          <td>${statusBadge}</td>
        </tr>
      `;
    }).join("");
  }

  const labels = data.timeline.map(d => d.date);
  const depts  = data.departments;

  // Stacked dept spend chart
  destroyChart("deptSpend");
  charts["deptSpend"] = new Chart(
    document.getElementById("chartDeptSpend").getContext("2d"),
    {
      type: "bar",
      data: {
        labels,
        datasets: depts.map((dept, i) => ({
          label: dept,
          data:  data.timeline.map(d => d[dept] || 0),
          backgroundColor: COLORS.dept[i % COLORS.dept.length] + "99",
          borderColor:     COLORS.dept[i % COLORS.dept.length],
          borderWidth: 1,
          stack: "dept",
        })),
      },
      options: chartDefaults(),
    }
  );

  // Dept cost doughnut
  destroyChart("deptCost");
  charts["deptCost"] = new Chart(
    document.getElementById("chartDeptCost").getContext("2d"),
    {
      type: "doughnut",
      data: {
        labels: data.scorecards.map(d => d.department),
        datasets: [{
          data: data.scorecards.map(d => d.total_cost_usd),
          backgroundColor: depts.map((_, i) => COLORS.dept[i % COLORS.dept.length]),
          borderWidth: 0,
        }],
      },
      options: { plugins: { legend: { labels: { color: COLORS.muted } } }, cutout: "60%" },
    }
  );
}

// ── TAB 4: BOT EFFICIENCY REVIEW ──────────────────────────────────────────────

async function generateEfficiencyReview() {
  const btn  = document.getElementById("effGenerateBtn");
  const grid = document.getElementById("effGrid");
  const days = document.getElementById("effDaysSelect").value;

  btn.disabled    = true;
  btn.textContent = "⚡ Analyzing...";
  grid.innerHTML  = `<div class="eff-empty" style="grid-column:1/-1">Analyzing ${days}-day transaction history across all agents...<br><span style="font-size:11px;color:var(--text-muted);margin-top:8px;display:block">This may take a few seconds</span></div>`;
  document.getElementById("effFleetBar").style.display = "none";

  try {
    const data = await apiPost(`/api/reports/bot-efficiency?days=${days}`, {});

    // Fleet summary bar
    document.getElementById("effFleetGrade").textContent   = data.fleet_grade || "—";
    document.getElementById("effAgentsCount").textContent  = data.total_agents_analyzed;
    document.getElementById("effTotalSavings").textContent = "$" + (data.total_projected_savings || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    document.getElementById("effGeneratedBy").textContent  = data.generated_by === "ai" ? "GPT-4o" : "FAGE Analytics";
    document.getElementById("effFleetBar").style.display   = "grid";

    if (!data.reviews || !data.reviews.length) {
      grid.innerHTML = `<div class="eff-empty" style="grid-column:1/-1">${data.message || "No agent data found for this period."}</div>`;
      return;
    }

    grid.innerHTML = data.reviews.map(r => renderEfficiencyCard(r)).join("");

  } catch (e) {
    grid.innerHTML = `<div class="eff-empty" style="grid-column:1/-1; color:var(--accent-red)">Analysis failed: ${e.message}</div>`;
  } finally {
    btn.disabled    = false;
    btn.textContent = "⚡ Generate Review";
  }
}

function renderEfficiencyCard(r) {
  const s    = r.stats;
  const grad = r.grade || "B";

  const trendColor = s.cost_trend === "increasing" ? "red"
    : s.cost_trend === "decreasing" ? "green" : "";

  const findings = (r.findings || []).map(f =>
    `<li>${f}</li>`
  ).join("");

  const recs = (r.recommendations || []).map(rec =>
    `<li>${rec}</li>`
  ).join("");

  const savings = (r.projected_savings || 0) > 0
    ? `<div class="eff-savings-row">
        <span class="eff-savings-label">Projected 30-day savings if actioned</span>
        <span class="eff-savings-value">$${(r.projected_savings).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
       </div>`
    : "";

  const aiBadge = r.generated_by === "ai"
    ? `<div class="eff-ai-badge">◈ GPT-4o Analysis</div>`
    : `<div class="eff-ai-badge" style="color:var(--text-muted)">◈ FAGE Analytics Engine</div>`;

  return `
    <div class="eff-card grade-${grad}">
      <div class="eff-card-header">
        <div class="eff-grade-badge ${grad}">${grad}</div>
        <div>
          <div class="eff-card-name">${r.agent_name}</div>
          <div class="eff-card-dept">${r.department} · ${s.target_table || ""}</div>
        </div>
      </div>

      <div class="eff-stats-row">
        <div class="eff-stat-item">
          <div class="eff-stat-label">Calls / Day</div>
          <div class="eff-stat-value">${s.calls_per_day}</div>
        </div>
        <div class="eff-stat-item">
          <div class="eff-stat-label">Total Cost</div>
          <div class="eff-stat-value">$${(s.total_cost_usd || 0).toFixed(2)}</div>
        </div>
        <div class="eff-stat-item">
          <div class="eff-stat-label">Flagship %</div>
          <div class="eff-stat-value ${s.flagship_pct > 60 ? 'yellow' : s.flagship_pct < 10 ? 'green' : ''}">${s.flagship_pct}%</div>
        </div>
        <div class="eff-stat-item">
          <div class="eff-stat-label">Prune Rate</div>
          <div class="eff-stat-value ${s.prune_rate > 70 ? 'green' : s.prune_rate < 40 ? 'yellow' : ''}">${s.prune_rate}%</div>
        </div>
      </div>

      <p class="eff-summary">${r.summary}</p>

      ${findings ? `<div class="eff-section-label">Findings</div><ul class="eff-findings">${findings}</ul>` : ""}

      ${recs ? `<div class="eff-section-label">Recommendations</div><ul class="eff-recs">${recs}</ul>` : ""}

      ${savings}
      ${aiBadge}
    </div>
  `;
}

// ── Boot ──────────────────────────────────────────────────────────────────────
loadSavings();

// ── Agent Activity Tab ────────────────────────────────────────────────────────

let _activityLoaded = false;
let _actOpenRows    = new Set(); // track which agent rows are expanded

function initActivityTab() {
  if (_activityLoaded) return;
  _activityLoaded = true;
  populateActivityDropdowns();
  loadAgentActivity();
}

async function populateActivityDropdowns() {
  try {
    const agents = await apiGet("/api/agents");

    // Populate agent dropdown
    const agentSel = document.getElementById("actAgent");
    agents.forEach(a => {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = a.name + " (" + (a.source_platform || "Custom") + ")";
      agentSel.appendChild(opt);
    });

    // Populate department dropdown from agent list (no extra API call needed)
    const deptSel = document.getElementById("actDept");
    const depts = [...new Set(agents.map(a => a.department).filter(Boolean))].sort();
    depts.forEach(d => {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d;
      deptSel.appendChild(opt);
    });
  } catch (e) { /* silent */ }
}

async function loadAgentActivity() {
  const platform = document.getElementById("actPlatform").value;
  const agentId  = document.getElementById("actAgent").value;
  const dept     = document.getElementById("actDept").value;
  const model    = document.getElementById("actModel").value;
  const { date_from, date_to } = getActiveDateRange();

  const params = new URLSearchParams();
  if (platform) params.set("platform",   platform);
  if (agentId)  params.set("agent_id",   agentId);
  if (dept)     params.set("department", dept);
  if (model)    params.set("model_tier", model);
  params.set("date_from", date_from);
  params.set("date_to",   date_to);

  const tbody = document.getElementById("actTableBody");
  tbody.innerHTML = '<tr><td colspan="11" class="placeholder">Loading...</td></tr>';

  try {
    const data = await apiGet("/api/reports/agent-activity?" + params.toString());
    renderActivitySummary(data.summary);
    renderActivityTable(data.agents);
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="11" class="placeholder" style="color:var(--accent-red)">Error: ' + e.message + '</td></tr>';
  }
}

function renderActivitySummary(s) {
  document.getElementById("actTotalCalls").textContent   = s.total_calls.toLocaleString();
  document.getElementById("actTotalCost").textContent    = "$" + s.total_cost_usd.toFixed(2);
  document.getElementById("actAgentsCount").textContent  = s.agents_count;
  document.getElementById("actPlatformsCount").textContent = s.platforms.length;
}

function renderActivityTable(agents) {
  const tbody = document.getElementById("actTableBody");

  if (!agents.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="placeholder">No agent activity found for the selected filters.</td></tr>';
    return;
  }

  tbody.innerHTML = agents.map(a => {
    const platformColor = a.platform === "Salesforce" ? "var(--accent)"
      : a.platform === "ServiceNow"  ? "var(--accent-green)"
      : a.platform === "HubSpot"     ? "var(--accent-yellow)"
      : "var(--text-muted)";

    const statusBadge = a.status === "locked" ? "badge-locked"
      : a.status === "active"  ? "badge-active"
      : "badge-idle";

    const lastActive = a.last_active
      ? new Date(a.last_active).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : "—";

    const isOpen = _actOpenRows.has(a.id);

    return `
      <tr class="act-agent-row ${isOpen ? "act-row-open" : ""}" onclick="toggleCallLog(${a.id})">
        <td class="act-expand-cell">${isOpen ? "▾" : "▸"}</td>
        <td style="font-weight:600">${a.name}</td>
        <td style="font-size:11px; font-weight:700; color:${platformColor}">${a.platform}</td>
        <td>${a.department}</td>
        <td><span class="badge ${statusBadge}">${a.status.toUpperCase()}</span></td>
        <td style="font-weight:600">${a.calls.toLocaleString()}</td>
        <td style="color:var(--accent-red)">$${a.cost_usd.toFixed(4)}</td>
        <td style="color:var(--text-muted); font-size:11px">$${a.avg_cost_usd.toFixed(5)}</td>
        <td>${a.flagship_pct}%</td>
        <td style="color:var(--accent-green)">${a.pruned_pct}%</td>
        <td style="font-size:11px; color:var(--text-muted)">${lastActive}</td>
      </tr>
      <tr class="act-log-row" id="act-log-${a.id}" style="display:${isOpen ? "table-row" : "none"}">
        <td colspan="11" class="act-log-cell">
          ${renderCallLog(a)}
        </td>
      </tr>
    `;
  }).join("");
}

function toggleCallLog(agentId) {
  const logRow = document.getElementById("act-log-" + agentId);
  const isOpen = _actOpenRows.has(agentId);

  if (isOpen) {
    _actOpenRows.delete(agentId);
    logRow.style.display = "none";
  } else {
    _actOpenRows.add(agentId);
    logRow.style.display = "table-row";
  }

  // Flip the expand arrow without full re-render
  const agentRows = document.querySelectorAll(".act-agent-row");
  agentRows.forEach(row => {
    if (row.getAttribute("onclick") === "toggleCallLog(" + agentId + ")") {
      row.querySelector(".act-expand-cell").textContent = _actOpenRows.has(agentId) ? "▾" : "▸";
      row.classList.toggle("act-row-open", _actOpenRows.has(agentId));
    }
  });
}

function renderCallLog(agent) {
  if (!agent.transactions || !agent.transactions.length) {
    return '<div class="act-log-empty">No transactions found.</div>';
  }

  const rows = agent.transactions.map(t => {
    const ts = new Date(t.timestamp).toLocaleString("en-US", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
    const tierColor  = t.model_tier === "flagship" ? "var(--accent-red)" : "var(--accent-green)";
    const tierLabel  = t.model_tier === "flagship" ? "Flagship" : "Turbo";
    const routeColor = t.routing_reason === "COMPLEX" ? "var(--accent-red)"
      : t.routing_reason === "ROUTINE"  ? "var(--accent-green)"
      : "var(--accent-yellow)";
    const pruneTag = t.was_pruned
      ? '<span class="act-prune-tag">✂ pruned · ' + t.tokens_saved.toLocaleString() + ' tokens saved</span>'
      : "";

    return `
      <tr class="act-tx-row">
        <td style="font-size:11px; color:var(--text-muted); white-space:nowrap">${ts}</td>
        <td style="font-size:11px; font-weight:700; color:${tierColor}">${tierLabel}</td>
        <td style="font-size:11px; font-weight:700; color:${routeColor}">${t.routing_reason}</td>
        <td style="font-size:11px">${t.input_tokens.toLocaleString()} in · ${t.output_tokens.toLocaleString()} out</td>
        <td style="font-size:11px; color:var(--accent-red)">$${t.cost_usd.toFixed(5)}</td>
        <td style="font-size:11px">${pruneTag}</td>
      </tr>
    `;
  }).join("");

  return `
    <div class="act-log-wrap">
      <div class="act-log-header">
        Call Log — ${agent.name} &nbsp;·&nbsp; ${agent.transactions.length} most recent calls shown
      </div>
      <table class="act-log-table">
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Model</th>
            <th>Routing</th>
            <th>Tokens</th>
            <th>Cost</th>
            <th>Pruning</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function resetActivityFilters() {
  document.getElementById("actPlatform").value = "";
  document.getElementById("actAgent").value    = "";
  document.getElementById("actDept").value     = "";
  document.getElementById("actModel").value    = "";
  loadAgentActivity();
}
