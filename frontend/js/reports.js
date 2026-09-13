/**
 * reports.js — CostPilot Reporting Engine UI
 *
 * Three tabs: Savings, Risk & Compliance, Departments
 * Chart.js for all visualizations.
 * Date range selector: 7D / 30D / 90D / 1Y
 */

let activeTab  = "savings";
const charts   = {};
let reportDrillDateRange = null;

const ASK_DRILL_FILTER_IDS = {
  project_id: "ctxProjectFilter",
  user_external_id: "ctxPersonFilter",
  account_id: "ctxAccountFilter",
  agent_id: "ctxAgentFilter",
  source_platform: "ctxSourceFilter",
  record_type: "ctxRecordTypeFilter",
  charged_unit: "ctxOrgFilter",
  business_purpose: "ctxPurposeFilter",
};
// audit_event_id has no corresponding Contexts-tab select (it identifies a
// single governed request, not a filterable dimension), so it must NOT go
// into ASK_DRILL_FILTER_IDS -- but it still needs to survive
// normalizeAskDrillScope's allowlist or it's silently stripped out before
// drillFromAskCostPilot ever sees it. Confirmed live: every "View activity"
// button on a per-request evidence row (built from filter_name:
// "audit_event_id" in the Supporting AI activity branch) carried this field
// all the way to askDrillScopeForEvidence's *first* normalizeAskDrillScope
// call, which dropped it immediately since it wasn't an allowed key --
// silently switching to the Contexts tab with no scope applied instead of
// jumping to the actual request.
const ASK_DRILL_KEYS = ["date_from", "date_to", "audit_event_id", ...Object.keys(ASK_DRILL_FILTER_IDS)];

// Cached API data for export
let _rptRiskEvents  = [];
let _rptDeptData    = [];
let _rptSavingsData = null;
let _rptContextData = null;
let _biData             = null;
let _biDeptRows         = [];
let _biTopWorkItemsRows = [];
let _biTopWorkItemsByRatio = false;
let _biRecommendations  = [];
let _rptRiskData = null;
let _rptGovernanceDashboard = null;
const _contextBreakdownExpanded = { project: false, person: false, agent: false };
const CONTEXT_BREAKDOWN_LIMIT = 7;
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
  // Thousand separators for anything $1+ -- a real $7,700,287.00 (a
  // closed-won/pipeline total, not a per-call cost) rendered as
  // "$7700287.00" with no way to tell at a glance whether that's seven
  // million or seven hundred thousand. toFixed(2) alone never added
  // them; toLocaleString does, without changing the 2-decimal precision
  // every existing caller of this function already expects.
  return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtNum(v) {
  if (v == null) return "—";
  return v.toLocaleString();
}

