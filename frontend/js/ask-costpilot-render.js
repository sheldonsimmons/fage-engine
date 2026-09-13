/**
 * ask-costpilot-render.js — shared Ask CostPilot answer-rendering helpers.
 *
 * There are two independent Ask CostPilot UIs in this app: the full panel
 * on reports.html (js/reports.js) and the floating widget embedded in the
 * global nav on every other page (js/global-nav.js). Both call the same
 * /api/reports/bot-efficiency/ask endpoint and need to render the same
 * response shape the same way. Keep that rendering logic here, once, so
 * the two UIs can't silently drift apart again.
 *
 * Load this file before reports.js and before global-nav.js.
 */

function askRenderEscapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// A real Ask CostPilot answer needs two sequential Claude turns (pick a
// tool, then write the answer), each taking several real seconds of model
// reasoning/generation -- confirmed live via direct timing instrumentation,
// not something caching or backend work can shorten. Rather than one
// static "thinking" line for the whole wait, cycle it through a few honest,
// timing-matched stages so silence doesn't read as "stuck." Shared here so
// both Ask CostPilot surfaces show the identical sequence.
const ASK_THINKING_STAGES = [
  { at: 0, text: "Thinking…" },
  { at: 2500, text: "Checking your data…" },
  { at: 5500, text: "Crunching the numbers…" },
  { at: 9000, text: "Almost there…" },
  { at: 15000, text: "Still working — this one's taking a bit longer…" },
];

function startAskThinkingCycle(textEl) {
  if (!textEl) return () => {};
  textEl.textContent = ASK_THINKING_STAGES[0].text;
  const timers = ASK_THINKING_STAGES.slice(1).map(stage =>
    setTimeout(() => { textEl.textContent = stage.text; }, stage.at)
  );
  return function stop() {
    timers.forEach(clearTimeout);
  };
}

function renderAskMarkdown(text) {
  const escaped = askRenderEscapeHtml(text);
  const inline = (line) => line
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+?)`/g, "<code>$1</code>");

  const lines = escaped.split("\n");
  const html = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*\|.*\|\s*$/.test(line)) {
      const tableLines = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        tableLines.push(lines[i]);
        i++;
      }
      const rows = tableLines
        .map(row => row.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim()))
        .filter(cells => !cells.every(cell => /^:?-{2,}:?$/.test(cell)));
      if (rows.length) {
        const [head, ...body] = rows;
        html.push(`<div style="overflow-x:auto"><table class="ask-markdown-table"><thead><tr>${
          head.map(cell => `<th>${inline(cell)}</th>`).join("")
        }</tr></thead><tbody>${
          body.map(cells => `<tr>${cells.map(cell => `<td>${inline(cell)}</td>`).join("")}</tr>`).join("")
        }</tbody></table></div>`);
      }
      continue;
    }

    if (/^#{1,4}\s+/.test(line)) {
      const level = Math.min(4, line.match(/^#+/)[0].length) + 2;
      html.push(`<h${level}>${inline(line.replace(/^#{1,4}\s+/, ""))}</h${level}>`);
      i++;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ""))}</li>`);
        i++;
      }
      html.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*\d+\.\s+/, ""))}</li>`);
        i++;
      }
      html.push(`<ol>${items.join("")}</ol>`);
      continue;
    }

    if (line.trim() === "") {
      i++;
      continue;
    }

    const paragraph = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== "" && !/^\s*[-*|#]/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i])) {
      paragraph.push(lines[i]);
      i++;
    }
    html.push(`<p>${inline(paragraph.join(" "))}</p>`);
  }
  return html.join("");
}

function renderAskWorkspaceLabel(workspaceName) {
  if (!workspaceName) return "";
  return `<div class="ask-workspace-label">📁 Viewing: <strong>${askRenderEscapeHtml(workspaceName)}</strong></div>`;
}

function renderAskBudgetFlag(flag) {
  if (!flag || !flag.severity || flag.severity === "unknown") return "";

  if (flag.severity === "ok") {
    return `<div class="ask-budget-flag severity-ok">✅ No departments over budget for the active workspace.</div>`;
  }
  const overNames = (flag.over_budget || []).map(d => askRenderEscapeHtml(d.department || "")).filter(Boolean);
  const nearNames = (flag.near_cap || []).map(d => askRenderEscapeHtml(d.department || "")).filter(Boolean);
  const parts = [];
  if (overNames.length) parts.push(`🚨 <strong>${overNames.length} over budget:</strong> ${overNames.join(", ")}`);
  if (nearNames.length) parts.push(`⚠️ <strong>${nearNames.length} near cap:</strong> ${nearNames.join(", ")}`);
  const severity = flag.severity === "critical" ? "critical" : "warning";
  return `<div class="ask-budget-flag severity-${severity}">${parts.join(" &nbsp;·&nbsp; ")}</div>`;
}

// ── Full answer-card rendering (drills, evidence, follow-ups) ──────────────
// Originally lived only inside global-nav.js's floating panel. The Overview
// page's inline "Ask CostPilot" box (frontend/index.html) needed the exact
// same card — not a second, thinner version of it — so this is now the one
// place both build it.

