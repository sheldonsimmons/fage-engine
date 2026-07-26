/**
 * event_stream.js — Governance Event Stream panel  [Dashboard]
 *
 * Fetches real audit events from GET /api/audit and renders them as
 * styled event cards matching the live operations center style.
 * Clicking any card expands it inline showing full rationale + context.
 */

let _streamOpenId  = null;
let _streamEvents  = [];
let _streamUserInteractingUntil = 0;
let _streamLoadedLimit = 0;

const STREAM_DEFAULT_LIMIT = 25;
const STREAM_FILTER_LIMIT  = 250;

function _markStreamUserInteracting() {
  _streamUserInteractingUntil = Date.now() + 1200;
}

function _streamFilterState() {
  return {
    type:   (document.getElementById("streamFilterType")?.value   || "").toLowerCase(),
    dept:   (document.getElementById("streamFilterDept")?.value   || "").toLowerCase(),
    search: (document.getElementById("streamFilterSearch")?.value || "").toLowerCase(),
  };
}

function _streamHasActiveFilters() {
  const f = _streamFilterState();
  return !!(f.type || f.dept || f.search);
}

function _renderStreamCards(events) {
  const list = document.getElementById("eventStreamList");
  if (!list) return;

  if (!events.length) {
    list.innerHTML = '<p class="placeholder">No events match the current filters.</p>';
    return;
  }

  const streamItems = _groupStreamEvents(events);
  list.innerHTML = streamItems.map((item, index) => {
    if (item.events.length > 1) return _renderStreamGroup(item, index);
    return _renderStreamEvent(item.events[0]);
  }).join("");
}

function _streamEscape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function _streamEventSummary(e) {
  const type = _streamEventType(e);
  const dept = e.display_department || e.department || "No department";
  const agent = e.display_agent_name || e.agent_name || "Agent not linked";
  const cost = Number(e.cost_usd || 0);
  const savedTokens = Number(e.tokens_saved || 0);
  const outcome = e.decision_outcome || (
    type === "blocked"
      ? "Stopped before reaching an AI provider"
      : "Request governed successfully"
  );
  return { type, dept, agent, cost, savedTokens, outcome };
}

function _renderStreamEvent(e, nested = false) {
    const summary = _streamEventSummary(e);
    const type   = summary.type;
    const title  = _streamEventTitle(e);
    const color  = _streamEventColor(type);
    const ts     = _fmtStreamTs(e.timestamp);
    const metrics = [
      e.cost_usd != null ? `$${summary.cost.toFixed(6)}` : "",
      summary.savedTokens > 0 ? `${summary.savedTokens.toLocaleString()} tokens saved` : "",
    ].filter(Boolean);

    return `
      <div class="gov-event type-${type}${nested ? " gov-event-nested" : ""}" id="gov-ev-${e.id}"
        role="button" tabindex="0" aria-expanded="false"
        onclick="event.stopPropagation();toggleStreamEvent(${e.id})"
        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();toggleStreamEvent(${e.id})}">
        <div class="gov-event-header">
          <span class="gov-event-type" style="color:${color}">${title}</span>
          <span class="gov-event-time">${ts}</span>
        </div>
        <div class="gov-event-body">${_streamEscape(summary.outcome)}</div>
        <div class="gov-event-context">
          <span>${_streamEscape(summary.agent)}</span>
          <span>${_streamEscape(summary.dept)}</span>
        </div>
        ${metrics.length ? `<div class="gov-event-metrics">${metrics.map(metric => `<span>${_streamEscape(metric)}</span>`).join("")}</div>` : ""}
        <div class="gov-event-inspect">View audit evidence <span>›</span></div>
        <div class="gov-event-detail" id="gov-detail-${e.id}">
          <span style="color:var(--text-muted);font-size:11px">Loading detail...</span>
        </div>
      </div>`;
}

function _groupStreamEvents(events) {
  const groups = [];
  const groupMap = new Map();

  events.forEach((event) => {
    const type = _streamEventType(event);
    const canGroup = ["scout", "analyst", "pruning"].includes(type);
    const timestamp = _streamDate(event.timestamp);
    const minute = timestamp && !Number.isNaN(timestamp.getTime())
      ? Math.floor(timestamp.getTime() / 60000)
      : event.id;
    const dept = (event.display_department || event.department || "").toLowerCase();
    const key = canGroup ? `${type}|${dept}|${minute}` : `event|${event.id}`;

    if (!groupMap.has(key)) {
      const group = { type, events: [] };
      groupMap.set(key, group);
      groups.push(group);
    }
    groupMap.get(key).events.push(event);
  });
  return groups;
}

