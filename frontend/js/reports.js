/**
 * reports.js — CostPilot Reporting Engine UI
 *
 * Three tabs: Savings, Risk & Compliance, Departments
 * Chart.js for all visualizations.
 * Date range selector: 7D / 30D / 90D / 1Y
 */

let activeTab  = "savings";
const charts   = {};

// Cached API data for export
let _rptRiskEvents  = [];
let _rptDeptData    = [];
let _rptSavingsData = null;

const COLORS = {
  scout:     "#3fb950",   // Tier 1 — green (cheapest)
  analyst:   "#58a6ff",   // Tier 2 — blue
  advisor:   "#d29922",   // Tier 3 — amber
  strategist:"#f85149",   // Tier 4 — red (most expensive)
  // Legacy aliases
  micro:    "#3fb950",
  flagship: "#d29922",
  green:    "#3fb950",
  yellow:   "#d29922",
  red:      "#f85149",
  muted:    "#8b949e",
  border:   "#30363d",
  panel:    "#161b22",
  dept: ["#58a6ff","#3fb950","#d29922","#f85149","#a371f7","#79c0ff","#56d364","#ffa657"],
};

// ── Utilities ─────────────────────────────────────────────────────────────────

/**
 * Animate a KPI element from its current displayed value to a new target string.
 * Handles dollar amounts, integers, and strings with numeric content.
 * Falls back to instant swap for non-numeric strings (badges, labels, etc.).
 */
function countUp(el, targetStr, duration = 700) {
  if (!el) return;
  if (el._countUpRaf) cancelAnimationFrame(el._countUpRaf);

  const numMatch = String(targetStr).replace(/,/g, "").match(/-?[\d.]+/);
  if (!numMatch) { el.textContent = targetStr; return; }

  const endNum   = parseFloat(numMatch[0]);
  const prefix   = String(targetStr).slice(0, numMatch.index);
  const suffix   = String(targetStr).slice(numMatch.index + numMatch[0].length);
  const decPlaces = numMatch[0].includes(".") ? numMatch[0].split(".")[1].length : 0;

  const fromMatch = String(el.textContent).replace(/,/g, "").match(/-?[\d.]+/);
  const startNum  = fromMatch ? parseFloat(fromMatch[0]) : 0;

  const startTime = performance.now();

  function tick(now) {
    const elapsed  = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased    = 1 - Math.pow(1 - progress, 3); // ease-out cubic
    const current  = startNum + (endNum - startNum) * eased;

    const formatted = decPlaces > 0
      ? current.toFixed(decPlaces).replace(/\B(?=(\d{3})+(?!\d))/g, ",")
      : Math.round(current).toLocaleString();

    el.textContent = prefix + formatted + suffix;

    if (progress < 1) {
      el._countUpRaf = requestAnimationFrame(tick);
    } else {
      el.textContent = targetStr; // snap to exact value
    }
  }

  el._countUpRaf = requestAnimationFrame(tick);
}

/** Set a KPI by element ID with count-up animation */
function setKpi(id, text) {
  countUp(document.getElementById(id), String(text));
}

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
    // Re-init drag for newly shown tab (Sortable needs visible elements)
    setTimeout(() => initDraggableReports(activeTab), 50);
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
  _rptSavingsData = data;

  setKpi("sv-total-saved",  fmtUsd(data.total_saved_usd));
  setKpi("sv-no-fage",     fmtUsd(data.cost_if_no_fage_usd));
  setKpi("sv-actual",      fmtUsd(data.total_cost_usd));
  setKpi("sv-pruning",     fmtUsd(data.pruning_saved_usd));
  setKpi("sv-tokens",      fmtNum(data.tokens_pruned) + " tokens removed");
  setKpi("sv-downgrade",   fmtUsd(data.downgrade_saved_usd));
  setKpi("sv-micro-pct",   data.micro_pct + "% routed to micro");
  setKpi("sv-calls",       fmtNum(data.total_calls));
  setKpi("sv-call-split",  fmtNum(data.micro_calls) + " micro / " + fmtNum(data.flagship_calls) + " flagship");

  const labels = (data.timeline || []).map(d => d.date);

  try {
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
            data: (data.timeline || []).map(d => d.cost),
            borderColor: COLORS.micro,
            backgroundColor: "rgba(88,166,255,0.08)",
            fill: true, tension: 0.3, pointRadius: 2,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          ...chartDefaults(),
          plugins: { legend: { display: false } },
        },
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
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: COLORS.muted } } },
          cutout: "65%",
        },
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
            data: (data.timeline || []).map(d => d.tokens_saved),
            backgroundColor: "rgba(63,185,80,0.5)",
            borderColor: COLORS.green, borderWidth: 1,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          ...chartDefaults(),
          plugins: { legend: { display: false } },
        },
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
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: COLORS.muted } } },
          cutout: "60%",
        },
      }
    );
  } catch (err) {
    console.error("[CostPilot] Chart render error in loadSavings:", err);
  }
}