const ASK_RENDER_DRILL_KEYS = [
  "date_from", "date_to", "project_id", "user_external_id", "account_id", "agent_id",
  "source_platform", "record_type", "charged_unit", "business_purpose", "audit_event_id",
];

function normalizeAskRenderDrillScope(scopeOrName, filterValue) {
  let source = scopeOrName;
  if (typeof source === "string" && source.trim().startsWith("{")) {
    try { source = JSON.parse(source); } catch (_) { source = {}; }
  } else if (typeof source === "string") {
    source = { [source]: filterValue };
  }
  if (source?.scope) source = source.scope;
  if (source?.filterName) source = { [source.filterName]: source.filterValue };
  const normalized = {};
  ASK_RENDER_DRILL_KEYS.forEach((key) => {
    const value = source?.[key];
    if (value !== null && value !== undefined && String(value).trim() !== "") {
      normalized[key] = key === "date_from" || key === "date_to"
        ? String(value).trim().slice(0, 10)
        : String(value).trim();
    }
  });
  // Not a filter — carried alongside so reports.html can show the real name
  // instead of the raw id when the drilled-to person/project/account/agent
  // isn't already in its default-loaded dropdown options.
  if (source?.filter_label !== null && source?.filter_label !== undefined && String(source.filter_label).trim() !== "") {
    normalized.filter_label = String(source.filter_label).trim();
  }
  return normalized;
}

function askDrillScope(data, item) {
  const provenance = data?.data_provenance || {};
  const scope = normalizeAskRenderDrillScope({
    ...(data?.filters || {}),
    ...(provenance.active_filters || {}),
    date_from: data?.period?.date_from,
    date_to: data?.period?.date_to,
  });
  if (item?.filter_name && item.filter_value !== null && item.filter_value !== undefined) {
    scope[item.filter_name] = String(item.filter_value);
    if (item.label) scope.filter_label = String(item.label);
  }
  return normalizeAskRenderDrillScope(scope);
}

function askDrillUrl(scope) {
  const params = new URLSearchParams({ tab: "contexts" });
  Object.entries(scope).forEach(([key, value]) => {
    if (key !== "audit_event_id") params.set(key, value);
  });
  return `/reports.html?${params.toString()}`;
}

function renderAskEvidence(item, data) {
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
      ? ` data-ask-filter-name="${askRenderEscapeHtml(item.filter_name)}" data-ask-filter-value="${askRenderEscapeHtml(String(item.filter_value))}"`
      : "";
    return `<button type="button" class="cp-ask-evidence" data-ask-question="${askRenderEscapeHtml(choice)}"${filterAttrs}>
      <div><strong>${askRenderEscapeHtml(item.label || "Unknown")}</strong><span>${askRenderEscapeHtml(item.detail || "")}</span></div>
      <div><strong>${askRenderEscapeHtml(item.value || "—")}</strong><span>${askRenderEscapeHtml(item.metric_label || "")}</span></div>
    </button>`;
  }
  const scope = askDrillScope(data, item);
  const drill = item.filter_name && item.filter_value !== null && item.filter_value !== undefined
    ? `<button type="button" data-ask-scope="${askRenderEscapeHtml(encodeURIComponent(JSON.stringify(scope)))}">View activity →</button>`
    : "";
  return `<div class="cp-ask-evidence">
    <div><strong>${askRenderEscapeHtml(item.label || "Unknown")}</strong><span>${askRenderEscapeHtml(item.detail || "")}</span></div>
    <div><strong>${askRenderEscapeHtml(item.value || "—")}</strong><span>${askRenderEscapeHtml(item.metric_label || "")}</span>${drill}</div>
  </div>`;
}

function askFollowUps(data) {
  if (Array.isArray(data.suggested_questions) && data.suggested_questions.length) {
    return data.suggested_questions.slice(0, 2).map(String);
  }
  if (data.intent === "clarification" || data.intent === "clarification_required") return [];
  if (data.intent === "comparison" || data.intent === "drivers") {
    return ["Which department contributed most to the change?", "Was the change within budget?"];
  }
  if (data.entity === "people") return ["Compare the top people with the previous period."];
  if (data.entity === "agents") return ["Which models did the top agents use?"];
  return ["What changed compared with the previous period?"];
}

function askFormatProposalValue(valueObj) {
  if (!valueObj || typeof valueObj !== "object") return "—";
  const entries = Object.entries(valueObj);
  if (!entries.length) return "—";
  const [key, val] = entries[0];
  if (typeof val === "number" && key.toLowerCase().includes("usd")) {
    return `$${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  return String(val);
}

function askFormatEstimatedImpact(impact) {
  if (!impact || typeof impact !== "object") return "";
  const label = impact.label || "Estimated";
  const parts = Object.entries(impact)
    .filter(([key]) => key !== "label" && key !== "note")
    .map(([key, val]) => {
      const niceKey = key.replaceAll("_", " ");
      if (typeof val === "number" && key.toLowerCase().includes("usd")) {
        const sign = val >= 0 ? "+" : "-";
        return `${niceKey}: ${sign}$${Math.abs(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      }
      return `${niceKey}: ${val}`;
    });
  const note = impact.note ? ` (${impact.note})` : "";
  return `${label} — ${parts.join(", ")}${note}`;
}