function _renderStreamGroup(group, index) {
  const events = group.events;
  const first = events[0];
  const color = _streamEventColor(group.type);
  const totalCost = events.reduce((sum, event) => sum + Number(event.cost_usd || 0), 0);
  const totalSaved = events.reduce((sum, event) => sum + Number(event.tokens_saved || 0), 0);
  const agents = new Set(events.map(event => event.display_agent_name || event.agent_name).filter(Boolean));
  const dept = first.display_department || first.department || "No department";
  const groupId = `gov-group-${index}`;
  const activityLabel = group.type === "pruning" ? "PRUNING" : "ROUTING";

  return `
    <div class="gov-event gov-event-group type-${group.type}" role="button" tabindex="0"
      aria-expanded="false" onclick="toggleStreamGroup('${groupId}', this)"
      onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleStreamGroup('${groupId}',this)}">
      <div class="gov-event-header">
        <span class="gov-event-type" style="color:${color}">◈ ${events.length} ${activityLabel} EVENTS</span>
        <span class="gov-event-time">${_fmtStreamTs(first.timestamp)}</span>
      </div>
      <div class="gov-event-body">${_streamEscape(dept)} activity summarized for the last minute</div>
      <div class="gov-event-context">
        <span>${agents.size} agent${agents.size === 1 ? "" : "s"}</span>
        <span>${_streamEscape(dept)}</span>
      </div>
      <div class="gov-event-metrics">
        <span>$${totalCost.toFixed(6)} spent</span>
        ${totalSaved > 0 ? `<span>${totalSaved.toLocaleString()} tokens saved</span>` : ""}
      </div>
      <div class="gov-event-inspect">Show ${events.length} events <span>›</span></div>
      <div class="gov-event-group-detail" id="${groupId}">
        ${events.map(event => _renderStreamEvent(event, true)).join("")}
      </div>
    </div>`;
}

function toggleStreamGroup(groupId, card) {
  const detail = document.getElementById(groupId);
  if (!detail) return;
  const willOpen = !detail.classList.contains("open");
  detail.classList.toggle("open", willOpen);
  card?.setAttribute("aria-expanded", String(willOpen));
  const inspect = card?.querySelector(":scope > .gov-event-inspect");
  if (inspect) inspect.innerHTML = `${willOpen ? "Hide" : "Show"} ${detail.children.length} events <span>${willOpen ? "⌄" : "›"}</span>`;
}

// ── Event classification ──────────────────────────────────────────────────────

function _streamEventType(e) {
  const outcome = (e.decision_outcome || "").toLowerCase();
  if (outcome.includes("blocked"))    return "blocked";
  if (outcome.includes("throttled") || outcome.includes("downgrad")) return "throttled";
  if (outcome.includes("collision") || outcome.includes("locked")) return "collision";
  const tier = (e.model_tier || "").toLowerCase();
  if (tier === "strategist" || tier === "flagship") return "strategist";
  if (tier === "advisor")   return "advisor";
  if (tier === "analyst")   return "analyst";
  if ((e.tokens_saved || 0) > 0) return "pruning";
  return "scout";
}

function _streamEventTitle(e) {
  const type = _streamEventType(e);
  if (type === "blocked")    return "🛡 REQUEST BLOCKED";
  if (type === "throttled")  return "⚡ THROTTLED — Budget Cap";
  if (type === "collision")  return "⚡ CONCURRENCY LOCK";
  if (type === "pruning")    return "▼ CONTEXT PRUNING";
  if (type === "strategist") return "◈ HIGH-CAPABILITY ROUTING";
  if (type === "advisor")    return "◈ COMPLEX ROUTING";
  if (type === "analyst")    return "◈ ROUTINE ROUTING";
  return "◈ ROUTINE ROUTING";
}

function _streamEventColor(type) {
  return type === "blocked"    ? "var(--accent-red)"
       : type === "throttled"  ? "var(--accent-yellow)"
       : type === "collision"  ? "var(--accent-yellow)"
       : type === "strategist" ? "var(--accent-purple)"
       : type === "advisor"    ? "var(--tier-advisor)"
       : type === "analyst"    ? "var(--tier-analyst)"
       : type === "pruning"    ? "var(--tier-scout)"
       : "var(--tier-scout)";
}

function _streamTypeMatchesFilter(eventType, filterType) {
  if (!filterType) return true;
  if (filterType === "routine") return ["scout", "analyst"].includes(eventType);
  if (filterType === "complex") return ["advisor", "strategist"].includes(eventType);
  return eventType === filterType;
}