function reportTimestampDate(value) {
  if (!value) return null;
  const raw = String(value).trim();
  const hasTimeZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw);
  const parsed = new Date(hasTimeZone ? raw : `${raw}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatReportTimestamp(value, options = {}) {
  const parsed = reportTimestampDate(value);
  if (!parsed) return value ? String(value) : "—";
  return parsed.toLocaleString("en-US", {
    month: "numeric",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
    ...options,
  });
}

function reportTimeZoneLabel() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Local time";
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

// renderAskMarkdown() and renderAskBudgetFlag() live in js/ask-costpilot-render.js,
// shared with the global nav's Ask CostPilot widget (js/global-nav.js) — keep
// both UIs rendering the same response shape the same way instead of drifting.

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
  return formatReportTimestamp(iso, {
    month: "numeric", day: "numeric",
    year: undefined, second: undefined,
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

const REPORT_PARENT_TABS = {
  savings: "savings",
  departments: "savings",
  contexts: "contexts",
  activity: "contexts",
  risk: "risk",
  impact: "impact",
};

function openReportView(tab, options = {}) {
  const pane = document.getElementById(`tab-${tab}`);
  if (!pane) return;
  document.querySelectorAll(".rpt-tab").forEach(button => {
    button.classList.toggle("active", button.dataset.tab === REPORT_PARENT_TABS[tab]);
  });
  document.querySelectorAll("[data-report-utility]").forEach(button => {
    button.classList.toggle("active", button.dataset.reportUtility === tab);
  });
  document.querySelectorAll(".rpt-pane").forEach(item => item.classList.remove("active"));
  pane.classList.add("active");
  activeTab = tab;
  if (tab === "activity") initActivityTab();
  if (tab === "roi") initRoiTab();
  loadActiveTab();
  updateReportRangeSummary();
  setTimeout(() => initDraggableReports(activeTab), 50);
  if (!options.preserveUrl) {
    const url = new URL(window.location.href);
    if (tab === "savings") url.searchParams.delete("tab");
    else url.searchParams.set("tab", tab);
    history.replaceState({}, "", url);
  }
}

document.querySelectorAll(".rpt-tab").forEach(btn => {
  btn.addEventListener("click", () => openReportView(btn.dataset.tab));
});

// ── Date preset picker ────────────────────────────────────────────────────────

// <input type="date">.value is always a bare "YYYY-MM-DD" string.
// new Date("2026-09-01") parses that as UTC midnight, not local midnight
// -- in any timezone behind UTC (most of the US), that instant falls on
// the previous local calendar day, so every downstream local-time
// display (toLocaleDateString, etc.) silently shows Aug 31 for a
// picker that says Sep 1. Confirmed live 2026-09-11: a custom range of
// Sep 1 - Sep 11 rendered as "Aug 31 - Sep 10". Splitting the string and
// constructing the Date from y/m/d components uses the local-time
// constructor instead, which doesn't have this offset.
function parseDateInputLocal(value) {
  const [y, m, d] = value.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function resolveDatePreset(preset) {
  const now   = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
  function addMonths(d, n) { const r = new Date(d); r.setMonth(r.getMonth() + n); return r; }

  let from, to;

  if (preset === "custom") {
    const f = document.getElementById("rptDateFrom").value;
    const t = document.getElementById("rptDateTo").value;
    from = f ? parseDateInputLocal(f) : addDays(today, -30);
    to   = t ? addDays(parseDateInputLocal(t), 1) : addDays(today, 1);
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
  } else if (preset === "last_7")   { from = addDays(today, -6);   to = addDays(today, 1); }
  else if (preset === "last_14")    { from = addDays(today, -13);  to = addDays(today, 1); }
  else if (preset === "last_60")    { from = addDays(today, -59);  to = addDays(today, 1); }
  else if (preset === "last_90")    { from = addDays(today, -89);  to = addDays(today, 1); }
  else if (preset === "last_120")   { from = addDays(today, -119); to = addDays(today, 1); }
  else if (preset === "last_6m")    { from = addMonths(today, -6); to = addDays(today, 1); }
  else if (preset === "last_365")   { from = addDays(today, -364); to = addDays(today, 1); }
  else { // last_30 (default)
    from = addDays(today, -29); to = addDays(today, 1);
  }

  const days = Math.max(1, Math.ceil((to - from) / 86400000));
  return { date_from: from.toISOString(), date_to: to.toISOString(), days };
}

// range.date_from/date_to is a bare "YYYY-MM-DD" string when it came from
// a drill-down URL's date_from/date_to query params (see the reportDrillDateRange
// assignment below), but a full ISO datetime string (already UTC-instant-
// correct, safe for plain `new Date()`) everywhere else -- e.g.
// resolveDatePreset's own return value. Only the bare-date-string case
// hits the UTC-midnight parsing bug parseDateInputLocal exists to avoid.
function parseRangeBoundary(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? parseDateInputLocal(value) : new Date(value);
}

function updateReportRangeSummary() {
  const host = document.getElementById("rptRangeSummary");
  if (!host) return;
  const range = getActiveDateRange();
  const from = parseRangeBoundary(range.date_from);
  const exclusiveTo = parseRangeBoundary(range.date_to);
  exclusiveTo.setDate(exclusiveTo.getDate() - 1);
  const format = value => value.toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric",
  });
  host.textContent = `${format(from)} – ${format(exclusiveTo)} · ${range.days} day${range.days === 1 ? "" : "s"}`;
}

function getActiveDateRange() {
  if (reportDrillDateRange) return { ...reportDrillDateRange };
  const preset = (document.getElementById("rptDatePreset") || {}).value || "last_30";
  return resolveDatePreset(preset);
}

function onDatePresetChange() {
  reportDrillDateRange = null;
  const preset = document.getElementById("rptDatePreset").value;
  document.getElementById("rptCustomDates").style.display = preset === "custom" ? "flex" : "none";
  updateReportRangeSummary();
  if (preset !== "custom") {
    if (activeTab === "contexts") syncAskDrillQuery();
    loadActiveTab();
  }
}

function onCustomReportDateChange() {
  reportDrillDateRange = null;
  updateReportRangeSummary();
  if (activeTab === "contexts") syncAskDrillQuery();
  loadActiveTab();
}

function loadActiveTab() {
  if (activeTab === "savings")     loadSavings();
  if (activeTab === "risk")        loadRisk();
  if (activeTab === "contexts")    { initExplorerControls(); loadBusinessContexts(); }
  if (activeTab === "departments") loadDepartments();
  if (activeTab === "activity")    loadAgentActivity();
  if (activeTab === "impact")      loadBusinessImpact();
  if (activeTab === "efficiency") {
    restoreEfficiencyReviewForSelectedDays();
    refreshAskCostPilotExperience();
  }
  // efficiency tab remains on-demand. Restore cached reviews, but do not rerun analysis automatically.
}

// 5-label set, matching business-profile.html's BP_EVIDENCE_LABELS/
// .bp-evidence-tag precedent (the correct per-metric pattern) rather
// than the 3-label subset this tab shipped with originally.
const BI_EVIDENCE_LABELS = {
  measured: "Measured",
  associated: "Associated",
  estimated: "Estimated",
  early_signal: "Early Signal",
  meaningful: "Meaningful",
  executive_eligible: "Executive-Eligible",
};

function biEvidenceTag(elementId, evidenceLabel) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (!evidenceLabel) { el.hidden = true; return; }
  el.hidden = false;
  el.className = `bi-evidence-tag ${evidenceLabel}`;
  el.textContent = BI_EVIDENCE_LABELS[evidenceLabel] || evidenceLabel;
}

function biTrendText(pct) {
  if (pct === null || pct === undefined) return "—";
  const arrow = pct > 0 ? "▲" : pct < 0 ? "▼" : "—";
  const cls = pct > 0 ? "down" : pct < 0 ? "up" : ""; // higher cost-per-outcome is worse, not better
  return `<span class="bi-trend ${cls}">${arrow} ${Math.abs(pct)}% vs prior 30 days</span>`;
}

// Deterministic sentence built from the same fields the KPI cards
// already render -- not free-form LLM math, so every number in it is
// already trusted by the time this template runs.
function biNarrativeSummary(data) {
  const parts = [];
  if (data.opportunities_won > 0) {
    parts.push(
      `AI activity was associated with ${fmtUsd(data.closed_won_value_usd)} in closed-won value across `
      + `${fmtNum(data.opportunities_won)} won opportunit${data.opportunities_won === 1 ? "y" : "ies"} in the trailing 30 days.`
    );
  }
  if (data.support_cases_total > 0) {
    parts.push(
      `Support resolved ${fmtNum(data.support_cases_resolved)} of ${fmtNum(data.support_cases_total)} AI-touched support items.`
    );
  }
  if (data.outcome_coverage_pct != null) {
    parts.push(`Outcome coverage is ${data.outcome_coverage_pct}%.`);
  }
  if (!parts.length) return "Not enough outcome data yet to summarize.";
  return parts.join(" ");
}

async function loadBusinessImpact() {
  const data = await apiGet(reportScopedPath("/api/dashboard/business-impact"));
  _biData = data;

  document.getElementById("biNoDataBanner").hidden = !!data.has_outcome_data;
  document.getElementById("biContent").style.display = data.has_outcome_data ? "" : "none";
  if (!data.has_outcome_data) return;

  document.getElementById("biSummary").textContent = biNarrativeSummary(data);

  const evidenceByKpi = data.evidence_by_kpi || {};

  // Executive KPI row
  setKpi("bi-assoc-value", fmtUsd(data.closed_won_value_usd));
  document.getElementById("bi-assoc-value-sub").textContent = "closed-won + resolved support value";
  biEvidenceTag("bi-evidence-successful", evidenceByKpi.cost_per_won_opportunity_usd);
  setKpi("bi-successful-outcomes", fmtNum((data.opportunities_won || 0) + (data.support_cases_resolved || 0)));
  setKpi("bi-investment-successful", fmtUsd((data.won_ai_investment_usd || 0) + (data.support_resolved_ai_investment_usd || 0)));
  setKpi("bi-investment-unsuccessful", fmtUsd((data.lost_ai_investment_usd || 0) + (data.support_unresolved_ai_investment_usd || 0)));

  biEvidenceTag("bi-evidence-cost-per-outcome", evidenceByKpi.cost_per_successful_outcome_usd);
  setKpi("bi-cost-per-outcome", fmtUsd(data.cost_per_successful_outcome_usd));
  document.getElementById("bi-cost-per-outcome-trend").innerHTML =
    biTrendText(data.trend_pct_change?.cost_per_successful_outcome_usd);

  biEvidenceTag("bi-evidence-coverage", evidenceByKpi.outcome_coverage_pct);
  setKpi("bi-coverage", data.outcome_coverage_pct != null ? `${data.outcome_coverage_pct}%` : "—");

  // Sales Impact -- Won vs Lost vs Open
  document.getElementById("biWonLostBody").innerHTML = `
    <tr><td>Count</td><td>${fmtNum(data.opportunities_won)}</td><td>${fmtNum(data.opportunities_lost)}</td><td>${fmtNum(data.opportunities_open)}</td></tr>
    <tr><td>AI Investment</td><td>${fmtUsd(data.won_ai_investment_usd)}</td><td>${fmtUsd(data.lost_ai_investment_usd)}</td><td>—</td></tr>
    <tr><td>Associated Value</td><td>${fmtUsd(data.closed_won_value_usd)}</td><td>—</td><td>${fmtUsd(data.pipeline_value_usd)} pipeline</td></tr>
    <tr><td>Cost per Opportunity</td><td>${fmtUsd(data.cost_per_won_opportunity_usd)}</td><td colspan="2">${fmtUsd(data.avg_ai_investment_per_opportunity_usd)} avg across all</td></tr>
  `;

  // Support Impact -- Resolved vs Unresolved
  document.getElementById("biSupportBody").innerHTML = `
    <tr><td>Count</td><td>${fmtNum(data.support_cases_resolved)}</td><td>${fmtNum(data.support_cases_unresolved)}</td></tr>
    <tr><td>AI Investment</td><td>${fmtUsd(data.support_resolved_ai_investment_usd)}</td><td>${fmtUsd(data.support_unresolved_ai_investment_usd)}</td></tr>
  `;
  document.getElementById("bi-cost-per-resolution").textContent = fmtUsd(data.support_cost_per_resolution_usd);
  document.getElementById("bi-cost-per-resolution-trend").innerHTML =
    biTrendText(data.trend_pct_change?.support_cost_per_resolution_usd);
  biEvidenceTag("bi-evidence-resolution", evidenceByKpi.support_cost_per_resolution_usd);

  loadBusinessImpactByDepartment();
  loadBusinessImpactTopWorkItems();
  loadBusinessImpactRecommendations();
}

async function loadBusinessImpactByDepartment() {
  const body = document.getElementById("biDepartmentBody");
  if (!body) return;
  body.innerHTML = `<tr><td colspan="8">Loading…</td></tr>`;
  try {
    const data = await apiGet(reportScopedPath("/api/dashboard/business-impact/by-department"));
    const rows = data.rows || [];
    _biDeptRows = rows;
    body.innerHTML = rows.length
      ? rows.map((row, i) => `
        <tr>
          <td class="bi-rank">${i + 1}</td>
          <td>${escapeHtml(row.department)}</td>
          <td>${fmtUsd(row.ai_investment_usd)}</td>
          <td>${fmtNum(row.opportunities_won)}</td>
          <td>${fmtNum(row.opportunities_lost)}</td>
          <td>${fmtNum(row.opportunities_open)}</td>
          <td>${fmtUsd(row.closed_won_value_usd)}</td>
          <td>${fmtUsd(row.cost_per_won_opportunity_usd)}</td>
        </tr>`).join("")
      : `<tr><td colspan="8">No department-level outcome data yet.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="8">Could not load: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function fmtCostRatioPct(ratio) {
  const pct = ratio * 100;
  if (pct === 0) return "0%";
  // Demo/low-spend data can produce ratios well under 0.01% -- fixed
  // 2-decimal formatting would print "0.00%" for every row and hide the
  // ranking entirely, so scale precision to the magnitude instead.
  if (pct < 0.01) return `${pct.toPrecision(2)}%`;
  return `${pct.toFixed(2)}%`;
}

async function loadBusinessImpactTopWorkItems() {
  const body = document.getElementById("biTopWorkItemsBody");
  const head = document.getElementById("biTopWorkItemsHead");
  if (!body) return;
  const outcomeStatus = document.getElementById("biTopWorkItemsFilter")?.value || "";
  const rankBy = document.getElementById("biTopWorkItemsRankBy")?.value || "spend";
  const byRatio = rankBy === "cost_ratio";
  if (head) {
    head.innerHTML = byRatio
      ? `<tr><th></th><th>WorkItem</th><th>AI Investment</th><th>Outcome Value</th><th>Cost / Value</th><th></th></tr>`
      : `<tr><th></th><th>WorkItem</th><th>AI Investment</th><th>Requests</th><th></th></tr>`;
  }
  const colspan = byRatio ? 6 : 5;
  body.innerHTML = `<tr><td colspan="${colspan}">Loading…</td></tr>`;
  try {
    const params = new URLSearchParams({ limit: "10", rank_by: rankBy });
    if (outcomeStatus) params.set("outcome_status", outcomeStatus);
    const path = reportScopedPath(`/api/dashboard/business-impact/top-work-items`);
    const url = `${path}${path.includes("?") ? "&" : "?"}${params.toString()}`;
    const data = await apiGet(url);
    const rows = data.rows || [];
    _biTopWorkItemsRows = rows;
    _biTopWorkItemsByRatio = byRatio;
    body.innerHTML = rows.length
      ? rows.map((row, i) => {
          const href = EXPLORER_DIMENSION_CONFIG.work_item.profile(row.work_item_id);
          const middleCells = byRatio
            ? `<td>${fmtUsd(row.ai_spend_usd)}</td><td>${fmtUsd(row.outcome_value_usd)}</td><td>${fmtCostRatioPct(row.cost_ratio)}</td>`
            : `<td>${fmtUsd(row.ai_spend_usd)}</td><td>${fmtNum(row.ai_requests)}</td>`;
          return `<tr>
            <td class="bi-rank">${i + 1}</td>
            <td>${escapeHtml(row.label)}</td>
            ${middleCells}
            <td><a href="${href}" class="context-view-toggle">View Profile →</a></td>
          </tr>`;
        }).join("")
      : `<tr><td colspan="${colspan}">No matching AI activity.</td></tr>`;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="${colspan}">Could not load: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function loadBusinessImpactRecommendations() {
  const wrap = document.getElementById("biRecommendations");
  if (!wrap) return;
  wrap.innerHTML = "Loading…";
  try {
    const data = await apiGet(reportScopedPath("/api/dashboard/recommendations"));
    const recs = data.recommendations || [];
    _biRecommendations = recs;
    wrap.innerHTML = recs.length
      ? recs.slice(0, 6).map(rec => `
        <div class="bi-rec-card">
          <div class="bi-rec-head">
            <span class="bi-rec-title">${escapeHtml(rec.title)}</span>
            <span class="bi-rec-priority ${escapeHtml(rec.priority || "low")}">${escapeHtml(rec.priority || "low")}</span>
          </div>
          <div class="bi-rec-body">${escapeHtml(rec.why_it_matters || rec.current_state || "")}</div>
          <div class="bi-rec-action">→ ${escapeHtml(rec.recommended_action || "")}</div>
          ${rec.impact_type === "savings_usd" && rec.estimated_impact != null
            ? `<div class="bi-rec-impact">Potential savings: ${fmtUsd(rec.estimated_impact)}/mo</div>` : ""}
        </div>
      `).join("")
      : `<div class="bi-note">No recommendations right now.</div>`;
  } catch (err) {
    wrap.innerHTML = `<div class="bi-note">Could not load recommendations: ${escapeHtml(err.message)}</div>`;
  }
}

// Inlined, not an <img src="..."> to the real asset -- confirmed live: an
// <img> logo hadn't finished its network load by the time printSection()
// called window.print(), the same "must not depend on something loading
// asynchronously" class of bug the chart-image conversion already had to
// solve (there, by capturing an already-rendered canvas; here, by never
// making a network request in the first place). Also recolored: the real
// asset's wordmark uses a near-white fill (#e9eef7) meant for the app's
// dark header -- invisible on this report's white background -- so the
// wordmark paths use navy (matching the compass mark) instead.
const REPORT_LOGO_SVG = `<svg class="report-logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 930 150" role="img" aria-label="CostPilot">
  <g transform="translate(32 22)">
    <circle fill="none" stroke="#07336f" stroke-width="9" cx="55" cy="55" r="47"/>
    <circle fill="none" stroke="#0a2a5b" stroke-width="5" opacity="0.55" cx="55" cy="55" r="35"/>
    <rect fill="#07336f" x="35" y="58" width="15" height="34" rx="1"/>
    <rect fill="#07336f" x="61" y="38" width="15" height="54" rx="1"/>
    <rect fill="#07336f" x="87" y="15" width="15" height="77" rx="1"/>
    <path fill="#25c4b5" d="M77 46 123 32 98 90Z"/>
    <path fill="#07336f" d="M88 15h14v38H88z"/>
  </g>
  <g transform="translate(178 36)">
    <path fill="#07336f" d="M48 80c-27 0-45-17-45-41S21 0 48 0c23 0 39 12 43 32H67c-3-8-10-13-20-13-13 0-22 8-22 20s9 21 22 21c10 0 18-5 21-14h24C88 67 72 80 48 80Z"/>
    <path fill="#07336f" d="M139 80c-26 0-45-17-45-40s19-40 45-40 45 17 45 40-19 40-45 40Zm0-20c13 0 23-8 23-20s-10-20-23-20-23 8-23 20 10 20 23 20Z"/>
    <path fill="#07336f" d="M232 80c-25 0-42-11-44-31h23c2 8 10 12 22 12 10 0 16-3 16-9 0-7-8-9-23-12-18-4-35-9-35-29 0-18 15-30 39-30 23 0 39 11 41 30h-23c-2-7-8-11-18-11-9 0-15 3-15 9 0 6 8 8 22 11 19 4 37 9 37 30 0 18-16 30-42 30Z"/>
    <path fill="#07336f" d="M299 78V21h-30V2h82v19h-30v57Z"/>
    <path fill="#25c4b5" d="M359 78V2h47c21 0 35 13 35 32s-14 32-35 32h-25v12Zm22-31h22c10 0 16-5 16-13s-6-13-16-13h-22Z"/>
    <path fill="#25c4b5" d="M452 78V2h22v76Z"/>
    <path fill="#25c4b5" d="M490 78V2h22v57h44v19Z"/>
    <path fill="#25c4b5" d="M604 80c-26 0-45-17-45-40s19-40 45-40 45 17 45 40-19 40-45 40Zm0-20c13 0 23-8 23-20s-10-20-23-20-23 8-23 20 10 20 23 20Z"/>
    <path fill="#25c4b5" d="M679 78V21h-30V2h82v19h-30v57Z"/>
  </g>
</svg>`;

// Shared banner for every .report-doc (Business Impact, Savings, Governance
// & Risk, Departments) -- one place so the brand mark/title/meta layout
// can't drift between reports the way four independently hand-written
// headers eventually would.
function reportDocHeaderHtml(title, generatedAt) {
  return `
    <header class="report-header">
      ${REPORT_LOGO_SVG}
      <h1 class="report-title">${escapeHtml(title)}</h1>
      <div class="report-meta">
        <span>${escapeHtml(askCostPilotWorkspaceLabel())}</span>
        <span>${askCostPilotDateLabel()}</span>
        <span>Generated ${escapeHtml(generatedAt)}</span>
      </div>
    </header>`;
}

// Premium banner variant for composite reports (Support Cost Briefing and
// future story-driven reports) -- kept separate from reportDocHeaderHtml
// rather than changed in place, so the plainer existing reports (Business
// Impact, Savings, Governance & Risk, Departments) are untouched.
function reportPremiumHeaderHtml(title, periodLabel, generatedAt) {
  return `
    <header class="report-header rpt-header-premium">
      <div class="rpt-header-top">
        ${REPORT_LOGO_SVG}
        <span class="rpt-header-confidential">Confidential</span>
      </div>
      <div class="rpt-header-eyebrow">Report</div>
      <h1 class="report-title">${escapeHtml(title)}</h1>
      <div class="rpt-header-tagline">AI cost governance, told as a story your leadership can act on.</div>
      <div class="report-meta">
        <span>${escapeHtml(askCostPilotWorkspaceLabel())}</span>
        <span>${escapeHtml(periodLabel || askCostPilotDateLabel())}</span>
        <span>Generated ${escapeHtml(generatedAt)}</span>
      </div>
    </header>`;
}

// ── Business Impact — printable report ──────────────────────────────────────
// A purpose-built report document, not a clone of the live interactive tab
// (printSection()'s usual DOM-clone approach would also carry the rank-by/
// filter dropdowns and any "Loading…" placeholder still on screen). Every
// number below is read from _biData/_biDeptRows/_biTopWorkItemsRows/
// _biRecommendations -- the exact same already-fetched, already-trusted data
// the live tab renders -- never recomputed here.
function biReportEvidenceLegend() {
  return Object.entries(BI_EVIDENCE_LABELS).map(([key, label]) =>
    `<span class="bi-evidence-tag ${key}">${escapeHtml(label)}</span>`
  ).join(" ");
}

function renderBusinessImpactReportHtml() {
  const data = _biData;
  if (!data || !data.has_outcome_data) {
    return `<div class="report-doc"><p>No outcome data available for this workspace/period yet.</p></div>`;
  }
  const evidenceByKpi = data.evidence_by_kpi || {};
  const generatedAt = new Date().toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
  const successfulOutcomes = (data.opportunities_won || 0) + (data.support_cases_resolved || 0);
  const investmentSuccessful = (data.won_ai_investment_usd || 0) + (data.support_resolved_ai_investment_usd || 0);
  const investmentUnsuccessful = (data.lost_ai_investment_usd || 0) + (data.support_unresolved_ai_investment_usd || 0);

  const kpiCard = (label, value, evidenceLabel, sub) => `
    <div class="report-kpi">
      <div class="report-kpi-label-row">
        <span class="report-kpi-label">${escapeHtml(label)}</span>
        ${evidenceLabel ? `<span class="bi-evidence-tag ${evidenceLabel}">${escapeHtml(BI_EVIDENCE_LABELS[evidenceLabel] || evidenceLabel)}</span>` : ""}
      </div>
      <div class="report-kpi-value">${value}</div>
      ${sub ? `<div class="report-kpi-sub">${sub}</div>` : ""}
    </div>`;

  const deptRows = _biDeptRows.length
    ? _biDeptRows.map((row, i) => `
        <tr>
          <td class="bi-rank">${i + 1}</td>
          <td>${escapeHtml(row.department)}</td>
          <td>${fmtUsd(row.ai_investment_usd)}</td>
          <td>${fmtNum(row.opportunities_won)}</td>
          <td>${fmtNum(row.opportunities_lost)}</td>
          <td>${fmtNum(row.opportunities_open)}</td>
          <td>${fmtUsd(row.closed_won_value_usd)}</td>
          <td>${fmtUsd(row.cost_per_won_opportunity_usd)}</td>
        </tr>`).join("")
    : `<tr><td colspan="8">No department-level outcome data.</td></tr>`;

  const workItemRows = _biTopWorkItemsRows.length
    ? _biTopWorkItemsRows.map((row, i) => `
        <tr>
          <td class="bi-rank">${i + 1}</td>
          <td>${escapeHtml(row.label)}</td>
          <td>${fmtUsd(row.ai_spend_usd)}</td>
          <td>${_biTopWorkItemsByRatio ? `${fmtUsd(row.outcome_value_usd)}</td><td>${fmtCostRatioPct(row.cost_ratio)}` : fmtNum(row.ai_requests)}</td>
        </tr>`).join("")
    : `<tr><td colspan="4">No matching AI activity.</td></tr>`;

  const recCards = _biRecommendations.length
    ? _biRecommendations.slice(0, 6).map(rec => `
        <div class="bi-rec-card" style="break-inside:avoid">
          <div class="bi-rec-head">
            <span class="bi-rec-title">${escapeHtml(rec.title)}</span>
            <span class="bi-rec-priority ${escapeHtml(rec.priority || "low")}">${escapeHtml(rec.priority || "low")}</span>
          </div>
          <div class="bi-rec-body">${escapeHtml(rec.why_it_matters || rec.current_state || "")}</div>
          <div class="bi-rec-action">→ ${escapeHtml(rec.recommended_action || "")}</div>
          ${rec.impact_type === "savings_usd" && rec.estimated_impact != null
            ? `<div class="bi-rec-impact">Potential savings: ${fmtUsd(rec.estimated_impact)}/mo</div>` : ""}
        </div>`).join("")
    : `<div class="bi-note">No recommendations right now.</div>`;

  return `
    <div class="report-doc">
      ${reportDocHeaderHtml("Business Impact Report", generatedAt)}

      <section class="report-section">
        <h2 class="report-section-title">Executive Brief</h2>
        <p class="bi-summary">${escapeHtml(biNarrativeSummary(data))}</p>
        <div class="report-kpi-row">
          ${kpiCard("Associated Business Value", fmtUsd(data.closed_won_value_usd), null, "closed-won + resolved support value")}
          ${kpiCard("Successful Outcomes", fmtNum(successfulOutcomes), evidenceByKpi.cost_per_won_opportunity_usd, "won opportunities + resolved support work")}
          ${kpiCard("AI Investment — Successful", fmtUsd(investmentSuccessful), null, "won + resolved")}
          ${kpiCard("AI Investment — Unsuccessful", fmtUsd(investmentUnsuccessful), null, "lost + unresolved")}
          ${kpiCard("Cost per Successful Outcome", fmtUsd(data.cost_per_successful_outcome_usd), evidenceByKpi.cost_per_successful_outcome_usd)}
          ${kpiCard("Outcome Coverage", data.outcome_coverage_pct != null ? `${data.outcome_coverage_pct}%` : "—", evidenceByKpi.outcome_coverage_pct, "of AI-touched work with a known outcome")}
        </div>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Sales Impact — Won vs. Lost</h2>
        <table class="rpt-context-table">
          <thead><tr><th></th><th>Won</th><th>Lost</th><th>Open</th></tr></thead>
          <tbody>
            <tr><td>Count</td><td>${fmtNum(data.opportunities_won)}</td><td>${fmtNum(data.opportunities_lost)}</td><td>${fmtNum(data.opportunities_open)}</td></tr>
            <tr><td>AI Investment</td><td>${fmtUsd(data.won_ai_investment_usd)}</td><td>${fmtUsd(data.lost_ai_investment_usd)}</td><td>—</td></tr>
            <tr><td>Associated Value</td><td>${fmtUsd(data.closed_won_value_usd)}</td><td>—</td><td>${fmtUsd(data.pipeline_value_usd)} pipeline</td></tr>
            <tr><td>Cost per Opportunity</td><td>${fmtUsd(data.cost_per_won_opportunity_usd)}</td><td colspan="2">${fmtUsd(data.avg_ai_investment_per_opportunity_usd)} avg across all</td></tr>
          </tbody>
        </table>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Support Impact — Resolved vs. Unresolved</h2>
        <table class="rpt-context-table">
          <thead><tr><th></th><th>Resolved</th><th>Unresolved</th></tr></thead>
          <tbody>
            <tr><td>Count</td><td>${fmtNum(data.support_cases_resolved)}</td><td>${fmtNum(data.support_cases_unresolved)}</td></tr>
            <tr><td>AI Investment</td><td>${fmtUsd(data.support_resolved_ai_investment_usd)}</td><td>${fmtUsd(data.support_unresolved_ai_investment_usd)}</td></tr>
          </tbody>
        </table>
        <div class="bi-note">
          <strong>Cost per Resolution</strong> ${fmtUsd(data.support_cost_per_resolution_usd)}
          <span class="bi-evidence-tag ${escapeHtml(evidenceByKpi.support_cost_per_resolution_usd || "")}">${escapeHtml(BI_EVIDENCE_LABELS[evidenceByKpi.support_cost_per_resolution_usd] || "")}</span>
        </div>
      </section>

      <section class="report-section">
        <h2 class="report-section-title">Business Impact by Department</h2>
        <table class="rpt-context-table">
          <thead><tr><th></th><th>Department</th><th>AI Investment</th><th>Won</th><th>Lost</th><th>Open</th><th>Closed-Won Value</th><th>Cost / Won Opp</th></tr></thead>
          <tbody>${deptRows}</tbody>
        </table>
      </section>

      <section class="report-section">
        <h2 class="report-section-title">Top WorkItems by AI Investment</h2>
        <table class="rpt-context-table">
          <thead><tr><th></th><th>WorkItem</th><th>AI Investment</th><th>${_biTopWorkItemsByRatio ? "Outcome Value</th><th>Cost / Value" : "Requests"}</th></tr></thead>
          <tbody>${workItemRows}</tbody>
        </table>
      </section>

      <section class="report-section">
        <h2 class="report-section-title">CostPilot Recommendations</h2>
        <div class="bi-rec-grid">${recCards}</div>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Evidence &amp; Methodology</h2>
        <div class="bi-note" style="margin-bottom:10px">${biReportEvidenceLegend()}</div>
        <div class="bi-note">
          <strong>Associated, not caused:</strong> these figures show AI activity that occurred on work which
          later reached a known outcome — not evidence the AI activity caused that outcome. Trend arrows compare
          the trailing 30 days against the prior 30. CostPilot reports consumption and attribution only; it does
          not score employee productivity or infer business outcomes.
        </div>
      </section>

      <footer class="report-footer">CostPilot — Business Impact Report — ${escapeHtml(askCostPilotWorkspaceLabel())}</footer>
    </div>`;
}

function exportBusinessImpactReport() {
  if (!_biData || !_biData.has_outcome_data) {
    alert("No outcome data loaded yet — open the Business Impact tab first.");
    return;
  }
  const container = document.getElementById("biReportDoc");
  if (!container) return;
  container.innerHTML = renderBusinessImpactReportHtml();
  printSection("biReportDoc", "CostPilot — Business Impact Report");
}

// ── Support AI Cost Increase Analysis — first composite briefing report ────
// Phase 5: unlike every report above (one chart, one data source), this
// pulls FOUR different views (trend, agent ranking, tier-mix composition,
// resolved/unresolved) from one backend call (GET /api/dashboard/support-
// briefing) into a single document -- see that endpoint's own docstring
// for why it's a standalone aggregation rather than four separate
// "Generate Report" replays stitched together by hand.
const REPORT_CHART_TEXT_COLOR = "#1a2733";
const REPORT_CHART_GRID_COLOR = "#e2e6ea";
const REPORT_CHART_PALETTE = ["#25c4b5", "#5a8dee", "#f5a623", "#e8618c", "#8c6fe0", "#4fb477"];

// ── Composite-report design system ──────────────────────────────────────────
// Built for the "boardroom-ready" polish pass (2026-09-13, following a real
// design-mockup review) -- a small, reusable set of icon badges, KPI cards,
// a scorecard callout, and CSS-drawn stacked bars, so every FUTURE composite
// report (not just Support Cost Briefing) gets this look for free instead
// of each one hand-rolling its own. Icons are plain SVG primitives (rects/
// circles/polylines, no path-data glyphs) specifically so they render
// correctly without depending on getting bezier curves right by hand.
const REPORT_ICON_COLORS = {
  blue: "#2f6fed", green: "#1a9c5c", orange: "#e08a1f", purple: "#7c5cff", red: "#d94f4f",
};
const REPORT_ICONS = {
  barChart: `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="4" y="12" width="4" height="8" rx="1" fill="currentColor"/><rect x="10" y="7" width="4" height="13" rx="1" fill="currentColor"/><rect x="16" y="3" width="4" height="17" rx="1" fill="currentColor"/></svg>`,
  dollarCircle: `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/><text x="12" y="16.5" font-size="11" font-weight="700" text-anchor="middle" fill="currentColor" font-family="sans-serif">$</text></svg>`,
  savingsArrow: `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/><path d="M8 9v6h6M8 15l8-8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  target: `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="5" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>`,
  lightbulb: `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="10" r="6" stroke="currentColor" stroke-width="2"/><rect x="9.5" y="16" width="5" height="3" rx="1" fill="currentColor"/><line x1="10.5" y1="20" x2="13.5" y2="20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
  checkTarget: `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/><polyline points="8,12.5 10.7,15 16,9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  document: `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6 3h8l4 4v14H6z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M14 3v4h4" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><line x1="9" y1="12" x2="15" y2="12" stroke="currentColor" stroke-width="1.5"/><line x1="9" y1="15.5" x2="15" y2="15.5" stroke="currentColor" stroke-width="1.5"/></svg>`,
};

function reportIconBadge(icon, colorKey) {
  const color = REPORT_ICON_COLORS[colorKey] || REPORT_ICON_COLORS.blue;
  return `<span class="rpt-icon-badge" style="background:${color}1a;color:${color}">${REPORT_ICONS[icon] || ""}</span>`;
}

// A richer KPI card than reportDocHeaderHtml's plain siblings elsewhere in
// this file (Business Impact, Savings, etc. keep their existing look --
// this is deliberately scoped to composite reports only, not a global
// restyle). delta is {text, positive} or null.
function reportKpiCardPremium(icon, colorKey, label, value, sub, delta) {
  const deltaHtml = delta
    ? `<span class="rpt-kpi-delta ${delta.positive ? "up" : "down"}">${delta.positive ? "▲" : "▼"} ${escapeHtml(delta.text)}</span>`
    : "";
  return `
    <div class="rpt-kpi-premium">
      ${reportIconBadge(icon, colorKey)}
      <div class="rpt-kpi-premium-label">${escapeHtml(label)}</div>
      <div class="rpt-kpi-premium-value">${value}</div>
      ${deltaHtml || (sub ? `<div class="rpt-kpi-premium-sub">${sub}</div>` : "")}
    </div>`;
}

function reportNumberedList(items, renderItem) {
  return `<ol class="rpt-numbered-list">${items.map((item, i) => `
    <li><span class="rpt-numbered-badge">${i + 1}</span><div class="rpt-numbered-body">${renderItem(item, i)}</div></li>`).join("")}</ol>`;
}

// Generic ad-hoc chart -> image capture, same reasoning as askReportChartImg
// in ask-costpilot-render.js (print output needs a bitmap, not a live
// canvas) -- text/grid colors are set explicitly rather than inherited
// from chart-theme.js's dark-dashboard defaults, since this always
// renders on a white report page regardless of the app's active theme
// (see this session's chart-legibility fix for why that distinction
// matters).
function reportChartImg(config, width, height) {
  if (typeof Chart === "undefined") return "";
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  document.body.appendChild(canvas);
  let dataUrl = "";
  try {
    const chart = new Chart(canvas.getContext("2d"), config);
    chart.resize();
    chart.render();
    dataUrl = canvas.toDataURL("image/png");
    chart.destroy();
  } catch (_err) { /* leave dataUrl empty -- report still renders without it */ }
  canvas.remove();
  if (!dataUrl) return "";
  return `<figure class="report-chart-figure"><img src="${dataUrl}" style="width:100%;height:auto" /></figure>`;
}

function supportBriefingSpendTrendChart(spendTrend) {
  if (!spendTrend || spendTrend.length < 2) return "";
  return reportChartImg({
    type: "line",
    data: {
      labels: spendTrend.map(r => r.date.slice(5)),
      datasets: [{
        label: "Daily support AI spend", data: spendTrend.map(r => r.spend_usd),
        borderColor: REPORT_CHART_PALETTE[0], backgroundColor: "rgba(37,196,181,0.12)",
        fill: true, tension: 0.25, pointRadius: 0, borderWidth: 2,
      }],
    },
    options: {
      responsive: false, animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: REPORT_CHART_TEXT_COLOR, maxTicksLimit: 10 }, grid: { color: REPORT_CHART_GRID_COLOR } },
        y: { beginAtZero: true, ticks: { color: REPORT_CHART_TEXT_COLOR }, grid: { color: REPORT_CHART_GRID_COLOR } },
      },
    },
  }, 700, 260);
}