function askFormatSimulation(simulation, department) {
  if (!simulation || typeof simulation !== "object") return "";
  const fmt = (n) => `$${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const who = department ? `${department} is` : "This department is";
  const projected = `${who} projected to spend ${fmt(simulation.projected_period_end_spend_usd)} this period (currently ${fmt(simulation.current_period_spend_usd)}, ${fmt(simulation.daily_rate_usd)}/day).`;
  if (simulation.proposed_cap_usd === undefined) return `Projected: ${projected}`;
  const capLabel = fmt(simulation.proposed_cap_usd);
  if (simulation.will_exceed_cap) {
    const when = simulation.projected_exceed_date ? ` around ${simulation.projected_exceed_date}` : "";
    return `Projected: ${projected} The ${capLabel} cap would likely be exceeded${when}.`;
  }
  return `Projected: ${projected} The ${capLabel} cap would not be exceeded (${fmt(simulation.headroom_usd)} of headroom).`;
}

function renderAskProposalCard(proposal) {
  if (!proposal) return "";
  const isPending = proposal.status === "awaiting_confirmation";
  const statusLabel = {
    awaiting_confirmation: "Awaiting confirmation",
    executed: "Executed",
    rejected: "Cancelled",
    expired: "Expired — ask again to create a new proposal",
  }[proposal.status] || proposal.status;
  const impact = askFormatEstimatedImpact(proposal.estimated_impact);
  const simulation = askFormatSimulation(proposal.simulation_result, proposal.department);
  return `<section><h4>Proposed action</h4><div class="cp-ask-proposal">
    <div><strong>${askRenderEscapeHtml(proposal.target_id || proposal.target_type || "Proposed change")}</strong>
      <span>${askRenderEscapeHtml(askFormatProposalValue(proposal.current_value))} → ${askRenderEscapeHtml(askFormatProposalValue(proposal.proposed_value))}</span></div>
    ${proposal.reason ? `<div><span>${askRenderEscapeHtml(proposal.reason)}</span></div>` : ""}
    ${impact ? `<div><span>${askRenderEscapeHtml(impact)}</span></div>` : ""}
    ${simulation ? `<div><span>${askRenderEscapeHtml(simulation)}</span></div>` : ""}
    <div><span>Risk: ${askRenderEscapeHtml(proposal.risk_level || "low")} · Status: ${askRenderEscapeHtml(statusLabel)}</span></div>
    ${isPending ? `<div class="cp-ask-proposal-actions">
      <button type="button" class="cp-ask-supporting" data-ask-confirm-id="${askRenderEscapeHtml(String(proposal.id))}">Confirm</button>
      <button type="button" data-ask-reject-id="${askRenderEscapeHtml(String(proposal.id))}">Cancel</button>
    </div>` : ""}
  </div></section>`;
}

let _askAnswerCardSeq = 0;
// Keyed by cardId, not a DOM dataset attribute -- the full answer object
// (evidence pool, query_plan, calculation) is too large/nested to round-trip
// through a data-* string, and this file runs on pages that don't load
// reports.js, so it can't rely on that file's report-building helpers
// either. Self-contained on purpose.
const _askAnswerDataByCardId = new Map();

// Every answer gets a report button, regardless of what the answer contains.
// Gating this on answer "richness" would need a classifier deciding what's
// report-worthy, and that classifier would be wrong sometimes -- especially
// since path selection upstream is non-deterministic, so the same question
// could grow or lose the button between identical runs. Instead the button
// always shows, and printSection() (js/api.js) just clones whatever sections
// this card actually rendered -- evidence, calculation, proposal, etc. are
// already each individually optional above, so a thin answer just prints
// thin. Still strictly better than making the user rebuild it by hand.
function renderAskReportButton(cardId, data) {
  const title = `CostPilot — ${data.title || "Ask CostPilot Answer"}`;
  return `<button type="button" class="cp-ask-report-btn" data-ask-report-target="${cardId}" data-ask-report-title="${askRenderEscapeHtml(title)}">🖨️ Generate report</button>`;
}

// Shared delegated-click handler for the report button, called from each
// Ask CostPilot surface's own click delegation (global-nav.js, index.html,
// business-profile.html) alongside their data-ask-question/-confirm-id/etc.
// handlers. Returns true if it handled the click.
function handleAskReportButtonClick(event) {
  const btn = event.target.closest("[data-ask-report-target]");
  if (!btn) return false;
  const cardId = btn.dataset.askReportTarget;
  generateAskReport(cardId, btn.dataset.askReportTitle, _askAnswerDataByCardId.get(cardId));
  return true;
}

// ── Ask CostPilot -> full report (Phase 2, "re-derive") ─────────────────────
// Printing the chat card as-is (the fallback below) only ever shows the
// answer's own curated top-5 evidence. When the answer came from the agent
// tool loop, query_plan records exactly which tool/args produced it -- this
// replays that ONE call at report scale (api/routes_efficiency.py's
// /ask/report-data, never a fresh LLM call) and builds a real multi-section
// report from the fuller result. Deterministic-path answers and anything
// the replay can't make sense of fall back to the plain card print --
// same "works uniformly, degrades gracefully" principle the button itself
// was built on.

function askReportFmtUsd(v) {
  const n = Number(v || 0);
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
}
function askReportFmtNum(v) {
  return Number(v || 0).toLocaleString();
}

// Data-producing tools whose result shape this knows how to turn into a
// report table + chart. Tools that only ever return a single fact (get_
// product_help, propose_/simulate_/measure_budget_cap_*, get_data_coverage)
// or free text are deliberately not listed -- there is no "more rows" to
// ask for, so the fallback (print the chat card) already shows everything
// a report could.
const ASK_REPORT_REPLAYABLE_TOOLS = new Set([
  "query_metrics", "get_budget_status", "get_priority_signals",
  "get_usage_report", "get_change_drivers", "get_agent_adoption",
]);

function askReportRowsFromResult(tool, result) {
  if (!result || typeof result !== "object") return null;
  if (tool === "get_budget_status" && Array.isArray(result.departments)) {
    return {
      metricLabel: "Budget used", valueFormat: "pct",
      rows: result.departments.map(d => ({
        label: d.label, value: Number(d.used_pct || 0),
        sub: `${askReportFmtUsd(d.current_spend_usd)} of ${askReportFmtUsd(d.monthly_cap_usd)}${d.throttled ? " · throttled" : ""}`,
      })),
    };
  }
  if (tool === "get_priority_signals" && Array.isArray(result.signals)) {
    // Severity is categorical, not a rankable number -- a table-only
    // section, no chart, matching "don't force a chart on data that isn't
    // a ranked comparison."
    return {
      metricLabel: "Severity", valueFormat: "text",
      rows: result.signals.map(s => ({ label: s.label, value: s.severity, sub: s.detail })),
    };
  }
  if (tool === "get_agent_adoption" && Array.isArray(result.agents)) {
    return {
      metricLabel: "Status", valueFormat: "text",
      rows: result.agents.map(a => ({ label: a.name, value: a.status, sub: a.department || "" })),
    };
  }
  if (tool === "get_change_drivers" && Array.isArray(result.top_contributors)) {
    return {
      metricLabel: "Change", valueFormat: "usd",
      rows: result.top_contributors.map(c => ({
        label: c.label || c.name, value: Number(c.absolute_change || 0),
        sub: c.percent_change != null ? `${c.percent_change}% change` : "",
      })),
    };
  }
  if (tool === "get_usage_report") {
    const list = result.top_people || result.top_departments || result.top_agents
      || result.top_accounts || result.top_platforms || result.top_models || result.top_providers;
    if (Array.isArray(list)) {
      return {
        metricLabel: "AI spend", valueFormat: "usd",
        rows: list.map(r => ({ label: r.label || r.name, value: Number(r.spend_usd || 0), sub: r.request_count != null ? `${askReportFmtNum(r.request_count)} requests` : "" })),
      };
    }
  }
  if (tool === "query_metrics" && Array.isArray(result.rows)) {
    const metricKey = (result.metrics && result.metrics[0]) || Object.keys(result.rows[0] || {}).find(k => k !== "dimensions" && k !== "dimension_ids");
    const valueFormat = metricKey && metricKey.toLowerCase().includes("spend") ? "usd" : "num";
    // A "compare to X" refinement re-fetches with compare_to set, and the
    // backend (core/metrics_query.py's comparison_block) comes back with a
    // SEPARATE comparison.rows array keyed by dimension label, not merged
    // into result.rows -- without this, the refinement silently changed
    // nothing visible (confirmed live: status said "Applied.", output was
    // byte-identical). Match by label and fold the prior-period delta into
    // each row's caption.
    let compareByLabel = null;
    if (result.comparison && Array.isArray(result.comparison.rows)) {
      compareByLabel = new Map();
      result.comparison.rows.forEach(cr => {
        const label = Object.values(cr.dimensions || {})[0] ?? "Unknown";
        const m = cr[metricKey];
        if (m) compareByLabel.set(label, m);
      });
    }
    return {
      metricLabel: metricKey || "Value", valueFormat,
      rows: result.rows.map(r => {
        const label = Object.values(r.dimensions || {})[0] ?? "Unknown";
        let sub = "";
        const cmp = compareByLabel && compareByLabel.get(label);
        if (cmp) {
          const prevFmt = askReportFormatValue(cmp.previous, valueFormat);
          const pct = cmp.pct_difference;
          const arrow = cmp.difference > 0 ? "▲" : cmp.difference < 0 ? "▼" : "—";
          sub = `vs ${prevFmt} prior period ${arrow}${pct != null ? ` ${Math.abs(pct)}%` : ""}`;
        }
        return { label, value: Number(r[metricKey] || 0), sub };
      }),
    };
  }
  return null;
}

function askReportFormatValue(value, format) {
  if (format === "usd") return askReportFmtUsd(value);
  if (format === "pct") return `${value}%`;
  if (format === "num") return askReportFmtNum(value);
  return askRenderEscapeHtml(String(value ?? ""));
}

// Renders an ad-hoc horizontal bar chart to a hidden canvas and captures it
// as a static image -- same reasoning as printSection()'s own canvas fix:
// print output needs a bitmap, not a live canvas. Chart.js isn't loaded on
// every page this file runs on, so this degrades to "table only, no chart"
// rather than throwing when it's unavailable.
function askReportChartImg(rows, metricLabel, valueFormat, forceChart) {
  if (forceChart === false) return "";
  // valueFormat === "text" (categorical, e.g. priority-signal severity) is
  // still skipped even when forceChart === true -- there's no numeric
  // ranking to plot, "add a chart" can't invent one.
  if (typeof Chart === "undefined" || valueFormat === "text" || rows.length < 2) return "";
  const canvas = document.createElement("canvas");
  canvas.width = 700;
  canvas.height = Math.max(220, rows.length * 28);
  document.body.appendChild(canvas);
  let dataUrl = "";
  try {
    const chart = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: rows.map(r => String(r.label)),
        datasets: [{ label: metricLabel, data: rows.map(r => r.value), backgroundColor: "rgba(37,196,181,0.75)" }],
      },
      options: {
        indexAxis: "y", responsive: false, animation: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true } },
      },
    });
    chart.resize();
    chart.render();
    dataUrl = canvas.toDataURL("image/png");
    chart.destroy();
  } catch (_err) { /* leave dataUrl empty -- table still renders without it */ }
  canvas.remove();
  if (!dataUrl) return "";
  return `<figure class="report-chart-figure" style="max-width:600px"><img src="${dataUrl}" style="width:100%;height:auto" /></figure>`;
}

function buildAskGeneratedReportHtml(data, tool, replayed, options = {}) {
  const provenance = data.data_provenance || {};
  const generatedAt = new Date().toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
  const workspaceLabel = data.workspace_name || (typeof getActiveWorkspace === "function" ? (getActiveWorkspace()?.name || getActiveWorkspace()?.label) : null) || "Current workspace";
  const extracted = askReportRowsFromResult(tool, replayed);

  const evidence = (data.evidence || []).map((item) => renderAskEvidence(item, data)).join("");
  const recommendations = (data.recommendations || []).length
    ? `<section class="report-section"><h2 class="report-section-title">Recommended Next Steps</h2><div class="bi-rec-grid">${
        data.recommendations.map(r => `<div class="bi-rec-card"><div class="bi-rec-title">${askRenderEscapeHtml(r.title || "")}</div><div class="bi-rec-body">${askRenderEscapeHtml(r.body || "")}</div></div>`).join("")
      }</div></section>`
    : "";

  const tableSection = extracted ? `
    <section class="report-section">
      <h2 class="report-section-title">${askRenderEscapeHtml(extracted.metricLabel)} — full breakdown</h2>
      ${askReportChartImg(extracted.rows, extracted.metricLabel, extracted.valueFormat, options.forceChart)}
      <table class="rpt-context-table">
        <thead><tr><th></th><th>${askRenderEscapeHtml(extracted.metricLabel)}</th><th></th></tr></thead>
        <tbody>${extracted.rows.map((r, i) => `
          <tr><td class="bi-rank">${i + 1}</td><td>${askRenderEscapeHtml(String(r.label))}</td>
          <td>${askReportFormatValue(r.value, extracted.valueFormat)}${r.sub ? ` <span style="color:#777;font-size:10px">(${askRenderEscapeHtml(r.sub)})</span>` : ""}</td></tr>`).join("")}
        </tbody>
      </table>
    </section>` : "";

  return `
    <div class="report-doc">
      ${ASK_REPORT_LOGO_SVG}
      <header class="report-header" style="border-top:none">
        <h1 class="report-title">${askRenderEscapeHtml(data.title || "CostPilot Report")}</h1>
        <div class="report-meta">
          <span>${askRenderEscapeHtml(workspaceLabel)}</span>
          <span>${askRenderEscapeHtml(provenance.period_label || "Selected period")}</span>
          <span>Generated ${askRenderEscapeHtml(generatedAt)}</span>
        </div>
      </header>
      <section class="report-section">
        <h2 class="report-section-title">Executive Brief</h2>
        <p class="bi-summary">${askRenderEscapeHtml(data.answer || "")}</p>
      </section>
      ${tableSection}
      ${evidence ? `<section class="report-section"><h2 class="report-section-title">Evidence</h2>${evidence}</section>` : ""}
      ${recommendations}
      <section class="report-section" style="break-inside:avoid">
        <h2 class="report-section-title">Evidence &amp; Methodology</h2>
        <div class="bi-note">${askRenderEscapeHtml(data.measurement_note || "CostPilot reports consumption and attribution only. It does not score employee productivity or infer business outcomes.")}</div>
      </section>
      <footer class="report-footer">CostPilot — ${askRenderEscapeHtml(data.title || "Report")} — ${askRenderEscapeHtml(workspaceLabel)}</footer>
    </div>`;
}

// Same inline-SVG logo as reports.js's ASK_REPORT_LOGO_SVG (duplicated, not
// shared -- this file runs on pages that never load reports.js). Inlined
// rather than an <img src> for the same reason: confirmed live there that
// a network-loaded image hadn't finished loading by the time window.print()
// fired.
const ASK_REPORT_LOGO_SVG = `<svg class="report-logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 930 150" role="img" aria-label="CostPilot" style="height:22px;width:auto;display:block;margin-bottom:8px">
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

// Deterministic-path answers have no query_plan (that's an agent-loop-only
// field) -- but they already carry the exact metric/dimension/filter shape
// a report needs, just under different names: {entity, metric, filters,
// interpreted_intent: {days, period_key}}, the parsed-intent structure
// _resolve_ask_intent already builds (api/routes_efficiency.py). Since
// query_metrics's args ARE metrics/dimensions/filters/days/period_key,
// a deterministic answer can be replayed the exact same way as an
// agent-loop one -- just synthesized client-side into the same shape,
// no backend change needed.
const ASK_REPORT_ENTITY_TO_DIMENSION = {
  person: "person", agent: "agent", department: "department", platform: "platform", model: "model",
  // "context" and "overview" both land on routes_efficiency.py's generic
  // project_breakdown catch-all ("Where your AI usage went" / "AI spend
  // on won opportunities") whenever no more specific entity was named --
  // confirmed live, a plain "AI spend overview" question comes back with
  // entity="overview", not "context" as the backend's own entity_config
  // naming would suggest. Both are the same per-work-item breakdown
  // shape, which query_metrics already exposes as the registered
  // "work_item" dimension (core/metrics_catalog.py). Without this
  // mapping, this was the single most common "Generate Report" case with
  // no chart/table at all -- silently falling through to the plain-card
  // print instead.
  context: "work_item", overview: "work_item",
};
const ASK_REPORT_METRIC_TO_CATALOG = {
  spend_usd: "ai_spend", request_count: "ai_requests", tokens_saved: "tokens_saved_count",
};
// "context"/"overview" are ALSO the entity value for several totally
// different answer shapes (department budgets, tier mix, activity
// narration...) that have nothing to do with a work-item breakdown -- the
// entity alone can't tell those apart (confirmed live: a budget question
// comes back with entity="overview" too). Only these two intents are the
// actual project_breakdown branches (routes_efficiency.py's "Where your
// AI usage went" / "AI spend on won opportunities"); gating on intent as
// well is what keeps this from attaching a work-item chart to an
// unrelated answer.
const _ASK_REPORT_WORK_ITEM_INTENTS = new Set(["overview", "total"]);

function askReportSyntheticStep(data) {
  let dimension = ASK_REPORT_ENTITY_TO_DIMENSION[data.entity];
  if ((data.entity === "context" || data.entity === "overview") && !_ASK_REPORT_WORK_ITEM_INTENTS.has(data.intent)) {
    dimension = null;
  }
  const metric = ASK_REPORT_METRIC_TO_CATALOG[data.metric];
  // "overview"/"request" entities and non-catalog metrics
  // (risk_event_count, avg_cost_per_request, contract_validation,
  // product_knowledge, ...) have no ranked-table shape to report on --
  // correctly falls through to the plain card print below, same as an
  // agent-loop answer whose tool isn't in ASK_REPORT_REPLAYABLE_TOOLS.
  if (!dimension || !metric) return null;
  const filters = {};
  Object.entries(data.filters || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== "") filters[k] = v;
  });
  const interpreted = data.interpreted_intent || {};
  let days = Number(interpreted.days) || 0;
  if (!days && data.period?.date_from && data.period?.date_to) {
    const from = new Date(data.period.date_from);
    const to = new Date(data.period.date_to);
    if (!isNaN(from) && !isNaN(to) && to > from) days = Math.max(1, Math.ceil((to - from) / 86400000));
  }
  return {
    tool: "query_metrics", status: "ok",
    args: {
      metrics: [metric], dimensions: [dimension], filters,
      days: days || 30, period_key: interpreted.period_key || "none",
      sort: metric, limit: 20,
    },
  };
}

