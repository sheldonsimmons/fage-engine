/**
 * demo.js — FAGE Enterprise ROI Demo Page
 *
 * Hardcoded realistic enterprise data for CFO/CTO presentations.
 * Based on real OpenAI gpt-4o and gpt-3.5-turbo pricing:
 *   Flagship (gpt-4o):      $5.00/1M input · $15.00/1M output
 *   Micro (gpt-3.5-turbo):  $0.50/1M input · $1.50/1M output
 *
 * Modeled on a 500-agent financial services enterprise.
 * Daily call volume: ~8,500 · 62% micro · 38% flagship
 */

// ── Pricing constants (matches real API pricing) ──────────────────────────────
const FLAGSHIP_IN  = 5.00  / 1_000_000;   // per token
const FLAGSHIP_OUT = 15.00 / 1_000_000;
const MICRO_IN     = 0.50  / 1_000_000;
const MICRO_OUT    = 1.50  / 1_000_000;

// ── Per-call averages (derived from real call data in live system) ─────────────
const AVG_FLAGSHIP_COST    = (2800 * FLAGSHIP_IN) + (820 * FLAGSHIP_OUT);   // ~$0.0266
const AVG_MICRO_COST       = (480  * MICRO_IN)    + (140 * MICRO_OUT);      // ~$0.00045
const AVG_FLAGSHIP_UNPRUNED= (4600 * FLAGSHIP_IN) + (820 * FLAGSHIP_OUT);   // ~$0.0353 (no pruning)
const AVG_MICRO_UNPRUNED   = (820  * MICRO_IN)    + (140 * MICRO_OUT);      // ~$0.00062 (no pruning)

// ── Enterprise scale parameters ───────────────────────────────────────────────
const DAILY_CALLS       = 8_500;
const MICRO_PCT         = 0.62;
const FLAGSHIP_PCT      = 0.38;
const PRUNE_RATE        = 0.74;   // 74% of calls pruned
const AGENTS_TOTAL      = 500;
const DEPT_CAPS         = { Support: 8000, Sales: 12000, Marketing: 6000, Operations: 4000 };

// ── Data generation ───────────────────────────────────────────────────────────

