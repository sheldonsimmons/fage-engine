/**
 * event_stream.js — Governance Event Stream panel  [Dashboard]
 *
 * Fetches real audit events from GET /api/audit and renders them as
 * styled event cards matching the live operations center style.
 * Clicking any card expands it inline showing full rationale + context.
 */

let _streamOpenId = null;
let _streamEvents = [];

// ── Event classification ──────────────────────────────────────────────────────

function _streamEventType(e) {
  const outcome = (e.decision_outcome || "").toLowerCase();
  if (outcome.includes("blocked"))    return "blocked";
  if (outcome.includes("throttled") || outcome.includes("downgrad")) return "throttled";
  const tier = (e.model_tier || "").toLowerCase();
  if (tier === "advisor" || tier === "strategist") return "complex";
  if (outcome.includes("collision") || outcome.includes("locked")) return "collision";
  if ((e.tokens_saved || 0) > 0)     return "pruning";
  return "routine";
}

function _streamEventTitle(e) {
  const type = _streamEventType(e);
  const tier  = e.model_tier || "";
  if (type === "blocked")   return "🛡 REQUEST BLOCKED";
  if (type === "throttled") return "⚡ THROTTLED — Budget Cap";
  if (type === "collision") return "⚡ CONCURRENCY LOCK";
  if (type === "pruning")   return "▼ CONTEXT PRUNING";
  if (tier === "Strategist") return "◈ STRATEGIST MODEL INVOKED";
  if (tier === "Advisor")    return "◈ ADVISOR MODEL INVOKED";
  return "◈ ROUTINE ROUTING";
}

function _streamEventColor(type) {
  return type === "blocked"   ? "var(--accent-red)"
       : type === "throttled" ? "var(--accent-yellow)"
       : type === "collision" ? "var(--accent-yellow)"
       : type === "complex"   ? "var(--accent-purple)"
       : type === "pruning"   ? "var(--accent-green)"
       : "var(--accent)";
}

