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
