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
  printSection(btn.dataset.askReportTarget, btn.dataset.askReportTitle);
  return true;
}

function renderAskAnswerCard(data) {
  const cardId = `cp-ask-answer-${++_askAnswerCardSeq}`;
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
