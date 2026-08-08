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
  if (data.intent === "clarification") return [];
  if (data.intent === "comparison" || data.intent === "drivers") {
    return ["Which department contributed most to the change?", "Was the change within budget?"];
  }
  if (data.entity === "people") return ["Compare the top people with the previous period."];
  if (data.entity === "agents") return ["Which models did the top agents use?"];
  return ["What changed compared with the previous period?"];
}

function renderAskAnswerCard(data) {
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
  const clarification = provenance.scope === "clarification_required" || data.intent === "clarification";
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
  const followUps = askFollowUps(data);
  const budgetFlag = renderAskBudgetFlag(data.budget_flag);
  const workspaceLabel = renderAskWorkspaceLabel(data.workspace_name);
  return `<article class="cp-ask-answer">
    ${workspaceLabel}
    <div class="cp-ask-answer-head"><span>${askRenderEscapeHtml(provenance.period_label || "Selected period")}</span><b class="${clarification ? "clarification" : ""}">${clarification ? "Needs clarification" : "Calculated"}</b></div>
    <h3>${askRenderEscapeHtml(data.title || "CostPilot answer")}</h3>
    ${budgetFlag}
    ${data.interpreted_as ? `<div class="cp-ask-interpretation"><strong>Interpreted as</strong><span>${askRenderEscapeHtml(data.interpreted_as)}</span></div>` : ""}
    <div class="cp-ask-answer-scope"><span><b>Date range</b>${askRenderEscapeHtml(provenance.period_label || "Selected period")}</span><span><b>Scope</b>${askRenderEscapeHtml(sourceLabel)}</span></div>
    <div class="cp-ask-answer-body">${data.answer ? renderAskMarkdown(data.answer) : "<p>No answer was returned.</p>"}</div>
    ${activeFilters ? `<div class="cp-ask-active-filters"><strong>Active filters</strong>${activeFilters}</div>` : ""}
    ${evidence ? `<section><h4>Evidence</h4>${evidence}</section>` : ""}
    ${calculation}
    ${supportingRecords}
    ${recommendations}
    ${followUps.length ? `<section><h4>You might also ask</h4><div class="cp-ask-followups">${followUps.map(question => `<button type="button" data-ask-question="${askRenderEscapeHtml(question)}">${askRenderEscapeHtml(question)}</button>`).join("")}</div></section>` : ""}
    <small>${askRenderEscapeHtml(data.measurement_note || "Calculated from governed CostPilot activity.")}</small>
  </article>`;
}