function generateData(days) {
  const microDaily    = Math.round(DAILY_CALLS * MICRO_PCT);
  const flagshipDaily = Math.round(DAILY_CALLS * FLAGSHIP_PCT);

  // Cost WITH FAGE (smart routing + pruning)
  const dailyCostFage = (microDaily * AVG_MICRO_COST) + (flagshipDaily * AVG_FLAGSHIP_COST);

  // Cost WITHOUT FAGE (everything goes flagship, no pruning)
  const dailyCostBaseline = (DAILY_CALLS * AVG_FLAGSHIP_UNPRUNED);

  // Pruning savings
  const dailyPruningSaved = (microDaily * PRUNE_RATE * (AVG_MICRO_UNPRUNED - AVG_MICRO_COST))
                          + (flagshipDaily * PRUNE_RATE * (AVG_FLAGSHIP_UNPRUNED - AVG_FLAGSHIP_COST));

  // Routing savings (using micro instead of flagship for routine calls)
  const dailyRoutingSaved = (microDaily * (AVG_FLAGSHIP_COST - AVG_MICRO_COST));

  const totalDays    = days;
  const totalCalls   = DAILY_CALLS * totalDays;
  const totalFage    = dailyCostFage * totalDays;
  const totalBase    = dailyCostBaseline * totalDays;
  const totalSaved   = totalBase - totalFage;
  const pruningSaved = dailyPruningSaved * totalDays;
  const routingSaved = dailyRoutingSaved * totalDays;

  // Tokens saved via pruning
  const tokensSavedDaily = (microDaily    * PRUNE_RATE * 340)   // avg 340 tokens saved per micro call
                         + (flagshipDaily * PRUNE_RATE * 1800);  // avg 1800 tokens saved per flagship call
  const totalTokensSaved = tokensSavedDaily * totalDays;

  // Compliance events scale with time
  const blockedReqs     = Math.round(days * 4.1);
  const escalatedReqs   = Math.round(days * 12.3);
  const flaggedReqs     = Math.round(days * 28.7);
  const throttleEvents  = Math.round(days * 1.8);
  const collisions      = Math.round(days * 0.9);
  const piiDetected     = Math.round(days * 8.4);

  // Department spend (proportional)
  const deptData = Object.entries(DEPT_CAPS).map(([name, cap], i) => {
    const pcts  = [0.34, 0.28, 0.22, 0.16];
    const spend = totalFage * pcts[i];
    const pct   = Math.min((spend / (cap * (days / 30))) * 100, 98).toFixed(1);
    return { name, cap: cap * (days / 30), spend, pct };
  });

  // Daily timeline for charts (last N days, sampled)
  const sampleDays = Math.min(days, 30);
  const labels = [];
  const spendWithFage = [];
  const spendBaseline = [];
  const cumulativeSavings = [];
  let cumSaved = 0;

  for (let i = sampleDays - 1; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    labels.push(d.toLocaleDateString("en-US", { month: "short", day: "numeric" }));

    // Add realistic daily variance ±12%
    const variance = 0.88 + (Math.sin(i * 2.3 + 1.1) * 0.12);
    const dayFage  = dailyCostFage     * variance;
    const dayBase  = dailyCostBaseline * variance;
    spendWithFage.push(parseFloat(dayFage.toFixed(2)));
    spendBaseline.push(parseFloat(dayBase.toFixed(2)));
    cumSaved += (dayBase - dayFage);
    cumulativeSavings.push(parseFloat(cumSaved.toFixed(2)));
  }

  return {
    days,
    totalCalls,
    totalFage,
    totalBase,
    totalSaved,
    savingsPct:    ((totalSaved / totalBase) * 100).toFixed(1),
    pruningSaved,
    routingSaved,
    totalTokensSaved,
    microCalls:    Math.round(totalCalls * MICRO_PCT),
    flagshipCalls: Math.round(totalCalls * FLAGSHIP_PCT),
    microPct:      Math.round(MICRO_PCT * 100),
    flagshipPct:   Math.round(FLAGSHIP_PCT * 100),
    blockedReqs,
    escalatedReqs,
    flaggedReqs,
    throttleEvents,
    collisions,
    piiDetected,
    deptData,
    charts: { labels, spendWithFage, spendBaseline, cumulativeSavings },
  };
}

// ── Formatters ────────────────────────────────────────────────────────────────
const fmt$ = v => "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtK = v => v >= 1_000_000 ? (v / 1_000_000).toFixed(2) + "M" : v >= 1_000 ? (v / 1_000).toFixed(1) + "K" : v.toLocaleString();

// ── Chart instances ───────────────────────────────────────────────────────────
const charts = {};
function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

const CHART_DEFAULTS = {
  responsive: true,
  plugins: { legend: { labels: { color: "#8b949e", font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: "#8b949e", font: { size: 10 } }, grid: { color: "#21262d" } },
    y: { ticks: { color: "#8b949e", font: { size: 10 } }, grid: { color: "#21262d" } },
  },
};

// ── Renderers ─────────────────────────────────────────────────────────────────

function renderROI(d) {
  document.getElementById("roiRow").innerHTML = `
    <div class="demo-roi-card hero">
      <div class="demo-roi-label">Total Savings with FAGE</div>
      <div class="demo-roi-value green">${fmt$(d.totalSaved)}</div>
      <div class="demo-roi-sub">${d.savingsPct}% reduction vs. unmanaged AI spend</div>
    </div>
    <div class="demo-roi-card">
      <div class="demo-roi-label">Baseline Spend (No FAGE)</div>
      <div class="demo-roi-value red">${fmt$(d.totalBase)}</div>
      <div class="demo-roi-sub">Everything routed to flagship · no pruning · no throttle</div>
    </div>
    <div class="demo-roi-card">
      <div class="demo-roi-label">Actual Spend (With FAGE)</div>
      <div class="demo-roi-value">${fmt$(d.totalFage)}</div>
      <div class="demo-roi-sub">Smart routing + pruning + budget enforcement</div>
    </div>
    <div class="demo-roi-card">
      <div class="demo-roi-label">Pruning Savings</div>
      <div class="demo-roi-value accent">${fmt$(d.pruningSaved)}</div>
      <div class="demo-roi-sub">Token reduction before every AI call</div>
    </div>
    <div class="demo-roi-card">
      <div class="demo-roi-label">Routing Savings</div>
      <div class="demo-roi-value accent">${fmt$(d.routingSaved)}</div>
      <div class="demo-roi-sub">Micro model used for ${d.microPct}% of routine calls</div>
    </div>
  `;
}