function supportBriefingAgentChart(topAgents) {
  if (!topAgents || topAgents.length < 2) return "";
  return reportChartImg({
    type: "bar",
    data: {
      labels: topAgents.map(r => r.agent),
      datasets: [{ data: topAgents.map(r => r.spend_usd), backgroundColor: REPORT_CHART_PALETTE[1] }],
    },
    options: {
      indexAxis: "y", responsive: false, animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { beginAtZero: true, ticks: { color: REPORT_CHART_TEXT_COLOR }, grid: { color: REPORT_CHART_GRID_COLOR } },
        y: { ticks: { color: REPORT_CHART_TEXT_COLOR }, grid: { color: REPORT_CHART_GRID_COLOR } },
      },
    },
  }, 700, Math.max(220, topAgents.length * 32));
}

// CSS-drawn stacked bars for Model Mix, replacing the earlier Chart.js
// image version --
// sharper at print resolution and lets each segment carry a real
// percentage label instead of relying on a legend to decode color.
const MODEL_TIER_ORDER = ["Scout", "Analyst", "Advisor", "Strategist"];
const MODEL_TIER_COLORS = { Scout: "#4fb477", Analyst: "#5a8dee", Advisor: "#f5a623", Strategist: "#e8618c" };

function supportBriefingModelMixBars(modelMixByAgent) {
  if (!modelMixByAgent || !modelMixByAgent.length) return "";
  const legend = MODEL_TIER_ORDER.map(tier => `
    <span class="rpt-stackbar-legend-item"><span class="rpt-stackbar-swatch" style="background:${MODEL_TIER_COLORS[tier]}"></span>${tier}</span>`).join("");
  const rows = modelMixByAgent.map(r => {
    const total = MODEL_TIER_ORDER.reduce((sum, t) => sum + (r[t] || 0), 0) || 1;
    const segments = MODEL_TIER_ORDER.filter(t => (r[t] || 0) > 0).map(t => {
      const pct = (r[t] || 0) / total * 100;
      return `<span class="rpt-stackbar-seg" style="width:${pct.toFixed(1)}%;background:${MODEL_TIER_COLORS[t]}" title="${escapeHtml(t)}: ${pct.toFixed(0)}%">${pct >= 12 ? `${pct.toFixed(0)}%` : ""}</span>`;
    }).join("");
    return `
      <div class="rpt-stackbar-row">
        <div class="rpt-stackbar-label">${escapeHtml(r.agent)}</div>
        <div class="rpt-stackbar-track">${segments}</div>
      </div>`;
  }).join("");
  return `<div class="rpt-stackbar-chart"><div class="rpt-stackbar-legend">${legend}</div>${rows}</div>`;
}

function supportBriefingOutcomeChart(resolved, unresolved) {
  if (!resolved && !unresolved) return "";
  return reportChartImg({
    type: "doughnut",
    data: {
      labels: ["Resolved", "Unresolved"],
      datasets: [{ data: [resolved, unresolved], backgroundColor: [REPORT_CHART_PALETTE[0], "#c9ccd1"], borderWidth: 0 }],
    },
    options: {
      responsive: false, animation: false,
      plugins: { legend: { display: true, position: "right", labels: { color: REPORT_CHART_TEXT_COLOR, boxWidth: 14 } } },
    },
  }, 500, 260);
}

function renderSupportBriefingReportHtml(data) {
  const generatedAt = new Date().toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
  const k = data.kpis || {};
  const department = data.department || "Support";
  const reportTitle = `${department} AI Cost Increase Analysis`;
  const evidenceByKpi = data.evidence_by_kpi || {};
  const changePositive = k.pct_change != null && k.pct_change < 0; // a cost DECREASE is the "good" direction
  const changeLabel = k.pct_change == null ? "—" : `${Math.abs(k.pct_change)}% vs prior period`;

  const findingsList = (data.findings || []).length
    ? reportNumberedList(data.findings, f => `<p>${escapeHtml(f)}</p>`)
    : `<p class="bi-note">Not enough activity in this period to surface findings.</p>`;

  const recCards = (data.recommendations || []).length
    ? reportNumberedList(data.recommendations, rec => `
        <div class="rpt-rec-head">
          <span class="rpt-rec-title">${escapeHtml(rec.title || "")}</span>
          <span class="bi-rec-priority ${escapeHtml(rec.priority || "low")}">${escapeHtml(rec.priority || "low")}</span>
        </div>
        <div class="bi-rec-body">${escapeHtml(rec.why_it_matters || rec.current_state || "")}</div>
        <div class="bi-rec-action">→ ${escapeHtml(rec.recommended_action || "")}</div>
        ${rec.impact_type === "savings_usd" && rec.estimated_impact != null
          ? `<div class="bi-rec-impact">Potential savings: ${fmtUsd(rec.estimated_impact)}/mo</div>` : ""}`)
    : `<div class="bi-note">No recommendations right now.</div>`;

  const agentTableRows = (data.top_agents || []).length
    ? data.top_agents.map((r, i) => `<tr><td class="bi-rank">${i + 1}</td><td>${escapeHtml(r.agent)}</td><td>${fmtUsd(r.spend_usd)}</td></tr>`).join("")
    : `<tr><td colspan="3">No ${escapeHtml(department.toLowerCase())} agent activity in this period.</td></tr>`;

  const resolutionRatePct = (k.resolved_cases != null && k.unresolved_cases != null && (k.resolved_cases + k.unresolved_cases) > 0)
    ? Math.round(k.resolved_cases / (k.resolved_cases + k.unresolved_cases) * 100) : null;

  return `
    <div class="report-doc rpt-premium">
      ${reportPremiumHeaderHtml(reportTitle, data.period_label, generatedAt)}

      <section class="report-section">
        <h2 class="report-section-title">Executive Summary</h2>
        <div class="rpt-exec-summary">
          ${reportIconBadge(changePositive ? "savingsArrow" : "barChart", changePositive ? "green" : "orange")}
          <p class="bi-summary">
            ${escapeHtml(department)} AI investment ${k.pct_change == null
              ? `totaled ${fmtUsd(k.ai_investment_usd)}`
              : `${k.pct_change >= 0 ? "increased" : "decreased"} ${Math.abs(k.pct_change)}% to ${fmtUsd(k.ai_investment_usd)}`
            } over ${escapeHtml(data.period_label || "the selected period")}${
              data.recommendations && data.recommendations.length ? ", with a clear optimization opportunity identified below." : "."
            }
          </p>
        </div>
        <div class="report-kpi-row rpt-kpi-row-premium">
          ${reportKpiCardPremium("dollarCircle", "blue", "AI Investment", fmtUsd(k.ai_investment_usd), `vs ${fmtUsd(k.prior_period_usd)} prior period`, null)}
          ${reportKpiCardPremium(changePositive ? "savingsArrow" : "barChart", changePositive ? "green" : "red", "Change vs Prior Period", changeLabel, null, k.pct_change == null ? null : { text: changeLabel, positive: changePositive })}
          ${reportKpiCardPremium("target", "purple", "Savings Opportunity", k.savings_opportunity_usd != null ? fmtUsd(k.savings_opportunity_usd) : "—", "workspace-wide model-routing estimate", null)}
          ${reportKpiCardPremium("checkTarget", "green", "Cost per Resolution", k.cost_per_resolution_usd != null ? fmtUsd(k.cost_per_resolution_usd) : "—", evidenceByKpi.cost_per_resolution_usd ? `<span class="bi-evidence-tag ${evidenceByKpi.cost_per_resolution_usd}">${escapeHtml(BI_EVIDENCE_LABELS[evidenceByKpi.cost_per_resolution_usd] || evidenceByKpi.cost_per_resolution_usd)}</span>` : null, null)}
          ${reportKpiCardPremium("document", "blue", "Outcome Coverage", k.outcome_coverage_pct != null ? `${k.outcome_coverage_pct}%` : "—", evidenceByKpi.outcome_coverage_pct ? `<span class="bi-evidence-tag ${evidenceByKpi.outcome_coverage_pct}">${escapeHtml(BI_EVIDENCE_LABELS[evidenceByKpi.outcome_coverage_pct] || evidenceByKpi.outcome_coverage_pct)}</span>` : null, null)}
        </div>
      </section>

      <section class="report-section">
        <h2 class="report-section-title">AI Spend Trend</h2>
        ${supportBriefingSpendTrendChart(data.spend_trend) || `<p class="bi-note">Not enough daily data to chart a trend.</p>`}
      </section>

      <section class="report-section">
        <h2 class="report-section-title">Spend by ${escapeHtml(department)} Agent</h2>
        ${supportBriefingAgentChart(data.top_agents) || `<p class="bi-note">Not enough agent-level activity to chart.</p>`}
        <table class="rpt-context-table">
          <thead><tr><th></th><th>Agent</th><th>AI Spend</th></tr></thead>
          <tbody>${agentTableRows}</tbody>
        </table>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Model Mix by Agent</h2>
        ${supportBriefingModelMixBars(data.model_mix_by_agent) || `<p class="bi-note">Not enough per-agent tier data to chart.</p>`}
      </section>

      <section class="report-section rpt-outcome-section" style="break-inside:avoid">
        <h2 class="report-section-title">Resolved vs. Unresolved Cases</h2>
        <div class="rpt-outcome-grid">
          ${supportBriefingOutcomeChart(k.resolved_cases, k.unresolved_cases) || `<p class="bi-note">No case outcome data in this period.</p>`}
          ${resolutionRatePct != null ? `
            <div class="rpt-scorecard">
              ${reportIconBadge("checkTarget", "green")}
              <div class="rpt-scorecard-value">${resolutionRatePct}%</div>
              <div class="rpt-scorecard-label">Resolution Rate</div>
              <div class="rpt-scorecard-sub">${fmtNum(k.resolved_cases)} of ${fmtNum(k.resolved_cases + k.unresolved_cases)} cases resolved</div>
            </div>` : ""}
        </div>
      </section>

      <section class="report-section">
        <h2 class="report-section-title">Key Findings</h2>
        ${findingsList}
      </section>

      <section class="report-section">
        <h2 class="report-section-title">Recommendations</h2>
        ${recCards}
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Evidence &amp; Methodology</h2>
        <div class="bi-note">
          <div><strong>Data period:</strong> ${escapeHtml(data.evidence?.data_period_label || "")}</div>
          <div><strong>AI requests analyzed:</strong> ${fmtNum(data.evidence?.requests_analyzed)}</div>
          <div><strong>Cases analyzed:</strong> ${fmtNum(data.evidence?.cases_analyzed)}</div>
          <div><strong>Outcome-covered cases:</strong> ${fmtNum(data.evidence?.outcome_covered_cases)}</div>
          ${data.truncated ? `<div><strong>Note:</strong> this period had more matching activity than could be fully loaded — figures reflect a bounded sample, not silently under-counted without notice.</div>` : ""}
          <div style="margin-top:8px">Associated business outcomes reflect cases with recorded AI activity and do not imply AI caused the outcome. Savings Opportunity is an estimate (routine calls priced at the cheapest active model tier), not money already saved.</div>
        </div>
      </section>

      <footer class="report-footer rpt-footer-premium">
        <span>CostPilot — ${escapeHtml(reportTitle)}</span>
        <span>${escapeHtml(askCostPilotWorkspaceLabel())}</span>
        <span>Confidential</span>
      </footer>
    </div>`;
}