async function fetchAskReportData(step, isRefinement) {
  const response = await fetch("/api/reports/bot-efficiency/ask/report-data", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question: "", workspace_id: localStorage.getItem("cp_workspace_id") || null,
      tool: step.tool, args: step.args || {}, is_refinement: !!isRefinement,
    }),
  });
  if (!response.ok) throw new Error(`report-data request failed (${response.status})`);
  return (await response.json()).result;
}

async function generateAskReport(cardId, title, data) {
  const lastDataStep = data && Array.isArray(data.query_plan)
    ? [...data.query_plan].reverse().find(step => step.status === "ok" && ASK_REPORT_REPLAYABLE_TOOLS.has(step.tool))
    : null;
  const step = lastDataStep || (data ? askReportSyntheticStep(data) : null);
  if (!data || !step) {
    printSection(cardId, title); // nothing worth replaying (a help/product/decision-style answer) -- print the card as-is
    return;
  }
  try {
    const result = await fetchAskReportData(step);
    showAskReportPreview(cardId, title, data, step, result);
  } catch (_err) {
    printSection(cardId, title); // fetch/build failed -- still strictly better than no report at all
  }
}

// ── Phase 3: conversational report refinement ───────────────────────────────
// Scoped deliberately narrow: a fixed set of PATTERN-MATCHED refinements
// that map onto real query_metrics parameters (comparison period, row
// count, chart on/off) -- never a free-form LLM rewrite of the report.
// "No LLM-generated numbers" was already the rule for the report's own
// data; extending that same rule to *how the report gets edited* is the
// point, not a limitation to hide. An unrecognized request says plainly
// what IS supported instead of guessing at intent.
const _askReportPreviewState = new Map(); // keyed by cardId