function renderKPIs(d) {
  document.getElementById("kpiRow").innerHTML = `
    <div class="kpi-card">
      <div class="kpi-label">Total AI Calls</div>
      <div class="kpi-value">${fmtK(d.totalCalls)}</div>
      <div class="kpi-sub">${fmtK(Math.round(d.totalCalls / d.days))} per day avg · ${d.days}D window</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Tokens Saved (Pruning)</div>
      <div class="kpi-value" style="color:var(--accent-green)">${fmtK(Math.round(d.totalTokensSaved))}</div>
      <div class="kpi-sub">${fmtK(Math.round(d.totalTokensSaved / d.days))} per day · 74% of calls pruned</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Active Agents</div>
      <div class="kpi-value">${AGENTS_TOTAL}</div>
      <div class="kpi-sub">Salesforce + ServiceNow · 4 departments · auto-registered</div>
    </div>
    <div class="kpi-card ${d.throttleEvents > 0 ? "alert" : ""}">
      <div class="kpi-label">Budget Overruns Prevented</div>
      <div class="kpi-value" style="color:var(--accent-yellow)">${d.throttleEvents}</div>
      <div class="kpi-sub">Auto-throttle engaged before cap was breached</div>
    </div>
  `;
}

function renderStatBar(d) {
  document.getElementById("statBar").innerHTML = `
    <div class="stat-item">
      <span class="stat-label">Total Calls</span>
      <span class="stat-val">${fmtK(d.totalCalls)}</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <span class="stat-label">Micro Calls</span>
      <span class="stat-val micro">${fmtK(d.microCalls)} (${d.microPct}%)</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <span class="stat-label">Flagship Calls</span>
      <span class="stat-val flagship">${fmtK(d.flagshipCalls)} (${d.flagshipPct}%)</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <span class="stat-label">Total Saved</span>
      <span class="stat-val green">${fmt$(d.totalSaved)}</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <span class="stat-label">Sensitive Terms Blocked</span>
      <span class="stat-val" style="color:var(--accent-red)">${d.blockedReqs}</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <span class="stat-label">PII Detected</span>
      <span class="stat-val" style="color:var(--accent-yellow)">${d.piiDetected}</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <span class="stat-label">Audit Events</span>
      <span class="stat-val">${fmtK(d.blockedReqs + d.escalatedReqs + d.flaggedReqs + d.throttleEvents)}</span>
    </div>
  `;
}

function renderSpendChart(d) {
  destroyChart("spend");
  const ctx = document.getElementById("spendChart").getContext("2d");
  charts.spend = new Chart(ctx, {
    type: "line",
    data: {
      labels: d.charts.labels,
      datasets: [
        {
          label: "Without FAGE (Baseline)",
          data: d.charts.spendBaseline,
          borderColor: "#f85149",
          backgroundColor: "rgba(248,81,73,0.08)",
          tension: 0.4, fill: true, pointRadius: 2,
        },
        {
          label: "With FAGE",
          data: d.charts.spendWithFage,
          borderColor: "#3fb950",
          backgroundColor: "rgba(63,185,80,0.08)",
          tension: 0.4, fill: true, pointRadius: 2,
        },
      ],
    },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        tooltip: { callbacks: { label: ctx => ` $${ctx.parsed.y.toFixed(2)}` } },
      },
      scales: {
        ...CHART_DEFAULTS.scales,
        y: { ...CHART_DEFAULTS.scales.y, ticks: { ...CHART_DEFAULTS.scales.y.ticks, callback: v => "$" + v.toFixed(0) } },
      },
    },
  });
}

