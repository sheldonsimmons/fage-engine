/**
 * auditor.js — AI Decision Audit Log UI  [Step 6]
 *
 * Loads audit events from GET /api/audit and renders the log table.
 * Clicking a row expands the full rationale inline.
 */

let openRationaleId = null;

async function loadAuditLog() {
  try {
    const events = await apiGet("/api/audit?limit=20");
    auditAllEvents = events;
    const toRender = auditFilterBlocked
      ? events.filter(e => (e.decision_outcome || "").toLowerCase().includes("blocked"))
      : events;
    renderAuditTable(toRender);
    updateBlockedBanner(events);
    // Restore open row and re-fetch its content after re-render
    if (openRationaleId) {
      const row = document.getElementById(`rationale-${openRationaleId}`);
      if (row) {
        row.style.display = "table-row";
        // Re-populate content since the table was re-rendered
        fetchRationaleContent(openRationaleId);
      } else {
        openRationaleId = null;
      }
    }
  } catch (err) {
    document.getElementById("auditTableBody").innerHTML =
      `<tr><td colspan="6" class="placeholder" style="color:var(--accent-red)">Failed to load audit log: ${err.message}</td></tr>`;
  }
}

function updateBlockedBanner(events) {
  const banner   = document.getElementById("blockedAlertBanner");
  const countEl  = document.getElementById("blockedBannerCount");
  const subEl    = document.getElementById("blockedBannerSub");
  if (!banner) return;

  // Count blocked events in the last 24 hours
  const cutoff  = Date.now() - 24 * 60 * 60 * 1000;
  const blocked = events.filter(e =>
    e.event_type === "DECISION" &&
    e.decision_outcome && e.decision_outcome.toLowerCase().includes("blocked") &&
    new Date(e.timestamp + "Z").getTime() >= cutoff
  );

  if (blocked.length === 0) {
    banner.style.display = "none";
    return;
  }

  banner.style.display = "block";
  countEl.textContent = `🚨 ${blocked.length} request${blocked.length > 1 ? "s" : ""} blocked in the last 24 hours`;
  subEl.textContent   = "Sensitive data was detected and stopped before reaching any AI model. Review the audit log below.";
}

let auditFilterBlocked = false;
let auditAllEvents = [];

function toggleBlockedFilter() {
  auditFilterBlocked = !auditFilterBlocked;
  const btn = document.querySelector(".blocked-banner-btn");
  if (btn) btn.textContent = auditFilterBlocked ? "Show All Events" : "Review Blocked Events ↓";

  const indicator = document.getElementById("auditFilterIndicator");
  if (indicator) indicator.style.display = auditFilterBlocked ? "inline-block" : "none";

  const filtered = auditFilterBlocked
    ? auditAllEvents.filter(e => (e.decision_outcome || "").toLowerCase().includes("blocked"))
    : auditAllEvents;
  renderAuditTable(filtered);
}