function _fmtStreamTs(iso) {
  if (!iso) return "—";
  return _streamDate(iso).toLocaleTimeString("en-US", {
    timeZone: "America/Chicago",
    hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true,
  });
}

function _streamDate(iso) {
  if (!iso) return new Date(NaN);
  const value = String(iso);
  const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

// ── Main render ───────────────────────────────────────────────────────────────

async function loadEventStream(limit) {
  const list = document.getElementById("eventStreamList");
  if (!list) return;

  // Remember which event was open so we can restore it after re-render
  const wasOpenId = _streamOpenId;
  const targetLimit = limit || (_streamHasActiveFilters() ? STREAM_FILTER_LIMIT : STREAM_DEFAULT_LIMIT);

  try {
    const events = await apiGet(`/api/audit?limit=${targetLimit}`);
    _streamEvents = events;
    _streamLoadedLimit = targetLimit;

    if (!events.length) {
      list.innerHTML = '<p class="placeholder">No events yet — route a payload to generate entries.</p>';
      return;
    }

    _streamOpenId = null; // reset before render so toggle logic works correctly

    _populateStreamDeptFilter(events);
    applyStreamFilters();

    // Restore the previously open event detail without closing it
    if (wasOpenId) {
      const detail = document.getElementById(`gov-detail-${wasOpenId}`);
      if (detail) {
        const groupDetail = detail.closest(".gov-event-group-detail");
        if (groupDetail && !groupDetail.classList.contains("open")) {
          toggleStreamGroup(groupDetail.id, groupDetail.closest(".gov-event-group"));
        }
        toggleStreamEvent(wasOpenId);
      }
    }

  } catch (err) {
    list.innerHTML = `<p class="placeholder" style="color:var(--accent-red)">Failed to load: ${err.message}</p>`;
  }
}

// ── Click to expand ───────────────────────────────────────────────────────────

async function toggleStreamEvent(eventId) {
  const detail = document.getElementById(`gov-detail-${eventId}`);
  if (!detail) return;
  const card = document.getElementById(`gov-ev-${eventId}`);

  const isOpen = detail.classList.contains("open");

  // Close any other open detail
  document.querySelectorAll(".gov-event-detail.open").forEach(d => {
    d.classList.remove("open");
    d.closest(".gov-event")?.setAttribute("aria-expanded", "false");
  });
  _streamOpenId = null;

  if (isOpen) {
    card?.setAttribute("aria-expanded", "false");
    return;
  }

  detail.classList.add("open");
  card?.setAttribute("aria-expanded", "true");
  _streamOpenId = eventId;

  // Fetch full detail
  try {
    const e = await apiGet(`/api/audit/${eventId}`);
    let snapshot = {};
    try { snapshot = JSON.parse(e.context_snapshot || "{}"); } catch {}

    const cost       = e.cost_usd != null ? `$${e.cost_usd.toFixed(6)}` : "—";
    const agentLine  = `${e.display_agent_name || e.agent_name || "not linked"} &nbsp;|&nbsp; ${e.source_platform || "unknown"} &nbsp;|&nbsp; ${e.display_department || e.department || "—"}`;
    const budgetLine = snapshot.budget_spent_usd != null
      ? `$${snapshot.budget_spent_usd ?? "?"} / $${snapshot.budget_cap_usd ?? "?"} &nbsp;(${snapshot.budget_used_pct ?? "?"}% used) &nbsp;|&nbsp; Throttled: ${snapshot.throttled ?? "?"} &nbsp;|&nbsp; Override: ${snapshot.override_granted ?? "?"}`
      : "—";
    const usageSource = e.usage_source || snapshot.usage_source || null;
    const usageLabel = usageSource === "provider_reported" ? "Provider reported" : (usageSource === "estimated" ? "Estimated" : "Not recorded");

    const detailSaved = Number(snapshot.tokens_saved ?? e.tokens_saved ?? 0);
    const hasPruningStats = snapshot.raw_tokens != null || snapshot.clean_tokens != null || snapshot.tokens_saved != null || e.tokens_saved != null;
    const pruningLine = hasPruningStats
      ? `<div class="gov-detail-label">Pruning</div>
         <div class="gov-detail-mono">${
           detailSaved > 0
             ? `Raw: ${snapshot.raw_tokens ?? e.raw_tokens ?? "?"} tokens &nbsp;→&nbsp; Clean: ${snapshot.clean_tokens ?? e.clean_tokens ?? "?"} tokens &nbsp;|&nbsp; <span style="color:var(--accent-green)">Saved: ${detailSaved.toLocaleString()} tokens (${snapshot.compression_pct ?? e.compression_pct ?? 0}% reduction)</span>`
             : `No token savings recorded for this event. Short payloads often have nothing for the pruner to remove.`
         }</div>`
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
      <div class="gov-detail-text">${formatExecutiveRationale(e, snapshot)}</div>

      <div class="gov-detail-label">Agent Context</div>
      <div class="gov-detail-mono">${agentLine}</div>

      <div class="gov-detail-label">Budget Context</div>
      <div class="gov-detail-mono">${budgetLine}</div>

      <div class="gov-detail-label">Token Usage Source</div>
      <div class="gov-detail-mono">${usageLabel}</div>

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

// ── Governance Event Stream Filters ──────────────────────────────────────────

async function applyStreamFilters() {
  _markStreamUserInteracting();
  const hasFilters = _streamHasActiveFilters();

  if (hasFilters && _streamLoadedLimit < STREAM_FILTER_LIMIT) {
    await loadEventStream(STREAM_FILTER_LIMIT);
    return;
  }

  if (!hasFilters && _streamLoadedLimit > STREAM_DEFAULT_LIMIT) {
    await loadEventStream(STREAM_DEFAULT_LIMIT);
    return;
  }

  const { type, dept, search } = _streamFilterState();

  const filtered = _streamEvents.filter(e => {
    if (!_streamTypeMatchesFilter(_streamEventType(e), type)) return false;
    if (dept   && (e.display_department || e.department || "").toLowerCase() !== dept) return false;
    if (search && !(
      (e.decision_outcome || "").toLowerCase().includes(search) ||
      (e.display_department || e.department || "").toLowerCase().includes(search) ||
      (e.display_agent_name || e.agent_name || "").toLowerCase().includes(search) ||
      (e.rationale        || "").toLowerCase().includes(search)
    )) return false;
    return true;
  });

  if (!filtered.length && hasFilters) {
    const list = document.getElementById("eventStreamList");
    if (list) {
      list.innerHTML = `<p class="placeholder">No events match the current filters in the latest ${_streamLoadedLimit} audit records.</p>`;
    }
    return;
  }

  _renderStreamCards(filtered);
}

function _populateStreamDeptFilter(events) {
  const sel = document.getElementById("streamFilterDept");
  if (!sel) return;
  const current = sel.value;
  while (sel.options.length > 1) sel.remove(1);  // keep "All Depts", rebuild the rest
  const depts = [...new Set(events.map(e => e.display_department || e.department).filter(Boolean))].sort();
  depts.forEach(d => {
    const opt = document.createElement("option");
    opt.value = d.toLowerCase(); opt.textContent = d;
    sel.appendChild(opt);
  });
  if ([...sel.options].some(opt => opt.value === current)) {
    sel.value = current; // restore previously selected dept if still valid
  }
}

// ── Routing Decision Feed Filters ─────────────────────────────────────────────

let _routingEvents    = [];
let _routingBlockedOn = false;

function applyRoutingFilters() {
  const risk    = (document.getElementById("routingFilterRisk")?.value || "").toLowerCase();
  const tier    = (document.getElementById("routingFilterTier")?.value || "").toLowerCase();

  const filtered = _routingEvents.filter(e => {
    if (_routingBlockedOn && !(e.decision_outcome || "").toLowerCase().includes("blocked")) return false;
    if (risk && (e.risk_level || "").toLowerCase() !== risk) return false;
    if (tier) {
      const t = (e.model_tier || "").toLowerCase();
      if (tier === "scout"     && !["scout","analyst","micro"].includes(t))     return false;
      if (tier === "advisor"   && !["advisor"].includes(t))                     return false;
      if (tier === "strategist"&& !["strategist","flagship"].includes(t))       return false;
    }
    return true;
  });

  _renderRoutingRows(filtered);
}

function toggleRoutingBlocked() {
  _routingBlockedOn = !_routingBlockedOn;
  const btn = document.getElementById("routingBlockedBtn");
  if (btn) {
    btn.style.color       = _routingBlockedOn ? "var(--accent-red)" : "";
    btn.style.borderColor = _routingBlockedOn ? "var(--accent-red)" : "";
  }
  applyRoutingFilters();
}

// Staggered 1600ms
setTimeout(loadEventStream, 1600);
setInterval(() => {
  if (_streamOpenId) return;
  if (Date.now() < _streamUserInteractingUntil) return;
  loadEventStream();
}, 15000);
