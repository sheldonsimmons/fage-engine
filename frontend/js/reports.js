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
let _rptContextData = null;
const _hiddenDeptChartLabels = new Set();
let _riskDrillDate = "";
let _riskDrillKind = "";
let _riskOpenEventId = null;
const EFFICIENCY_REVIEW_CACHE_PREFIX = "fage_efficiency_review_v1";

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

function roundMoney(v) {
  return Math.round((Number(v) || 0) * 1000000) / 1000000;
}

function displayDeptName(name) {
  if (!name) return "Unknown";
  const text = String(name);
  const colonIndex = text.indexOf(":");
  return colonIndex >= 0 ? text.slice(colonIndex + 1).trim() || text : text;
}

function normEventValue(value) {
  return String(value || "").toLowerCase();
}

function isPremiumTierEvent(e) {
  const tier = normEventValue(e.model_tier);
  const outcome = normEventValue(e.decision_outcome);
  const type = normEventValue(e.event_type);
  return type === "escalated"
    || ["advisor", "strategist", "flagship"].includes(tier)
    || outcome.includes("routed_to_advisor")
    || outcome.includes("routed_to_strategist")
    || outcome.includes("advisor model")
    || outcome.includes("strategist model")
    || outcome.includes("flagship");
}

function riskDrillKindMatch(e, kind) {
  const type = normEventValue(e.event_type);
  const outcome = normEventValue(e.decision_outcome);
  const risk = normEventValue(e.risk_level);
  const keywords = Array.isArray(e.matched_keywords)
    ? e.matched_keywords.join(" ").toLowerCase()
    : normEventValue(e.matched_keywords);

  switch (kind) {
    case "blocked":
      return type === "blocked" || outcome.includes("blocked");
    case "premium":
      return isPremiumTierEvent(e);
    case "pii":
      return type.includes("pii") || outcome.includes("pii") || keywords.includes("pii");
    case "throttle":
      return type.includes("throttle") || outcome.includes("throttle") || outcome.includes("budget");
    case "locks":
      return type === "lock" || outcome.includes("collision") || outcome.includes("conflict") || outcome.includes("lock");
    case "flagged":
      return risk === "high" || risk === "critical";
    default:
      return true;
  }
}

function countRiskDrill(kind, fallback = 0) {
  if (!_rptRiskEvents.length) return Number(fallback) || 0;
  return _rptRiskEvents.filter(e => riskDrillKindMatch(e, kind)).length;
}