function askReportParseRefinement(text) {
  const t = (text || "").toLowerCase();
  if (/\b(compare|comparison|vs\.?|versus)\b/.test(t)) {
    if (/\bquarter\b/.test(t)) return { type: "compare", compare_to: "previous_quarter", label: "previous quarter" };
    if (/\byear\b/.test(t)) return { type: "compare", compare_to: "previous_year", label: "previous year" };
    if (/\bmonth\b/.test(t)) return { type: "compare", compare_to: "previous_month", label: "previous month" };
    if (/\b(period|week)\b/.test(t)) return { type: "compare", compare_to: "previous_period", label: "the prior period" };
  }
  const topMatch = t.match(/\btop\s*(\d{1,3})\b/) || t.match(/\bshow\s*(\d{1,3})\b/);
  if (topMatch) return { type: "limit", limit: Math.max(1, Math.min(100, parseInt(topMatch[1], 10))) };
  if (/\b(remove|hide|no)\b.*\bchart\b/.test(t)) return { type: "chart", show: false };
  if (/\b(add|show)\b.*\bchart\b/.test(t)) return { type: "chart", show: true };
  return null;
}

function askReportApplyRefinementToStep(step, refinement) {
  const next = { ...step, args: { ...step.args } };
  if (refinement.type === "compare") next.args.compare_to = refinement.compare_to;
  if (refinement.type === "limit") next.args.limit = refinement.limit;
  return next;
}