// ── TAB 2: RISK ───────────────────────────────────────────────────────────────

async function loadRisk() {
  const { days } = getActiveDateRange();
  const data = await apiGet(`/api/reports/risk?days=${days}`);
  _rptRiskEvents = data.recent_events || [];

  setKpi("rk-total",    fmtNum(data.total_events));
  setKpi("rk-critical", fmtNum(data.critical));
  setKpi("rk-high",     fmtNum(data.high));
  setKpi("rk-blocked",  fmtNum(data.blocked));
  setKpi("rk-locks",    fmtNum(data.locks));
  setKpi("rk-terms",    fmtNum(data.term_library.total));
  setKpi("rk-terms-sub", data.term_library.block + " block / " + data.term_library.escalate + " escalate");

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
  _rptDeptData = data.scorecards || [];

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
    document.getElementById("effGeneratedBy").textContent  = data.generated_by === "ai" ? "GPT-4o" : "CostPilot Analytics";
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
    : `<div class="eff-ai-badge" style="color:var(--text-muted)">◈ CostPilot Analytics Engine</div>`;

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
    const tierColorMap = {
      "Scout":      "var(--tier-scout)",
      "Analyst":    "var(--tier-analyst)",
      "Advisor":    "var(--tier-advisor)",
      "Strategist": "var(--tier-strategist)",
      "micro":      "var(--tier-scout)",
      "flagship":   "var(--tier-advisor)",
    };
    const tierColor = tierColorMap[t.model_tier] || "var(--text-muted)";
    const tierLabel = t.model_tier || "—";
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

// ── Export functions ──────────────────────────────────────────────────────────

function exportRiskCsv() {
  if (!_rptRiskEvents.length) { alert("No risk events loaded — open the Risk tab first."); return; }
  const headers = ["Timestamp", "Event Type", "Department", "Risk Level", "Decision Outcome"];
  const rows = _rptRiskEvents.map(e => [
    fmtTs(e.timestamp),
    e.event_type || "",
    e.department || "",
    e.risk_level || "",
    e.decision_outcome || "",
  ]);
  const date = new Date().toISOString().slice(0, 10);
  downloadCsv(`fage_risk_events_${date}.csv`, headers, rows);
}

function exportRiskPdf() {
  printSection("tab-risk", "CostPilot — Risk & Compliance Report");
}

function exportDeptCsv() {
  if (!_rptDeptData.length) { alert("No department data loaded — open the Departments tab first."); return; }
  const headers = ["Department", "Total Calls", "Micro %", "Actual Cost (USD)", "Pruning Saved (USD)", "Budget Used %", "Monthly Cap (USD)", "Status"];
  const rows = _rptDeptData.map(d => [
    d.department,
    d.total_calls,
    d.micro_pct + "%",
    d.total_cost_usd != null ? d.total_cost_usd.toFixed(4) : "",
    d.pruning_saved_usd != null ? d.pruning_saved_usd.toFixed(4) : "",
    d.budget_used_pct != null ? d.budget_used_pct.toFixed(1) + "%" : "",
    d.monthly_cap_usd != null ? d.monthly_cap_usd.toFixed(2) : "",
    d.throttled ? "THROTTLED" : d.override_granted ? "OVERRIDE" : "OK",
  ]);
  const date = new Date().toISOString().slice(0, 10);
  downloadCsv(`fage_departments_${date}.csv`, headers, rows);
}

function exportDeptPdf() {
  printSection("tab-departments", "CostPilot — Department Report");
}

function exportSavingsCsv() {
  if (!_rptSavingsData) { alert("No savings data loaded — open the Savings tab first."); return; }
  const d = _rptSavingsData;
  const headers = ["Metric", "Value"];
  const rows = [
    ["Total Saved (USD)",         d.total_saved_usd?.toFixed(4)    ?? ""],
    ["Cost Without CostPilot (USD)",   d.cost_if_no_fage_usd?.toFixed(4)?? ""],
    ["Actual Cost (USD)",         d.total_cost_usd?.toFixed(4)     ?? ""],
    ["Pruning Savings (USD)",     d.pruning_saved_usd?.toFixed(4)  ?? ""],
    ["Tokens Pruned",             d.tokens_pruned ?? ""],
    ["Model Downgrade Savings (USD)", d.downgrade_saved_usd?.toFixed(4) ?? ""],
    ["Total Calls",               d.total_calls ?? ""],
    ["Micro Calls %",             d.micro_pct != null ? d.micro_pct + "%" : ""],
  ];
  const date = new Date().toISOString().slice(0, 10);
  downloadCsv(`fage_savings_${date}.csv`, headers, rows);
}

function exportSavingsPdf() {
  printSection("tab-savings", "CostPilot — Savings Report");
}

// ── Draggable report cards ────────────────────────────────────────────────────

const RPT_CONTAINER_IDS = {
  savings:     "savings-cards",
  risk:        "risk-cards",
  departments: "dept-cards",
};

const _rptSortables = {};

function initDraggableReports(tab) {
  if (typeof Sortable === "undefined") return;
  const containerId = RPT_CONTAINER_IDS[tab];
  if (!containerId) return;
  const container = document.getElementById(containerId);
  if (!container) return;

  // Destroy existing instance before recreating (tab may have been re-shown)
  if (_rptSortables[tab]) {
    try { _rptSortables[tab].destroy(); } catch(e) {}
  }

  _rptSortables[tab] = Sortable.create(container, {
    animation:   150,
    handle:      ".rpt-drag-bar",
    ghostClass:  "sortable-ghost",
    chosenClass: "sortable-chosen",
    onEnd() {
      const order = [...container.querySelectorAll(".rpt-drag-card")]
        .map(el => el.dataset.cardId);
      try { localStorage.setItem(`fage_rpt_order_${tab}`, JSON.stringify(order)); } catch(e) {}
    },
  });

  // Restore saved order
  try {
    const saved = JSON.parse(localStorage.getItem(`fage_rpt_order_${tab}`) || "null");
    if (Array.isArray(saved)) {
      saved.forEach(id => {
        const el = container.querySelector(`.rpt-drag-card[data-card-id="${id}"]`);
        if (el) container.appendChild(el);
      });
    }
  } catch(e) {}
}

function randomizeReportCards() {
  const containerId = RPT_CONTAINER_IDS[activeTab];
  if (!containerId) return;
  const container = document.getElementById(containerId);
  if (!container) return;
  const cards = [...container.querySelectorAll(".rpt-drag-card")];
  // Fisher-Yates shuffle
  for (let i = cards.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [cards[i], cards[j]] = [cards[j], cards[i]];
  }
  cards.forEach(el => container.appendChild(el));
  const order = cards.map(el => el.dataset.cardId);
  try { localStorage.setItem(`fage_rpt_order_${activeTab}`, JSON.stringify(order)); } catch(e) {}
}

// Init drag for the default (savings) tab on load
document.addEventListener("DOMContentLoaded", () => {
  setTimeout(() => initDraggableReports("savings"), 100);
});

// Auto-refresh active tab every 30 seconds so KPIs tick to new values live
setInterval(() => loadActiveTab(), 30000);

// ── Today Counter ─────────────────────────────────────────────────────────────

async function refreshTodayCounter() {
  try {
    const d = await apiGet("/api/dashboard");
    countUp(document.getElementById("td-calls"),  String(d.calls_today ?? 0));
    countUp(document.getElementById("td-cost"),   "$" + (d.spend_today_usd ?? 0).toFixed(4));
    countUp(document.getElementById("td-tokens"), fmtNum(d.tokens_saved_today ?? 0));
    const el = document.getElementById("td-updated");
    if (el) el.textContent = "updated " + new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" });
  } catch (e) { /* silent — counter just stays at last value */ }
}

// Load immediately, then refresh every 10 seconds
refreshTodayCounter();
setInterval(refreshTodayCounter, 10000);
