/**
 * user-activity.js — Users page controller.
 *
 * Search-and-drill-down for one person's AI activity: search WorkUser
 * records via GET /api/audit/users, then load that person's own events via
 * GET /api/audit?work_user_id=... (both new, added alongside this page --
 * see backend/api/routes_auditor.py).
 *
 * The detail-expand rendering (rationale, context snapshot, pruning
 * breakdown, prompt payload, raw-vs-clean modal) mirrors auditor.js's
 * fetchRationaleContent/showRawPayloadModal rather than importing that
 * file directly -- auditor.js pulls in dashboard.js's full live-dashboard
 * machinery as a side effect on operate.html, which this page has no DOM
 * for. Duplicating this ~150 lines of pure rendering logic keeps
 * operate.html's own Decision Timeline completely untouched.
 */

// Copied from dashboard.js -- small and pure, not worth loading that whole
// file just for this one helper.
function workspaceScopedApiPath(path) {
  const wsId = localStorage.getItem("cp_workspace_id") || "";
  if (!wsId) return path;
  const joiner = path.includes("?") ? "&" : "?";
  return `${path}${joiner}workspace_id=${encodeURIComponent(wsId)}`;
}

function escHtml(str) {
  return (str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function formatAuditCapturedAt(value) {
  if (!value) return "—";
  const raw = String(value);
  const iso = raw.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(raw) ? raw : `${raw}Z`;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true,
  });
}

function formatExecutiveRationale(detail, snapshot = {}) {
  const original = String(detail?.rationale || "No rationale recorded.");
  const escape = value => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

  if (!original.toUpperCase().startsWith("ROUTINE CALL") &&
      !original.toUpperCase().startsWith("ROUTINE REQUEST")) {
    return escape(original);
  }

  const tier = detail?.model_tier || snapshot.model_tier || "Scout";
  const department = detail?.display_department ||
    String(detail?.department || snapshot.department || "Unknown").split(":").pop();
  const tokens = Number(snapshot.raw_tokens ?? detail?.raw_tokens);
  const tokenLine = Number.isFinite(tokens)
    ? `The request contained ${tokens.toLocaleString()} tokens and no complexity or high-risk indicators required escalation.`
    : "No complexity or high-risk indicators required escalation.";
  const budgetCap = snapshot.budget_cap_usd;
  const budgetSpent = snapshot.budget_spent_usd;
  const budgetPct = snapshot.budget_used_pct;
  const budgetLine = budgetCap != null
    ? `<strong>Budget:</strong> ${escape(budgetPct ?? "—")}% used — $${escape(budgetSpent ?? "0")} of $${escape(budgetCap)}`
    : "<strong>Budget:</strong> Not configured";
  const controlLine = snapshot.throttled
    ? "<strong>Control:</strong> Throttling applied"
    : "<strong>Control:</strong> No throttling applied";
  const cost = detail?.cost_usd != null ? `$${Number(detail.cost_usd).toFixed(6)}` : "Not recorded";
  const retention = snapshot.raw_retention_days != null
    ? `${escape(snapshot.raw_retention_days)}-day retention policy`
    : "configured retention policy";

  return [
    `<strong>Routine request — ${escape(tier)} selected</strong>`,
    escape(tokenLine), budgetLine, controlLine,
    `<strong>Call cost:</strong> ${escape(cost)}`,
    `<strong>Audit:</strong> Evidence retained according to the ${retention}`,
  ].join("<br>");
}

let usOpenRationaleId = null;
const usDetailCache = {};