async function openSupportBriefingReport(opts = {}) {
  const department = opts.department || "Support";
  const days = opts.days || 30;
  const reportTitle = `${department} AI Cost Increase Analysis`;
  const modalId = "supportBriefingPreview";
  const existing = document.getElementById(modalId);
  if (existing) existing.remove();
  const workspaceId = reportWorkspaceId();
  let data;
  try {
    const qs = new URLSearchParams({ days: String(days), department });
    if (workspaceId) qs.set("workspace_id", workspaceId);
    data = await apiGet(`/api/dashboard/support-briefing?${qs.toString()}`);
  } catch (err) {
    alert(`Could not load the ${department} Cost Briefing: ` + (err.message || "unknown error"));
    return;
  }
  const html = renderSupportBriefingReportHtml(data);
  const modal = document.createElement("div");
  modal.id = modalId;
  modal.className = "cp-report-preview-backdrop";
  modal.innerHTML = `
    <div class="cp-report-preview-modal">
      <div class="cp-report-preview-toolbar">
        <div style="font-weight:600">${escapeHtml(reportTitle)}</div>
        <button type="button" data-support-briefing-save class="cp-report-preview-print">💾 Save</button>
        <button type="button" data-support-briefing-print class="cp-report-preview-print">🖨 Print / Save as PDF</button>
        <button type="button" data-support-briefing-close class="cp-report-preview-close">✕</button>
      </div>
      <div class="cp-report-preview-scroll">
        <div id="${modalId}-body">${html}</div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener("click", async (event) => {
    if (event.target === modal || event.target.closest("[data-support-briefing-close]")) { modal.remove(); return; }
    if (event.target.closest("[data-support-briefing-print]")) { printSection(`${modalId}-body`, reportTitle); return; }
    if (event.target.closest("[data-support-briefing-save]")) {
      try {
        await apiPost("/api/saved-reports", {
          workspace_id: workspaceId || null,
          title: `${reportTitle} — ${new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`,
          report_type: "support_briefing",
          source: { days, department },
        });
        alert('Report saved. Reopen it anytime from "Saved reports."');
      } catch (err) {
        alert("Could not save report: " + (err.message || "unknown error"));
      }
    }
  });
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

// Names each active attribution filter using its dropdown's own visible
// option text (not the raw id/value) -- e.g. "Person: David Kim", not
// "Person: HIST2Y:...:USER:42". Confirmed live 2026-09-11: a filtered
// Usage & Attribution view looked pixel-identical to the unfiltered
// company total, with nothing on the page indicating a filter was
// active -- the KPI cards were correct for that filter, but nothing
// said so. This must render (or hide) every time the KPI cards do, so
// the two can never disagree about whether a filter is active.
const CONTEXT_FILTER_LABELS = {
  ctxOrgFilter: "Department/Team", ctxProjectFilter: "Project",
  ctxPersonFilter: "Person", ctxAccountFilter: "Account",
  ctxAgentFilter: "Agent", ctxSourceFilter: "Source",
  ctxRecordTypeFilter: "Record Type", ctxPurposeFilter: "Business Purpose",
};

function updateContextFilterBanner() {
  const banner = document.getElementById("rptContextFilterBanner");
  const text = document.getElementById("rptContextFilterBannerText");
  if (!banner || !text) return;
  const active = Object.entries(CONTEXT_FILTER_LABELS)
    .map(([id, label]) => {
      const select = document.getElementById(id);
      const optionText = select?.selectedOptions?.[0]?.textContent?.trim();
      return select?.value ? `${label}: ${optionText || select.value}` : null;
    })
    .filter(Boolean);
  banner.hidden = active.length === 0;
  if (active.length) {
    text.textContent = `Filtered to ${active.join(", ")} — showing this activity only, not the full company.`;
  }
}

function resetProjectAttributionFilters() {
  reportDrillDateRange = null;
  [
    "ctxOrgFilter", "ctxProjectFilter", "ctxPersonFilter", "ctxAccountFilter",
    "ctxAgentFilter", "ctxSourceFilter", "ctxRecordTypeFilter", "ctxPurposeFilter",
  ].forEach(id => {
    const select = document.getElementById(id);
    if (select) select.value = "";
  });
  _explorerSearchTerm = "";
  const explorerSearchInput = document.getElementById("explorerSearch");
  if (explorerSearchInput) explorerSearchInput.value = "";
  clearAskDrillQuery();
  loadBusinessContexts();
}

function selectOrganizationalUnit(name) {
  const select = document.getElementById("ctxOrgFilter");
  if (!select) return;
  select.value = select.value === name ? "" : name;
  loadBusinessContexts();
}

function clearProjectAttributionFilter(id) {
  const select = document.getElementById(id);
  if (select) select.value = "";
  syncAskDrillQuery();
  loadBusinessContexts();
}

function renderProjectAttributionActiveFilters() {
  const definitions = [
    ["ctxOrgFilter", "Team"], ["ctxProjectFilter", "Work"],
    ["ctxPersonFilter", "Person"], ["ctxAccountFilter", "Account"],
    ["ctxAgentFilter", "Agent"], ["ctxSourceFilter", "Source"],
    ["ctxRecordTypeFilter", "Record type"], ["ctxPurposeFilter", "Purpose"],
  ];
  const chips = definitions.flatMap(([id, label]) => {
    const select = document.getElementById(id);
    if (!select?.value) return [];
    const selected = select.options[select.selectedIndex]?.textContent || select.value;
    return `<button type="button" class="project-filter-chip" onclick="clearProjectAttributionFilter('${id}')">
      <span>${escapeHtml(label)}: ${escapeHtml(selected)}</span><span aria-hidden="true">×</span>
    </button>`;
  });
  const container = document.getElementById("ctxActiveFilters");
  if (!container) return;
  container.hidden = chips.length === 0;
  container.innerHTML = chips.length
    ? `<span>Active filters</span>${chips.join("")}`
    : "";
  renderContextDrillPath();
}

function selectBusinessPurpose(value) {
  const select = document.getElementById("ctxPurposeFilter");
  if (!select) return;
  select.value = select.value === value ? "" : value;
  loadBusinessContexts();
}

function applyProjectAttributionFilter(id, value) {
  const select = document.getElementById(id);
  if (!select) return;
  select.value = String(value ?? "");
  loadBusinessContexts();
}

function contextDrillButton(id, value, primary, secondary = "") {
  const label = primary || "Unknown";
  if (value === null || value === undefined || value === "") {
    return `<div class="ctx-name">${escapeHtml(label)}</div>` +
      (secondary ? `<div class="ctx-meta">${escapeHtml(secondary)}</div>` : "");
  }
  return `<button type="button" class="ctx-drill-button"
      data-value="${escapeHtml(String(value))}"
      onclick="applyProjectAttributionFilter('${id}', this.dataset.value)">
      <span class="ctx-name">${escapeHtml(label)}</span>
      ${secondary ? `<span class="ctx-meta">${escapeHtml(secondary)}</span>` : ""}
    </button>`;
}

function renderContextDrillPath() {
  const path = document.getElementById("ctxDrillPath");
  if (!path) return;
  const definitions = [
    ["ctxOrgFilter", "Team"],
    ["ctxAccountFilter", "Account"],
    ["ctxProjectFilter", "Work"],
    ["ctxPersonFilter", "Person"],
    ["ctxAgentFilter", "Agent"],
    ["ctxPurposeFilter", "Purpose"],
    ["ctxSourceFilter", "Source"],
    ["ctxRecordTypeFilter", "Record type"],
  ];
  const selected = definitions.flatMap(([id, label]) => {
    const select = document.getElementById(id);
    if (!select?.value) return [];
    return [{
      id,
      label,
      value: select.options[select.selectedIndex]?.textContent || select.value,
    }];
  });
  path.innerHTML = `<strong>Company</strong>` + (
    selected.length
      ? selected.map(item => `<span aria-hidden="true">›</span><button type="button"
          onclick="clearProjectAttributionFilter('${item.id}')"
          title="Remove ${escapeHtml(item.label)} filter">${escapeHtml(item.value)} ×</button>`).join("")
      : "<span>All AI activity</span>"
  );
}

function toggleContextBreakdown(kind) {
  if (!Object.prototype.hasOwnProperty.call(_contextBreakdownExpanded, kind)) return;
  _contextBreakdownExpanded[kind] = !_contextBreakdownExpanded[kind];
  if (_rptContextData) renderContextBreakdowns(_rptContextData);
}

function renderContextBreakdowns(data) {
  const singular = data.context_label || "Work";
  const plural = data.context_label_plural || "Work";
  const title = document.getElementById("ctx-work-breakdown-title");
  const column = document.getElementById("ctx-work-column-label");
  if (title) title.textContent = `Usage by ${singular}`;
  if (column) column.textContent = singular;

  const definitions = {
    project: {
      rows: data.project_breakdown || [],
      body: "ctx-project-rows",
      toggle: "ctx-project-toggle",
      empty: `No ${singular.toLowerCase()} activity matches these filters.`,
      render: row => `<tr>
        <td>${contextDrillButton("ctxProjectFilter", row.id, row.label)}</td>
        <td>${contextDrillButton("ctxAccountFilter", row.account_external_id, row.account_name || "Unassigned account")}</td>
        <td class="ctx-mono">${fmtNum(row.request_count || 0)}</td>
        <td class="ctx-mono">${fmtNum(row.total_tokens || 0)}</td>
        <td class="ctx-mono">${fmtUsd(Number(row.spend_usd || 0))}</td>
      </tr>`,
    },
    person: {
      rows: data.people_breakdown || [],
      body: "ctx-person-rows",
      toggle: "ctx-person-toggle",
      empty: "No identified user activity matches these filters.",
      render: row => `<tr>
        <td>${contextDrillButton("ctxPersonFilter", row.id, row.label, row.email || row.source_platform || "")}</td>
        <td class="ctx-mono">${fmtNum(row.request_count || 0)}</td>
        <td class="ctx-mono">${fmtNum(row.total_tokens || 0)}</td>
        <td class="ctx-mono">${fmtNum(row.tokens_saved || 0)}</td>
        <td class="ctx-mono">${fmtUsd(Number(row.spend_usd || 0))}</td>
      </tr>`,
    },
    agent: {
      rows: data.agent_breakdown || [],
      body: "ctx-agent-rows",
      toggle: "ctx-agent-toggle",
      empty: "No agent activity matches these filters.",
      render: row => `<tr>
        <td>${contextDrillButton("ctxAgentFilter", row.id, row.label, row.source_platform || "")}</td>
        <td class="ctx-mono">${fmtNum(row.request_count || 0)}</td>
        <td class="ctx-mono">${fmtNum(row.total_tokens || 0)}</td>
        <td class="ctx-mono">${fmtNum(row.tokens_saved || 0)}</td>
        <td class="ctx-mono">${fmtUsd(Number(row.spend_usd || 0))}</td>
      </tr>`,
    },
  };

  Object.entries(definitions).forEach(([kind, definition]) => {
    const expanded = _contextBreakdownExpanded[kind];
    const visible = expanded ? definition.rows : definition.rows.slice(0, CONTEXT_BREAKDOWN_LIMIT);
    const body = document.getElementById(definition.body);
    if (body) {
      body.innerHTML = visible.length
        ? visible.map(definition.render).join("")
        : `<tr><td colspan="5">${escapeHtml(definition.empty)}</td></tr>`;
    }
    const toggle = document.getElementById(definition.toggle);
    if (toggle) {
      toggle.hidden = definition.rows.length <= CONTEXT_BREAKDOWN_LIMIT;
      toggle.textContent = expanded
        ? `Show top ${CONTEXT_BREAKDOWN_LIMIT}`
        : `View all (${definition.rows.length})`;
    }
  });
}

async function loadBusinessContexts() {
  const { date_from, date_to, days } = getActiveDateRange();
  const wsId = reportWorkspaceId();
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
    business_purpose: projectAttributionFilterValue("ctxPurposeFilter"),
  };
  Object.entries(selectedFilters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  try {
    const data = await apiGet(`/api/work-items/activity-report?${params.toString()}`);
    _rptContextData = data;
    const summary = data.summary || {};
    setKpi("ctx-requests", fmtNum(summary.request_count || 0));
    setKpi("ctx-tokens", fmtNum(summary.total_tokens || 0));
    setKpi("ctx-token-split", `${fmtNum(summary.input_tokens || 0)} input · ${fmtNum(summary.output_tokens || 0)} output`);
    setKpi("ctx-pruned", fmtNum(summary.tokens_saved || 0));
    setKpi("ctx-spend", fmtUsd(Number(summary.spend_usd || 0)));
    setKpi("ctx-people", fmtNum(summary.people_count || 0));
    setKpi("ctx-agents", fmtNum(summary.agent_count || 0));
    updateContextFilterBanner();

    const options = data.filter_options || {};
    projectAttributionSelect("ctxOrgFilter", "All Departments & Teams", options.organizational_units);
    projectAttributionSelect("ctxProjectFilter", `All ${data.context_label_plural || "Work"}`, options.projects);
    projectAttributionSelect("ctxPersonFilter", "All People", options.people);
    projectAttributionSelect("ctxAccountFilter", "All Accounts", options.accounts);
    projectAttributionSelect("ctxAgentFilter", "All Agents", options.agents);
    projectAttributionSelect("ctxSourceFilter", "All Sources", options.source_platforms);
    projectAttributionSelect("ctxRecordTypeFilter", "All Record Types", options.record_types);
    projectAttributionSelect("ctxPurposeFilter", "All Business Purposes", options.business_purposes);
    renderProjectAttributionActiveFilters();

    const selectedOrg = projectAttributionFilterValue("ctxOrgFilter");
    const company = summary;
    document.getElementById("ctx-org-company-requests").textContent =
      `${fmtNum(company.request_count || 0)} requests`;
    document.getElementById("ctx-org-company-spend").textContent =
      fmtUsd(Number(company.spend_usd || 0));
    document.getElementById("ctx-org-path").textContent = selectedOrg
      ? `Company → ${selectedOrg}`
      : "Company · all departments and teams";
    const orgUnits = data.organizational_unit_breakdown || [];
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

    const purposes = data.business_purpose_breakdown || [];
    const maxPurposeSpend = Math.max(...purposes.map(row => Number(row.spend_usd || 0)), 0);
    const totalPurposeSpend = purposes.reduce((sum, row) => sum + Number(row.spend_usd || 0), 0);
    const selectedPurpose = projectAttributionFilterValue("ctxPurposeFilter");
    document.getElementById("ctx-purpose-total").textContent = `${fmtUsd(totalPurposeSpend)} total spend`;
    document.getElementById("ctx-purpose-chart").innerHTML = purposes.length
      ? purposes.map(row => {
          const spend = Number(row.spend_usd || 0);
          const width = maxPurposeSpend > 0
            ? Math.max(2, spend / maxPurposeSpend * 100)
            : 0;
          return `<button type="button"
              class="purpose-usage-row${selectedPurpose === row.label ? " active" : ""}"
              data-purpose="${escapeHtml(row.label || "")}"
              onclick="selectBusinessPurpose(this.dataset.purpose)">
            <span class="purpose-usage-label">${escapeHtml(row.label)}</span>
            <span class="purpose-usage-track"><span style="width:${width.toFixed(2)}%"></span></span>
            <strong>${fmtUsd(spend)}</strong>
            <span class="purpose-usage-meta">${fmtNum(row.total_tokens || 0)} tokens · ${fmtNum(row.request_count || 0)} requests</span>
          </button>`;
        }).join("")
      : '<div class="org-unit-empty">No business-purpose usage matches these filters.</div>';

    renderContextBreakdowns(data);

    _ctxActivityRaw = data.activities || [];
    _ctxActivityPage = 0;
    _ctxActivitySummary = summary;
    renderActivityLedger();

    // AI Activity Explorer's View By / Break Down By pivot lives in the
    // same tab, keyed off the same Department/Team filter -- refreshed
    // here (loadBusinessContexts' single hub) rather than duplicating a
    // call at every filter-select onclick handler above.
    loadExplorerPivot();
  } catch (err) {
    document.getElementById("ctx-activity-rows").innerHTML =
      `<tr><td colspan="12">Could not load AI usage attribution: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// ── AI Activity Ledger — client-side search + pagination ───────────────────
//
// The backend already fetches up to activity_limit (1000) rows in one call
// (loadBusinessContexts' params above); this paces how much renders into
// the DOM at once and lets the user search within what's already fetched,
// same client-side pattern the existing Risk Events table search already
// uses (renderRiskEventTable) -- not a new mechanism, not a backend
// pagination/offset change to project_activity_reporting().
let _ctxActivityRaw = [];
let _ctxActivityPage = 0;
let _ctxActivitySummary = {};
const CTX_ACTIVITY_PAGE_SIZE = 50;

function renderActivityLedger() {
  const body = document.getElementById("ctx-activity-rows");
  if (!body) return;
  const search = (document.getElementById("ctxActivitySearch")?.value || "").trim().toLowerCase();

  const filtered = search
    ? _ctxActivityRaw.filter(row => [
        row.charged_unit, row.user_name, row.agent_name, row.account_name,
        row.project_name, row.source_record_name, row.business_purpose,
        row.model_name, row.model_tier,
      ].join(" ").toLowerCase().includes(search))
    : _ctxActivityRaw;

  const totalPages = Math.max(1, Math.ceil(filtered.length / CTX_ACTIVITY_PAGE_SIZE));
  _ctxActivityPage = Math.min(_ctxActivityPage, totalPages - 1);
  const pageRows = filtered.slice(
    _ctxActivityPage * CTX_ACTIVITY_PAGE_SIZE, (_ctxActivityPage + 1) * CTX_ACTIVITY_PAGE_SIZE
  );

  body.innerHTML = pageRows.length
    ? pageRows.map(row => `<tr>
        <td class="ctx-mono">${escapeHtml(formatReportTimestamp(row.timestamp))}</td>
        <td>${contextDrillButton("ctxOrgFilter", row.charged_unit, row.charged_unit || "Unassigned", row.attribution_source || "")}</td>
        <td>${contextDrillButton("ctxPersonFilter", row.user_external_id, row.user_name || "Unknown user", row.user_source_platform || "")}</td>
        <td>${contextDrillButton("ctxAgentFilter", row.agent_id, row.agent_name || "Unknown agent", row.agent_platform || "")}</td>
        <td>${contextDrillButton("ctxAccountFilter", row.account_external_id, row.account_name || "Unassigned account")}</td>
        <td>${contextDrillButton("ctxProjectFilter", row.project_external_id, row.project_name || "Unattributed")}</td>
        <td>${contextDrillButton("ctxRecordTypeFilter", row.source_record_type, row.source_record_name || row.source_record_id || "Not recorded", row.source_record_type || row.source_platform || "")}</td>
        <td>${contextDrillButton("ctxPurposeFilter", row.business_purpose, row.business_purpose || "Unclassified")}</td>
        <td><div>${escapeHtml(row.model_name || row.model_tier || "—")}</div><div class="ctx-meta">${escapeHtml(row.model_tier || "")}${row.is_simulation ? ' · <span class="rpt-badge badge-event">SIMULATION</span>' : ""}</div></td>
        <td class="ctx-mono">${fmtNum(row.total_tokens || 0)}</td>
        <td class="ctx-mono">${fmtNum(row.tokens_saved || 0)}</td>
        <td class="ctx-mono">${fmtUsd(Number(row.cost_usd || 0))}</td>
      </tr>`).join("")
    : `<tr><td colspan="12">${search ? "No AI activity matches this search." : "No AI activity matches these filters."}</td></tr>`;

  const summary = _ctxActivitySummary || {};
  document.getElementById("ctx-activity-count").textContent =
    `${fmtNum(filtered.length)} ${filtered.length === 1 ? "activity" : "activities"}` +
    `${search ? ` (of ${fmtNum(_ctxActivityRaw.length)})` : ""} · ` +
    `${fmtNum(summary.live_count || 0)} live · ${fmtNum(summary.simulation_count || 0)} simulation · ` +
    `${reportTimeZoneLabel()}`;

  const pager = document.getElementById("ctxActivityPager");
  if (pager) {
    pager.hidden = filtered.length <= CTX_ACTIVITY_PAGE_SIZE;
    document.getElementById("ctxActivityPageLabel").textContent = `Page ${_ctxActivityPage + 1} of ${totalPages}`;
    document.getElementById("ctxActivityPrev").disabled = _ctxActivityPage === 0;
    document.getElementById("ctxActivityNext").disabled = _ctxActivityPage >= totalPages - 1;
  }
}

function ctxActivityPageChange(delta) {
  _ctxActivityPage = Math.max(0, _ctxActivityPage + delta);
  renderActivityLedger();
}

// ── end AI Activity Ledger pagination ───────────────────────────────────

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
  downloadCsv(`costpilot_ai_usage_attribution_${new Date().toISOString().slice(0, 10)}.csv`, headers, rows);
}

// ── AI Activity Explorer — Tableau-style View By / Break Down By pivot ─────
//
// Backed by POST /api/metrics/query (core.metrics_query.run_metrics_query,
// the metrics registry) -- deliberately NOT project_activity_reporting()
// (the /api/work-items/activity-report call the rest of this tab uses).
// Per the approved reporting plan's architectural guardrail, the registry
// is this Explorer's only source of truth; the fixed Usage-by-Work/Person/
// Agent tables above stay on their existing data source for this phase.
//
// Click-to-filter is consistent: clicking any row always drills in place
// (breadcrumb pushes, table re-pivots) -- it never navigates away. Opening
// a full entity profile is always the separate, explicit "View Profile →"
// action next to the row.

const EXPLORER_DIMENSIONS = [
  { key: "department",    label: "Department / Team" },
  { key: "person",        label: "Person" },
  { key: "agent",         label: "Agent" },
  { key: "account",       label: "Account / Customer" },
  { key: "work_item",     label: "Work Item" },
  { key: "context_type",  label: "Kind of Work" },
  { key: "model",         label: "Model" },
  { key: "provider",      label: "Provider" },
  { key: "platform",      label: "Platform" },
];

// Maps a dimension to (a) the run_metrics_query filter key it drills by,
// using the row's real dimension_ids value (never the display label --
// see core/metrics_query.py's dimension_ids for why that's required for
// "person" specifically), and (b) the profile page it links to, if any.
// department/model/provider/platform have no profile page: drilling is
// the only interaction those rows support.
const EXPLORER_DIMENSION_CONFIG = {
  department: { filterKey: "department", byId: false },
  person:     { filterKey: "person",     byId: true, profile: (id, ws) => `/person-profile.html?id=${encodeURIComponent(id)}&workspace_id=${encodeURIComponent(ws || "")}` },
  agent:      { filterKey: "agent_id",   byId: true, numeric: true, profile: (id) => `/agent-profile.html?id=${encodeURIComponent(id)}` },
  account:    { filterKey: "account",    byId: true, profile: (id, ws) => `/business-profile.html?account=${encodeURIComponent(id)}&workspace_id=${encodeURIComponent(ws || "")}` },
  work_item:  { filterKey: "work_item",  byId: true, profile: (id) => `/work-item-profile.html?id=${encodeURIComponent(id)}` },
  context_type: { filterKey: "context_type", byId: false },
  model:      { filterKey: "model",      byId: false },
  provider:   { filterKey: "provider",   byId: false },
  platform:   { filterKey: "platform",   byId: false },
};

let _explorerViewBy = "department";
let _explorerBreakDownBy = "";
let _explorerScope = []; // [{dim, value, label}, ...] -- breadcrumb/drill stack, root-to-leaf

function initExplorerControls() {
  const viewBySel = document.getElementById("explorerViewBy");
  if (!viewBySel || viewBySel.options.length) return; // already built once
  EXPLORER_DIMENSIONS.forEach(d => viewBySel.appendChild(new Option(d.label, d.key)));
  viewBySel.value = _explorerViewBy;
  rebuildExplorerBreakDownOptions();
}

function rebuildExplorerBreakDownOptions() {
  const sel = document.getElementById("explorerBreakDownBy");
  if (!sel) return;
  const previous = _explorerBreakDownBy;
  sel.innerHTML = "";
  sel.appendChild(new Option("None", ""));
  EXPLORER_DIMENSIONS.filter(d => d.key !== _explorerViewBy).forEach(d => sel.appendChild(new Option(d.label, d.key)));
  _explorerBreakDownBy = EXPLORER_DIMENSIONS.some(d => d.key === previous && previous !== _explorerViewBy) ? previous : "";
  sel.value = _explorerBreakDownBy;
}

function explorerViewByChanged() {
  _explorerViewBy = document.getElementById("explorerViewBy").value;
  _explorerScope = []; // changing the primary dimension starts a fresh drill
  // A search term meaningful for the old dimension (e.g. "acme" while
  // viewing by Account) is very unlikely to mean anything for the new
  // one (e.g. Model) -- clearing avoids a silently-empty table that
  // looks like "no data" instead of "stale search."
  _explorerSearchTerm = "";
  const searchInput = document.getElementById("explorerSearch");
  if (searchInput) searchInput.value = "";
  rebuildExplorerBreakDownOptions();
  document.getElementById("explorerSecondaryWrap").hidden = true;
  loadExplorerPivot();
}

let _explorerSearchTerm = "";
let _explorerSearchTimer = null;

function explorerSearchChanged() {
  const input = document.getElementById("explorerSearch");
  const value = input ? input.value : "";
  // Debounced, not per-keystroke -- this hits /api/metrics/query on
  // every call (server-side search, not a client-side filter of already-
  // fetched rows, so a search term can find a match outside the current
  // top-50-by-spend page too).
  if (_explorerSearchTimer) clearTimeout(_explorerSearchTimer);
  _explorerSearchTimer = setTimeout(() => {
    _explorerSearchTerm = value.trim();
    loadExplorerPivot();
    if (_explorerBreakDownBy) loadExplorerSecondary();
  }, 300);
}

function explorerBreakDownByChanged() {
  _explorerBreakDownBy = document.getElementById("explorerBreakDownBy").value;
  document.getElementById("explorerSecondaryWrap").hidden = true;
}

function explorerCrossFilters() {
  // The one filter shared with the rest of this tab (see the approved
  // plan's Phase 1 scope note): the other filter selects use id-vs-name
  // matching semantics that don't line up with the registry's filters
  // yet -- reconciling that is a later-phase item, not silently guessed
  // at here.
  const filters = {};
  const dept = typeof projectAttributionFilterValue === "function" ? projectAttributionFilterValue("ctxOrgFilter") : "";
  if (dept) filters.department = dept;
  return filters;
}

function explorerScopeFilters() {
  const filters = explorerCrossFilters();
  _explorerScope.forEach(level => {
    const cfg = EXPLORER_DIMENSION_CONFIG[level.dim];
    filters[cfg.filterKey] = cfg.numeric ? Number(level.value) : level.value;
  });
  // Applies to whichever single dimension the caller is currently
  // grouping by (primary "View By" or secondary "Break Down By" -- each
  // is its own call into this function with its own dimension already
  // baked into _explorerScope/loadExplorerSecondary's own request). A
  // term meaningful for one (e.g. "acme" under Account) may just show no
  // matches for the other (e.g. under Model) -- expected, not a bug.
  if (_explorerSearchTerm) filters.search = _explorerSearchTerm;
  return filters;
}

async function loadExplorerPivot() {
  const dimDef = EXPLORER_DIMENSIONS.find(d => d.key === _explorerViewBy);
  const head = document.getElementById("explorerPrimaryHead");
  if (!head) return; // Explorer markup not on this page/tab
  head.innerHTML = `<th>${escapeHtml(dimDef ? dimDef.label : _explorerViewBy)}</th><th>AI Investment</th><th>Requests</th><th>Tokens</th><th></th>`;

  const { days, date_from, date_to } = getActiveDateRange();
  const body = {
    workspace_id: reportWorkspaceId() || null,
    metrics: ["ai_spend", "ai_requests", "total_tokens"],
    dimensions: [_explorerViewBy],
    filters: explorerScopeFilters(),
    days: Math.min(365, days),
    date_from,
    date_to,
    period_key: "none",
    sort: "ai_spend",
    limit: 50,
  };
  try {
    const data = await apiPost("/api/metrics/query", body);
    renderExplorerBreadcrumb();
    const rows = data.rows || [];
    document.getElementById("explorerPrimaryRows").innerHTML = rows.length
      ? rows.map(row => explorerRowHtml(row, _explorerViewBy)).join("")
      : `<tr><td colspan="5">No AI activity matches this view.</td></tr>`;
    // Secondary-panel visibility is NOT decided here -- this function is
    // called concurrently with loadExplorerSecondary() from explorerDrill()
    // (both fired without awaiting each other), so unconditionally hiding
    // it here raced against that reveal and silently hid a fully-loaded
    // breakdown (found via live browser verification). explorerDrill() /
    // explorerBreadcrumbReset() own showing/hiding it instead.
  } catch (err) {
    document.getElementById("explorerPrimaryRows").innerHTML =
      `<tr><td colspan="5">Could not load: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function explorerRowHtml(row, dim) {
  const cfg = EXPLORER_DIMENSION_CONFIG[dim];
  const label = row.dimensions?.[dim] ?? "Unassigned";
  const idValue = cfg.byId ? row.dimension_ids?.[dim] : label;
  const profileHref = cfg.profile && idValue != null ? cfg.profile(idValue, reportWorkspaceId()) : null;
  const profileHtml = profileHref
    ? `<a href="${profileHref}" class="context-view-toggle" onclick="event.stopPropagation()">View Profile →</a>`
    : "";
  const clickable = idValue != null && idValue !== "__unassigned__" && idValue !== "__unknown__";
  const onclick = clickable
    ? `onclick="explorerDrill('${escapeHtml(dim)}', '${escapeHtml(String(idValue))}', '${escapeHtml(String(label))}')"`
    : "";
  return `<tr class="${clickable ? "explorer-row" : ""}" ${onclick}>
    <td>${escapeHtml(String(label))}</td>
    <td>${fmtUsd(Number(row.ai_spend || 0))}</td>
    <td>${fmtNum(row.ai_requests || 0)}</td>
    <td>${fmtNum(row.total_tokens || 0)}</td>
    <td>${profileHtml}</td>
  </tr>`;
}

function explorerDrill(dim, value, label) {
  _explorerScope.push({ dim, value, label });
  loadExplorerPivot();
  if (_explorerBreakDownBy) {
    loadExplorerSecondary();
  } else {
    document.getElementById("explorerSecondaryWrap").hidden = true;
  }
}

function explorerBreadcrumbReset(toIndex) {
  _explorerScope = _explorerScope.slice(0, toIndex);
  loadExplorerPivot();
  // A secondary breakdown is scoped to "the row currently drilled into" --
  // resetting to Company (or any level) with no deeper row selected has
  // nothing left to break down, so hide it; re-show/refresh only happens
  // via a fresh explorerDrill() click.
  if (_explorerScope.length === 0 || !_explorerBreakDownBy) {
    document.getElementById("explorerSecondaryWrap").hidden = true;
  } else {
    loadExplorerSecondary();
  }
}

function renderExplorerBreadcrumb() {
  const nav = document.getElementById("explorerBreadcrumb");
  if (!nav) return;
  const crumbs = [`<button type="button" class="ctx-drill-button" onclick="explorerBreadcrumbReset(0)"><strong>Company</strong></button>`];
  _explorerScope.forEach((level, i) => {
    crumbs.push(`<span><button type="button" class="ctx-drill-button" onclick="explorerBreadcrumbReset(${i + 1})">${escapeHtml(level.label)}</button></span>`);
  });
  nav.innerHTML = crumbs.join("");
}

async function loadExplorerSecondary() {
  const wrap = document.getElementById("explorerSecondaryWrap");
  if (!wrap || !_explorerBreakDownBy) return;
  const current = _explorerScope[_explorerScope.length - 1];
  const dimDef = EXPLORER_DIMENSIONS.find(d => d.key === _explorerBreakDownBy);
  document.getElementById("explorerSecondaryTitle").textContent =
    `${current ? current.label : "Company"} — break down by ${dimDef ? dimDef.label : _explorerBreakDownBy}`;
  document.getElementById("explorerSecondaryHead").innerHTML =
    `<th>${escapeHtml(dimDef ? dimDef.label : _explorerBreakDownBy)}</th><th>AI Investment</th><th>Requests</th><th>Tokens</th><th></th>`;
  wrap.hidden = false;

  const secondaryRange = getActiveDateRange();
  const body = {
    workspace_id: reportWorkspaceId() || null,
    metrics: ["ai_spend", "ai_requests", "total_tokens"],
    dimensions: [_explorerBreakDownBy],
    filters: explorerScopeFilters(),
    days: Math.min(365, secondaryRange.days),
    date_from: secondaryRange.date_from,
    date_to: secondaryRange.date_to,
    period_key: "none",
    sort: "ai_spend",
    limit: 25,
  };
  try {
    const data = await apiPost("/api/metrics/query", body);
    const rows = data.rows || [];
    document.getElementById("explorerSecondaryRows").innerHTML = rows.length
      ? rows.map(row => explorerRowHtml(row, _explorerBreakDownBy)).join("")
      : `<tr><td colspan="5">No AI activity matches this breakdown.</td></tr>`;
  } catch (err) {
    document.getElementById("explorerSecondaryRows").innerHTML =
      `<tr><td colspan="5">Could not load: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// ── end AI Activity Explorer (Ask CostPilot scope inheritance is wired
// into the existing window.getCostPilotAskScope below) ─────────────────

function isReportFilterActive() {
  const active = document.activeElement;
  return !!active && !!active.closest(".rpt-date-controls, .act-filter-bar");
}

// ── TAB 1: SAVINGS ────────────────────────────────────────────────────────────

function reportWorkspaceId() {
  const params = new URLSearchParams(window.location.search);
  return params.get("workspace_id") || localStorage.getItem("cp_workspace_id") || "";
}

function reportScopedPath(path) {
  const workspaceId = reportWorkspaceId();
  if (!workspaceId) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}workspace_id=${encodeURIComponent(workspaceId)}`;
}

function updateReportWorkspaceBanner() {
  const banner = document.getElementById("rptScopeBanner");
  if (!banner) return;
  const workspaceId = new URLSearchParams(window.location.search).get("workspace_id");
  banner.hidden = !workspaceId;
  if (workspaceId) {
    const isHistoricalDemo = workspaceId === "SIM-HISTORICAL-2Y";
    banner.innerHTML = isHistoricalDemo
      ? `<strong>Historical date-testing workspace</strong><span>Two years of deterministic simulated activity · no live customer usage</span>`
      : `<strong>Workspace scope</strong><span>${escapeHtml(workspaceId)}</span>`;
  }
}

function reportApiParams(range) {
  const params = new URLSearchParams({
    days: String(Math.min(365, range.days || 30)),
    date_from: range.date_from,
    date_to: range.date_to,
  });
  const workspaceId = reportWorkspaceId();
  const explicitlyScoped = new URLSearchParams(window.location.search).has("workspace_id")
    || new URLSearchParams(window.location.search).get("ws");
  if (workspaceId && explicitlyScoped) params.set("workspace_id", workspaceId);
  return params.toString();
}

// ── Printable Reports Phase 4: saved, shareable reports ────────────────────
// Saves a RECIPE (report_type + date-range params), never a data snapshot --
// see database/models.py's SavedReport docstring. Business Impact has no
// date-range controls of its own (always all-time), so its source is empty;
// the other three classic tabs carry whatever range is currently active.
const SAVED_REPORT_TITLES = {
  business_impact: "Business Impact Report",
  savings: "Savings & Performance Report",
  risk: "Governance & Risk Report",
  departments: "Department Scorecard",
  ask_costpilot: "Ask CostPilot Report",
  support_briefing: "Support AI Cost Increase Analysis",
};

async function saveCurrentReport(reportType) {
  let source = {};
  if (reportType !== "business_impact") {
    const range = getActiveDateRange();
    source = { days: Math.min(365, range.days || 30), date_from: range.date_from, date_to: range.date_to };
  }
  const stamp = new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  const defaultTitle = `${SAVED_REPORT_TITLES[reportType] || "Report"} — ${stamp}`;
  const title = prompt("Save this report as:", defaultTitle);
  if (title === null) return; // cancelled
  try {
    await apiPost("/api/saved-reports", {
      workspace_id: reportWorkspaceId() || null,
      title: (title || "").trim() || defaultTitle,
      report_type: reportType,
      source,
    });
    alert('Report saved. Reopen it anytime from "Saved reports."');
  } catch (err) {
    alert("Could not save report: " + (err.message || "unknown error"));
  }
}

async function openSavedReportsModal() {
  let list;
  try {
    const workspaceId = reportWorkspaceId();
    const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
    const data = await apiGet(`/api/saved-reports${qs}`);
    list = data.reports || [];
  } catch (err) {
    alert("Could not load saved reports: " + (err.message || "unknown error"));
    return;
  }
  const existing = document.getElementById("cpSavedReportsModal");
  if (existing) existing.remove();
  const rows = list.length ? list.map(r => `
    <tr>
      <td>${escapeHtml(r.title)}</td>
      <td>${escapeHtml(SAVED_REPORT_TITLES[r.report_type] || r.report_type)}</td>
      <td>${new Date(r.created_at).toLocaleDateString()}</td>
      <td style="white-space:nowrap">
        <button type="button" class="export-btn" data-saved-report-open="${r.id}">Open</button>
        <button type="button" class="export-btn" data-saved-report-delete="${r.id}">Delete</button>
      </td>
    </tr>`).join("") : `<tr><td colspan="4">No saved reports yet.</td></tr>`;
  const modal = document.createElement("div");
  modal.id = "cpSavedReportsModal";
  modal.className = "cp-report-preview-backdrop";
  modal.innerHTML = `
    <div class="cp-report-preview-modal" style="max-width:640px">
      <div class="cp-report-preview-toolbar">
        <div style="font-weight:600">Saved reports</div>
        <button type="button" data-saved-report-close class="cp-report-preview-close">✕</button>
      </div>
      <div class="cp-report-preview-scroll">
        <table class="rpt-context-table">
          <thead><tr><th>Title</th><th>Type</th><th>Saved</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener("click", async (event) => {
    if (event.target === modal || event.target.closest("[data-saved-report-close]")) { modal.remove(); return; }
    const openBtn = event.target.closest("[data-saved-report-open]");
    if (openBtn) { await openSavedReport(Number(openBtn.dataset.savedReportOpen)); modal.remove(); return; }
    const delBtn = event.target.closest("[data-saved-report-delete]");
    if (delBtn) {
      if (!confirm("Delete this saved report?")) return;
      try {
        await apiDelete(`/api/saved-reports/${delBtn.dataset.savedReportDelete}`);
        openSavedReportsModal(); // refresh the list in place
      } catch (err) {
        alert("Could not delete: " + (err.message || "unknown error"));
      }
    }
  });
}

async function openSavedReport(id) {
  let saved;
  try {
    saved = await apiGet(`/api/saved-reports/${id}`);
  } catch (err) {
    alert("Could not open saved report: " + (err.message || "unknown error"));
    return;
  }
  if (saved.report_type === "support_briefing") {
    // Same "re-derive, don't persist a snapshot" principle as every other
    // saved report -- openSupportBriefingReport() re-fetches the live
    // /api/dashboard/support-briefing endpoint itself, so this just needs
    // to open it, not replay any stored data.
    openSupportBriefingReport(saved.source || {});
    return;
  }
  if (saved.report_type === "ask_costpilot") {
    // No original chat card to attach to (this can be reopened long after
    // the conversation that created it) -- fetchAskReportData/
    // showAskReportPreview (ask-costpilot-render.js, Phase 2/3) both work
    // from a bare {tool, args} step and append their own modal straight to
    // document.body, so this needs nothing from the DOM to reopen cleanly.
    const step = { tool: saved.source.tool, args: saved.source.args || {} };
    try {
      const result = await fetchAskReportData(step);
      showAskReportPreview(`saved-report-${id}`, saved.title, {}, step, result);
    } catch (err) {
      alert("Could not regenerate this report: " + (err.message || "unknown error"));
    }
    return;
  }
  const tabByType = { business_impact: "impact", savings: "savings", risk: "risk", departments: "departments" };
  const tab = tabByType[saved.report_type];
  if (!tab) return;
  if (saved.source && saved.source.date_from && saved.source.date_to) {
    reportDrillDateRange = { days: saved.source.days || 30, date_from: saved.source.date_from, date_to: saved.source.date_to };
  }
  openReportView(tab);
}

async function loadSavings() {
  const range = getActiveDateRange();
  const data = await apiGet(`/api/reports/savings?${reportApiParams(range)}`);
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
  const range = getActiveDateRange();
  const data = await apiGet(`/api/reports/risk?${reportApiParams(range)}`);
  _rptRiskData = data;
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
    const d = await apiGet(reportScopedPath("/api/dashboard"));
    _rptGovernanceDashboard = d;
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
    const detail = await apiGet(reportScopedPath(`/api/audit/${eventId}`));
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
  const range = getActiveDateRange();
  const rawData = await apiGet(`/api/reports/departments?${reportApiParams(range)}`);
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

// ── TAB 4: ASK COSTPILOT + AGENT FLEET REVIEW ─────────────────────────────────

function askCostPilotActorScope() {
  return localStorage.getItem("cp_user_external_id")
    || localStorage.getItem("cp_user_email")
    || localStorage.getItem("cp_actor_id")
    || "anonymous";
}

function askCostPilotStorageKey(kind) {
  const workspace = reportWorkspaceId() || "default";
  return `cp_ask_costpilot_${kind}:${workspace}:${askCostPilotActorScope()}`;
}

function readAskCostPilotStorage(kind, fallback) {
  try {
    const stored = JSON.parse(localStorage.getItem(askCostPilotStorageKey(kind)) || "null");
    return stored === null ? fallback : stored;
  } catch (_error) {
    return fallback;
  }
}

function writeAskCostPilotStorage(kind, value) {
  try {
    localStorage.setItem(askCostPilotStorageKey(kind), JSON.stringify(value));
  } catch (_error) {
    // The assistant still works when browser storage is unavailable.
  }
}

function removeAskCostPilotStorage() {
  try {
    localStorage.removeItem(askCostPilotStorageKey("history"));
    localStorage.removeItem(askCostPilotStorageKey("context"));
  } catch (_error) {
    // Nothing else is required when storage is unavailable.
  }
}

let askCostPilotHistory = readAskCostPilotStorage("history", []);
if (!Array.isArray(askCostPilotHistory)) askCostPilotHistory = [];
let askCostPilotContext = readAskCostPilotStorage("context", null);

const ASK_STARTER_QUESTIONS = [
  {
    id: "spend-change",
    category: "Spend",
    title: "Why did AI spend increase?",
    question: "Why did our AI spend increase compared with the previous period?",
    description: "Compare periods and identify the largest measured contributors.",
    available: () => true,
  },
  {
    id: "top-people",
    category: "People",
    title: "Who used the most tokens this month?",
    question: "Who used the most tokens this month?",
    description: "Rank attributed people using recorded token consumption.",
    available: data => Number(data?.summary?.people_count || 0) > 0,
  },
  {
    id: "inactive-agents",
    category: "Agents",
    title: "Which agents are inactive?",
    question: "Which agents have not been used in this period?",
    description: "Find registered agents with no matching governed activity.",
    available: data => (data?.filter_options?.agents || []).length > 0,
  },
  {
    id: "period-compare",
    category: "Trend",
    title: "Compare this month with last month",
    question: "Compare our token usage and AI spend this month with last month.",
    description: "See the absolute and percentage change between periods.",
    available: () => true,
  },
  {
    id: "top-accounts",
    category: "Accounts",
    title: "Which accounts generated the most AI activity?",
    question: "Which accounts generated the most AI activity this month?",
    description: "Rank customer accounts using attributed governed requests.",
    available: data => (data?.filter_options?.accounts || []).length > 0,
  },
  {
    id: "budget-forecast",
    category: "Budget",
    title: "Are we on track to exceed our budget?",
    question: "Are we on track to exceed our AI budget this month?",
    description: "Compare current spend pace with configured monthly limits.",
    available: (_data, departments) => (departments?.scorecards || [])
      .some(row => Number(row.monthly_cap_usd || 0) > 0),
  },
  {
    id: "budget-remaining",
    category: "Budget",
    title: "How much budget is left this month?",
    question: "How much AI budget is left this month?",
    description: "See current spend, remaining budget, and configured limits.",
    available: (_data, departments) => (departments?.scorecards || [])
      .some(row => Number(row.monthly_cap_usd || 0) > 0),
  },
  {
    id: "budget-alerts",
    category: "Budget",
    title: "Which departments are close to their limits?",
    question: "Which departments are closest to their AI budget limits?",
    description: "Find teams that need attention before they exceed budget.",
    available: (_data, departments) => (departments?.scorecards || [])
      .some(row => Number(row.monthly_cap_usd || 0) > 0),
  },
];

let askCostPilotAvailability = null;

// Set only when a disambiguation choice was just clicked -- carries the
// exact resolved row directly through to askCostPilotPayload, since
// re-asking text alone can't disambiguate two candidates that share a
// root word (e.g. "Support" the department vs. "Support Agent" the
// agent). Consumed and cleared on the next payload build, mirroring
// global-nav.js's identical _askPendingVoiceMeta pattern.
let _askPendingPinnedFilter = null;

function askCostPilotQuestion(question, pinnedFilter = null) {
  const input = document.getElementById("askCostPilotInput");
  if (!input || !question) return;
  input.value = question;
  input.focus();
  _askPendingPinnedFilter = pinnedFilter;
  submitAskCostPilot();
}

function askCostPilotSuggestion(button) {
  if (!button) return;
  const pinnedFilter = button.dataset.filterName
    ? { name: button.dataset.filterName, value: button.dataset.filterValue }
    : null;
  askCostPilotQuestion(button.dataset.question || button.textContent.trim(), pinnedFilter);
}

function askCostPilotDateLabel(range = getActiveDateRange()) {
  const from = parseRangeBoundary(range.date_from);
  const exclusiveTo = parseRangeBoundary(range.date_to);
  exclusiveTo.setDate(exclusiveTo.getDate() - 1);
  const format = value => value.toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric",
  });
  return `${format(from)} – ${format(exclusiveTo)}`;
}

function askCostPilotWorkspaceLabel() {
  const active = typeof getActiveWorkspace === "function" ? getActiveWorkspace() : null;
  return active?.name || active?.label || localStorage.getItem("cp_workspace_name") || "Current workspace";
}

function updateAskCostPilotScope(data = askCostPilotAvailability?.activity) {
  const workspace = document.getElementById("askWorkspaceScope");
  const date = document.getElementById("askDateScope");
  const source = document.getElementById("askDataScope");
  if (workspace) workspace.textContent = askCostPilotWorkspaceLabel();
  if (date) date.textContent = askCostPilotDateLabel();
  if (source && data) {
    const summary = data.summary || {};
    const live = Number(summary.live_count || 0);
    const simulated = Number(summary.simulation_count || 0);
    source.textContent = live && simulated
      ? `${fmtNum(live)} live · ${fmtNum(simulated)} sample`
      : live
        ? `${fmtNum(live)} live requests`
        : simulated
          ? `${fmtNum(simulated)} sample requests`
          : "No matching activity";
  }
}

function renderAskCostPilotSuggestions(activity, departments) {
  const host = document.getElementById("askSuggestions");
  if (!host) return;
  const available = ASK_STARTER_QUESTIONS.filter(item => item.available(activity, departments));
  host.innerHTML = available.length
    ? available.map(item => `<button type="button" class="ask-starter-card"
        data-question="${escapeHtml(item.question)}" onclick="askCostPilotSuggestion(this)">
        <span>${escapeHtml(item.category)}</span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.description)}</small>
        <b>Ask CostPilot <span aria-hidden="true">→</span></b>
      </button>`).join("")
    : `<div class="ask-suggestions-empty">
        <strong>No governed activity matches this scope yet.</strong>
        <span>Change the date range or send a governed request, then CostPilot can suggest data-backed questions.</span>
      </div>`;
}

async function refreshAskCostPilotExperience() {
  updateAskCostPilotScope();
  const range = getActiveDateRange();
  const params = new URLSearchParams({
    date_from: range.date_from,
    date_to: range.date_to,
    days: String(Math.min(365, range.days || 30)),
    activity_limit: "1",
  });
  const workspaceId = reportWorkspaceId();
  if (workspaceId) params.set("workspace_id", workspaceId);
  try {
    const [activityResult, departmentResult] = await Promise.allSettled([
      apiGet(`/api/work-items/activity-report?${params.toString()}`),
      apiGet(`/api/reports/departments?${reportApiParams(range)}`),
    ]);
    const activity = activityResult.status === "fulfilled" ? activityResult.value : null;
    const departments = departmentResult.status === "fulfilled" ? departmentResult.value : null;
    askCostPilotAvailability = { activity, departments };
    updateAskCostPilotScope(activity);
    renderAskCostPilotSuggestions(activity, departments);
  } catch (_error) {
    renderAskCostPilotSuggestions(null, null);
    const source = document.getElementById("askDataScope");
    if (source) source.textContent = "Availability check failed";
  }
}

function focusAskCostPilotScope() {
  const control = document.getElementById("rptDatePreset");
  control?.scrollIntoView({ behavior: "smooth", block: "center" });
  control?.focus();
}

window.focusAskCostPilotScope = focusAskCostPilotScope;

function askCostPilotFilterValue(id) {
  const element = document.getElementById(id);
  return element ? element.value || null : null;
}

function askCostPilotPayload(question) {
  const range = getActiveDateRange();
  const workspaceId = reportWorkspaceId() || null;
  const agentValue = askCostPilotFilterValue("ctxAgentFilter");
  const payload = {
    question,
    days: Math.min(365, range.days || 30),
    timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    workspace_id: workspaceId,
    date_from: range.date_from || null,
    date_to: range.date_to || null,
    project_id: askCostPilotFilterValue("ctxProjectFilter"),
    user_external_id: askCostPilotFilterValue("ctxPersonFilter"),
    agent_id: agentValue ? Number(agentValue) : null,
    account_id: askCostPilotFilterValue("ctxAccountFilter"),
    source_platform: askCostPilotFilterValue("ctxSourceFilter"),
    record_type: askCostPilotFilterValue("ctxRecordTypeFilter"),
    charged_unit: askCostPilotFilterValue("ctxOrgFilter"),
    business_purpose: askCostPilotFilterValue("ctxPurposeFilter"),
    screen_context: {
      page_path: `${location.pathname}${location.search}`.slice(0, 500),
      page_title: (document.title || "CostPilot Reports").slice(0, 200),
      section: (document.querySelector(".rpt-tab.active")?.textContent || "Ask CostPilot").trim().slice(0, 200),
      visible_metric: null,
      selected_label: null,
    },
    conversation: askCostPilotHistory.slice(-12).map(({ role, content }) => ({ role, content })),
    context: askCostPilotContext,
  };
  // A disambiguation choice the user just clicked -- set after every
  // filter field above so it can't be silently overridden by a stale page
  // filter of the same name. pinned_filter_name tells the backend to keep
  // this one field even though the re-asked question necessarily
  // re-mentions the chosen name.
  const pinnedFilter = _askPendingPinnedFilter;
  _askPendingPinnedFilter = null;
  if (pinnedFilter && pinnedFilter.name && pinnedFilter.value !== null && pinnedFilter.value !== undefined) {
    payload[pinnedFilter.name] = pinnedFilter.name === "agent_id" ? Number(pinnedFilter.value) : pinnedFilter.value;
    payload.pinned_filter_name = pinnedFilter.name;
  }
  return payload;
}

function askCostPilotComposerKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submitAskCostPilot(event);
  }
}

