/**
 * auditor.js — AI Decision Audit Log UI  [Step 6]
 *
 * Loads audit events from GET /api/audit and renders the log table.
 * Clicking a row expands the full rationale inline.
 */

let openRationaleId = null;
const _auditDetailCache = {};  // eventId → full detail object, populated on expand
const BLOCKED_BANNER_DISMISS_KEY = "costpilot_blocked_banner_dismissed_signature";

function isAuditDetailOpen() {
  return !!openRationaleId || !!document.getElementById("rawPayloadModal");
}

function formatAuditCapturedAt(value) {
  if (!value) return "—";
  const raw = String(value);
  const iso = raw.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(raw) ? raw : `${raw}Z`;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}

async function loadAuditLog() {
  try {
    const events = await apiGet("/api/audit?limit=50");
    auditAllEvents = events;
    _populateAuditDeptFilter(events);
    updateBlockedBanner(events);
    applyAuditFilters();
    // Restore open row and re-fetch its content after re-render
    if (openRationaleId) {
      const row = document.getElementById(`rationale-${openRationaleId}`);
      if (row) {
        row.style.display = "block";
        // Re-populate content since the table was re-rendered
        fetchRationaleContent(openRationaleId);
      } else {
        openRationaleId = null;
      }
    }
  } catch (err) {
    document.getElementById("auditTableBody").innerHTML =
      `<div class="placeholder" style="color:var(--accent-red)">Failed to load audit log: ${err.message}</div>`;
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

  const latestBlockedTs = blocked
    .map(e => e.timestamp || "")
    .sort()
    .pop() || "";
  const signature = `${blocked.length}:${latestBlockedTs}`;
  banner.dataset.dismissSignature = signature;
  if (localStorage.getItem(BLOCKED_BANNER_DISMISS_KEY) === signature) {
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
  // Banner "Review Blocked" button — activates blocked-only filter
  _auditBlockedOnly = !_auditBlockedOnly;
  auditFilterBlocked = _auditBlockedOnly;

  const btn = document.querySelector(".blocked-banner-btn");
  if (btn) btn.textContent = _auditBlockedOnly ? "Show All Events" : "Review Blocked Events ↓";

  const indicator = document.getElementById("auditFilterIndicator");
  if (indicator) indicator.style.display = _auditBlockedOnly ? "inline-block" : "none";

  const auditBtn = document.getElementById("auditBlockedOnlyBtn");
  if (auditBtn) {
    auditBtn.style.color       = _auditBlockedOnly ? "var(--accent-red)" : "";
    auditBtn.style.borderColor = _auditBlockedOnly ? "var(--accent-red)" : "";
  }

  applyAuditFilters();
}

function dismissBlockedBanner(event) {
  if (event) event.stopPropagation();
  const banner = document.getElementById("blockedAlertBanner");
  if (!banner) return;
  const signature = banner.dataset.dismissSignature || "";
  if (signature) localStorage.setItem(BLOCKED_BANNER_DISMISS_KEY, signature);
  banner.style.display = "none";
}

function renderAuditTable(events) {
  const tbody = document.getElementById("auditTableBody");
  if (!events.length) {
    tbody.innerHTML =
      '<div class="placeholder">No audit events yet — run a COMPLEX or THROTTLED routing call to generate entries.</div>';
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
    const rowClass   = isBlocked ? "audit-event row-blocked" : "audit-event";

    // Normalize tier display name
    const tierLabel = e.model_tier ? normaliseTierName(e.model_tier) : "—";
    const tierBadgeClass = getTierBadgeClassByName(e.model_tier || "");

    return `
      <article class="${rowClass}" id="audit-entry-${e.id}" onclick="toggleRationale(${e.id})" title="Click to expand rationale and payload">
        <div class="audit-event-marker" aria-hidden="true"></div>
        <div class="audit-event-time">${ts}</div>
        <div class="audit-event-main">
          <div class="audit-event-title">
            <strong>${e.display_agent_name || e.agent_name || "Unlinked request"}</strong>
            <span class="badge ${isBlocked ? 'badge-critical' : 'badge-scout'}">${isBlocked ? "BLOCKED" : e.event_type}</span>
            <span class="badge ${riskClass}">${(e.risk_level || "low").toUpperCase()} RISK</span>
          </div>
          <div class="audit-event-context">${e.display_department || e.department} · <span class="badge ${tierBadgeClass}">${tierLabel}</span></div>
          <div class="audit-event-outcome${isBlocked ? " blocked" : ""}">${outcome}</div>
        </div>
        <div class="audit-event-open" aria-hidden="true">›</div>
      </article>
      <div class="rationale-row" id="rationale-${e.id}" style="display:none">
        <div class="rationale-box" id="rationale-content-${e.id}">
          <span style="color:var(--text-muted)">Loading rationale...</span>
        </div>
      </div>
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
    const hasBudgetContext = snapshot.budget_cap_usd != null || snapshot.budget_spent_usd != null;
    const callCost = detail.cost_usd != null ? `$${Number(detail.cost_usd).toFixed(6)}` : "not recorded";
    const capturedAt = formatAuditCapturedAt(snapshot.captured_at);
    const agentLine = `Agent: ${detail.display_agent_name || detail.agent_name || "not linked"}
          &nbsp;|&nbsp; Platform: ${detail.source_platform || "unknown"}
          &nbsp;|&nbsp; Department: ${detail.display_department || detail.department || "—"}`;
    const contextLine = hasBudgetContext
      ? `Budget: $${snapshot.budget_spent_usd ?? "0"} / $${snapshot.budget_cap_usd ?? "0"}
          &nbsp;(${snapshot.budget_used_pct ?? 0}% used)
          &nbsp;|&nbsp; Throttled: ${snapshot.throttled ?? false}
          &nbsp;|&nbsp; Override: ${snapshot.override_granted ?? false}
          &nbsp;|&nbsp; Captured: ${capturedAt}`
      : `Budget: Not configured for ${snapshot.department || detail.display_department || detail.department || "this department"}
          &nbsp;|&nbsp; Call cost: ${callCost}
          &nbsp;|&nbsp; Captured: ${capturedAt}`;

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
          ${agentLine}<br>
          ${contextLine}
          ${snapshot.tokens_saved > 0 ? `<br><br>
          <span style="color:var(--accent-green)">&#9660; Pruning:</span>
          Raw: ${snapshot.raw_tokens ?? "?"} tokens
          &nbsp;&rarr;&nbsp; Clean: ${snapshot.clean_tokens ?? "?"} tokens
          &nbsp;|&nbsp; <span style="color:var(--accent-green)">Saved: ${snapshot.tokens_saved ?? 0} tokens (${snapshot.compression_pct ?? 0}% reduction)</span>` : ""}
        </div>
      </div>
      <div class="rationale-section">
        <div class="rationale-label" style="display:flex;align-items:center;gap:10px">
          PROMPT PAYLOAD (first 400 chars)
          ${detail.raw_payload ? `<button onclick="showRawPayloadModal(${eventId})" style="
            background:transparent; border:1px solid var(--accent); color:var(--accent);
            border-radius:4px; padding:2px 10px; font-size:10px; cursor:pointer;
            text-transform:uppercase; letter-spacing:0.06em; font-weight:600;">
            ⊞ View Original
          </button>` : (detail.has_raw_payload ? `<span style="font-size:10px;color:var(--text-muted);font-style:italic">Raw payload expired</span>` : "")}
        </div>
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
    // Cache detail for modal use (avoid re-fetch when View Original is clicked)
    _auditDetailCache[eventId] = detail;
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
  row.style.display = "block";
  await fetchRationaleContent(eventId);
}

/** Render routing rows from a pre-filtered event array */
function _renderRoutingRows(events) {
  const tbody = document.getElementById("liveRoutingBody");
  if (!tbody) return;
  if (!events.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="placeholder">No events match the current filters.</td></tr>';
    return;
  }
  tbody.innerHTML = events.map(e => {
    const ts = e.timestamp
      ? new Date(e.timestamp + "Z").toLocaleTimeString("en-US", {
          hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true
        })
      : "—";
    const isBlocked = (e.decision_outcome || "").toLowerCase().includes("blocked");
    const tierLabel = e.model_tier ? normaliseTierName(e.model_tier) : "—";
    const _tierBadgeCls = getTierBadgeClassByName(e.model_tier || "");
    const tierBadge = isBlocked
      ? `<span class="badge badge-critical">🛡 BLOCKED</span>`
      : tierLabel === "—"
      ? `<span style="color:var(--text-muted)">—</span>`
      : `<span class="badge ${_tierBadgeCls}">${tierLabel}</span>`;
    const riskClass = `badge-${e.risk_level || "low"}`;
    const rowClass  = isBlocked ? "audit-row row-blocked" : "audit-row";
    return `
      <tr class="${rowClass}" onclick="jumpToMainAuditRow(${e.id})" style="cursor:pointer" title="Click to jump to audit log entry">
        <td style="font-family:var(--font-mono);color:var(--text-muted);font-size:11px">${ts}</td>
        <td style="font-weight:600;color:var(--accent);font-size:11px">${e.display_agent_name || e.agent_name || "—"}</td>
        <td style="color:var(--text-muted);font-size:11px">${e.display_department || e.department || "—"}</td>
        <td>${tierBadge}</td>
        <td><span class="badge ${riskClass}">${(e.risk_level || "low").toUpperCase()}</span></td>
        <td style="font-size:11px;color:${isBlocked ? 'var(--accent-red)' : 'var(--text-muted)'}">${e.decision_outcome || "—"}</td>
      </tr>`;
  }).join("");
}

/** Fetch routing events, cache them, and render with current filters applied */
async function loadLiveRoutingFeed() {
  const tbody = document.getElementById("liveRoutingBody");
  if (!tbody) return;
  try {
    const events = await apiGet("/api/audit?limit=50");
    _routingEvents = events;
    applyRoutingFilters();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="placeholder" style="color:var(--accent-red)">Failed to load: ${err.message}</td></tr>`;
  }
}

// Staggered 800ms
setTimeout(loadLiveRoutingFeed, 800);
setInterval(() => {
  if (isAuditDetailOpen()) return;
  loadLiveRoutingFeed();
}, 15000);

// ── Audit Log Filters ─────────────────────────────────────────────────────────

let _auditBlockedOnly = false;

function applyAuditFilters() {
  const dept   = (document.getElementById("auditFilterDept")?.value   || "").toLowerCase();
  const risk   = (document.getElementById("auditFilterRisk")?.value   || "").toLowerCase();
  const window = parseInt(document.getElementById("auditFilterWindow")?.value || "0");

  const cutoff = window > 0 ? Date.now() - window * 24 * 60 * 60 * 1000 : 0;

  const filtered = auditAllEvents.filter(e => {
    if (_auditBlockedOnly && !(e.decision_outcome || "").toLowerCase().includes("blocked")) return false;
    if (dept && (e.display_department || e.department || "").toLowerCase() !== dept) return false;
    if (risk && (e.risk_level || "").toLowerCase() !== risk) return false;
    if (cutoff && new Date(e.timestamp + "Z").getTime() < cutoff) return false;
    return true;
  });

  renderAuditTable(filtered);
}

function toggleAuditBlockedOnly() {
  _auditBlockedOnly = !_auditBlockedOnly;
  const btn = document.getElementById("auditBlockedOnlyBtn");
  if (btn) {
    btn.style.color       = _auditBlockedOnly ? "var(--accent-red)" : "";
    btn.style.borderColor = _auditBlockedOnly ? "var(--accent-red)" : "";
  }
  applyAuditFilters();
}

function clearAuditFilters() {
  _auditBlockedOnly = false;
  const fields = ["auditFilterDept","auditFilterRisk","auditFilterWindow"];
  fields.forEach(id => { const el = document.getElementById(id); if (el) el.value = ""; });
  const btn = document.getElementById("auditBlockedOnlyBtn");
  if (btn) { btn.style.color = ""; btn.style.borderColor = ""; }
  renderAuditTable(auditAllEvents);
}

function _populateAuditDeptFilter(events) {
  const sel = document.getElementById("auditFilterDept");
  if (!sel) return;
  const current = sel.value;
  while (sel.options.length > 1) sel.remove(1);  // keep "All Depts", rebuild the rest
  const depts = [...new Set(events.map(e => e.display_department || e.department).filter(Boolean))].sort();
  depts.forEach(d => {
    const opt = document.createElement("option");
    opt.value = d.toLowerCase(); opt.textContent = d;
    sel.appendChild(opt);
  });
  sel.value = current;
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
    e.display_department || e.department,
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
  printSection("auditBody", "CostPilot — AI Decision Audit Log");
}

// Staggered 1200ms
setTimeout(loadAuditLog, 1200);
setInterval(() => {
  if (isAuditDetailOpen()) return;
  loadAuditLog();
}, 15000);

// ── Raw Payload Modal ─────────────────────────────────────────────────────────
// Shows a side-by-side view of the original (pre-prune) text vs the cleaned
// payload that was actually sent to the AI model.

async function showRawPayloadModal(eventId) {
  // Use cached detail if available; otherwise fetch
  let detail = _auditDetailCache[eventId];
  if (!detail) {
    try {
      detail = await apiGet(`/api/audit/${eventId}`);
      _auditDetailCache[eventId] = detail;
    } catch (err) {
      alert(`Could not load raw payload: ${err.message}`);
      return;
    }
  }

  let snapshot = {};
  try { snapshot = JSON.parse(detail.context_snapshot || "{}"); } catch {}

  const rawText   = detail.raw_payload   || "";
  const cleanText = detail.prompt_payload || "";
  const rawTokens   = snapshot.raw_tokens      ?? "—";
  const cleanTokens = snapshot.clean_tokens     ?? "—";
  const saved       = snapshot.tokens_saved     ?? "—";
  const pct         = snapshot.compression_pct  ?? "—";

  // Remove existing modal if present
  const existing = document.getElementById("rawPayloadModal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "rawPayloadModal";
  modal.style.cssText = `
    position:fixed; inset:0; z-index:9999;
    background:rgba(0,0,0,0.75); backdrop-filter:blur(4px);
    display:flex; align-items:center; justify-content:center; padding:24px;
  `;
  modal.innerHTML = `
    <div style="
      background:var(--bg-panel); border:1px solid var(--border); border-radius:10px;
      width:100%; max-width:1100px; max-height:90vh; display:flex; flex-direction:column;
      overflow:hidden; box-shadow:0 24px 80px rgba(0,0,0,0.6);
    ">
      <!-- Header -->
      <div style="
        display:flex; align-items:center; justify-content:space-between;
        padding:14px 20px; border-bottom:1px solid var(--border); flex-shrink:0;
      ">
        <div style="font-size:13px; font-weight:700; color:var(--text-primary); letter-spacing:0.05em; text-transform:uppercase">
          ⊞ Raw vs. Clean Payload — Audit #${eventId}
        </div>
        <button onclick="document.getElementById('rawPayloadModal').remove()" style="
          background:transparent; border:1px solid var(--border); color:var(--text-muted);
          border-radius:4px; padding:4px 12px; font-size:12px; cursor:pointer;">
          ✕ Close
        </button>
      </div>
      <!-- Column headers -->
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:0; flex-shrink:0;">
        <div style="padding:10px 20px; border-bottom:1px solid var(--border); border-right:1px solid var(--border);">
          <div style="font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.07em; color:var(--accent-yellow)">
            Original (Pre-Prune)
          </div>
          <div style="font-size:10px; color:var(--text-muted); margin-top:2px">
            ${rawTokens} tokens · first 5,000 chars stored
          </div>
        </div>
        <div style="padding:10px 20px; border-bottom:1px solid var(--border);">
          <div style="font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.07em; color:var(--accent-green)">
            Clean (Sent to AI)
          </div>
          <div style="font-size:10px; color:var(--text-muted); margin-top:2px">
            ${cleanTokens} tokens ·
            <span style="color:var(--accent-green)">&#x25BC; ${saved} tokens removed (${pct}% reduction)</span>
          </div>
        </div>
      </div>
      <!-- Panels -->
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:0; flex:1; overflow:hidden; min-height:0;">
        <div style="
          padding:16px 20px; overflow-y:auto; border-right:1px solid var(--border);
          font-family:var(--font-mono); font-size:11px; line-height:1.6;
          color:var(--accent-yellow); white-space:pre-wrap; word-break:break-word;
        ">${escHtml(rawText) || "<em style='color:var(--text-muted)'>No raw text stored.</em>"}</div>
        <div style="
          padding:16px 20px; overflow-y:auto;
          font-family:var(--font-mono); font-size:11px; line-height:1.6;
          color:var(--text-primary); white-space:pre-wrap; word-break:break-word;
        ">${escHtml(cleanText) || "<em style='color:var(--text-muted)'>No clean text stored.</em>"}</div>
      </div>
    </div>
  `;

  // Close on backdrop click
  modal.addEventListener("click", e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

function escHtml(str) {
  return (str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ── Jump from Routing Feed → Audit Log ───────────────────────────────────────
// Called when user clicks a routing feed row. Expands the audit panel,
// scrolls to the matching audit log entry, highlights it, then opens its
// inline rationale so the full detail is visible immediately.

function jumpToMainAuditRow(eventId) {
  // 1. Expand audit panel if collapsed
  const auditBody = document.getElementById("auditBody");
  const auditChev = document.getElementById("auditChevron");
  if (auditBody && auditBody.style.display === "none") {
    auditBody.style.display = "";
    if (auditChev) auditChev.textContent = "▾";
  }

  // 2. Load audit log if not yet loaded (first time)
  const tbody = document.getElementById("auditTableBody");
  if (tbody && !tbody.querySelector(`#audit-entry-${eventId}`)) {
    // Reload audit log then retry jump after render
    loadAuditLog().then(() => {
      setTimeout(() => _doJumpToAuditEntry(eventId), 400);
    }).catch(() => _doJumpToAuditEntry(eventId));
    return;
  }

  _doJumpToAuditEntry(eventId);
}

function _doJumpToAuditEntry(eventId) {
  const row = document.getElementById(`audit-entry-${eventId}`);
  if (!row) {
    // Entry not in loaded window — scroll to the audit section header at minimum
    const auditSection = document.getElementById("auditBody");
    if (auditSection) auditSection.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  // 3. Scroll into view
  row.scrollIntoView({ behavior: "smooth", block: "center" });

  // 4. Flash highlight
  row.classList.remove("audit-row-highlight");
  void row.offsetWidth; // force reflow so animation restarts
  row.classList.add("audit-row-highlight");

  // 5. Auto-expand the rationale inline
  setTimeout(() => toggleRationale(eventId), 400);
}