function mergeDepartmentReportRows(data) {
  const sourceScorecards = data.scorecards || [];
  const rawDepartments   = data.departments || [];
  const deptNameMap      = {};

  rawDepartments.forEach(dept => {
    deptNameMap[dept] = displayDeptName(dept);
  });
  sourceScorecards.forEach(row => {
    deptNameMap[row.department] = displayDeptName(row.display_department || row.department);
  });

  const scorecardMap = new Map();
  sourceScorecards.forEach(row => {
    const displayName = displayDeptName(row.display_department || row.department);
    const current = scorecardMap.get(displayName) || {
      ...row,
      department: displayName,
      display_department: displayName,
      total_calls: 0,
      micro_calls: 0,
      flagship_calls: 0,
      total_cost_usd: 0,
      tokens_pruned: 0,
      pruning_saved_usd: 0,
      monthly_cap_usd: 0,
      current_spend_usd: 0,
      budget_used_pct: 0,
      throttled: false,
      override_granted: false,
    };

    current.total_calls       += row.total_calls || 0;
    current.micro_calls       += row.micro_calls || 0;
    current.flagship_calls    += row.flagship_calls || 0;
    current.total_cost_usd     = roundMoney(current.total_cost_usd + (row.total_cost_usd || 0));
    current.tokens_pruned     += row.tokens_pruned || 0;
    current.pruning_saved_usd  = roundMoney(current.pruning_saved_usd + (row.pruning_saved_usd || 0));
    current.monthly_cap_usd    = roundMoney(current.monthly_cap_usd + (row.monthly_cap_usd || 0));
    current.current_spend_usd  = roundMoney(current.current_spend_usd + (row.current_spend_usd || 0));
    current.throttled          = current.throttled || !!row.throttled;
    current.override_granted   = current.override_granted || !!row.override_granted;

    scorecardMap.set(displayName, current);
  });

  const scorecards = Array.from(scorecardMap.values()).map(row => {
    const calls = row.total_calls || 0;
    return {
      ...row,
      micro_pct: calls ? Math.round((row.micro_calls / calls) * 1000) / 10 : 0,
      budget_used_pct: row.monthly_cap_usd > 0
        ? Math.round((row.current_spend_usd / row.monthly_cap_usd) * 1000) / 10
        : 0,
    };
  }).sort((a, b) => a.department.localeCompare(b.department));

  const timeline = (data.timeline || []).map(point => {
    const merged = { date: point.date };
    Object.keys(point).forEach(key => {
      if (key === "date") return;
      const displayName = deptNameMap[key] || displayDeptName(key);
      merged[displayName] = roundMoney((merged[displayName] || 0) + (point[key] || 0));
    });
    return merged;
  });

  return {
    ...data,
    scorecards,
    timeline,
    departments: scorecards.map(row => row.department),
  };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function selectValue(id) {
  return (document.getElementById(id)?.value || "").trim();
}

function fillRiskEventSelect(id, values, allLabel) {
  const select = document.getElementById(id);
  if (!select) return;
  const current = select.value;
  select.innerHTML = `<option value="">${allLabel}</option>` + values.map(v =>
    `<option value="${String(v).replace(/"/g, "&quot;")}">${v}</option>`
  ).join("");
  if (values.includes(current)) select.value = current;
}

function deptChartLabel(label) {
  return String(label || "").trim().toLowerCase();
}

function setBarDatasetVisible(chart, index, visible) {
  if (!chart) return;
  if (typeof chart.setDatasetVisibility === "function") {
    chart.setDatasetVisibility(index, visible);
  } else {
    const meta = chart.getDatasetMeta(index);
    if (meta) meta.hidden = !visible;
  }
}

function setDoughnutSliceVisible(chart, index, visible) {
  if (!chart) return;
  if (typeof chart.setDataVisibility === "function") {
    chart.setDataVisibility(index, visible);
  } else {
    const slice = chart.getDatasetMeta(0)?.data?.[index];
    if (slice) slice.hidden = !visible;
  }
}

function applyDeptChartVisibility() {
  const spendChart = charts["deptSpend"];
  if (spendChart) {
    spendChart.data.datasets.forEach((dataset, index) => {
      setBarDatasetVisible(spendChart, index, !_hiddenDeptChartLabels.has(deptChartLabel(dataset.label)));
    });
    spendChart.update();
  }

  const costChart = charts["deptCost"];
  if (costChart) {
    costChart.data.labels.forEach((label, index) => {
      setDoughnutSliceVisible(costChart, index, !_hiddenDeptChartLabels.has(deptChartLabel(label)));
    });
    costChart.update();
  }
}

function toggleDeptChartLabel(label) {
  const key = deptChartLabel(label);
  if (!key) return;
  if (_hiddenDeptChartLabels.has(key)) {
    _hiddenDeptChartLabels.delete(key);
  } else {
    _hiddenDeptChartLabels.add(key);
  }
  applyDeptChartVisibility();
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
  if (activeTab === "contexts")    loadBusinessContexts();
  if (activeTab === "departments") loadDepartments();
  if (activeTab === "activity")    loadAgentActivity();
  if (activeTab === "efficiency")  restoreEfficiencyReviewForSelectedDays();
  // efficiency tab remains on-demand. Restore cached reviews, but do not rerun analysis automatically.
}

function projectAttributionSelect(id, defaultLabel, options) {
  const select = document.getElementById(id);
  if (!select) return;
  const current = select.value;
  select.innerHTML = `<option value="">${escapeHtml(defaultLabel)}</option>` +
    (options || []).map(option => {
      const value = String(option.value ?? option);
      const label = option.label ?? option;
      return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
    }).join("");
  if ([...select.options].some(option => option.value === current)) select.value = current;
}

function projectAttributionFilterValue(id) {
  return document.getElementById(id)?.value || "";
}

function resetProjectAttributionFilters() {
  [
    "ctxOrgFilter", "ctxProjectFilter", "ctxPersonFilter", "ctxAccountFilter",
    "ctxAgentFilter", "ctxSourceFilter", "ctxRecordTypeFilter",
  ].forEach(id => {
    const select = document.getElementById(id);
    if (select) select.value = "";
  });
  loadBusinessContexts();
}

function selectOrganizationalUnit(name) {
  const select = document.getElementById("ctxOrgFilter");
  if (!select) return;
  select.value = select.value === name ? "" : name;
  loadBusinessContexts();
}

async function loadBusinessContexts() {
  const { date_from, date_to, days } = getActiveDateRange();
  const wsId = localStorage.getItem("cp_workspace_id") || "";
  const params = new URLSearchParams({
    date_from,
    date_to,
    days: String(Math.min(365, days)),
    activity_limit: "1000",
  });
  if (wsId) params.set("workspace_id", wsId);
  const selectedFilters = {
    project_id: projectAttributionFilterValue("ctxProjectFilter"),
    user_external_id: projectAttributionFilterValue("ctxPersonFilter"),
    account_id: projectAttributionFilterValue("ctxAccountFilter"),
    agent_id: projectAttributionFilterValue("ctxAgentFilter"),
    source_platform: projectAttributionFilterValue("ctxSourceFilter"),
    record_type: projectAttributionFilterValue("ctxRecordTypeFilter"),
    charged_unit: projectAttributionFilterValue("ctxOrgFilter"),
  };
  Object.entries(selectedFilters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  try {
    const orgParams = new URLSearchParams({
      date_from,
      date_to,
      days: String(Math.min(365, days)),
    });
    if (wsId) orgParams.set("workspace_id", wsId);
    const [data, orgData] = await Promise.all([
      apiGet(`/api/work-items/activity-report?${params.toString()}`),
      apiGet(`/api/work-items/organizational-usage?${orgParams.toString()}`),
    ]);
    _rptContextData = data;
    const summary = data.summary || {};
    setKpi("ctx-requests", fmtNum(summary.request_count || 0));
    setKpi("ctx-tokens", fmtNum(summary.total_tokens || 0));
    setKpi("ctx-token-split", `${fmtNum(summary.input_tokens || 0)} input · ${fmtNum(summary.output_tokens || 0)} output`);
    setKpi("ctx-pruned", fmtNum(summary.tokens_saved || 0));
    setKpi("ctx-spend", fmtUsd(Number(summary.spend_usd || 0)));
    setKpi("ctx-people", fmtNum(summary.people_count || 0));
    setKpi("ctx-agents", fmtNum(summary.agent_count || 0));

    const options = data.filter_options || {};
    projectAttributionSelect("ctxOrgFilter", "All Departments & Teams", options.organizational_units);
    projectAttributionSelect("ctxProjectFilter", "All Projects", options.projects);
    projectAttributionSelect("ctxPersonFilter", "All People", options.people);
    projectAttributionSelect("ctxAccountFilter", "All Accounts", options.accounts);
    projectAttributionSelect("ctxAgentFilter", "All Agents", options.agents);
    projectAttributionSelect("ctxSourceFilter", "All Sources", options.source_platforms);
    projectAttributionSelect("ctxRecordTypeFilter", "All Record Types", options.record_types);

    const selectedOrg = projectAttributionFilterValue("ctxOrgFilter");
    const company = orgData.company || {};
    document.getElementById("ctx-org-company-requests").textContent =
      `${fmtNum(company.request_count || 0)} requests`;
    document.getElementById("ctx-org-company-spend").textContent =
      fmtUsd(Number(company.spend_usd || 0));
    document.getElementById("ctx-org-path").textContent = selectedOrg
      ? `Company → ${selectedOrg}`
      : "Company · all departments and teams";
    const orgUnits = orgData.organizational_units || [];
    document.getElementById("ctx-org-units").innerHTML = orgUnits.length
      ? orgUnits.map(row => `<button type="button"
          class="org-unit-card${selectedOrg === row.label ? " active" : ""}"
          data-unit="${escapeHtml(row.label || "")}"
          onclick="selectOrganizationalUnit(this.dataset.unit)">
          <span class="org-unit-name">${escapeHtml(row.label)}</span>
          <strong>${fmtUsd(Number(row.spend_usd || 0))}</strong>
          <span>${fmtNum(row.request_count || 0)} requests · ${fmtNum(row.total_tokens || 0)} tokens</span>
        </button>`).join("")
      : '<div class="org-unit-empty">No organizational usage is available for this period.</div>';

    const projectBody = document.getElementById("ctx-project-rows");
    projectBody.innerHTML = (data.project_breakdown || []).length
      ? data.project_breakdown.map(row => `<tr>
          <td><div class="ctx-name">${escapeHtml(row.label)}</div></td>
          <td>${escapeHtml(row.account_name || "Unassigned account")}</td>
          <td class="ctx-mono">${fmtNum(row.request_count || 0)}</td>
          <td class="ctx-mono">${fmtNum(row.total_tokens || 0)}</td>
          <td class="ctx-mono">${fmtUsd(Number(row.spend_usd || 0))}</td>
        </tr>`).join("")
      : '<tr><td colspan="5">No project activity matches these filters.</td></tr>';

    const personBody = document.getElementById("ctx-person-rows");
    personBody.innerHTML = (data.people_breakdown || []).length
      ? data.people_breakdown.map(row => `<tr>
          <td><div class="ctx-name">${escapeHtml(row.label)}</div><div class="ctx-meta">${escapeHtml(row.email || row.source_platform || "")}</div></td>
          <td class="ctx-mono">${fmtNum(row.request_count || 0)}</td>
          <td class="ctx-mono">${fmtNum(row.total_tokens || 0)}</td>
          <td class="ctx-mono">${fmtNum(row.tokens_saved || 0)}</td>
          <td class="ctx-mono">${fmtUsd(Number(row.spend_usd || 0))}</td>
        </tr>`).join("")
      : '<tr><td colspan="5">No identified user activity matches these filters.</td></tr>';

    const agentBody = document.getElementById("ctx-agent-rows");
    agentBody.innerHTML = (data.agent_breakdown || []).length
      ? data.agent_breakdown.map(row => `<tr>
          <td><div class="ctx-name">${escapeHtml(row.label)}</div><div class="ctx-meta">${escapeHtml(row.source_platform || "")}</div></td>
          <td class="ctx-mono">${fmtNum(row.request_count || 0)}</td>
          <td class="ctx-mono">${fmtNum(row.total_tokens || 0)}</td>
          <td class="ctx-mono">${fmtNum(row.tokens_saved || 0)}</td>
          <td class="ctx-mono">${fmtUsd(Number(row.spend_usd || 0))}</td>
        </tr>`).join("")
      : '<tr><td colspan="5">No agent activity matches these filters.</td></tr>';

    const activityBody = document.getElementById("ctx-activity-rows");
    const activities = data.activities || [];
    activityBody.innerHTML = activities.length
      ? activities.map(row => `<tr>
          <td class="ctx-mono">${escapeHtml(row.timestamp ? new Date(row.timestamp).toLocaleString() : "—")}</td>
          <td><div class="ctx-name">${escapeHtml(row.charged_unit || "Unassigned")}</div><div class="ctx-meta">${escapeHtml(row.attribution_source || "")}</div></td>
          <td><div class="ctx-name">${escapeHtml(row.user_name || "Unknown user")}</div><div class="ctx-meta">${escapeHtml(row.user_source_platform || "")}</div></td>
          <td><div class="ctx-name">${escapeHtml(row.agent_name || "Unknown agent")}</div><div class="ctx-meta">${escapeHtml(row.agent_platform || "")}</div></td>
          <td>${escapeHtml(row.account_name || "Unassigned account")}</td>
          <td>${escapeHtml(row.project_name || "Unattributed")}</td>
          <td><div class="ctx-name">${escapeHtml(row.source_record_name || row.source_record_id || "Not recorded")}</div><div class="ctx-meta">${escapeHtml(row.source_record_type || row.source_platform || "")}</div></td>
          <td><div>${escapeHtml(row.model_name || row.model_tier || "—")}</div><div class="ctx-meta">${escapeHtml(row.model_tier || "")}${row.is_simulation ? ' · <span class="rpt-badge badge-event">SIMULATION</span>' : ""}</div></td>
          <td class="ctx-mono">${fmtNum(row.total_tokens || 0)}</td>
          <td class="ctx-mono">${fmtNum(row.tokens_saved || 0)}</td>
          <td class="ctx-mono">${fmtUsd(Number(row.cost_usd || 0))}</td>
        </tr>`).join("")
      : '<tr><td colspan="11">No AI activity matches these filters.</td></tr>';
    document.getElementById("ctx-activity-count").textContent =
      `${fmtNum(data.activity_count || 0)} ${Number(data.activity_count || 0) === 1 ? "activity" : "activities"} · ` +
      `${fmtNum(summary.live_count || 0)} live · ${fmtNum(summary.simulation_count || 0)} simulation`;
  } catch (err) {
    document.getElementById("ctx-activity-rows").innerHTML =
      `<tr><td colspan="11">Could not load AI usage attribution: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function exportContextCsv() {
  const data = _rptContextData;
  if (!data) return;
  const headers = [
    "Timestamp", "Charged Department or Team", "Attribution Source",
    "Person", "Person External ID", "Agent", "Account", "Project",
    "Source System", "Record Type", "Source Record", "Model", "Input Tokens",
    "Output Tokens", "Tokens Pruned", "Cost USD", "Execution Mode",
  ];
  const rows = (data.activities || []).map(row => [
    row.timestamp, row.charged_unit, row.attribution_source,
    row.user_name, row.user_external_id, row.agent_name, row.account_name,
    row.project_name, row.source_platform, row.source_record_type,
    row.source_record_name || row.source_record_id, row.model_name || row.model_tier,
    row.input_tokens, row.output_tokens, row.tokens_saved, row.cost_usd,
    row.is_simulation ? "Simulation" : "Live",
  ]);
  downloadCsv(`costpilot_project_attribution_${new Date().toISOString().slice(0, 10)}.csv`, headers, rows);
}

function isReportFilterActive() {
  const active = document.activeElement;
  return !!active && !!active.closest(".rpt-date-controls, .act-filter-bar");
}

// ── TAB 1: SAVINGS ────────────────────────────────────────────────────────────

// Workspace filter — set when a trial user clicks through from workspace.html
const _wsParam = new URLSearchParams(window.location.search).get("ws")
  ? `&workspace_id=${localStorage.getItem("cp_workspace_id") || ""}`
  : "";

async function loadSavings() {
  const { days } = getActiveDateRange();
  const data = await apiGet(`/api/reports/savings?days=${days}${_wsParam}`);
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
  const data = await apiGet(`/api/reports/risk?days=${days}${_wsParam}`);
  _rptRiskEvents = data.recent_events || [];
  populateRiskEventFilters(_rptRiskEvents);

  setKpi("rk-total",    fmtNum(data.total_events));
  setKpi("rk-critical", fmtNum(data.critical));
  setKpi("rk-high",     fmtNum(data.high));
  setKpi("rk-blocked",  fmtNum(data.blocked));
  setKpi("rk-locks",    fmtNum(data.locks));
  setKpi("rk-terms",    fmtNum(data.term_library.total));
  setKpi("rk-terms-sub", data.term_library.block + " block / " + data.term_library.escalate + " escalate");
  wireRiskDrilldowns();

  const labels = data.timeline.map(d => d.date);
  const riskChartBase = chartDefaults();

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
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              color: COLORS.muted,
              boxWidth: 12,
              padding: 12,
              font: { size: 10 },
            },
          },
        },
        scales: {
          x: {
            ...riskChartBase.scales.x,
            stacked: true,
            ticks: { ...riskChartBase.scales.x.ticks, maxTicksLimit: 7, maxRotation: 25 },
          },
          y: {
            ...riskChartBase.scales.y,
            stacked: true,
            beginAtZero: true,
            ticks: { ...riskChartBase.scales.y.ticks, precision: 0 },
          },
        },
        onClick: (_evt, elements) => {
          if (!elements.length) return;
          const el = elements[0];
          const risk = charts["riskTimeline"].data.datasets[el.datasetIndex].label.toLowerCase();
          const date = charts["riskTimeline"].data.labels[el.index];
          applyRiskDrilldown({ risk, date, label: `${risk.toUpperCase()} events on ${date}` });
        },
      },
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
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              color: COLORS.muted,
              boxWidth: 12,
              padding: 12,
              font: { size: 10 },
            },
          },
        },
        cutout: "68%",
        onClick: (_evt, elements) => {
          if (!elements.length) return;
          const risk = charts["riskBreakdown"].data.labels[elements[0].index].toLowerCase();
          applyRiskDrilldown({ risk, label: `${risk.toUpperCase()} risk events` });
        },
      },
    }
  );

  renderRiskEventTable();

  // Load governance summary panels from dashboard API
  try {
    const d = await apiGet("/api/dashboard");
    renderComplianceGrid(d);
    renderExecSummary(d);
  } catch (e) {
    console.warn("Governance panels failed:", e.message);
  }
}

function wireRiskDrilldowns() {
  document.querySelectorAll("[data-risk-drill]").forEach(el => {
    if (el.dataset.drillReady === "1") return;
    el.dataset.drillReady = "1";
    el.addEventListener("click", () => {
      const target = el.dataset.riskDrill;
      if (target === "terms") {
        window.location.href = "/policy.html";
      } else if (target === "all") {
        applyRiskDrilldown({ label: "All audit events" });
      } else if (target === "blocked") {
        applyRiskDrilldown({ drill: "blocked", label: "Blocked request events" });
      } else if (target === "locks") {
        applyRiskDrilldown({ drill: "locks", label: "Agent collision and lock events" });
      } else {
        applyRiskDrilldown({ risk: target, label: `${target.toUpperCase()} risk events` });
      }
    });
  });
}

function setRiskControl(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value || "";
}

function applyRiskDrilldown({ risk = "", type = "", search = "", date = "", drill = "", label = "Filtered events" } = {}) {
  _riskDrillDate = date;
  _riskDrillKind = drill;
  _riskOpenEventId = null;
  setRiskControl("riskEventDept", "");
  setRiskControl("riskEventRisk", risk);
  setRiskControl("riskEventType", type);
  setRiskControl("riskEventSearch", search);
  const banner = document.getElementById("riskDrillBanner");
  if (banner) {
    banner.style.display = "flex";
    banner.innerHTML = `
      <span><strong>Drill-down:</strong> ${escapeHtml(label)}</span>
      <button type="button" onclick="resetRiskEventFilters()">Clear drill-down</button>
    `;
  }
  renderRiskEventTable();
  document.querySelector('[data-card-id="rk-events"]')?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function populateRiskEventFilters(events) {
  const active = document.activeElement;
  if (active && active.closest(".risk-event-filter-bar")) return;

  const depts = [...new Set(events.map(e => displayDeptName(e.display_department || e.department)).filter(Boolean))].sort();
  const types = [...new Set(events.map(e => e.event_type).filter(Boolean))].sort();
  fillRiskEventSelect("riskEventDept", depts, "All Departments");
  fillRiskEventSelect("riskEventType", types, "All Types");
}

function renderRiskEventTable() {
  const tbody = document.getElementById("riskEventTable");
  if (!tbody) return;

  if (!_rptRiskEvents.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="placeholder">No events in this period.</td></tr>`;
    const count = document.getElementById("riskEventCount");
    if (count) count.textContent = "0 events";
    return;
  }

  const dept = selectValue("riskEventDept").toLowerCase();
  const risk = selectValue("riskEventRisk").toLowerCase();
  const type = selectValue("riskEventType").toLowerCase();
  const search = selectValue("riskEventSearch").toLowerCase();

  const rows = _rptRiskEvents.filter(e => {
    const displayDept = displayDeptName(e.display_department || e.department);
    const eventDate = e.timestamp ? String(e.timestamp).slice(0, 10) : "";
    if (_riskDrillDate && eventDate !== _riskDrillDate) return false;
    if (_riskDrillKind && !riskDrillKindMatch(e, _riskDrillKind)) return false;
    if (dept && displayDept.toLowerCase() !== dept) return false;
    if (risk && String(e.risk_level || "").toLowerCase() !== risk) return false;
    if (type && String(e.event_type || "").toLowerCase() !== type) return false;
    if (search) {
      const haystack = [
        e.event_type,
        displayDept,
        e.risk_level,
        e.decision_outcome,
        e.rationale,
      ].join(" ").toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    return true;
  });

  const count = document.getElementById("riskEventCount");
  if (count) count.textContent = `${rows.length} of ${_rptRiskEvents.length} events`;

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="placeholder">No events match these filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(e => `
    <tr class="risk-event-row" onclick="toggleRiskEventDetail(${e.id})">
      <td style="font-size:11px; font-family:var(--font-mono)">${fmtTs(e.timestamp)}</td>
      <td><span class="rpt-badge badge-event">${escapeHtml(e.event_type)}</span></td>
      <td>${escapeHtml(displayDeptName(e.display_department || e.department))}</td>
      <td>${riskBadge(e.risk_level)}</td>
      <td style="font-size:11px; color:var(--text-muted)">${escapeHtml(e.decision_outcome)}</td>
    </tr>
    ${_riskOpenEventId === e.id ? `<tr class="risk-detail-row"><td colspan="5" id="riskEventDetail-${e.id}" class="risk-detail-cell">Loading detail...</td></tr>` : ""}
  `).join("");

  if (_riskOpenEventId) loadRiskEventDetail(_riskOpenEventId);
}

function resetRiskEventFilters() {
  ["riskEventDept", "riskEventRisk", "riskEventType", "riskEventSearch"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  _riskDrillDate = "";
  _riskDrillKind = "";
  _riskOpenEventId = null;
  const banner = document.getElementById("riskDrillBanner");
  if (banner) {
    banner.style.display = "none";
    banner.innerHTML = "";
  }
  renderRiskEventTable();
}

async function toggleRiskEventDetail(eventId) {
  _riskOpenEventId = _riskOpenEventId === eventId ? null : eventId;
  renderRiskEventTable();
}

function formatMatchedKeywords(keywords) {
  if (!keywords || !keywords.length) return "None";
  return keywords.map(k => `<span class="risk-keyword">${escapeHtml(k)}</span>`).join("");
}

async function loadRiskEventDetail(eventId) {
  const cell = document.getElementById(`riskEventDetail-${eventId}`);
  if (!cell) return;
  try {
    const detail = await apiGet(`/api/audit/${eventId}`);
    const usageSource = detail.usage_source === "provider_reported"
      ? "Provider reported"
      : (detail.usage_source === "estimated" ? "Estimated" : "Not recorded");
    cell.innerHTML = `
      <div class="risk-detail-grid">
        <div>
          <div class="risk-detail-label">Why It Happened</div>
          <div class="risk-detail-text">${escapeHtml(detail.rationale || "No rationale recorded.")}</div>
        </div>
        <div>
          <div class="risk-detail-label">Decision Context</div>
          <div class="risk-detail-meta">
            Agent: ${escapeHtml(detail.display_agent_name || detail.agent_name || "Not linked")}<br/>
            Platform: ${escapeHtml(detail.source_platform || "Unknown")}<br/>
            Model tier: ${escapeHtml(detail.model_tier || "none")}<br/>
            Cost: ${fmtUsd(detail.cost_usd || 0)}<br/>
            Token usage source: ${escapeHtml(usageSource)}
          </div>
        </div>
        <div>
          <div class="risk-detail-label">Matched Keywords</div>
          <div class="risk-keyword-list">${formatMatchedKeywords(detail.matched_keywords)}</div>
        </div>
        <div>
          <div class="risk-detail-label">Payload Preview</div>
          <pre class="risk-payload-preview">${escapeHtml(detail.prompt_payload || "No payload preview stored.")}</pre>
        </div>
      </div>
    `;
  } catch (err) {
    cell.innerHTML = `<span style="color:var(--accent-red)">Failed to load event detail: ${escapeHtml(err.message)}</span>`;
  }
}

function renderComplianceGrid(d) {
  const grid = document.getElementById("complianceGrid");
  if (!grid) return;
  const blockedCount = countRiskDrill("blocked", d.blocked_count);
  const premiumCount = countRiskDrill("premium", d.escalated_count);
  const flaggedCount = countRiskDrill("flagged", d.flagged_count);
  const piiCount = countRiskDrill("pii", d.pii_count);
  const throttleCount = countRiskDrill("throttle", d.throttle_prevented);
  const collisionCount = countRiskDrill("locks", d.collision_count);
  const collisionBreakdown = d.collision_breakdown || { lock: d.collision_count || 0, queue: 0, skip: 0 };
  const collisionSummary = `${collisionBreakdown.lock || 0} locked · ${collisionBreakdown.queue || 0} queued · ${collisionBreakdown.skip || 0} skipped`;
  const items = [
    { icon: "🚫", value: blockedCount,  label: "Requests Blocked",          sub: "Sensitive terms triggered block policy — request never reached AI model",          cls: "critical", drill: { drill: "blocked", label: "Blocked request events" } },
    { icon: "⚠️",  value: premiumCount,  label: "Escalated to Flagship",     sub: "Requests routed to Advisor, Strategist, or flagship review",                      cls: "high",     drill: { drill: "premium", label: "Premium-tier routed events" } },
    { icon: "🔍", value: flaggedCount,  label: "Flagged in Audit Log",      sub: "High-risk keywords logged for compliance review",                                   cls: "medium",   drill: { drill: "flagged", label: "High-risk flagged audit events" } },
    { icon: "🔒", value: piiCount,      label: "PII Detected",              sub: "Credit cards, SSNs, emails, phone numbers caught before AI processing",             cls: "high",     drill: { drill: "pii", label: "PII-related events" } },
    { icon: "💰", value: throttleCount, label: "Budget Overruns Prevented", sub: "Auto-throttle engaged before department cap was breached",                          cls: "low",      drill: { drill: "throttle", label: "Budget throttle events" } },
    { icon: "⚡", value: collisionCount, label: "Agent Collisions Controlled", sub: collisionSummary + " — zero silent overwrites",                              cls: "low",      drill: { drill: "locks", label: "Agent collision events" } },
  ];
  const hasData = items.some(i => i.value > 0);
  if (!hasData) {
    grid.innerHTML = '<p class="placeholder">No compliance events yet — load enterprise demo or route a call.</p>';
    return;
  }
  grid.innerHTML = items.map((i, idx) => `
    <div class="demo-compliance-card ${i.cls} rpt-drill" onclick="applyRiskDrilldown(${JSON.stringify(i.drill).replace(/"/g, "&quot;")})" title="Drill down into related events">
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
  const governed = (d.requests_governed || d.total_calls || 0).toLocaleString();
  const items = [
    { icon: "$", cls: "green",  color: "green",  title: "PROJECTED ANNUAL SAVINGS",  value: "$" + savings,                        sub: "Based on 30-day actual performance extrapolated to 12 months" },
    { icon: "%", cls: "accent", color: "accent", title: "AI COST REDUCTION",         value: (d.cost_reduction_pct || 0) + "%",    sub: "Achieved through smart routing, token pruning, and budget enforcement" },
    { icon: "◈", cls: "purple", color: "purple", title: "COMPLIANCE EVENTS LOGGED",  value: eventsLabel,                          sub: "Immutable audit trail — every AI decision logged, timestamped, exportable" },
    { icon: "✓", cls: "yellow", color: "yellow", title: "REQUESTS GOVERNED",         value: governed,                             sub: "AI requests routed, blocked, audited, or budget-checked by CostPilot" },
    { icon: "↑", cls: "green",  color: "green",  title: "TOKENS SAVED",              value: tokensLabel,                          sub: "Context pruned before every AI call — savings start from day one" },
    { icon: "0", cls: "red",    color: "",        title: "DATA CORRUPTION EVENTS",    value: "Zero",                               sub: `${d.collision_count || 0} agent collisions controlled — ${((d.collision_breakdown || {}).lock || 0)} locked, ${((d.collision_breakdown || {}).queue || 0)} queued, ${((d.collision_breakdown || {}).skip || 0)} skipped` },
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
  const rawData = await apiGet(`/api/reports/departments?days=${days}${_wsParam}`);
  const data = mergeDepartmentReportRows(rawData);
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
          <td><strong>${displayDeptName(d.display_department || d.department)}</strong></td>
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
  const deptChartBase = chartDefaults();

  // Stacked dept spend chart
  destroyChart("deptSpend");
  charts["deptSpend"] = new Chart(
    document.getElementById("chartDeptSpend").getContext("2d"),
    {
      type: "bar",
      data: {
        labels,
        datasets: depts.map((dept, i) => ({
          label: displayDeptName(dept),
          data:  data.timeline.map(d => d[dept] || 0),
          backgroundColor: COLORS.dept[i % COLORS.dept.length] + "99",
          borderColor:     COLORS.dept[i % COLORS.dept.length],
          borderWidth: 1,
          stack: "dept",
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            onClick: (_evt, item) => toggleDeptChartLabel(item.text),
            labels: {
              color: COLORS.muted,
              boxWidth: 12,
              padding: 12,
              font: { size: 10 },
            },
          },
        },
        scales: {
          x: {
            ...deptChartBase.scales.x,
            stacked: true,
            ticks: { ...deptChartBase.scales.x.ticks, maxTicksLimit: 7, maxRotation: 25 },
          },
          y: {
            ...deptChartBase.scales.y,
            stacked: true,
            beginAtZero: true,
          },
        },
      },
    }
  );

  // Dept cost doughnut
  destroyChart("deptCost");
  charts["deptCost"] = new Chart(
    document.getElementById("chartDeptCost").getContext("2d"),
    {
      type: "doughnut",
      data: {
        labels: data.scorecards.map(d => d.display_department || d.department),
        datasets: [{
          data: data.scorecards.map(d => d.total_cost_usd),
          backgroundColor: depts.map((_, i) => COLORS.dept[i % COLORS.dept.length]),
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            onClick: (_evt, item) => toggleDeptChartLabel(item.text),
            labels: {
              color: COLORS.muted,
              boxWidth: 12,
              padding: 12,
              font: { size: 10 },
            },
          },
        },
        cutout: "68%",
      },
    }
  );

  applyDeptChartVisibility();
}

// ── TAB 4: BOT EFFICIENCY REVIEW ──────────────────────────────────────────────

function getEfficiencyDays() {
  return (document.getElementById("effDaysSelect") || {}).value || "30";
}

function efficiencyCacheKey(days) {
  return `${EFFICIENCY_REVIEW_CACHE_PREFIX}_${days}`;
}

function formatEfficiencyTimestamp(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function setEfficiencyGeneratedStatus(generatedAt, days) {
  const status = document.getElementById("effGeneratedStatus");
  if (!status) return;

  const formatted = formatEfficiencyTimestamp(generatedAt);
  if (!formatted) {
    status.textContent = "No review generated yet.";
    status.classList.remove("ready");
    return;
  }

  status.textContent = `Generated ${formatted} for the last ${days} days.`;
  status.classList.add("ready");
}

function setEfficiencyButtonState(hasReview) {
  const btn = document.getElementById("effGenerateBtn");
  if (!btn || btn.disabled) return;
  btn.textContent = hasReview ? "Refresh Review" : "⚡ Generate Review";
}

function saveEfficiencyReview(days, data) {
  const cached = {
    generated_at: new Date().toISOString(),
    days,
    data,
  };

  try {
    sessionStorage.setItem(efficiencyCacheKey(days), JSON.stringify(cached));
  } catch (e) {}

  return cached;
}

function readEfficiencyReview(days) {
  try {
    const raw = sessionStorage.getItem(efficiencyCacheKey(days));
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function renderEfficiencyReview(data, generatedAt, days) {
  const grid = document.getElementById("effGrid");
  if (!grid) return;

  document.getElementById("effFleetGrade").textContent   = data.fleet_grade || "—";
  document.getElementById("effAgentsCount").textContent  = data.total_agents_analyzed || 0;
  document.getElementById("effTotalSavings").textContent = "$" + (data.total_projected_savings || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  document.getElementById("effGeneratedBy").textContent  = data.generated_by === "ai" ? "GPT-4o" : "CostPilot Analytics";
  document.getElementById("effFleetBar").style.display   = "grid";
  setEfficiencyGeneratedStatus(generatedAt, days);
  setEfficiencyButtonState(true);

  if (!data.reviews || !data.reviews.length) {
    grid.innerHTML = `<div class="eff-empty" style="grid-column:1/-1">${data.message || "No agent data found for this period."}</div>`;
    return;
  }

  grid.innerHTML = data.reviews.map(r => renderEfficiencyCard(r)).join("");
}

function restoreEfficiencyReviewForSelectedDays() {
  const days = getEfficiencyDays();
  const cached = readEfficiencyReview(days);

  if (!cached || !cached.data) {
    document.getElementById("effFleetBar").style.display = "none";
    setEfficiencyGeneratedStatus(null, days);
    setEfficiencyButtonState(false);
    const grid = document.getElementById("effGrid");
    if (grid) {
      grid.innerHTML = `
        <div class="eff-empty">
          Click <strong>Generate Review</strong> to analyze your bot fleet.
        </div>
      `;
    }
    return false;
  }

  renderEfficiencyReview(cached.data, cached.generated_at, days);
  return true;
}

async function generateEfficiencyReview() {
  const btn  = document.getElementById("effGenerateBtn");
  const grid = document.getElementById("effGrid");
  const days = getEfficiencyDays();

  btn.disabled    = true;
  btn.textContent = "⚡ Analyzing...";
  grid.innerHTML  = `<div class="eff-empty" style="grid-column:1/-1">Analyzing ${days}-day transaction history across all agents...<br><span style="font-size:11px;color:var(--text-muted);margin-top:8px;display:block">This may take a few seconds</span></div>`;
  document.getElementById("effFleetBar").style.display = "none";

  try {
    const data = await apiPost(`/api/reports/bot-efficiency?days=${days}`, {});
    const cached = saveEfficiencyReview(days, data);
    renderEfficiencyReview(data, cached.generated_at, days);

  } catch (e) {
    grid.innerHTML = `<div class="eff-empty" style="grid-column:1/-1; color:var(--accent-red)">Analysis failed: ${e.message}</div>`;
  } finally {
    btn.disabled    = false;
    setEfficiencyButtonState(!!readEfficiencyReview(days));
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
          <div class="eff-card-name">${r.display_name || r.agent_name}</div>
          <div class="eff-card-dept">${displayDeptName(r.display_department || r.department)} · ${s.target_table || ""}</div>
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
      opt.textContent = (a.display_name || a.name) + " (" + (a.source_platform || "Custom") + ")";
      agentSel.appendChild(opt);
    });

    // Populate department dropdown from agent list (no extra API call needed)
    const deptSel = document.getElementById("actDept");
    const depts = [...new Set(agents.map(a => a.department).filter(Boolean))].sort();
    depts.forEach(d => {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = displayDeptName(d);
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

    const status = (a.status || "idle").toLowerCase();
    const effectiveStatus = status === "locked" ? "locked" : a.active_recently ? "active" : "idle";
    const statusBadge = effectiveStatus === "locked" ? "badge-locked"
      : effectiveStatus === "active"  ? "badge-active"
      : "badge-idle";

    const lastActive = a.last_active
      ? new Date(a.last_active).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : "—";

    const isOpen = _actOpenRows.has(a.id);

    return `
      <tr class="act-agent-row ${isOpen ? "act-row-open" : ""}" onclick="toggleCallLog(${a.id})">
        <td class="act-expand-cell">${isOpen ? "▾" : "▸"}</td>
        <td style="font-weight:600">${a.display_name || a.name}</td>
        <td style="font-size:11px; font-weight:700; color:${platformColor}">${a.platform}</td>
        <td>${displayDeptName(a.display_department || a.department)}</td>
        <td><span class="badge ${statusBadge}">${effectiveStatus.toUpperCase()}</span></td>
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
  const requestedTab = new URLSearchParams(window.location.search).get("tab");
  const requestedButton = requestedTab
    ? document.querySelector(`.rpt-tab[data-tab="${CSS.escape(requestedTab)}"]`)
    : null;
  if (requestedButton && requestedTab !== activeTab) requestedButton.click();
  setTimeout(() => initDraggableReports("savings"), 100);
});

// Auto-refresh active tab every 30 seconds without interrupting filter edits.
setInterval(() => {
  if (isReportFilterActive()) return;
  loadActiveTab();
}, 30000);

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