async function applyAskReportRefinement(cardId) {
  const state = _askReportPreviewState.get(cardId);
  if (!state) return;
  const input = document.getElementById(`${cardId}-refine-input`);
  const status = document.getElementById(`${cardId}-refine-status`);
  const text = input ? input.value.trim() : "";
  if (!text) return;
  const refinement = askReportParseRefinement(text);
  if (!refinement) {
    if (status) status.textContent = 'Not sure how to apply that. Try: "compare to last month/quarter/year", "top 10", or "remove the chart".';
    return;
  }
  if (status) status.textContent = "Applying…";
  try {
    let html;
    if (refinement.type === "chart") {
      html = buildAskGeneratedReportHtml(state.data, state.step.tool, state.result, { forceChart: refinement.show });
      state.forceChart = refinement.show;
    } else {
      state.step = askReportApplyRefinementToStep(state.step, refinement);
      state.result = await fetchAskReportData(state.step, true);
      html = buildAskGeneratedReportHtml(state.data, state.step.tool, state.result, { forceChart: state.forceChart });
    }
    const body = document.getElementById(`${cardId}-preview-body`);
    if (body) body.innerHTML = html;
    if (status) status.textContent = "Applied.";
    if (input) input.value = "";
  } catch (_err) {
    if (status) status.textContent = "Could not apply that refinement — the report is unchanged.";
  }
}