function askCostPilotWelcomeMarkup() {
  return `<div class="ask-message assistant">
    <div class="ask-avatar" aria-hidden="true">CP</div>
    <div class="ask-message-body">
      <strong>Ask a question or choose one above.</strong>
      <p>CostPilot will show how it understood the question, the exact date range, supporting records, and the calculation behind the answer.</p>
    </div>
  </div>`;
}

function restoreAskCostPilotConversation() {
  const conversation = document.getElementById("askConversation");
  if (!conversation || !askCostPilotHistory.length) return;
  conversation.innerHTML = "";
  askCostPilotHistory.slice(-12).forEach(item => {
    if (item.role === "user") {
      appendAskMessage("user", `<p>${escapeHtml(item.content || "")}</p>`);
    } else if (item.role === "assistant") {
      appendAskMessage(
        "assistant",
        item.data ? renderAskCostPilotAnswer(item.data) : `<p>${escapeHtml(item.content || "")}</p>`,
      );
    }
  });
}

function clearAskCostPilotConversation() {
  askCostPilotHistory = [];
  askCostPilotContext = null;
  removeAskCostPilotStorage();
  const conversation = document.getElementById("askConversation");
  if (conversation) conversation.innerHTML = askCostPilotWelcomeMarkup();
}

window.clearAskCostPilotConversation = clearAskCostPilotConversation;