function usRenderTable(events) {
  const tbody = document.getElementById("auditTableBody");
  if (!events.length) {
    tbody.innerHTML = '<div class="placeholder">No AI activity on record for this user yet.</div>';
    return;
  }

  tbody.innerHTML = events.map(e => {
    const ts = e.timestamp
      ? new Date(e.timestamp + "Z").toLocaleString("en-US", {
          month: "numeric", day: "numeric", year: "numeric",
          hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true,
        })
      : "—";
    const riskClass = `badge-${e.risk_level || "low"}`;
    const outcome = e.decision_outcome || "—";
    const isBlocked = outcome.toLowerCase().includes("blocked");
    const rowClass = isBlocked ? "audit-event row-blocked" : "audit-event";
    const tierLabel = e.model_tier ? normaliseTierName(e.model_tier) : "—";
    const tierBadgeClass = getTierBadgeClassByName(e.model_tier || "");

    return `
      <article class="${rowClass}" id="audit-entry-${e.id}" onclick="usToggleRationale(${e.id})" title="Click to expand rationale and payload">
        <div class="audit-event-marker" aria-hidden="true"></div>
        <div class="audit-event-time">${ts}</div>
        <div class="audit-event-main">
          <div class="audit-event-title">
            <strong>${e.display_agent_name || e.agent_name || "Unlinked request"}</strong>
            <span class="badge ${isBlocked ? 'badge-critical' : 'badge-scout'}">${isBlocked ? "BLOCKED" : e.event_type}</span>
            <span class="badge ${riskClass}">${(e.risk_level || "low").toUpperCase()} RISK</span>
            ${e.is_simulation ? '<span class="badge badge-event">SIMULATION</span>' : ""}
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

async function usFetchRationaleContent(eventId) {
  const content = document.getElementById(`rationale-content-${eventId}`);
  if (!content) return;
  try {
    const detail = await apiGet(workspaceScopedApiPath(`/api/audit/${eventId}`));
    let snapshot = {};
    try { snapshot = JSON.parse(detail.context_snapshot || "{}"); } catch {}
    const hasBudgetContext = snapshot.budget_cap_usd != null || snapshot.budget_spent_usd != null;
    const callCost = detail.cost_usd != null ? `$${Number(detail.cost_usd).toFixed(6)}` : "not recorded";
    const capturedAt = formatAuditCapturedAt(snapshot.captured_at);
    const requestedByLine = detail.actor_name
      ? `<br>Requested by: ${detail.actor_name}${detail.actor_email ? ` (${detail.actor_email})` : ""}${detail.actor_source_platform ? ` via ${detail.actor_source_platform}` : ""}`
      : "";
    const agentLine = `Agent: ${detail.display_agent_name || detail.agent_name || "not linked"}
          &nbsp;|&nbsp; Platform: ${detail.source_platform || "unknown"}
          &nbsp;|&nbsp; Department: ${detail.display_department || detail.department || "—"}${requestedByLine}`;
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
        <button onclick="usToggleRationale(${eventId})" style="
          background:transparent; border:1px solid var(--border); color:var(--text-muted);
          border-radius:4px; padding:3px 10px; font-size:11px; cursor:pointer;">
          ✕ Close
        </button>
      </div>
      <div class="rationale-section">
        <div class="rationale-label">PLAIN-ENGLISH RATIONALE</div>
        <div class="rationale-text">${formatExecutiveRationale(detail, snapshot)}</div>
      </div>
      <div class="rationale-section">
        <div class="rationale-label">CONTEXT SNAPSHOT (at time of decision)</div>
        <div class="rationale-text" style="font-family:var(--font-mono); font-size:11px">
          ${agentLine}<br>
          Mode: ${detail.is_simulation ? "Simulation" : "Live"}<br>
          ${contextLine}
          ${snapshot.tokens_saved > 0 ? `<br><br>
          <span style="color:var(--accent-green)">&#9660; Pruning:</span>
          Raw: ${snapshot.raw_tokens ?? "?"} tokens
          &nbsp;&rarr;&nbsp; Clean: ${snapshot.clean_tokens ?? "?"} tokens
          &nbsp;|&nbsp; <span style="color:var(--accent-green)">Saved: ${snapshot.tokens_saved ?? 0} tokens (${snapshot.compression_pct ?? 0}% reduction)</span>
          ${(snapshot.filter_details || []).length ? `<br>${snapshot.filter_details
            .map(f => `&nbsp;&nbsp;&#8226; ${f.name}: &minus;${f.tokens_saved} tokens`)
            .join("<br>")}` : ""}` : ""}
        </div>
      </div>
      <div class="rationale-section">
        <div class="rationale-label" style="display:flex;align-items:center;gap:10px">
          PROMPT PAYLOAD (first 400 chars)
          ${detail.raw_payload ? `<button onclick="usShowRawPayloadModal(${eventId})" style="
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
        <a class="export-link" href="${workspaceScopedApiPath('/api/audit/export')}" download="fage_audit.jsonl">
          &#8595; Download full JSONL audit file
        </a>
      </div>
    `;
    usDetailCache[eventId] = detail;
  } catch (err) {
    content.innerHTML = `<span style="color:var(--accent-red)">Failed to load detail: ${err.message}</span>`;
  }
}

async function usToggleRationale(eventId) {
  const row = document.getElementById(`rationale-${eventId}`);
  const isOpen = row.style.display !== "none";
  document.querySelectorAll(".rationale-row").forEach(r => r.style.display = "none");
  if (isOpen) { usOpenRationaleId = null; return; }
  usOpenRationaleId = eventId;
  row.style.display = "block";
  await usFetchRationaleContent(eventId);
}

async function usShowRawPayloadModal(eventId) {
  let detail = usDetailCache[eventId];
  if (!detail) {
    try {
      detail = await apiGet(workspaceScopedApiPath(`/api/audit/${eventId}`));
      usDetailCache[eventId] = detail;
    } catch (err) {
      alert(`Could not load raw payload: ${err.message}`);
      return;
    }
  }
  let snapshot = {};
  try { snapshot = JSON.parse(detail.context_snapshot || "{}"); } catch {}
  const rawText = detail.raw_payload || "";
  const cleanText = detail.prompt_payload || "";
  const rawTokens = snapshot.raw_tokens ?? "—";
  const cleanTokens = snapshot.clean_tokens ?? "—";
  const saved = snapshot.tokens_saved ?? "—";
  const pct = snapshot.compression_pct ?? "—";

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
  modal.addEventListener("click", e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

// ── Search + selection ─────────────────────────────────────────────────────

let usSearchDebounce = null;
let usSelectedUser = null;

function usRenderResults(users) {
  const box = document.getElementById("usResults");
  if (!users.length) {
    box.innerHTML = '<div class="us-empty">No matching users in this workspace.</div>';
    box.classList.add("open");
    return;
  }
  box.innerHTML = users.map(u => `
    <div class="us-result-row" onclick='usSelectUser(${JSON.stringify(u).replace(/'/g, "&#39;")})'>
      <div class="us-result-name">${escHtml(u.name)}</div>
      <div class="us-result-meta">${escHtml(u.email || "no email on file")} · ${escHtml(u.source_platform)}</div>
    </div>
  `).join("");
  box.classList.add("open");
}

async function usRunSearch(term) {
  const box = document.getElementById("usResults");
  box.innerHTML = '<div class="us-loading">Searching…</div>';
  box.classList.add("open");
  try {
    const path = workspaceScopedApiPath(`/api/audit/users?q=${encodeURIComponent(term)}`);
    const users = await apiGet(path);
    usRenderResults(users);
  } catch (err) {
    box.innerHTML = `<div class="us-empty">Search failed: ${err.message}</div>`;
  }
}

function usSelectUser(user) {
  usSelectedUser = user;
  document.getElementById("usSearchInput").value = "";
  document.getElementById("usResults").classList.remove("open");
  document.getElementById("usSelectedName").textContent = user.name;
  document.getElementById("usSelectedMeta").textContent =
    `${user.email || "no email on file"} · ${user.source_platform}`;
  document.getElementById("usSelected").classList.add("open");
  document.getElementById("usActivitySection").style.display = "block";
  usLoadActivity(user.id);
}

function usClearSelection() {
  usSelectedUser = null;
  document.getElementById("usSelected").classList.remove("open");
  document.getElementById("usActivitySection").style.display = "none";
  document.getElementById("auditTableBody").innerHTML = "";
  usOpenRationaleId = null;
}

// Returns {date_from, date_to} ISO strings, or {} for "All time" -- read
// from the range select/date inputs rather than passed in, since the range
// control is shared across whichever user is currently selected.
function usComputeDateRange() {
  const select = document.getElementById("usRangeSelect");
  const value = select ? select.value : "30";

  if (value === "custom") {
    const fromEl = document.getElementById("usDateFrom");
    const toEl = document.getElementById("usDateTo");
    const params = {};
    if (fromEl && fromEl.value) params.date_from = new Date(`${fromEl.value}T00:00:00`).toISOString();
    if (toEl && toEl.value) params.date_to = new Date(`${toEl.value}T23:59:59`).toISOString();
    return params;
  }

  const days = parseInt(value, 10);
  if (!days) return {}; // "All time"
  const from = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
  return { date_from: from.toISOString() };
}

function usOnRangeChange() {
  const select = document.getElementById("usRangeSelect");
  const customWrap = document.getElementById("usCustomRange");
  if (customWrap) customWrap.style.display = select && select.value === "custom" ? "inline-flex" : "none";
  if (usSelectedUser) usLoadActivity(usSelectedUser.id);
}

async function usLoadActivity(workUserId) {
  const tbody = document.getElementById("auditTableBody");
  tbody.innerHTML = '<div class="us-placeholder">Loading activity…</div>';
  try {
    const range = usComputeDateRange();
    const params = new URLSearchParams({ work_user_id: workUserId, limit: "200" });
    if (range.date_from) params.set("date_from", range.date_from);
    if (range.date_to) params.set("date_to", range.date_to);
    const path = workspaceScopedApiPath(`/api/audit?${params.toString()}`);
    const events = await apiGet(path);
    usRenderTable(events);
  } catch (err) {
    tbody.innerHTML = `<div class="placeholder" style="color:var(--accent-red)">Failed to load activity: ${err.message}</div>`;
  }
}

function usInit() {
  const input = document.getElementById("usSearchInput");
  input.addEventListener("input", () => {
    const term = input.value.trim();
    clearTimeout(usSearchDebounce);
    if (!term) {
      document.getElementById("usResults").classList.remove("open");
      return;
    }
    usSearchDebounce = setTimeout(() => usRunSearch(term), 250);
  });
  document.addEventListener("click", (e) => {
    const wrap = document.querySelector(".us-search-wrap");
    if (wrap && !wrap.contains(e.target)) {
      document.getElementById("usResults").classList.remove("open");
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", usInit);
} else {
  usInit();
}
