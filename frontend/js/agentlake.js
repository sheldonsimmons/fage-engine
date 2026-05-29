/**
 * agentlake.js — Agentlake Registry & Traffic Cop UI  [Step 5]
 */

/** Fetch all agents and render the registry table */
async function loadAgents() {
  try {
    const agents = await apiGet("/api/agents");
    renderAgentTable(agents);
    updateKpiAgents(agents);
  } catch (err) {
    document.getElementById("agentTableBody").innerHTML =
      `<tr><td colspan="9" class="placeholder" style="color:var(--accent-red)">Failed to load agents: ${err.message}</td></tr>`;
  }
}

function renderAgentTable(agents) {
  const tbody     = document.getElementById("agentTableBody");
  const alertBox  = document.getElementById("collisionAlert");

  // Auto-show collision alert if any agents are locked
  const locked = agents.filter(a => a.status === "locked");
  if (locked.length >= 2) {
    // Group by record to find actual collisions
    const byRecord = {};
    locked.forEach(a => {
      const key = `${a.target_table}#${a.target_record_id}`;
      if (!byRecord[key]) byRecord[key] = [];
      byRecord[key].push(a);
    });
    const collisions = Object.entries(byRecord).filter(([, agents]) => agents.length >= 2);
    if (collisions.length) {
      const [record, collidedAgents] = collisions[0];
      const names = collidedAgents.map(a => a.name).join(" & ");
      alertBox.innerHTML = `
        <div class="collision-header">&#9888;&nbsp; CONCURRENCY COLLISION DETECTED</div>
        <div class="collision-body">
          <strong>${names}</strong> both attempted to write to
          <code>${record}</code> simultaneously.<br/>
          Both agents are now <strong>LOCKED</strong> — no data was written.
          Use the <em>Release</em> buttons above to resolve.
        </div>
      `;
      alertBox.style.display = "block";
    }
  } else if (locked.length === 0) {
    alertBox.style.display = "none";
  }

  if (!agents.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="placeholder">No agents registered.</td></tr>';
    return;
  }

  tbody.innerHTML = agents.map(a => {
    const badgeClass = a.status === "locked"  ? "badge-locked"
                     : a.status === "queued"  ? "badge-locked"
                     : a.status === "active"  ? "badge-active"
                     : "badge-idle";

    const target = a.target_table
      ? `${a.target_table}${a.target_record_id ? " #" + a.target_record_id : ""}`
      : "—";

    const actionBtn = (a.status === "locked" || a.status === "active" || a.status === "queued")
      ? `<button class="btn-release" onclick="releaseAgent(${a.id})">Release</button>`
      : a.archived
        ? `<button class="btn-deregister" style="color:var(--accent-green);border-color:var(--accent-green)" onclick="unarchiveAgent(${a.id}, '${a.name}')">Restore</button>`
        : `<button class="btn-deregister" onclick="archiveAgent(${a.id}, '${a.name}')">Archive</button>`;

    const lockTip = a.lock_reason
      ? `title="${a.lock_reason}"`
      : "";

    const policyLabel = a.collision_policy === "queue" ? "QUEUE"
                      : a.collision_policy === "skip"  ? "SKIP"
                      : "LOCK";
    const policyColor = a.collision_policy === "queue" ? "var(--accent-yellow)"
                      : a.collision_policy === "skip"  ? "var(--text-muted)"
                      : "var(--accent-red)";

    const lastActive = a.last_used_at
      ? new Date(a.last_used_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : "—";

    const platform = a.source_platform || "Custom";
    const platformColor = platform === "Salesforce" ? "var(--accent)"
      : platform === "ServiceNow" ? "var(--accent-green)"
      : platform === "HubSpot"    ? "var(--accent-yellow)"
      : "var(--text-muted)";

    const TIER_OPTS = [
      [1, "Scout",      "var(--tier-scout)"],
      [2, "Analyst",    "var(--tier-analyst)"],
      [3, "Advisor",    "var(--tier-advisor)"],
      [4, "Strategist", "var(--tier-strategist)"],
    ];
    const tierSelect = (id, label, current) =>
      `<select id="${id}" onchange="saveTierBounds(${a.id})"
         style="background:var(--panel-bg);border:1px solid var(--border);color:var(--text-muted);
                font-size:10px;padding:2px 4px;border-radius:3px;cursor:pointer">
        ${TIER_OPTS.map(([v, n]) =>
          `<option value="${v}" ${v === current ? "selected" : ""}>${label}: ${n}</option>`
        ).join("")}
       </select>`;

    const minVal = a.min_tier || 1;
    const maxVal = a.max_tier || 4;

    const rowClass = a.status === "locked" ? "row-locked"
                   : a.status === "active" ? "row-active"
                   : "";

    return `
      <tr id="agent-row-${a.id}" ${rowClass ? `class="${rowClass}"` : ""}>
        <td ${lockTip}>${a.name}</td>
        <td>${a.department}</td>
        <td style="font-size:11px; font-weight:600; color:${platformColor}">${platform}</td>
        <td style="font-family:var(--font-mono); font-size:11px">${target}</td>
        <td style="font-size:11px; font-weight:600; color:${policyColor}">${policyLabel}</td>
        <td><span class="badge ${badgeClass}">${a.status.toUpperCase()}</span></td>
        <td style="font-size:11px; color:var(--text-muted)">${lastActive}</td>
        <td style="white-space:nowrap">
          <div style="display:flex;flex-direction:column;gap:3px">
            ${tierSelect(`min-tier-${a.id}`, "Min", minVal)}
            ${tierSelect(`max-tier-${a.id}`, "Max", maxVal)}
          </div>
        </td>
        <td>${actionBtn}</td>
      </tr>
    `;
  }).join("");
}

/** Save tier bounds for an agent — called on dropdown change */
async function saveTierBounds(agentId) {
  const minEl = document.getElementById(`min-tier-${agentId}`);
  const maxEl = document.getElementById(`max-tier-${agentId}`);
  if (!minEl || !maxEl) return;

  const minTier = parseInt(minEl.value, 10);
  const maxTier = parseInt(maxEl.value, 10);

  if (minTier > maxTier) {
    // Snap max up to match min if user set min above max
    maxEl.value = minTier;
  }

  try {
    await apiPatch(`/api/agents/${agentId}/tier-bounds`, {
      min_tier: parseInt(minEl.value, 10),
      max_tier: parseInt(maxEl.value, 10),
    });
    // Brief visual confirmation
    const row = document.getElementById(`agent-row-${agentId}`);
    if (row) {
      row.style.outline = "1px solid var(--tier-scout)";
      setTimeout(() => { row.style.outline = ""; }, 800);
    }
  } catch (err) {
    alert("Failed to save tier bounds: " + err.message);
  }
}

function updateKpiAgents(agents) {
  const el = document.getElementById("kpiAgents");
  if (el) el.textContent = agents.length;
}

/** Release a locked or active agent back to idle */
async function releaseAgent(agentId) {
  try {
    await apiPost(`/api/agents/${agentId}/release`, {});
    document.getElementById("collisionAlert").style.display = "none";
    await loadAgents();
  } catch (err) {
    alert("Failed to release agent: " + err.message);
  }
}

/** Register a new agent via the UI form */
async function registerAgent() {
  const name   = document.getElementById("regName").value.trim();
  const dept   = document.getElementById("regDept").value;
  const table  = document.getElementById("regTable").value;
  const perms  = document.getElementById("regPerms").value;
  const status = document.getElementById("registerStatus");

  if (!name) {
    status.textContent = "Agent name is required.";
    status.style.color = "var(--accent-red)";
    return;
  }

  status.textContent = "Registering...";
  status.style.color = "var(--text-muted)";

  try {
    const agent = await apiPost("/api/agents/register", {
      name:             name,
      department:       dept,
      source_platform:  document.getElementById("regPlatform").value || null,
      permissions:      perms,
      target_table:     table,
      collision_policy: document.getElementById("regPolicy").value,
    });
    document.getElementById("regName").value = "";
    status.textContent = `✓ "${agent.name}" registered with ID ${agent.id}`;
    status.style.color = "var(--accent-green)";
    await loadAgents();
    setTimeout(() => { status.textContent = ""; }, 4000);
  } catch (err) {
    status.textContent = "Error: " + err.message;
    status.style.color = "var(--accent-red)";
  }
}

/** Archive an agent (soft-delete — history preserved, removed from live grid) */
async function archiveAgent(agentId, agentName) {
  if (!confirm(`Archive "${agentName}"?\n\nThe agent will be removed from the live registry but all audit history and cost data will be preserved in reports.`)) return;
  try {
    await apiPost(`/api/agents/${agentId}/archive`, {});
    await loadAgents();
  } catch (err) {
    alert("Failed to archive agent: " + err.message);
  }
}

/** Restore an archived agent back to the live registry */
async function unarchiveAgent(agentId, agentName) {
  try {
    await apiPost(`/api/agents/${agentId}/unarchive`, {});
    await loadAgents();
  } catch (err) {
    alert("Failed to restore agent: " + err.message);
  }
}

/** Simulate a concurrency collision between SupportBot-Alpha and SupportBot-Beta */
async function runCollisionSim() {
  const alertBox = document.getElementById("collisionAlert");
  alertBox.style.display = "none";

  try {
    const result = await apiPost("/api/agents/simulate-collision", {
      agent_id_1: 1,
      agent_id_2: 2,
      table:      "tickets",
      record_id:  3,
    });

    await loadAgents();

    if (result.collision) {
      const names = result.locked_agents.map(a => a.name).join(" & ");
      alertBox.innerHTML = `
        <div class="collision-header">&#9888;&nbsp; CONCURRENCY COLLISION DETECTED</div>
        <div class="collision-body">
          <strong>${names}</strong> both attempted to write to
          <code>tickets #${result.locked_agents[0].target_record_id}</code>
          simultaneously.<br/>
          Both agents are now <strong>LOCKED</strong> — no data was written.
          Use the <em>Release</em> buttons above to resolve.
        </div>
      `;
      alertBox.style.display = "block";
    }
  } catch (err) {
    alertBox.innerHTML = `<div class="collision-body" style="color:var(--accent-red)">Error: ${err.message}</div>`;
    alertBox.style.display = "block";
  }
}

// Load on page ready, refresh every 10 seconds (staggered 400ms)
setTimeout(loadAgents, 400);
setInterval(loadAgents, 10000);

// ── Dashboard Agentlake Filters ───────────────────────────────────────────────

let _allAgents = []; // cache for client-side filtering

// Whether to include archived agents in the table (toggled by supervisor)
let _showArchived = false;

function toggleArchivedAgents() {
  _showArchived = !_showArchived;
  const btn = document.getElementById("toggleArchivedBtn");
  if (btn) btn.textContent = _showArchived ? "Hide Archived" : "Show Archived";
  loadAgents();
}

// Override loadAgents to cache results and populate dept dropdown
const _origLoadAgents = loadAgents;
async function loadAgents() {
  try {
    const url = _showArchived ? "/api/agents?include_archived=true" : "/api/agents";
    const agents = await apiGet(url);
    _allAgents = agents;
    renderAgentCards(agents.filter(a => !a.archived));  // cards show live only
    renderAgentTable(agents);
    updateKpiAgents(agents.filter(a => !a.archived));
    populateDeptFilter(agents);
  } catch (err) {
    document.getElementById("agentTableBody").innerHTML =
      `<tr><td colspan="8" class="placeholder" style="color:var(--accent-red)">Failed to load agents: ${err.message}</td></tr>`;
  }
}

/** Render live-status agent cards (the visual grid at the top of the registry) */
function renderAgentCards(agents) {
  const grid = document.getElementById("agentCardGrid");
  if (!grid) return;

  if (!agents.length) {
    grid.innerHTML = '<p class="placeholder" style="font-size:12px">No agents registered yet.</p>';
    return;
  }

  const platformColor = p =>
    p === "Salesforce"  ? "var(--accent)" :
    p === "ServiceNow"  ? "var(--accent-green)" :
    p === "HubSpot"     ? "var(--accent-yellow)" :
    "var(--text-muted)";

  const badgeClass = s =>
    s === "active" ? "badge-active" :
    s === "locked" ? "badge-critical" :
    s === "queued" ? "badge-locked"  :
    "badge-idle";

  const fmtLast = iso => iso
    ? new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "Never";

  grid.innerHTML = agents.map(a => {
    const platform = a.source_platform || "Custom";
    return `
      <div class="agent-status-card status-${a.status}" title="Click row below to manage">
        <div class="asc-name">${a.name}</div>
        <div class="asc-dept">${a.department}</div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:2px">
          <span class="asc-platform" style="color:${platformColor(platform)}">${platform}</span>
          <span class="badge ${badgeClass(a.status)}" style="font-size:9px;padding:1px 6px">${a.status.toUpperCase()}</span>
        </div>
        <div class="asc-last">${fmtLast(a.last_used_at)}</div>
      </div>`;
  }).join("");
}

function populateDeptFilter(agents) {
  const sel = document.getElementById("filterDept");
  if (!sel) return;
  const existing = new Set(Array.from(sel.options).map(o => o.value));
  const depts = [...new Set(agents.map(a => a.department).filter(Boolean))];
  depts.forEach(d => {
    if (!existing.has(d)) {
      const opt = document.createElement("option");
      opt.value = d; opt.textContent = d;
      sel.appendChild(opt);
    }
  });
}

function applyAgentFilters() {
  const platform = (document.getElementById("filterPlatform")?.value || "").toLowerCase();
  const status   = (document.getElementById("filterStatus")?.value   || "").toLowerCase();
  const dept     = (document.getElementById("filterDept")?.value     || "").toLowerCase();
  const search   = (document.getElementById("filterSearch")?.value   || "").toLowerCase();

  const filtered = _allAgents.filter(a => {
    if (platform && (a.source_platform || "custom").toLowerCase() !== platform) return false;
    if (status   && a.status.toLowerCase() !== status)   return false;
    if (dept     && a.department.toLowerCase() !== dept) return false;
    if (search   && !a.name.toLowerCase().includes(search)) return false;
    return true;
  });

  renderAgentTable(filtered);

  // Update count badge without affecting KPI
  const tbody = document.getElementById("agentTableBody");
  if (filtered.length < _allAgents.length) {
    // Show filter indicator in title
    const title = document.querySelector("#agentPanel .panel-title");
    if (title) {
      const existing = title.querySelector(".filter-count");
      if (existing) existing.remove();
      const chip = document.createElement("span");
      chip.className = "filter-count";
      chip.textContent = " " + filtered.length + " of " + _allAgents.length;
      title.appendChild(chip);
    }
  } else {
    const chip = document.querySelector("#agentPanel .filter-count");
    if (chip) chip.remove();
  }
}