function appendAskMessage(role, html) {
  const conversation = document.getElementById("askConversation");
  if (!conversation) return null;
  const message = document.createElement("div");
  message.className = `ask-message ${role}`;
  message.innerHTML = role === "assistant"
    ? `<div class="ask-avatar" aria-hidden="true">CP</div><div class="ask-message-body">${html}</div>`
    : `<div class="ask-message-body">${html}</div>`;
  conversation.appendChild(message);
  message.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return message;
}

function normalizeAskDrillScope(scopeOrName, filterValue) {
  let source = scopeOrName;
  if (typeof source === "string" && source.trim().startsWith("{")) {
    try { source = JSON.parse(source); } catch (_error) { source = {}; }
  } else if (typeof source === "string") {
    source = { [source]: filterValue };
  }
  if (source?.scope) source = source.scope;
  if (source?.filterName) source = { [source.filterName]: source.filterValue };
  const normalized = {};
  ASK_DRILL_KEYS.forEach(key => {
    const value = source?.[key];
    if (value !== null && value !== undefined && String(value).trim() !== "") {
      normalized[key] = key === "date_from" || key === "date_to"
        ? String(value).trim().slice(0, 10)
        : String(value);
    }
  });
  // Not a filter — the real display name for whichever filter this drill
  // just set, so the dropdown can show it instead of the raw id when the
  // drilled-to person/project/account/agent isn't in the default-loaded
  // option list yet (see applyScopeSelects in drillFromAskCostPilot).
  if (source?.filter_label !== null && source?.filter_label !== undefined && String(source.filter_label).trim() !== "") {
    normalized.filter_label = String(source.filter_label).trim();
  }
  return normalized;
}