function renderAuditTable(events) {
  const tbody = document.getElementById("auditTableBody");
  if (!events.length) {
    tbody.innerHTML =
      '<tr><td colspan="6" class="placeholder">No audit events yet — run a COMPLEX or THROTTLED routing call to generate entries.</td></tr>';
    return;
  }

  tbody.innerHTML = events.map(e => {
    const ts = e.timestamp
      ? new Date(e.timestamp + "Z").toLocaleString("en-US", {
          timeZone: "America/Chicago",
          month: "numeric", day: "numeric", year: "numeric",
          hour: "numeric", minute: "2-digit", second: "2-digit",
          hour12: true,
        })
      : "—";

    const riskClass  = `badge-${e.risk_level || "low"}`;
    const outcome    = e.decision_outcome || "—";
    const isBlocked  = outcome.toLowerCase().includes("blocked");
    const rowClass   = isBlocked ? "audit-row row-blocked" : "audit-row";
    const blockedIcon = isBlocked ? "🛡 " : "";

    // Normalize tier display name
    const tierLabel = e.model_tier || "—";
    const tierBadge = ["Scout","Analyst","micro"].includes(tierLabel) ? "badge-micro"
                    : ["Advisor","Strategist","flagship"].includes(tierLabel) ? "badge-flagship"
                    : "badge-micro";

    return `
      <tr class="${rowClass}" onclick="toggleRationale(${e.id})" style="cursor:pointer">
        <td style="font-family:var(--font-mono); font-size:11px">${ts}</td>
        <td><span class="badge ${isBlocked ? 'badge-critical' : 'badge-micro'}">${isBlocked ? "🛡 BLOCKED" : e.event_type}</span></td>
        <td>${e.department}</td>
        <td><span class="badge ${tierBadge}">${tierLabel}</span></td>
        <td><span class="badge ${riskClass}">${(e.risk_level || "low").toUpperCase()}</span></td>
        <td style="font-size:11px; color:${isBlocked ? 'var(--accent-red)' : 'var(--text-muted)'}; font-weight:${isBlocked ? '600' : 'normal'}">${blockedIcon}${outcome}</td>
      </tr>
      <tr class="rationale-row" id="rationale-${e.id}" style="display:none">
        <td colspan="6">
          <div class="rationale-box" id="rationale-content-${e.id}">
            <span style="color:var(--text-muted)">Loading rationale...</span>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

/** Fetch and render rationale content for a given event ID */
async function fetchRationaleContent(eventId) {
  const content = document.getElementById(`rationale-content-${eventId}`);
  if (!content) return;

  try {
    const detail = await apiGet(`/api/audit/${eventId}`);
    let snapshot = {};
    try { snapshot = JSON.parse(detail.context_snapshot || "{}"); } catch {}

    content.innerHTML = `
      <div style="display:flex; justify-content:flex-end; margin-bottom:8px">
        <button onclick="toggleRationale(${eventId})" style="
          background:transparent; border:1px solid var(--border); color:var(--text-muted);
          border-radius:4px; padding:3px 10px; font-size:11px; cursor:pointer;">
          ✕ Close
        </button>
      </div>
      <div class="rationale-section">
        <div class="rationale-label">PLAIN-ENGLISH RATIONALE</div>
        <div class="rationale-text">${detail.rationale || "No rationale recorded."}</div>
      </div>
      <div class="rationale-section">
        <div class="rationale-label">CONTEXT SNAPSHOT (at time of decision)</div>
        <div class="rationale-text" style="font-family:var(--font-mono); font-size:11px">
          Budget: $${snapshot.budget_spent_usd ?? "?"} / $${snapshot.budget_cap_usd ?? "?"}
          &nbsp;(${snapshot.budget_used_pct ?? "?"}% used)
          &nbsp;|&nbsp; Throttled: ${snapshot.throttled ?? "?"}
          &nbsp;|&nbsp; Override: ${snapshot.override_granted ?? "?"}
          &nbsp;|&nbsp; Captured: ${snapshot.captured_at ?? "?"}
          ${snapshot.tokens_saved > 0 ? `<br><br>
          <span style="color:var(--accent-green)">&#9660; Pruning:</span>
          Raw: ${snapshot.raw_tokens ?? "?"} tokens
          &nbsp;&rarr;&nbsp; Clean: ${snapshot.clean_tokens ?? "?"} tokens
          &nbsp;|&nbsp; <span style="color:var(--accent-green)">Saved: ${snapshot.tokens_saved ?? 0} tokens (${snapshot.compression_pct ?? 0}% reduction)</span>` : ""}
        </div>
      </div>
      <div class="rationale-section">
        <div class="rationale-label">PROMPT PAYLOAD (first 400 chars)</div>
        <div class="rationale-text" style="font-family:var(--font-mono); font-size:11px; color:var(--text-muted)">
          ${(detail.prompt_payload || "").slice(0, 400)}${(detail.prompt_payload || "").length > 400 ? "..." : ""}
        </div>
      </div>
      <div style="margin-top:8px">
        <a class="export-link" href="/api/audit/export" download="fage_audit.jsonl">
          &#8595; Download full JSONL audit file
        </a>
      </div>
    `;
  } catch (err) {
    content.innerHTML = `<span style="color:var(--accent-red)">Failed to load detail: ${err.message}</span>`;
  }
}

/** Toggle the rationale detail row and lazy-load the full event if needed */
async function toggleRationale(eventId) {
  const row    = document.getElementById(`rationale-${eventId}`);
  const isOpen = row.style.display !== "none";

  // Close all open rows first
  document.querySelectorAll(".rationale-row").forEach(r => r.style.display = "none");

  if (isOpen) {
    openRationaleId = null;
    return;  // was open — just close it
  }

  openRationaleId = eventId;
  row.style.display = "table-row";
  await fetchRationaleContent(eventId);
}

// ── Export functions ──────────────────────────────────────────────────────────

function exportAuditCsv() {
  if (!auditAllEvents.length) { alert("No audit events to export."); return; }
  const fmtTs = iso => iso
    ? new Date(iso + "Z").toLocaleString("en-US", { timeZone: "America/Chicago", hour12: true })
    : "";
  const headers = ["Timestamp", "Event Type", "Department", "Model Tier", "Risk Level", "Decision Outcome", "Cost USD", "Matched Keywords"];
  const rows = auditAllEvents.map(e => [
    fmtTs(e.timestamp),
    e.event_type,
    e.department,
    e.model_tier || "",
    e.risk_level || "",
    e.decision_outcome || "",
    e.cost_usd != null ? e.cost_usd.toFixed(6) : "",
    (e.matched_keywords || []).join("; "),
  ]);
  const date = new Date().toISOString().slice(0, 10);
  downloadCsv(`fage_audit_log_${date}.csv`, headers, rows);
}

function exportAuditPdf() {
  printSection("auditBody", "FAGE — AI Decision Audit Log");
}

// Load on page ready, refresh every 15 seconds
loadAuditLog();
setInterval(loadAuditLog, 15000);