function closeAskReportPreview(cardId) {
  const modal = document.getElementById(`${cardId}-preview`);
  if (modal) modal.remove();
  _askReportPreviewState.delete(cardId);
}

function printAskReportPreview(cardId, title) {
  const body = document.getElementById(`${cardId}-preview-body`);
  if (!body) return;
  printSection(body.id, title);
}

async function saveAskReportPreview(cardId, title) {
  const state = _askReportPreviewState.get(cardId);
  if (!state) return;
  const defaultTitle = title || "Ask CostPilot Report";
  const saveTitle = prompt("Save this report as:", defaultTitle);
  if (saveTitle === null) return; // cancelled
  try {
    await apiPost("/api/saved-reports", {
      workspace_id: localStorage.getItem("cp_workspace_id") || null,
      title: (saveTitle || "").trim() || defaultTitle,
      report_type: "ask_costpilot",
      // The step, including any refinement already applied (compare_to/
      // limit) -- reopening this saved report replays THIS exact recipe,
      // not the original unrefined question.
      source: { tool: state.step.tool, args: state.step.args || {} },
    });
    alert('Report saved. Reopen it anytime from "Saved reports."');
  } catch (err) {
    alert("Could not save report: " + (err.message || "unknown error"));
  }
}

function showAskReportPreview(cardId, title, data, step, result) {
  closeAskReportPreview(cardId);
  _askReportPreviewState.set(cardId, { data, step, result, forceChart: undefined });
  const modal = document.createElement("div");
  modal.id = `${cardId}-preview`;
  modal.className = "cp-report-preview-backdrop";
  modal.innerHTML = `
    <div class="cp-report-preview-modal">
      <div class="cp-report-preview-toolbar">
        <input type="text" id="${cardId}-refine-input" placeholder='Refine this report — e.g. "compare to last month", "top 10", "remove the chart"' />
        <button type="button" data-ask-report-refine="${cardId}">Apply</button>
        <button type="button" data-ask-report-save="${cardId}" class="cp-report-preview-print">💾 Save</button>
        <button type="button" data-ask-report-print="${cardId}" class="cp-report-preview-print">🖨 Print / Save as PDF</button>
        <button type="button" data-ask-report-close="${cardId}" class="cp-report-preview-close">✕</button>
      </div>
      <div class="cp-report-preview-status" id="${cardId}-refine-status"></div>
      <div class="cp-report-preview-scroll">
        <div id="${cardId}-preview-body">${buildAskGeneratedReportHtml(data, step.tool, result)}</div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeAskReportPreview(cardId);
    if (event.target.closest(`[data-ask-report-close="${cardId}"]`)) closeAskReportPreview(cardId);
    if (event.target.closest(`[data-ask-report-refine="${cardId}"]`)) applyAskReportRefinement(cardId);
    if (event.target.closest(`[data-ask-report-save="${cardId}"]`)) saveAskReportPreview(cardId, title);
    if (event.target.closest(`[data-ask-report-print="${cardId}"]`)) printAskReportPreview(cardId, title);
  });
  const input = document.getElementById(`${cardId}-refine-input`);
  if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") applyAskReportRefinement(cardId); });
}

function renderAskAnswerCard(data) {
  const cardId = `cp-ask-answer-${++_askAnswerCardSeq}`;
  _askAnswerDataByCardId.set(cardId, data);
  const provenance = data.data_provenance || {};
  const liveRequests = Number(provenance.live_requests || 0);
  const simulatorRequests = Number(provenance.simulator_requests || 0);
  const sourceLabel = {
    live: `Live data · ${liveRequests} requests`,
    simulator: `Simulator data · ${simulatorRequests} requests`,
    mixed: `Live + simulator · ${liveRequests} live / ${simulatorRequests} simulated`,
    no_activity: "No matching activity",
    product_knowledge: "CostPilot product knowledge",
    clarification_required: "Answer withheld for verification",
  }[provenance.scope] || "Governed activity";
  const clarification = provenance.scope === "clarification_required" || data.intent === "clarification" || data.intent === "clarification_required";
  const evidence = (data.evidence || []).map((item) => renderAskEvidence(item, data)).join("");
  const activeFilters = Object.entries(provenance.active_filters || {})
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "")
    .map(([key, value]) => `<span>${askRenderEscapeHtml(key.replaceAll("_", " "))}: ${askRenderEscapeHtml(String(value))}</span>`)
    .join("");
  const calculation = data.calculation
    ? `<div class="cp-ask-calculation"><strong>Calculation</strong><span>${askRenderEscapeHtml(data.calculation.formula || "")} across ${askRenderEscapeHtml(String(data.calculation.row_count || 0))} matching requests.</span></div>`
    : "";
  const recommendations = (data.recommendations || []).length
    ? `<section><h4>Recommended next steps</h4><div class="cp-ask-recommendations">${data.recommendations.map((item) => `
        <div><strong>${askRenderEscapeHtml(item.title || "Review opportunity")}</strong><span>${askRenderEscapeHtml(item.body || "")}</span></div>
      `).join("")}</div></section>`
    : "";
  const rowCount = Number(data.calculation?.row_count || data.summary?.request_count || 0);
  const answerScope = askDrillScope(data, null);
  const supportingRecords = rowCount > 0
    ? `<button type="button" class="cp-ask-supporting" data-ask-scope="${askRenderEscapeHtml(encodeURIComponent(JSON.stringify(answerScope)))}">View ${rowCount.toLocaleString()} supporting ${rowCount === 1 ? "record" : "records"} →</button>`
    : "";
  const proposalCard = renderAskProposalCard(data.proposal);
  const followUps = askFollowUps(data);
  const budgetFlag = renderAskBudgetFlag(data.budget_flag);
  const workspaceLabel = renderAskWorkspaceLabel(data.workspace_name);
  return `<article class="cp-ask-answer" id="${cardId}">
    ${workspaceLabel}
    <div class="cp-ask-answer-head"><span>${askRenderEscapeHtml(provenance.period_label || "Selected period")}</span><b class="${clarification ? "clarification" : ""}">${clarification ? "Needs clarification" : "Calculated"}</b></div>
    <h3>${askRenderEscapeHtml(data.title || "CostPilot answer")}</h3>
    ${renderAskReportButton(cardId, data)}
    ${budgetFlag}
    ${data.interpreted_as ? `<div class="cp-ask-interpretation"><strong>Interpreted as</strong><span>${askRenderEscapeHtml(data.interpreted_as)}</span></div>` : ""}
    <div class="cp-ask-answer-scope"><span><b>Date range</b>${askRenderEscapeHtml(provenance.period_label || "Selected period")}</span><span><b>Scope</b>${askRenderEscapeHtml(sourceLabel)}</span></div>
    <div class="cp-ask-answer-body">${data.answer ? renderAskMarkdown(data.answer) : "<p>No answer was returned.</p>"}</div>
    ${activeFilters ? `<div class="cp-ask-active-filters"><strong>Active filters</strong>${activeFilters}</div>` : ""}
    ${evidence ? `<section><h4>Evidence</h4>${evidence}</section>` : ""}
    ${calculation}
    ${supportingRecords}
    ${proposalCard}
    ${recommendations}
    ${followUps.length ? `<section><h4>You might also ask</h4><div class="cp-ask-followups">${followUps.map(question => `<button type="button" data-ask-question="${askRenderEscapeHtml(question)}">${askRenderEscapeHtml(question)}</button>`).join("")}</div></section>` : ""}
    <small>${askRenderEscapeHtml(data.measurement_note || "Calculated from governed CostPilot activity.")}</small>
  </article>`;
}