function askDrillScopeForEvidence(data, item) {
  const provenance = data?.data_provenance || {};
  const scope = normalizeAskDrillScope({
    ...(data?.filters || {}),
    ...(provenance.active_filters || {}),
    date_from: data?.period?.date_from,
    date_to: data?.period?.date_to,
  });
  if (item?.filter_name && item.filter_value !== null && item.filter_value !== undefined) {
    scope[item.filter_name] = String(item.filter_value);
    if (item.label) scope.filter_label = String(item.label);
  }
  return normalizeAskDrillScope(scope);
}

function askDrillScopeFromLocation() {
  const params = new URLSearchParams(location.search);
  const scope = {};
  ASK_DRILL_KEYS.forEach(key => { if (params.get(key)) scope[key] = params.get(key); });
  if (params.get("filter_label")) scope.filter_label = params.get("filter_label");
  return normalizeAskDrillScope(scope);
}

function syncAskDrillQuery(scope = null) {
  const params = new URLSearchParams(location.search);
  ASK_DRILL_KEYS.forEach(key => params.delete(key));
  params.set("tab", "contexts");
  const current = scope || {
    ...getActiveDateRange(),
    ...Object.fromEntries(Object.entries(ASK_DRILL_FILTER_IDS).map(([key, id]) => [key, projectAttributionFilterValue(id)])),
  };
  Object.entries(normalizeAskDrillScope(current)).forEach(([key, value]) => params.set(key, value));
  history.replaceState(null, "", `${location.pathname}?${params.toString()}${location.hash}`);
}

function clearAskDrillQuery() {
  const params = new URLSearchParams(location.search);
  ASK_DRILL_KEYS.forEach(key => params.delete(key));
  history.replaceState(null, "", `${location.pathname}?${params.toString()}${location.hash}`);
}

function askEvidenceButton(item, data) {
  const isClarification = data.intent === "clarification" || data.intent === "clarification_required" || data.data_provenance?.scope === "clarification_required";
  if (isClarification) {
    // item.question is a reconstructed, context-preserving follow-up
    // (e.g. "Tell me about the Support department") -- re-asking the
    // bare label alone lost all context and got misclassified as a
    // product/help question instead of continuing the drill-down.
    const choice = item.question || item.label || item.value || "";
    // filter_name/filter_value carry the exact resolved row (the same
    // convention drill-through buttons use) -- re-asking text alone can't
    // disambiguate two candidates that share a root word (e.g. "Support"
    // the department vs. "Support Agent" the agent both reduce to the
    // same name tokens), so the click pins the filter directly instead of
    // asking the matcher to guess a second time.
    const filterAttrs = item.filter_name && item.filter_value !== null && item.filter_value !== undefined
      ? ` data-filter-name="${escapeHtml(item.filter_name)}" data-filter-value="${escapeHtml(String(item.filter_value))}"`
      : "";
    return `<button type="button" class="ask-evidence-row ask-evidence-choice"
        data-question="${escapeHtml(choice)}"${filterAttrs} onclick="askCostPilotSuggestion(this)">
      <div>
        <strong>${escapeHtml(item.label || "Unknown")}</strong>
        <span>${escapeHtml(item.detail || "")}</span>
      </div>
    </button>`;
  }
  const canDrill = item.filter_name && item.filter_value !== null && item.filter_value !== undefined;
  const encodedScope = encodeURIComponent(JSON.stringify(askDrillScopeForEvidence(data, item)));
  const drill = canDrill
    ? `<button type="button" class="ask-evidence-drill"
         data-scope="${escapeHtml(encodedScope)}"
         onclick="drillFromAskCostPilot(decodeURIComponent(this.dataset.scope))">
         View activity <span aria-hidden="true">→</span>
       </button>`
    : "";
  return `<div class="ask-evidence-row">
    <div>
      <strong>${escapeHtml(item.label || "Unknown")}</strong>
      <span>${escapeHtml(item.detail || "")}</span>
    </div>
    <div class="ask-evidence-value">
      <strong>${escapeHtml(item.value || "—")}</strong>
      <span>${escapeHtml(item.metric_label || "")}</span>
      ${drill}
    </div>
  </div>`;
}

function renderAskCostPilotAnswer(data) {
  const provenance = data.data_provenance || {};
  const clarification = provenance.scope === "clarification_required" || data.intent === "clarification" || data.intent === "clarification_required";
  const evidence = (data.evidence || []).length
    ? `<div class="ask-answer-section">
        <h4>Evidence</h4>
        <div class="ask-evidence-list">${data.evidence.map(item => askEvidenceButton(item, data)).join("")}</div>
       </div>`
    : "";
  const recommendations = (data.recommendations || []).length
    ? `<div class="ask-answer-section">
        <h4>Recommended next steps</h4>
        <div class="ask-recommendations">${data.recommendations.map(item => `
          <div><strong>${escapeHtml(item.title || "Review opportunity")}</strong>
          <p>${escapeHtml(item.body || "")}</p></div>`).join("")}</div>
       </div>`
    : "";
  const period = provenance.period_label || (data.period?.date_from && data.period?.date_to
    ? `${String(data.period.date_from).slice(0, 10)} through ${String(data.period.date_to).slice(0, 10)}`
    : "Selected reporting period");
  const liveRequests = Number(provenance.live_requests || 0);
  const simulatorRequests = Number(provenance.simulator_requests || 0);
  const scopeLabel = provenance.scope === "product_knowledge"
    ? "CostPilot product knowledge"
    : provenance.scope === "live"
    ? `Live data · ${liveRequests} requests`
    : provenance.scope === "simulator"
      ? `Simulator data · ${simulatorRequests} requests`
      : provenance.scope === "mixed"
        ? `Live + simulator · ${liveRequests} live / ${simulatorRequests} simulated`
        : "No matching activity";
  const activeFilters = Object.entries(provenance.active_filters || {})
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "")
    .map(([key, value]) => `<span>${escapeHtml(key.replaceAll("_", " "))}: ${escapeHtml(String(value))}</span>`)
    .join("");
  const calculationUnit = data.intent === "budget"
    ? `${escapeHtml(String(data.calculation?.row_count || 0))} configured departments`
    : `${escapeHtml(String(data.calculation?.row_count || 0))} matching requests`;
  const calculation = data.calculation
    ? `<div class="ask-answer-section ask-calculation-detail">
        <h4>Calculation</h4>
        <p>${escapeHtml(data.calculation.formula || "")} across
        ${calculationUnit}.</p>
       </div>`
    : "";
  const supportingCount = Number(data.calculation?.row_count || data.summary?.request_count || 0);
  const supportingScope = encodeURIComponent(JSON.stringify(askDrillScopeForEvidence(data, null)));
  const supportingRecords = supportingCount > 0 && data.intent !== "budget"
    ? `<button type="button" class="ask-supporting-records" data-scope="${escapeHtml(supportingScope)}"
         onclick="drillFromAskCostPilot(decodeURIComponent(this.dataset.scope))">
         View ${fmtNum(supportingCount)} supporting ${supportingCount === 1 ? "record" : "records"} <span aria-hidden="true">→</span>
       </button>`
    : "";
  const followUps = askCostPilotFollowUps(data);
  const followUpMarkup = followUps.length
    ? `<div class="ask-answer-section ask-follow-ups"><h4>You might also ask</h4><div>${followUps.map(question => `
        <button type="button" data-question="${escapeHtml(question)}" onclick="askCostPilotSuggestion(this)">${escapeHtml(question)}</button>
      `).join("")}</div></div>`
    : "";
  const statusLabel = clarification ? "Needs clarification" : "Calculated";
  const proactiveNote = data.proactive_note
    ? `<div class="ask-proactive-note severity-${escapeHtml(data.proactive_note.severity || "info")}">
        <strong>${escapeHtml(data.proactive_note.title || "Worth a look")}</strong>
        <p>${escapeHtml(data.proactive_note.detail || "")}</p>
       </div>`
    : "";
  const budgetFlag = renderAskBudgetFlag(data.budget_flag);
  const workspaceLabel = renderAskWorkspaceLabel(data.workspace_name);
  return `
    ${workspaceLabel}
    <div class="ask-answer-header">
      <div><span class="ask-answer-kicker">${escapeHtml(period)}</span>
      <h3>${escapeHtml(data.title || "CostPilot answer")}</h3></div>
      <span class="ask-calculated${clarification ? " needs-clarification" : ""}">${escapeHtml(statusLabel)}</span>
    </div>
    ${budgetFlag}
    ${data.interpreted_as ? `<div class="ask-interpretation"><strong>Interpreted as</strong><span>${escapeHtml(data.interpreted_as)}</span></div>` : ""}
    <div class="ask-answer-meta">
      <div><strong>Date range</strong><span>${escapeHtml(period)}</span></div>
      <div><strong>Scope</strong><span>${escapeHtml(scopeLabel)}</span></div>
    </div>
    <div class="ask-answer-body${clarification ? " ask-clarification" : ""}">${
      data.answer ? renderAskMarkdown(data.answer) : "<p>No answer was returned.</p>"
    }</div>
    ${activeFilters ? `<div class="ask-active-filters"><strong>Active filters</strong>${activeFilters}</div>` : ""}
    ${evidence}
    ${calculation}
    ${supportingRecords}
    ${recommendations}
    ${proactiveNote}
    ${followUpMarkup}
    <div class="ask-answer-note">${escapeHtml(data.measurement_note || "Calculated from governed CostPilot activity.")}</div>
  `;
}

function askCostPilotFollowUps(data) {
  if (Array.isArray(data.suggested_questions) && data.suggested_questions.length) {
    return data.suggested_questions.slice(0, 6).map(String);
  }
  if (data.intent === "clarification" || data.intent === "clarification_required") return [];
  if (data.intent === "budget") {
    return [
      "How much AI budget is left this month?",
      "Are we on track to exceed budget this month?",
      "Which departments are closest to their budget limits?",
      "What is our budget variance so far this month?",
      "How much budget does each department have?",
    ];
  }
  if (["comparison", "drivers", "change_drivers"].includes(data.intent)) {
    return [
      "Which department contributed most to the change?",
      "Was the change within budget?",
      "Which people contributed most to the change?",
      "Which agents contributed most to the change?",
      "Which accounts contributed most to the change?",
    ];
  }
  if (["person", "people"].includes(data.entity)) {
    return ["Compare the top people with the previous period.", "Which agents did they use?", "How much did the top people spend?"];
  }
  if (["agent", "agents"].includes(data.entity)) {
    return ["Which agents have not been used?", "Which models did the top agents use?", "How much did these agents cost?"];
  }
  if (["account", "context"].includes(data.entity)) {
    return ["Which records drove the top account's usage?", "Compare the top accounts with last month.", "Which agents worked on these accounts?"];
  }
  return [
    "What changed compared with the previous period?",
    "Where is most of this activity coming from?",
    "Are we on track to exceed budget this month?",
    "Who used the most tokens this month?",
  ];
}

function askCostPilotErrorMessage(error) {
  const status = Number(error?.status || 0);
  if (status === 503) return "The analytics service is temporarily unavailable. No answer was generated. Please try again shortly.";
  if (status === 403) return "You do not have access to the requested workspace or records.";
  if (status === 404) return "CostPilot could not find matching activity for that request.";
  if (status === 422) return "CostPilot could not safely interpret that question. Add a subject and date range, then try again.";
  return error?.message || "CostPilot could not calculate the answer. Please try again.";
}

async function submitAskCostPilot(event) {
  if (event) event.preventDefault();
  const input = document.getElementById("askCostPilotInput");
  const button = document.getElementById("askCostPilotSend");
  const question = (input ? input.value : "").trim();
  if (!question || !button) return;

  appendAskMessage("user", `<p>${escapeHtml(question)}</p>`);
  input.value = "";
  button.disabled = true;
  button.textContent = "Calculating…";
  const pending = appendAskMessage(
    "assistant",
    `<div class="ask-thinking"><span></span><span></span><span></span> <span class="ask-thinking-text">Checking governed activity</span></div>`
  );
  const stopThinking = startAskThinkingCycle(pending.querySelector(".ask-thinking-text"));

  try {
    const payload = askCostPilotPayload(question);
    let data;
    try {
      data = await apiPost("/api/reports/bot-efficiency/ask", payload);
    } catch (error) {
      // A saved conversation from an older release can fail validation after
      // the assistant context schema evolves. Retry the same question once
      // without stale context instead of making the user clear chat manually.
      if (error && error.status === 422) {
        askCostPilotHistory = [];
        askCostPilotContext = null;
        writeAskCostPilotStorage("history", askCostPilotHistory);
        writeAskCostPilotStorage("context", askCostPilotContext);
        data = await apiPost("/api/reports/bot-efficiency/ask", {
          ...payload,
          conversation: [],
          context: null,
        });
      } else {
        throw error;
      }
    }
    stopThinking();
    if (pending) pending.querySelector(".ask-message-body").innerHTML = renderAskCostPilotAnswer(data);
    askCostPilotHistory.push(
      { role: "user", content: question },
      { role: "assistant", content: data.answer || "", data },
    );
    askCostPilotContext = data.conversation_context || askCostPilotContext;
    if (askCostPilotHistory.length > 12) {
      askCostPilotHistory.splice(0, askCostPilotHistory.length - 12);
    }
    writeAskCostPilotStorage("history", askCostPilotHistory);
    writeAskCostPilotStorage("context", askCostPilotContext);
  } catch (error) {
    stopThinking();
    if (pending) {
      pending.querySelector(".ask-message-body").innerHTML =
        `<div class="ask-error-state"><strong>CostPilot did not generate an answer.</strong><p>${escapeHtml(askCostPilotErrorMessage(error))}</p><span>Your question and existing data were not changed.</span></div>`;
    }
  } finally {
    button.disabled = false;
    button.innerHTML = `Ask CostPilot <span aria-hidden="true">→</span>`;
    input.focus();
  }
}

async function drillFromAskCostPilot(scopeOrName, filterValue) {
  const scope = normalizeAskDrillScope(scopeOrName, filterValue);
  // A single governed request has no Contexts-tab filter of its own --
  // route it to the audit log entry itself, same as the global Ask drawer's
  // openAskDrill (global-nav.js) already does for this exact scope shape.
  if (scope.audit_event_id) {
    try { sessionStorage.setItem("cp_audit_pending_event", JSON.stringify(scope.audit_event_id)); } catch (_error) {}
    location.href = "/operate.html#audit";
    return;
  }
  const tab = document.querySelector('.rpt-tab[data-tab="contexts"]');
  if (!Object.keys(scope).length || !tab) return;

  // Activate the attribution view without firing the tab click handler. The
  // click handler starts its own unawaited load, which can race this drill-down
  // and overwrite the selected value with the previous report state.
  document.querySelectorAll(".rpt-tab").forEach(button => button.classList.remove("active"));
  document.querySelectorAll(".rpt-pane").forEach(pane => pane.classList.remove("active"));
  tab.classList.add("active");
  activeTab = "contexts";
  const pane = document.getElementById("tab-contexts");
  if (pane) pane.classList.add("active");

  if (scope.date_from && scope.date_to) {
    const from = new Date(`${scope.date_from}T00:00:00`);
    const to = new Date(`${scope.date_to}T00:00:00`);
    if (!Number.isNaN(from.getTime()) && !Number.isNaN(to.getTime()) && to > from) {
      reportDrillDateRange = {
        date_from: scope.date_from,
        date_to: scope.date_to,
        days: Math.max(1, Math.ceil((to.getTime() - from.getTime()) / 86400000)),
      };
      const preset = document.getElementById("rptDatePreset");
      const custom = document.getElementById("rptCustomDates");
      const start = document.getElementById("rptDateFrom");
      const end = document.getElementById("rptDateTo");
      if (preset) preset.value = "custom";
      if (custom) custom.style.display = "flex";
      if (start) start.value = scope.date_from;
      if (end) end.value = new Date(to.getTime() - 1).toISOString().slice(0, 10);
    }
  }

  // Populate valid values first, then preserve every filter represented by the
  // answer. If a historical entity is absent from the current option set, add
  // it so the evidence link still produces the exact calculated scope.
  const applyScopeSelects = () => Object.entries(ASK_DRILL_FILTER_IDS).forEach(([key, selectId]) => {
      const value = scope[key];
      if (!value) return;
      const select = document.getElementById(selectId);
      if (!select) return;
      if (![...select.options].some(option => option.value === value)) {
        select.add(new Option(scope.filter_label || value, value));
      }
      select.value = value;
    });
  await loadBusinessContexts();
  applyScopeSelects();
  await loadBusinessContexts();
  applyScopeSelects();
  // loadBusinessContexts() only refreshes the KPI cards and the always-
  // unfiltered "Company AI Usage" summary -- confirmed live: the AI
  // Activity Explorer pivot above it kept showing every department's
  // company-wide numbers after an Ask CostPilot drill-through, even
  // though the Department/Team filter it reads (explorerCrossFilters())
  // was correctly set to the drilled department. Nothing had ever told
  // the pivot to refetch after the scope changed.
  _explorerScope = [];
  loadExplorerPivot();
  syncAskDrillQuery(scope);
  pane?.scrollIntoView({ behavior: "smooth", block: "start" });
}

window.drillFromAskCostPilot = drillFromAskCostPilot;