function _fmtStreamTs(iso) {
  if (!iso) return "—";
  return new Date(iso + "Z").toLocaleTimeString("en-US", {
    timeZone: "America/Chicago",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

// ── Main render ───────────────────────────────────────────────────────────────

async function loadEventStream() {
  const list = document.getElementById("eventStreamList");
  if (!list) return;

  try {
    const events = await apiGet("/api/audit?limit=25");
    _streamEvents = events;

    if (!events.length) {
      list.innerHTML = '<p class="placeholder">No events yet — route a payload to generate entries.</p>';
      return;
    }

    // Fetch dashboard summary for budget context
    let dashData = {};
    try { dashData = await apiGet("/api/dashboard"); } catch {}

    list.innerHTML = events.map(e => {
      const type   = _streamEventType(e);
      const title  = _streamEventTitle(e);
      const color  = _streamEventColor(type);
      const ts     = _fmtStreamTs(e.timestamp);
      const dept   = e.department || "—";
      const tier   = e.model_tier  || "—";
      const cost   = e.cost_usd != null ? `$${e.cost_usd.toFixed(6)}` : "—";
      const tokens = e.tokens_in != null ? `${(e.tokens_in + (e.tokens_out || 0)).toLocaleString()} tokens` : "";

      // Budget context from dashboard summary
      const budgetUsed  = dashData.budget_used_pct != null ? `${dashData.budget_used_pct.toFixed(1)}% budget used` : "";
      const monthSpend  = dashData.spend_month_usd  != null ? `$${dashData.spend_month_usd.toFixed(2)} this month` : "";
      const metaParts   = [tokens, cost, dept, budgetUsed, monthSpend].filter(Boolean);

      const keywords = (e.matched_keywords || []).length
        ? `<span style="color:var(--accent-red)"> · ${e.matched_keywords.slice(0,3).join(", ")}</span>`
        : "";

      const body = e.decision_outcome
        ? `${e.decision_outcome}${keywords}`
        : (type === "blocked" ? "Sensitive data detected — request stopped before reaching AI" : "");

      return `
        <div class="gov-event type-${type}" id="gov-ev-${e.id}" onclick="toggleStreamEvent(${e.id})">
          <div class="gov-event-header">
            <span class="gov-event-type" style="color:${color}">${title}</span>
            <span class="gov-event-time">${ts}</span>
          </div>
          <div class="gov-event-body">${body}</div>
          <div class="gov-event-meta">${metaParts.join(" · ")}</div>
          <div class="gov-event-detail" id="gov-detail-${e.id}">
            <span style="color:var(--text-muted);font-size:11px">Loading detail...</span>
          </div>
        </div>`;
    }).join("");

  } catch (err) {
    list.innerHTML = `<p class="placeholder" style="color:var(--accent-red)">Failed to load: ${err.message}</p>`;
  }
}

// ── Click to expand ───────────────────────────────────────────────────────────

async function toggleStreamEvent(eventId) {
  const detail = document.getElementById(`gov-detail-${eventId}`);
  if (!detail) return;

  const isOpen = detail.classList.contains("open");

  // Close any other open detail
  document.querySelectorAll(".gov-event-detail.open").forEach(d => d.classList.remove("open"));
  _streamOpenId = null;

  if (isOpen) return; // was open — just close it

  detail.classList.add("open");
  _streamOpenId = eventId;

  // Fetch full detail
  try {
    const e = await apiGet(`/api/audit/${eventId}`);
    let snapshot = {};
    try { snapshot = JSON.parse(e.context_snapshot || "{}"); } catch {}

    const cost       = e.cost_usd != null ? `$${e.cost_usd.toFixed(6)}` : "—";
    const budgetLine = snapshot.budget_spent_usd != null
      ? `$${snapshot.budget_spent_usd ?? "?"} / $${snapshot.budget_cap_usd ?? "?"} &nbsp;(${snapshot.budget_used_pct ?? "?"}% used) &nbsp;|&nbsp; Throttled: ${snapshot.throttled ?? "?"} &nbsp;|&nbsp; Override: ${snapshot.override_granted ?? "?"}`
      : "—";

    const pruningLine = snapshot.tokens_saved > 0
      ? `<div class="gov-detail-label">Pruning</div>
         <div class="gov-detail-mono">Raw: ${snapshot.raw_tokens ?? "?"} tokens &nbsp;→&nbsp; Clean: ${snapshot.clean_tokens ?? "?"} tokens &nbsp;|&nbsp; <span style="color:var(--accent-green)">Saved: ${snapshot.tokens_saved ?? 0} tokens (${snapshot.compression_pct ?? 0}% reduction)</span></div>`
      : "";

    const keywords = (e.matched_keywords || []).length
      ? `<span style="color:var(--accent-red)">${e.matched_keywords.join(", ")}</span>`
      : '<span style="color:var(--text-muted)">None</span>';

    detail.innerHTML = `
      <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
        <button onclick="event.stopPropagation(); toggleStreamEvent(${eventId})"
          style="background:transparent;border:1px solid var(--border);color:var(--text-muted);border-radius:4px;padding:3px 10px;font-size:11px;cursor:pointer">
          ✕ Close
        </button>
      </div>

      <div class="gov-detail-label">Rationale</div>
      <div class="gov-detail-text">${e.rationale || "No rationale recorded."}</div>

      <div class="gov-detail-label">Budget Context</div>
      <div class="gov-detail-mono">${budgetLine}</div>

      ${pruningLine}

      <div class="gov-detail-label">Matched Keywords</div>
      <div class="gov-detail-mono">${keywords}</div>

      <div class="gov-detail-label">Prompt Payload (first 400 chars)</div>
      <div class="gov-detail-mono" style="white-space:pre-wrap">${(e.prompt_payload || "").slice(0, 400)}${(e.prompt_payload || "").length > 400 ? "..." : ""}</div>

      <div style="margin-top:10px">
        <a class="export-link" href="/api/audit/export" download="fage_audit.jsonl" onclick="event.stopPropagation()">
          ↓ Download full JSONL audit file
        </a>
      </div>`;
  } catch (err) {
    detail.innerHTML = `<span style="color:var(--accent-red)">Failed to load detail: ${err.message}</span>`;
  }
}

// Load on page ready, refresh every 15 seconds
loadEventStream();
setInterval(loadEventStream, 15000);