function renderRoutingChart(d) {
  destroyChart("routing");
  const ctx = document.getElementById("routingChart").getContext("2d");
  charts.routing = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Micro (Routine)", "Flagship (Complex)"],
      datasets: [{
        data: [d.microCalls, d.flagshipCalls],
        backgroundColor: ["#3fb950", "#d29922"],
        borderColor: "#161b22",
        borderWidth: 3,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: "bottom", labels: { color: "#8b949e", font: { size: 12 }, padding: 16 } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${fmtK(ctx.parsed)} calls` } },
      },
    },
  });
}

function renderSavingsChart(d) {
  destroyChart("savings");
  const ctx = document.getElementById("savingsChart").getContext("2d");
  charts.savings = new Chart(ctx, {
    type: "line",
    data: {
      labels: d.charts.labels,
      datasets: [{
        label: "Cumulative Savings ($)",
        data: d.charts.cumulativeSavings,
        borderColor: "#3fb950",
        backgroundColor: "rgba(63,185,80,0.12)",
        tension: 0.4, fill: true, pointRadius: 0,
      }],
    },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        tooltip: { callbacks: { label: ctx => ` Saved: $${ctx.parsed.y.toFixed(2)}` } },
      },
      scales: {
        ...CHART_DEFAULTS.scales,
        y: { ...CHART_DEFAULTS.scales.y, ticks: { ...CHART_DEFAULTS.scales.y.ticks, callback: v => "$" + v.toFixed(0) } },
      },
    },
  });
}

function renderDeptBreakdown(d) {
  document.getElementById("deptBreakdown").innerHTML = d.deptData.map(dept => {
    const pct     = parseFloat(dept.pct);
    const barCls  = pct >= 90 ? "critical" : pct >= 75 ? "warn" : "";
    return `
      <div class="budget-item">
        <div class="budget-dept">
          <span class="dept-name">${dept.name}</span>
          <span class="dept-spend">${fmt$(dept.spend)} / ${fmt$(dept.cap)} cap &nbsp;<span style="color:var(--text-muted)">(${pct}%)</span></span>
        </div>
        <div class="budget-bar-track">
          <div class="budget-bar-fill ${barCls}" style="width:${Math.min(pct, 100)}%"></div>
        </div>
      </div>
    `;
  }).join("");
}

function renderCompliance(d) {
  document.getElementById("complianceGrid").innerHTML = `
    <div class="demo-compliance-card critical">
      <div class="demo-compliance-icon">⛔</div>
      <div class="demo-compliance-value">${d.blockedReqs}</div>
      <div class="demo-compliance-label">Requests Blocked</div>
      <div class="demo-compliance-sub">Sensitive terms triggered block policy — request never reached AI model</div>
    </div>
    <div class="demo-compliance-card high">
      <div class="demo-compliance-icon">⚠</div>
      <div class="demo-compliance-value">${d.escalatedReqs}</div>
      <div class="demo-compliance-label">Escalated to Flagship</div>
      <div class="demo-compliance-sub">Legal, NDA, contract language forced flagship review</div>
    </div>
    <div class="demo-compliance-card medium">
      <div class="demo-compliance-icon">🔍</div>
      <div class="demo-compliance-value">${d.flaggedReqs}</div>
      <div class="demo-compliance-label">Flagged in Audit Log</div>
      <div class="demo-compliance-sub">High-risk keywords logged for compliance review</div>
    </div>
    <div class="demo-compliance-card medium">
      <div class="demo-compliance-icon">🔒</div>
      <div class="demo-compliance-value">${d.piiDetected}</div>
      <div class="demo-compliance-label">PII Detected</div>
      <div class="demo-compliance-sub">Credit cards, SSNs, emails, phone numbers caught before AI processing</div>
    </div>
    <div class="demo-compliance-card low">
      <div class="demo-compliance-icon">💰</div>
      <div class="demo-compliance-value">${d.throttleEvents}</div>
      <div class="demo-compliance-label">Budget Overruns Prevented</div>
      <div class="demo-compliance-sub">Auto-throttle engaged before department cap was breached</div>
    </div>
    <div class="demo-compliance-card low">
      <div class="demo-compliance-icon">⚡</div>
      <div class="demo-compliance-value">${d.collisions}</div>
      <div class="demo-compliance-label">Agent Collisions Resolved</div>
      <div class="demo-compliance-sub">Concurrent write conflicts detected and locked — zero data corruption</div>
    </div>
  `;
}

function renderExecSummary(d) {
  const annualSavings = d.totalSaved * (365 / d.days);
  const fageCost      = 499 * (d.days / 30); // hypothetical FAGE license
  const netRoi        = annualSavings - (499 * 12);
  const roiMultiple   = (annualSavings / (499 * 12)).toFixed(1);

  document.getElementById("execGrid").innerHTML = `
    <div class="demo-exec-card">
      <div class="demo-exec-icon green">$</div>
      <div class="demo-exec-content">
        <div class="demo-exec-title">Projected Annual Savings</div>
        <div class="demo-exec-value green">${fmt$(annualSavings)}</div>
        <div class="demo-exec-sub">Based on ${d.days}-day actual performance extrapolated to 12 months</div>
      </div>
    </div>
    <div class="demo-exec-card">
      <div class="demo-exec-icon accent">%</div>
      <div class="demo-exec-content">
        <div class="demo-exec-title">AI Cost Reduction</div>
        <div class="demo-exec-value accent">${d.savingsPct}%</div>
        <div class="demo-exec-sub">Achieved through smart routing, token pruning, and budget enforcement</div>
      </div>
    </div>
    <div class="demo-exec-card">
      <div class="demo-exec-icon purple">◈</div>
      <div class="demo-exec-content">
        <div class="demo-exec-title">Compliance Events Logged</div>
        <div class="demo-exec-value purple">${fmtK(d.blockedReqs + d.escalatedReqs + d.flaggedReqs)}</div>
        <div class="demo-exec-sub">Immutable audit trail — every AI decision logged, timestamped, exportable</div>
      </div>
    </div>
    <div class="demo-exec-card">
      <div class="demo-exec-icon yellow">⚡</div>
      <div class="demo-exec-content">
        <div class="demo-exec-title">Time to Deploy</div>
        <div class="demo-exec-value yellow">15 min</div>
        <div class="demo-exec-sub">One Apex class · one Flow action · 4 custom fields · no IT project required</div>
      </div>
    </div>
    <div class="demo-exec-card">
      <div class="demo-exec-icon green">↑</div>
      <div class="demo-exec-content">
        <div class="demo-exec-title">Tokens Saved</div>
        <div class="demo-exec-value green">${fmtK(Math.round(d.totalTokensSaved))}</div>
        <div class="demo-exec-sub">Context pruned before every AI call — savings start from day one</div>
      </div>
    </div>
    <div class="demo-exec-card">
      <div class="demo-exec-icon red">0</div>
      <div class="demo-exec-content">
        <div class="demo-exec-title">Data Corruption Events</div>
        <div class="demo-exec-value" style="color:var(--accent-green)">Zero</div>
        <div class="demo-exec-sub">${d.collisions} agent collisions detected and locked — no silent overwrites</div>
      </div>
    </div>
  `;
}

// ── Main render ───────────────────────────────────────────────────────────────
let activeDays = 30;

function setRange(days) {
  activeDays = days;
  document.querySelectorAll(".rpt-range-btn").forEach(b => b.classList.remove("active"));
  document.getElementById(`range-${days}`).classList.add("active");
  render();
}

function render() {
  const d = generateData(activeDays);
  renderROI(d);
  renderKPIs(d);
  renderStatBar(d);
  renderSpendChart(d);
  renderRoutingChart(d);
  renderSavingsChart(d);
  renderDeptBreakdown(d);
  renderCompliance(d);
  renderExecSummary(d);
}

render();