window.getCostPilotAskScope = function getCostPilotAskScope() {
  const payload = askCostPilotPayload("");
  delete payload.question;
  delete payload.conversation;
  delete payload.context;
  // charged_unit already covers the Department/Team filter (read from the
  // same ctxOrgFilter select the Explorer's department drill also uses).
  // Layer the Explorer's own id-based drill scope (Person/Agent/Account/
  // Work Item) on top when present -- these come from real dimension_ids,
  // the same fields askCostPilotPayload already sends for the legacy
  // filter selects, so Ask CostPilot inherits validated scope either way
  // it was set.
  _explorerScope.forEach(level => {
    if (level.dim === "person") payload.user_external_id = level.value;
    if (level.dim === "agent") payload.agent_id = Number(level.value);
    if (level.dim === "account") payload.account_id = level.value;
    if (level.dim === "work_item") payload.project_id = level.value;
  });
  return payload;
};

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
    const agents = await apiGet(reportScopedPath("/api/agents"));

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

function renderGovernanceReportHtml() {
  const data = _rptRiskData;
  if (!data) return `<div class="report-doc"><p>No risk data loaded — open the Governance &amp; Risk tab first.</p></div>`;
  const d = _rptGovernanceDashboard || {};
  const generatedAt = new Date().toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
  const kpiCard = (label, value, sub) => `
    <div class="report-kpi">
      <div class="report-kpi-label-row"><span class="report-kpi-label">${escapeHtml(label)}</span></div>
      <div class="report-kpi-value">${value}</div>
      ${sub ? `<div class="report-kpi-sub">${escapeHtml(sub)}</div>` : ""}
    </div>`;

  const summary = data.critical > 0 || data.high > 0
    ? `Over the selected period, CostPilot logged ${fmtNum(data.total_events)} governance events, including `
      + `${fmtNum(data.critical)} critical and ${fmtNum(data.high)} high-risk events. `
      + `${fmtNum(data.blocked)} requests were blocked by policy before reaching an AI model.`
    : `Over the selected period, CostPilot logged ${fmtNum(data.total_events)} governance events with no critical `
      + `or high-risk findings. ${fmtNum(data.blocked)} requests were blocked by policy before reaching an AI model.`;

  const collisionBreakdown = d.collision_breakdown || { lock: d.collision_count || 0, queue: 0, skip: 0 };
  const complianceRows = [
    ["Requests Blocked", fmtNum(d.blocked_count), "Sensitive terms triggered block policy before the request reached an AI model."],
    ["Escalated to Flagship", fmtNum(d.escalated_count), "Requests routed to Advisor, Strategist, or flagship review."],
    ["Flagged in Audit Log", fmtNum(d.flagged_count), "High-risk keywords logged for compliance review."],
    ["PII Detected", fmtNum(d.pii_count), "Credit cards, SSNs, emails, phone numbers caught before AI processing."],
    ["Budget Overruns Prevented", fmtNum(d.throttle_prevented), "Auto-throttle engaged before a department cap was breached."],
    ["Agent Collisions Controlled", fmtNum(d.collision_count),
      `${collisionBreakdown.lock || 0} locked · ${collisionBreakdown.queue || 0} queued · ${collisionBreakdown.skip || 0} skipped — zero silent overwrites.`],
  ];

  const eventRows = _rptRiskEvents.slice(0, 15).map(e => `
    <tr>
      <td>${escapeHtml(fmtTs(e.timestamp))}</td>
      <td>${escapeHtml(e.event_type || "")}</td>
      <td>${escapeHtml(displayDeptName(e.display_department || e.department) || "")}</td>
      <td>${escapeHtml(e.risk_level || "")}</td>
      <td>${escapeHtml(e.decision_outcome || "")}</td>
    </tr>`).join("") || `<tr><td colspan="5">No events in this period.</td></tr>`;

  return `
    <div class="report-doc">
      ${reportDocHeaderHtml("Governance & Risk Report", generatedAt)}

      <section class="report-section">
        <h2 class="report-section-title">Executive Brief</h2>
        <p class="bi-summary">${summary}</p>
        <div class="report-kpi-row">
          ${kpiCard("Total Events", fmtNum(data.total_events), "audit log entries")}
          ${kpiCard("Critical", fmtNum(data.critical), "HIPAA, fraud, lawsuits")}
          ${kpiCard("High Risk", fmtNum(data.high), "legal, compliance, locks")}
          ${kpiCard("Blocked Requests", fmtNum(data.blocked), "stopped by term policy")}
          ${kpiCard("Agent Collisions", fmtNum(data.locks), "concurrent write conflicts")}
          ${kpiCard("Term Library", fmtNum(data.term_library?.total), `${data.term_library?.block ?? 0} block / ${data.term_library?.escalate ?? 0} escalate`)}
        </div>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Risk Trend</h2>
        <div class="report-chart-row">
          ${savingsReportChartImg("chartRiskTimeline", "Daily risk events by severity")}
          ${savingsReportChartImg("chartRiskBreakdown", "Risk level breakdown")}
        </div>
      </section>

      <section class="report-section">
        <h2 class="report-section-title">Governance &amp; Compliance Activity</h2>
        <table class="rpt-context-table">
          <thead><tr><th>Control</th><th>Count</th><th>What it means</th></tr></thead>
          <tbody>${complianceRows.map(([label, value, note]) => `
            <tr><td>${escapeHtml(label)}</td><td>${value}</td><td>${escapeHtml(note)}</td></tr>`).join("")}</tbody>
        </table>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Recent High-Stakes Events</h2>
        <table class="rpt-context-table">
          <thead><tr><th>Timestamp</th><th>Event Type</th><th>Department</th><th>Risk Level</th><th>Decision</th></tr></thead>
          <tbody>${eventRows}</tbody>
        </table>
        ${_rptRiskEvents.length > 15 ? `<div class="bi-note">Showing the 15 most recent of ${fmtNum(_rptRiskEvents.length)} events. See the live Governance &amp; Risk tab for the full audit log.</div>` : ""}
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Evidence &amp; Methodology</h2>
        <div class="bi-note">
          Every event in this report comes from CostPilot's immutable audit log — the same governed request ledger
          every other CostPilot report and Ask CostPilot answer reads from. This report introduces no separate
          calculation or risk scoring.
        </div>
      </section>

      <footer class="report-footer">CostPilot — Governance &amp; Risk Report — ${escapeHtml(askCostPilotWorkspaceLabel())}</footer>
    </div>`;
}

function exportRiskPdf() {
  if (!_rptRiskData) {
    alert("No risk data loaded yet — open the Governance & Risk tab first.");
    return;
  }
  let container = document.getElementById("riskReportDoc");
  if (!container) {
    container = document.createElement("div");
    container.id = "riskReportDoc";
    container.style.display = "none";
    document.getElementById("tab-risk").after(container);
  }
  container.innerHTML = renderGovernanceReportHtml();
  printSection("riskReportDoc", "CostPilot — Governance & Risk Report");
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

function renderDepartmentsReportHtml() {
  if (!_rptDeptData.length) return `<div class="report-doc"><p>No department data loaded — open the Department detail view first.</p></div>`;
  const generatedAt = new Date().toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
  const totalCost = _rptDeptData.reduce((sum, d) => sum + (d.total_cost_usd || 0), 0);
  const totalSaved = _rptDeptData.reduce((sum, d) => sum + (d.pruning_saved_usd || 0), 0);
  const throttled = _rptDeptData.filter(d => d.throttled);

  const summary = throttled.length
    ? `Across ${fmtNum(_rptDeptData.length)} departments, CostPilot governed ${fmtUsd(totalCost)} in AI spend this `
      + `period, with ${fmtUsd(totalSaved)} saved through pruning. ${throttled.length} department${throttled.length === 1 ? " is" : "s are"} `
      + `currently throttled: ${throttled.map(d => displayDeptName(d.display_department || d.department)).join(", ")}.`
    : `Across ${fmtNum(_rptDeptData.length)} departments, CostPilot governed ${fmtUsd(totalCost)} in AI spend this `
      + `period, with ${fmtUsd(totalSaved)} saved through pruning. No department is currently throttled.`;

  const rows = _rptDeptData.map(d => `
    <tr>
      <td>${escapeHtml(displayDeptName(d.display_department || d.department))}</td>
      <td>${fmtNum(d.total_calls)}</td>
      <td>${d.micro_pct}%</td>
      <td>${fmtUsd(d.total_cost_usd)}</td>
      <td>${fmtUsd(d.pruning_saved_usd)}</td>
      <td>${d.budget_used_pct}%</td>
      <td>${fmtUsd(d.monthly_cap_usd)}</td>
      <td>${d.throttled ? "Throttled" : d.override_granted ? "Override" : "OK"}</td>
    </tr>`).join("");

  return `
    <div class="report-doc">
      ${reportDocHeaderHtml("Department Report", generatedAt)}

      <section class="report-section">
        <h2 class="report-section-title">Executive Brief</h2>
        <p class="bi-summary">${summary}</p>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Spend by Department</h2>
        <div class="report-chart-row">
          ${savingsReportChartImg("chartDeptSpend", "Daily spend by department")}
          ${savingsReportChartImg("chartDeptCost", "Total cost by department")}
        </div>
      </section>

      <section class="report-section">
        <h2 class="report-section-title">Department Scorecard</h2>
        <table class="rpt-context-table">
          <thead><tr><th>Department</th><th>Calls</th><th>Micro %</th><th>Actual Cost</th><th>Pruning Saved</th><th>Budget Used</th><th>Monthly Cap</th><th>Status</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Evidence &amp; Methodology</h2>
        <div class="bi-note">
          Every figure above comes from the same governed request ledger every other CostPilot report and Ask
          CostPilot answer reads from. This report introduces no separate calculation.
        </div>
      </section>

      <footer class="report-footer">CostPilot — Department Report — ${escapeHtml(askCostPilotWorkspaceLabel())}</footer>
    </div>`;
}

function exportDeptPdf() {
  if (!_rptDeptData.length) {
    alert("No department data loaded yet — open the Department detail view first.");
    return;
  }
  let container = document.getElementById("deptReportDoc");
  if (!container) {
    container = document.createElement("div");
    container.id = "deptReportDoc";
    container.style.display = "none";
    document.getElementById("tab-departments").after(container);
  }
  container.innerHTML = renderDepartmentsReportHtml();
  printSection("deptReportDoc", "CostPilot — Department Report");
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

// Captures the four already-rendered Chart.js canvases as static images at
// build time (not relying on printSection's own canvas->image swap, which
// operates on the DOM section being printed -- this report is a separate,
// purpose-built document, not a clone of #tab-savings).
function savingsReportChartImg(canvasId, caption) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return "";
  // These charts are built once for the live, DARK dashboard (COLORS.muted
  // tick/legend text) and captured as-is via toDataURL() -- confirmed live:
  // once pasted onto this report's WHITE page, that text is nearly
  // invisible (same root cause as the Ask CostPilot report chart bug this
  // session). Rather than a second print-only chart instance, temporarily
  // swap the LIVE chart's text/grid colors to print-safe dark ones, snapshot,
  // then restore -- the on-screen dashboard is never left altered.
  const chartKey = canvasId.replace(/^chart/, "").replace(/^./, c => c.toLowerCase());
  const chart = charts[chartKey];
  const PRINT_TEXT = "#1a2733", PRINT_GRID = "#e2e6ea";
  const restoreFns = [];
  if (chart) {
    const swapColor = (obj, key, value) => {
      if (!obj) return;
      const prev = obj[key];
      obj[key] = value;
      restoreFns.push(() => { obj[key] = prev; });
    };
    if (chart.options?.plugins?.legend?.labels) swapColor(chart.options.plugins.legend.labels, "color", PRINT_TEXT);
    Object.values(chart.options?.scales || {}).forEach(scale => {
      swapColor(scale.ticks, "color", PRINT_TEXT);
      swapColor(scale.grid, "color", PRINT_GRID);
    });
    if (restoreFns.length) chart.update("none");
  }
  let dataUrl;
  try { dataUrl = canvas.toDataURL("image/png"); } catch (_err) { dataUrl = null; }
  if (restoreFns.length) {
    restoreFns.forEach(fn => fn());
    chart.update("none");
  }
  if (!dataUrl) return "";
  return `
    <figure class="report-chart-figure">
      <img src="${dataUrl}" style="max-width:100%;height:auto" />
      <figcaption>${escapeHtml(caption)}</figcaption>
    </figure>`;
}

function renderSavingsReportHtml() {
  const data = _rptSavingsData;
  if (!data) return `<div class="report-doc"><p>No savings data loaded — open the Performance tab first.</p></div>`;
  const generatedAt = new Date().toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
  const kpiCard = (label, value, sub) => `
    <div class="report-kpi">
      <div class="report-kpi-label-row"><span class="report-kpi-label">${escapeHtml(label)}</span></div>
      <div class="report-kpi-value">${value}</div>
      ${sub ? `<div class="report-kpi-sub">${escapeHtml(sub)}</div>` : ""}
    </div>`;

  // Deterministic sentence built from the same fields the KPI cards already
  // render -- same pattern biNarrativeSummary() uses for Business Impact,
  // not free-form LLM math.
  const summary = `Over the selected period, CostPilot processed ${fmtNum(data.total_calls)} calls at an actual `
    + `cost of ${fmtUsd(data.total_cost_usd)}, against an estimated ${fmtUsd(data.cost_if_no_fage_usd)} at full `
    + `flagship rate — a total savings of ${fmtUsd(data.total_saved_usd)} from context pruning and model routing combined.`;

  return `
    <div class="report-doc">
      ${reportDocHeaderHtml("Savings & Performance Report", generatedAt)}

      <section class="report-section">
        <h2 class="report-section-title">Executive Brief</h2>
        <p class="bi-summary">${summary}</p>
        <div class="report-kpi-row">
          ${kpiCard("Total Saved", fmtUsd(data.total_saved_usd), "pruning + model downgrade")}
          ${kpiCard("Without CostPilot", fmtUsd(data.cost_if_no_fage_usd), "est. cost at full flagship rate")}
          ${kpiCard("Actual Cost", fmtUsd(data.total_cost_usd), "what you paid")}
          ${kpiCard("Pruning Saved", fmtUsd(data.pruning_saved_usd), `${fmtNum(data.tokens_pruned)} tokens removed`)}
          ${kpiCard("Model Downgrade Saved", fmtUsd(data.downgrade_saved_usd), `${data.micro_pct}% routed to micro`)}
          ${kpiCard("Total Calls", fmtNum(data.total_calls), `${fmtNum(data.micro_calls)} micro / ${fmtNum(data.flagship_calls)} flagship`)}
        </div>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">AI Investment &amp; Trends</h2>
        <div class="report-chart-row">
          ${savingsReportChartImg("chartDailySpend", "Daily spend across the selected period")}
          ${savingsReportChartImg("chartModelSplit", "Model tier split — micro vs. flagship")}
        </div>
        <div class="report-chart-row">
          ${savingsReportChartImg("chartTokensPruned", "Daily tokens pruned")}
          ${savingsReportChartImg("chartSavingsBreakdown", "Savings breakdown")}
        </div>
      </section>

      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Evidence &amp; Methodology</h2>
        <div class="bi-note">
          CostPilot reports consumption and attribution only. Pruning and model-downgrade savings are calculated
          against the same governed request ledger every other CostPilot report and Ask CostPilot answer reads
          from — this report introduces no separate calculation.
        </div>
      </section>

      <footer class="report-footer">CostPilot — Savings &amp; Performance Report — ${escapeHtml(askCostPilotWorkspaceLabel())}</footer>
    </div>`;
}

function exportSavingsPdf() {
  if (!_rptSavingsData) {
    alert("No savings data loaded yet — open the Performance tab first.");
    return;
  }
  let container = document.getElementById("savingsReportDoc");
  if (!container) {
    container = document.createElement("div");
    container.id = "savingsReportDoc";
    container.style.display = "none";
    document.getElementById("tab-savings").after(container);
  }
  container.innerHTML = renderSavingsReportHtml();
  printSection("savingsReportDoc", "CostPilot — Savings Report");
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
  updateReportWorkspaceBanner();
  restoreAskCostPilotConversation();
  const requestedTab = new URLSearchParams(window.location.search).get("tab");
  let pendingScope = {};
  try {
    const pendingDrill = JSON.parse(sessionStorage.getItem("cp_ask_pending_drill") || "null");
    if (pendingDrill) {
      sessionStorage.removeItem("cp_ask_pending_drill");
      pendingScope = normalizeAskDrillScope(pendingDrill);
    }
  } catch (_error) {
    sessionStorage.removeItem("cp_ask_pending_drill");
  }
  const locationScope = askDrillScopeFromLocation();
  const drillScope = Object.keys(locationScope).length ? locationScope : pendingScope;
  // Open the requested tab immediately and unconditionally — this used to
  // only happen when there was NO filter scope, so any failure anywhere in
  // the scope-parsing path silently left the default Performance tab
  // active even though the URL correctly said tab=contexts (an Ask
  // CostPilot "View activity" link landing on the wrong tab). Tab
  // selection and filter application are now independent: the tab is
  // always right, and drillFromAskCostPilot only has to get the filters
  // right on top of it.
  if (requestedTab && document.getElementById(`tab-${requestedTab}`)) {
    openReportView(requestedTab, { preserveUrl: true });
  } else {
    // No ?tab= param -- the normal case when reaching this page from the
    // nav sidebar rather than a deep link. The default tab's pane is
    // already active in markup, but its data was never fetched, so it
    // showed empty/stale until the 30s auto-refresh interval first fired.
    loadActiveTab();
  }
  if (Object.keys(drillScope).length) {
    setTimeout(() => drillFromAskCostPilot(drillScope), 100);
  }
  updateReportRangeSummary();
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
    const d = await apiGet(reportScopedPath("/api/dashboard"));
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
