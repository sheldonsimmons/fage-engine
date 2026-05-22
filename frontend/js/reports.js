/**
 * reports.js — FAGE Reporting Engine UI
 *
 * Three tabs: Savings, Risk & Compliance, Departments
 * Chart.js for all visualizations.
 * Date range selector: 7D / 30D / 90D / 1Y
 */

let activeDays = 30;
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

// ── Range buttons ─────────────────────────────────────────────────────────────

document.querySelectorAll(".rpt-range-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".rpt-range-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    activeDays = parseInt(btn.dataset.days);
    loadActiveTab();
  });
});

function loadActiveTab() {
  if (activeTab === "savings")     loadSavings();
  if (activeTab === "risk")        loadRisk();
  if (activeTab === "departments") loadDepartments();
}

// ── TAB 1: SAVINGS ────────────────────────────────────────────────────────────

async function loadSavings() {
  const data = await apiGet(`/api/reports/savings?days=${activeDays}`);

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
  const data = await apiGet(`/api/reports/risk?days=${activeDays}`);

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
}

// ── TAB 3: DEPARTMENTS ────────────────────────────────────────────────────────

async function loadDepartments() {
  const data = await apiGet(`/api/reports/departments?days=${activeDays}`);

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

// ── Boot ──────────────────────────────────────────────────────────────────────
loadSavings();
