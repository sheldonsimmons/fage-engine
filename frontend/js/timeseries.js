/**
 * timeseries.js — 30-Day Spend & Activity Charts
 *
 * Fetches GET /api/timeseries and renders two Chart.js charts:
 *   - Daily spend (USD) stacked by department — line/area
 *   - Daily AI calls stacked by model tier — bar
 *
 * Refreshes every 60 seconds alongside the dashboard.
 */

let _tsSpendChart = null;
let _tsCallsChart = null;

const _DEPT_COLORS = [
  "#58a6ff", "#3fb950", "#d2993a", "#f85149",
  "#a371f7", "#79c0ff", "#7ee787", "#ffa657",
];

const _TIER_COLORS = {
  Scout:      "#3fb950",
  Analyst:    "#58a6ff",
  Advisor:    "#d29922",
  Strategist: "#f85149",
};

async function loadTimeSeries() {
  try {
    const data = await apiGet("/api/timeseries");
    _renderSpendChart(data);
    _renderCallsChart(data);
  } catch (e) {
    console.warn("Timeseries load failed:", e.message);
  }
}

function _renderSpendChart(data) {
  const ctx = document.getElementById("tsSpendCanvas");
  if (!ctx) return;
  if (_tsSpendChart) { _tsSpendChart.destroy(); _tsSpendChart = null; }

  const depts    = data.departments || [];
  const stacked  = depts.length > 1;
  const hasData  = data.daily_spend.some(d => d.total_usd > 0);

  const datasets = stacked
    ? depts.map((dept, i) => ({
        label:           dept,
        data:            data.daily_spend.map(d => d.by_dept[dept] || 0),
        borderColor:     _DEPT_COLORS[i % _DEPT_COLORS.length],
        backgroundColor: _DEPT_COLORS[i % _DEPT_COLORS.length] + "30",
        fill:            true,
        tension:         0.35,
        pointRadius:     hasData ? 2 : 0,
        borderWidth:     2,
      }))
    : [{
        label:           "Total Spend",
        data:            data.daily_spend.map(d => d.total_usd),
        borderColor:     "#58a6ff",
        backgroundColor: "#58a6ff30",
        fill:            true,
        tension:         0.35,
        pointRadius:     hasData ? 2 : 0,
        borderWidth:     2,
      }];

  _tsSpendChart = new Chart(ctx, {
    type: "line",
    data: { labels: _shortLabels(data.labels), datasets },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      interaction:         { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: { color: "#8b949e", font: { size: 11 }, boxWidth: 12 },
        },
        tooltip: {
          callbacks: {
            label: c => ` ${c.dataset.label}: $${c.parsed.y.toFixed(4)}`,
          },
        },
      },
      scales: {
        x: {
          stacked: stacked,
          grid:    { color: "#21262d" },
          ticks:   { color: "#8b949e", maxTicksLimit: 10, font: { size: 10 } },
        },
        y: {
          stacked: stacked,
          grid:    { color: "#21262d" },
          ticks: {
            color: "#8b949e",
            font:  { size: 10 },
            callback: v => "$" + (v < 0.01 ? v.toFixed(4) : v.toFixed(2)),
          },
        },
      },
    },
  });
}

function _renderCallsChart(data) {
  const ctx = document.getElementById("tsCallsCanvas");
  if (!ctx) return;
  if (_tsCallsChart) { _tsCallsChart.destroy(); _tsCallsChart = null; }

  const datasets = Object.entries(_TIER_COLORS).map(([tier, color]) => ({
    label:           tier,
    data:            data.daily_calls.map(d => d.by_tier[tier] || 0),
    backgroundColor: color + "bb",
    borderColor:     color,
    borderWidth:     1,
    stack:           "calls",
  }));

  _tsCallsChart = new Chart(ctx, {
    type: "bar",
    data: { labels: _shortLabels(data.labels), datasets },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      interaction:         { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: { color: "#8b949e", font: { size: 11 }, boxWidth: 12 },
        },
      },
      scales: {
        x: {
          stacked: true,
          grid:    { color: "#21262d" },
          ticks:   { color: "#8b949e", maxTicksLimit: 10, font: { size: 10 } },
        },
        y: {
          stacked: true,
          grid:    { color: "#21262d" },
          ticks:   { color: "#8b949e", font: { size: 10 } },
        },
      },
    },
  });
}

/** Convert ISO dates to short "Apr 25" format for chart x-axis */
function _shortLabels(isoLabels) {
  return isoLabels.map(s => {
    const d = new Date(s + "T00:00:00");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  });
}

document.addEventListener("DOMContentLoaded", loadTimeSeries);
setInterval(loadTimeSeries, 60000);
