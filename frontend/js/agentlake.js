/**
 * agentlake.js — Agentlake Registry & Traffic Cop UI  [Step 5]
 */

const AGENT_ACTIVE_WINDOW_MS = 5000;

function displayAgentName(agent) {
  return agent?.display_name || agent?.agent_name || agent?.name || "Unnamed agent";
}

function displayAgentDept(agent) {
  if (agent?.display_department) return agent.display_department;
  return String(agent?.department || "—").replace(/^[A-Z0-9-]{12,}:/i, "");
}

function platformFamily(value) {
  const p = String(value || "").trim().toLowerCase();
  if (!p) return "";
  if (p.startsWith("salesforce")) return "salesforce";
  if (p.startsWith("servicenow")) return "servicenow";
  if (p.startsWith("hubspot")) return "hubspot";
  if (p.startsWith("dynamics")) return "dynamics365";
  if (p.startsWith("custom")) return "custom";
  return p;
}

function platformFilterLabel(value, rawValue) {
  const labels = {
    salesforce: "Salesforce",
    servicenow: "ServiceNow",
    hubspot: "HubSpot",
    dynamics365: "Dynamics365",
    zendesk: "Zendesk",
    slack: "Slack",
    custom: "Custom"
  };
  return labels[value] || rawValue || value;
}

function jsString(value) {
  return String(value || "").replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/\n/g, " ");
}

function effectiveAgentStatus(agent) {
  const status = (agent.status || "idle").toLowerCase();
  if (status === "locked" || status === "queued") return status;

  const hasLiveClaim = !!agent.target_record_id;
  if (hasLiveClaim) return "active";
  if (status !== "active") return "idle";
  if (agent.active_recently) return "active";

  const lastSeenMs = agent.last_used_at ? new Date(agent.last_used_at).getTime() : 0;
  const recentlyUsed = lastSeenMs && (Date.now() - lastSeenMs <= AGENT_ACTIVE_WINDOW_MS);
  return recentlyUsed ? "active" : "idle";
}

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
  const locked = agents.filter(a => effectiveAgentStatus(a) === "locked");
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
      const names = collidedAgents.map(displayAgentName).join(" & ");
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

  if (document.body.classList.contains("admin-page")) {
    renderAdminAgentTable(agents, tbody);
    return;
  }

  if (!agents.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="placeholder">No agents registered.</td></tr>';
    return;
  }

  tbody.innerHTML = agents.map(a => {
    const effectiveStatus = effectiveAgentStatus(a);
    const badgeClass = effectiveStatus === "locked"  ? "badge-locked"
                     : effectiveStatus === "queued"  ? "badge-locked"
                     : effectiveStatus === "active"  ? "badge-active"
                     : "badge-idle";

    const target = a.target_table
      ? `${a.target_table}${a.target_record_id ? " #" + a.target_record_id : ""}`
      : "—";

    const actionBtn = (effectiveStatus === "locked" || effectiveStatus === "active" || effectiveStatus === "queued")
      ? `<button class="btn-release" onclick="releaseAgent(${a.id})">Release</button>`
      : a.archived
        ? `<button class="btn-deregister" style="color:var(--accent-green);border-color:var(--accent-green)" onclick="unarchiveAgent(${a.id}, '${jsString(displayAgentName(a))}')">Restore</button>`
        : `<button class="btn-deregister" onclick="archiveAgent(${a.id}, '${jsString(displayAgentName(a))}')">Archive</button>`;

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
    const platformColor = platformFamily(platform) === "salesforce" ? "var(--accent)"
      : platformFamily(platform) === "servicenow" ? "var(--accent-green)"
      : platformFamily(platform) === "hubspot"    ? "var(--accent-yellow)"
      : "var(--text-muted)";

    const TIER_OPTS = [1, 2, 3, 4].map(v => [v, getTierName(v), getTierCssVar(v)]);
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
    const deptRaw = jsString(a.department || "");
    const deptLabel = jsString(displayAgentDept(a));

    const pruningOn = a.pruning_enabled !== false;
    const pruneBtn = `
      <button
        id="prune-btn-${a.id}"
        onclick="toggleAgentPruning(${a.id}, ${pruningOn})"
        title="${pruningOn ? "Pruning ON — click to disable" : "Pruning OFF — click to enable"}"
        style="font-size:10px;padding:2px 7px;border-radius:3px;cursor:pointer;font-weight:600;
               border:1px solid ${pruningOn ? "var(--accent-green)" : "var(--border)"};
               color:${pruningOn ? "var(--accent-green)" : "var(--text-muted)"};
               background:transparent">
        ${pruningOn ? "PRUNE ON" : "PRUNE OFF"}
      </button>`;

    const rowClass = effectiveStatus === "locked" ? "row-locked"
                   : effectiveStatus === "active" ? "row-active"
                   : "";

    return `
      <tr id="agent-row-${a.id}" ${rowClass ? `class="${rowClass}"` : ""}>
        <td ${lockTip}>
          <span>${displayAgentName(a)}</span>
          <button type="button" onclick="event.stopPropagation(); renameAgent(${a.id}, '${jsString(displayAgentName(a))}')"
            title="Rename agent"
            style="margin-left:6px;background:transparent;border:1px solid var(--border);color:var(--text-muted);border-radius:4px;padding:1px 6px;font-size:10px;cursor:pointer">Rename</button>
        </td>
        <td>${displayAgentDept(a)}</td>
        <td style="font-size:11px; font-weight:600; color:${platformColor}">${platform}</td>
        <td style="font-family:var(--font-mono); font-size:11px">${target}</td>
        <td style="font-size:11px; font-weight:600; color:${policyColor}">${policyLabel}</td>
        <td><span class="badge ${badgeClass}">${effectiveStatus.toUpperCase()}</span></td>
        <td style="font-size:11px; color:var(--text-muted)">${lastActive}</td>
        <td style="white-space:nowrap">
          <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">
            ${tierSelect(`min-tier-${a.id}`, "Min", minVal)}
            ${tierSelect(`max-tier-${a.id}`, "Max", maxVal)}
            <button type="button"
              onclick="event.stopPropagation(); applyTierBoundsToDepartment(${a.id}, '${deptRaw}', '${deptLabel}')"
              title="Apply this min/max tier policy to all visible agents in this department"
              style="background:transparent;border:1px solid var(--border);color:var(--text-muted);
                     border-radius:3px;padding:2px 5px;font-size:10px;cursor:pointer">
              Apply to Dept
            </button>
          </div>
        </td>
        <td style="text-align:center">${pruneBtn}</td>
        <td>${actionBtn}</td>
      </tr>
    `;
  }).join("");
}

let _adminSelectedAgentId = null;

function renderAdminAgentTable(agents, tbody) {
  if (!agents.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="placeholder">No agents registered.</td></tr>';
    closeAdminAgentDrawer();
    return;
  }

  tbody.innerHTML = agents.map(agent => {
    const status = effectiveAgentStatus(agent);
    const badgeClass = status === "active" ? "badge-active"
      : status === "idle" ? "badge-idle"
      : "badge-locked";
    const lastActive = agent.last_used_at
      ? new Date(agent.last_used_at).toLocaleString("en-US", {
          month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
        })
      : "Never";
    return `<tr id="agent-row-${agent.id}" class="admin-agent-row${_adminSelectedAgentId === agent.id ? " selected" : ""}"
        onclick="openAdminAgentDrawer(${agent.id})">
      <td>
        <strong>${displayAgentName(agent)}</strong>
        <span class="admin-agent-secondary">${agent.target_table || "No target configured"}</span>
      </td>
      <td>${displayAgentDept(agent)}</td>
      <td>${agent.source_platform || "Custom"}</td>
      <td><span class="badge ${badgeClass}">${status.toUpperCase()}</span></td>
      <td class="admin-agent-last">${lastActive}</td>
      <td><button type="button" class="btn-cap" onclick="event.stopPropagation();openAdminAgentDrawer(${agent.id})">Manage</button></td>
    </tr>`;
  }).join("");

  if (_adminSelectedAgentId) {
    const selected = agents.find(agent => agent.id === _adminSelectedAgentId);
    if (selected) renderAdminAgentDrawer(selected);
    else closeAdminAgentDrawer();
  }
}

function openAdminAgentDrawer(agentId) {
  const agent = _allAgents.find(item => item.id === agentId);
  if (!agent) return;
  _adminSelectedAgentId = agentId;
  document.querySelectorAll(".admin-agent-row").forEach(row => row.classList.remove("selected"));
  document.getElementById(`agent-row-${agentId}`)?.classList.add("selected");
  renderAdminAgentDrawer(agent);
}

function closeAdminAgentDrawer() {
  _adminSelectedAgentId = null;
  document.getElementById("adminAgentDrawer")?.classList.remove("open");
  document.getElementById("adminAgentDrawerBackdrop")?.classList.remove("open");
  document.querySelectorAll(".admin-agent-row").forEach(row => row.classList.remove("selected"));
}

function renderAdminAgentDrawer(agent) {
  const drawer = document.getElementById("adminAgentDrawer");
  const backdrop = document.getElementById("adminAgentDrawerBackdrop");
  const content = document.getElementById("adminAgentDrawerContent");
  if (!drawer || !backdrop || !content) return;

  const status = effectiveAgentStatus(agent);
  const minTier = agent.min_tier || 1;
  const maxTier = agent.max_tier || 4;
  const pruningOn = agent.pruning_enabled !== false;
  const target = agent.target_table
    ? `${agent.target_table}${agent.target_record_id ? ` #${agent.target_record_id}` : ""}`
    : "Not configured";
  const policy = agent.collision_policy === "queue" ? "Queue"
    : agent.collision_policy === "skip" ? "Skip"
    : "Lock";
  const tierOptions = current => [1, 2, 3, 4].map(value =>
    `<option value="${value}" ${value === current ? "selected" : ""}>${getTierName(value)}</option>`
  ).join("");
  const actionButton = ["locked", "active", "queued"].includes(status)
    ? `<button class="btn-release" onclick="releaseAgent(${agent.id});closeAdminAgentDrawer()">Release Agent</button>`
    : agent.archived
      ? `<button class="btn-deregister admin-restore" onclick="unarchiveAgent(${agent.id}, '${jsString(displayAgentName(agent))}');closeAdminAgentDrawer()">Restore Agent</button>`
      : `<button class="btn-deregister" onclick="archiveAgent(${agent.id}, '${jsString(displayAgentName(agent))}');closeAdminAgentDrawer()">Archive Agent</button>`;

  content.innerHTML = `
    <div class="admin-drawer-kicker">Agent management</div>
    <div class="admin-drawer-title-row">
      <div>
        <h2>${displayAgentName(agent)}</h2>
        <p>${displayAgentDept(agent)} · ${agent.source_platform || "Custom"}</p>
      </div>
      <span class="badge ${status === "active" ? "badge-active" : status === "idle" ? "badge-idle" : "badge-locked"}">${status.toUpperCase()}</span>
    </div>
    <div class="admin-drawer-section">
      <h3>Connection</h3>
      <div class="admin-detail-grid">
        <div><span>Target</span><strong>${target}</strong></div>
        <div><span>Permissions</span><strong>${agent.permissions || "—"}</strong></div>
        <div><span>Collision policy</span><strong>${policy}</strong></div>
        <div><span>Last active</span><strong>${agent.last_used_at ? new Date(agent.last_used_at).toLocaleString() : "Never"}</strong></div>
      </div>
    </div>
    <div class="admin-drawer-section">
      <h3>Routing controls</h3>
      <div class="admin-routing-controls">
        <label>Minimum tier<select id="min-tier-${agent.id}" onchange="saveTierBounds(${agent.id})">${tierOptions(minTier)}</select></label>
        <label>Maximum tier<select id="max-tier-${agent.id}" onchange="saveTierBounds(${agent.id})">${tierOptions(maxTier)}</select></label>
      </div>
      <button type="button" class="admin-secondary-btn"
        onclick="applyTierBoundsToDepartment(${agent.id}, '${jsString(agent.department || "")}', '${jsString(displayAgentDept(agent))}')">
        Apply these bounds to ${displayAgentDept(agent)}
      </button>
    </div>
    <div class="admin-drawer-section">
      <h3>Context pruning</h3>
      <div class="admin-pruning-row">
        <div><strong>${pruningOn ? "Enabled" : "Disabled"}</strong><span>${pruningOn ? "CostPilot removes unnecessary context before routing." : "Requests are sent without context pruning."}</span></div>
        <button id="prune-btn-${agent.id}" class="admin-toggle-btn ${pruningOn ? "on" : ""}"
          onclick="toggleAgentPruning(${agent.id}, ${pruningOn})">${pruningOn ? "ON" : "OFF"}</button>
      </div>
    </div>
    <div class="admin-drawer-actions">
      <button class="admin-secondary-btn" onclick="renameAgent(${agent.id}, '${jsString(displayAgentName(agent))}')">Rename</button>
      ${actionButton}
    </div>`;
  drawer.classList.add("open");
  backdrop.classList.add("open");
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

async function applyTierBoundsToDepartment(agentId, department, departmentLabel) {
  const minEl = document.getElementById(`min-tier-${agentId}`);
  const maxEl = document.getElementById(`max-tier-${agentId}`);
  if (!minEl || !maxEl || !department) return;

  const minTier = parseInt(minEl.value, 10);
  const maxTier = parseInt(maxEl.value, 10);
  if (minTier > maxTier) {
    alert("Min tier cannot be higher than max tier.");
    return;
  }

  const ok = confirm(`Apply ${getTierName(minTier)}-${getTierName(maxTier)} tier bounds to all visible ${departmentLabel || department} agents?`);
  if (!ok) return;

  try {
    const result = await apiPatch("/api/agents/department-tier-bounds", {
      department,
      min_tier: minTier,
      max_tier: maxTier,
    });
    await loadAgents();
    alert(`Updated ${result.updated || 0} visible ${departmentLabel || department} agent(s).`);
  } catch (err) {
    alert("Failed to apply department tier bounds: " + err.message);
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

/** Toggle context pruning on/off for an agent */
async function toggleAgentPruning(agentId, currentlyOn) {
  const newState = !currentlyOn;
  try {
    await apiPatch(`/api/agents/${agentId}/pruning`, { enabled: newState });
    const btn = document.getElementById(`prune-btn-${agentId}`);
    if (btn) {
      btn.textContent = newState ? "PRUNE ON" : "PRUNE OFF";
      btn.title       = newState ? "Pruning ON — click to disable" : "Pruning OFF — click to enable";
      btn.style.borderColor = newState ? "var(--accent-green)" : "var(--border)";
      btn.style.color       = newState ? "var(--accent-green)" : "var(--text-muted)";
      btn.setAttribute("onclick", `toggleAgentPruning(${agentId}, ${newState})`);
    }
    const row = document.getElementById(`agent-row-${agentId}`);
    if (row) {
      row.style.outline = `1px solid ${newState ? "var(--accent-green)" : "var(--border)"}`;
      setTimeout(() => { row.style.outline = ""; }, 800);
    }
    if (document.body.classList.contains("admin-page")) {
      const agent = _allAgents.find(item => item.id === agentId);
      if (agent) {
        agent.pruning_enabled = newState;
        renderAdminAgentDrawer(agent);
      }
    }
  } catch (err) {
    alert("Failed to update pruning setting: " + err.message);
  }
}

async function renameAgent(agentId, currentName) {
  const next = prompt("Agent display name", currentName || "");
  if (next == null) return;
  const clean = next.trim();
  if (!clean) {
    alert("Agent name cannot be blank.");
    return;
  }
  try {
    await apiPatch(`/api/agents/${agentId}/name`, { name: clean });
    await loadAgents();
  } catch (err) {
    alert("Failed to rename agent: " + err.message);
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

// Load on page ready, then use adaptive polling:
// 1s while any agent is active/locked, 5s when all idle
setTimeout(loadAgents, 400);

let _agentPollTimer = null;
function scheduleAgentPoll(agents) {
  clearTimeout(_agentPollTimer);
  const hasLive = agents && agents.some(a => ["active", "locked", "queued"].includes(effectiveAgentStatus(a)));
  _agentPollTimer = setTimeout(async () => {
    await loadAgents();
  }, hasLive ? 1000 : 5000);
}

// ── Dashboard Agentlake Filters ───────────────────────────────────────────────

let _allAgents = []; // cache for client-side filtering
let _agentSpend = [];
let _agentSpendLoadedAt = 0;
let _agentlakeView = "overview";
let _agentlakeProjects = [];
let _agentlakeProjectSummary = {};
let _agentlakeProjectsLoadedAt = 0;

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
    await Promise.all([loadAgentlakeSpend(), loadAgentlakeProjects()]);
    populateAgentCardFilters(agents.filter(a => !a.archived));
    applyAgentCardFilters();
    renderAgentlakeViews();
    updateKpiAgents(agents.filter(a => !a.archived));
    populatePlatformFilter(agents);
    populateDeptFilter(agents);
    applyAgentFilters();
    scheduleAgentPoll(agents);
  } catch (err) {
    document.getElementById("agentTableBody").innerHTML =
      `<tr><td colspan="8" class="placeholder" style="color:var(--accent-red)">Failed to load agents: ${err.message}</td></tr>`;
    scheduleAgentPoll([]);
  }
}

async function loadAgentlakeSpend() {
  const stale = Date.now() - _agentSpendLoadedAt > 30000;
  if (!stale) return;
  try {
    _agentSpend = await apiGet("/api/agents/spend");
    _agentSpendLoadedAt = Date.now();
  } catch (error) {
    console.warn("Agent spend summary unavailable:", error);
  }
}

async function loadAgentlakeProjects() {
  const stale = Date.now() - _agentlakeProjectsLoadedAt > 30000;
  if (!stale) return;
  try {
    const workspaceId = localStorage.getItem("cp_workspace_id") || "";
    const workspaceQuery = workspaceId
      ? `?workspace_id=${encodeURIComponent(workspaceId)}`
      : "";
    const [projects, summary] = await Promise.all([
      apiGet(`/api/work-items${workspaceQuery}`),
      apiGet(`/api/work-items/summary${workspaceQuery}`)
    ]);
    _agentlakeProjects = Array.isArray(projects) ? projects : [];
    _agentlakeProjectSummary = summary || {};
    _agentlakeProjectsLoadedAt = Date.now();
  } catch (error) {
    console.warn("AgentLake project summary unavailable:", error);
  }
}

function agentlakeEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function agentlakeDate(iso) {
  if (!iso) return null;
  const value = String(iso);
  const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

function agentlakeLastUsed(iso) {
  if (!iso) return "Never";
  return agentlakeDate(iso).toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
  });
}

function setAgentlakeView(view) {
  _agentlakeView = ["overview", "usage", "departments", "projects", "all"].includes(view) ? view : "overview";
  const config = {
    overview: {
      panel: "agentlakeOverview", tab: "agentlakeTabOverview",
      description: "Operational summary of the agents that need attention or are being used."
    },
    usage: {
      panel: "agentlakeUsage", tab: "agentlakeTabUsage",
      description: "Compare 30-day adoption, traffic, spend, pruning, and last activity by agent."
    },
    departments: {
      panel: "agentlakeDepartments", tab: "agentlakeTabDepartments",
      description: "Browse the registry in smaller, collapsible department groups."
    },
    projects: {
      panel: "agentlakeProjects", tab: "agentlakeTabProjects",
      description: "Monitor which projects are using AI, which agents are involved, and where spend or risk needs attention."
    },
    all: {
      panel: "agentlakeAllAgents", tab: "agentlakeTabAll",
      description: "Complete agent registry with the existing filters and live-status cards."
    }
  };
  Object.values(config).forEach(item => {
    const panel = document.getElementById(item.panel);
    const tab = document.getElementById(item.tab);
    if (panel) panel.hidden = item !== config[_agentlakeView];
    if (tab) {
      const selected = item === config[_agentlakeView];
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", String(selected));
    }
  });
  const description = document.getElementById("agentlakeViewDescription");
  if (description) description.textContent = config[_agentlakeView].description;
  if (_agentlakeView === "projects") renderAgentlakeProjects();
}

function showNeverUsedAgents() {
  setAgentlakeView("usage");
  setAgentUsageView("unused");
}

function renderAgentlakeViews() {
  const agents = _allAgents.filter(agent => !agent.archived);
  const attention = agents.filter(agent => ["locked", "queued"].includes(effectiveAgentStatus(agent)));
  const active = agents.filter(agent => effectiveAgentStatus(agent) === "active");
  const usedRows = _agentSpend.filter(row => row.call_count > 0);
  const neverUsed = _agentSpend.filter(row => !row.call_count);

  const metrics = document.getElementById("agentlakeSummaryMetrics");
  if (metrics) {
    metrics.innerHTML = [
      [agents.length, "Registered"],
      [active.length, "Active now"],
      [attention.length, "Need attention"],
      [neverUsed.length, "Never used"]
    ].map(([value, label]) => `
      <div class="agentlake-metric">
        <span class="agentlake-metric-value">${Number(value).toLocaleString()}</span>
        <span class="agentlake-metric-label">${label}</span>
      </div>`).join("");
  }

  const attentionCount = document.getElementById("agentlakeAttentionCount");
  if (attentionCount) attentionCount.textContent = attention.length;
  const focusAttention = document.getElementById("agentlakeFocusAttention");
  if (focusAttention) focusAttention.textContent = attention.length.toLocaleString();
  renderAgentlakeCompactList("agentlakeAttentionList", attention.slice(0, 5), agent => ({
    name: displayAgentName(agent),
    meta: `${displayAgentDept(agent)} · ${agent.source_platform || "Custom"}`,
    value: effectiveAgentStatus(agent).toUpperCase()
  }), "No agents need attention.");

  const recent = [...agents]
    .filter(agent => agent.last_used_at)
    .sort((a, b) => new Date(b.last_used_at) - new Date(a.last_used_at))
    .slice(0, 5);
  renderAgentlakeCompactList("agentlakeRecentList", recent, agent => ({
    name: displayAgentName(agent),
    meta: `${displayAgentDept(agent)} · ${agent.source_platform || "Custom"}`,
    value: agentlakeLastUsed(agent.last_used_at)
  }), "No agent activity has been recorded.");

  renderAgentlakeCompactList("agentlakeSpendList", usedRows.slice(0, 5), row => ({
    name: row.display_name || row.agent_name,
    meta: `${Number(row.call_count).toLocaleString()} requests · ${row.display_department || row.department || "—"}${Number(row.simulation_call_count || 0) > 0 ? ` · ${Number(row.simulation_call_count).toLocaleString()} simulated` : ""}`,
    value: `$${Number(row.total_cost_usd || 0).toFixed(2)}`
  }), "No attributed agent spending yet.");

  const neverCount = document.getElementById("agentlakeNeverUsedCount");
  if (neverCount) neverCount.textContent = neverUsed.length.toLocaleString();
  const neverButton = document.getElementById("agentlakeNeverUsedButton");
  if (neverButton) neverButton.disabled = neverUsed.length === 0;

  renderAgentlakeDepartments(agents);
  renderAgentlakeProjects();
}

function agentlakeProjectStatusLabel(status) {
  return {
    active: "Active",
    on_hold: "On hold",
    completed: "Completed",
    cancelled: "Cancelled",
    archived: "Archived"
  }[status] || "Unknown";
}

function agentlakeWorkLabels() {
  const visible = _agentlakeProjects.filter(project => project.status !== "archived");
  const configured = visible
    .map(project => project.business_context?.work_label)
    .find(Boolean);
  const singular = String(configured || "Work").trim() || "Work";
  const plural = /[^aeiou]y$/i.test(singular)
    ? `${singular.slice(0, -1)}ies`
    : /(s|x|z|ch|sh)$/i.test(singular)
      ? `${singular}es`
      : `${singular}s`;
  return { singular, plural };
}

function clearAgentlakeProjectFilters() {
  const status = document.getElementById("agentlakeProjectStatus");
  const search = document.getElementById("agentlakeProjectSearch");
  if (status) status.value = "";
  if (search) search.value = "";
  renderAgentlakeProjects();
}

function renderAgentlakeProjects() {
  const list = document.getElementById("agentlakeProjectList");
  if (!list) return;

  // Agentlake polls for live status and rebuilds this list frequently. Preserve
  // the user's expanded project cards across those renders.
  const openProjects = new Set(
    [...list.querySelectorAll(".agentlake-project-card[open][data-project-id]")]
      .map(card => card.dataset.projectId)
      .filter(Boolean)
  );

  const statusFilter = document.getElementById("agentlakeProjectStatus")?.value || "";
  const search = (document.getElementById("agentlakeProjectSearch")?.value || "").trim().toLowerCase();
  const labels = agentlakeWorkLabels();
  const projects = _agentlakeProjects.filter(project => {
    // Archived work is historical evidence, not live AgentLake inventory.
    // It remains available through the dedicated work-management/reporting
    // surfaces but must never appear in this operational list.
    if (project.status === "archived") return false;
    if (statusFilter && project.status !== statusFilter) return false;
    const searchable = [
      project.name, project.external_id, project.account_name, project.owner,
      project.department, project.source_platform,
      ...(project.agents || []).map(agent => agent.name)
    ].filter(Boolean).join(" ").toLowerCase();
    return !search || searchable.includes(search);
  }).sort((a, b) => {
    const activityA = agentlakeDate(a.last_activity_at)?.getTime() || 0;
    const activityB = agentlakeDate(b.last_activity_at)?.getTime() || 0;
    return activityB - activityA || Number(b.spend_usd || 0) - Number(a.spend_usd || 0);
  });

  const totalProjects = _agentlakeProjects.filter(project => project.status !== "archived").length;
  const activeProjects = Number(_agentlakeProjectSummary.active_project_count || 0);
  const attributedPct = Number(_agentlakeProjectSummary.attributed_spend_pct || 0);
  const riskProjects = _agentlakeProjects.filter(project =>
    project.status !== "archived" && Number(project.risk_event_count || 0) > 0
  ).length;
  const metrics = document.getElementById("agentlakeProjectMetrics");
  if (metrics) {
    metrics.innerHTML = [
      [totalProjects, labels.plural],
      [activeProjects, "Active"],
      [`${attributedPct.toFixed(1)}%`, "Spend attributed"],
      [riskProjects, "With risk events"]
    ].map(([value, label]) => `
      <div class="agentlake-metric">
        <span class="agentlake-metric-value">${agentlakeEscape(value)}</span>
        <span class="agentlake-metric-label">${agentlakeEscape(label)}</span>
      </div>`).join("");
  }

  const count = document.getElementById("agentlakeProjectCount");
  if (count) count.textContent = `Showing ${projects.length} of ${totalProjects} ${labels.plural.toLowerCase()}`;

  const unattributedSpend = Number(_agentlakeProjectSummary.unattributed_spend_usd || 0);
  const unattributed = !statusFilter && !search && unattributedSpend > 0 ? `
    <section class="agentlake-project-card agentlake-project-unattributed">
      <div class="agentlake-project-identity">
        <span class="agentlake-project-status needs-attribution">Needs attribution</span>
        <h3>Unattributed AI activity</h3>
        <p>Usage that has not been linked to ${agentlakeEscape(labels.singular.toLowerCase())}.</p>
      </div>
      <div class="agentlake-project-stat"><strong>$${unattributedSpend.toFixed(2)}</strong><span>unattributed spend</span></div>
      <a class="agentlake-project-link" href="/work-items.html">Review attribution →</a>
    </section>` : "";

  const cards = projects.map(project => {
    const spend = Number(project.spend_usd || 0);
    const monthlySpend = Number(project.spend_month_usd || 0);
    const budget = project.monthly_ai_budget == null ? null : Number(project.monthly_ai_budget);
    const budgetPct = budget > 0 ? Math.min(100, monthlySpend / budget * 100) : null;
    const risks = Number(project.risk_event_count || 0);
    const team = project.agent_team || [];
    const assignedCount = Number(project.assigned_agent_count || 0);
    const platforms = project.activity_platforms?.length
      ? project.activity_platforms
      : [project.source_platform].filter(Boolean);
    const agentTeamRows = team.length
      ? team.map(member => {
          const unexpected = member.assignment_status === "unexpected";
          const state = unexpected ? "Unexpected" : member.usage_status === "used" ? "Used" : "Never used";
          return `<span class="agentlake-project-agent">
            <b>${agentlakeEscape(member.display_name || member.name)}</b>
            <i class="${unexpected ? "unexpected" : member.usage_status === "used" ? "used" : ""}">${agentlakeEscape(state)}</i>
          </span>`;
        }).join("")
      : '<span class="agentlake-empty-agent">No agents assigned or observed</span>';
    const tierNames = project.model_tiers?.length ? project.model_tiers.join(", ") : "—";
    return `
      <details
        class="agentlake-project-card${risks ? " has-risk" : ""}"
        data-project-id="${agentlakeEscape(project.external_id)}"
        ${openProjects.has(String(project.external_id)) ? "open" : ""}
      >
        <summary>
          <div class="agentlake-project-identity">
            <span class="agentlake-project-status status-${agentlakeEscape(project.status)}">${agentlakeEscape(agentlakeProjectStatusLabel(project.status))}</span>
            <h3>${agentlakeEscape(project.name)}</h3>
            <p>${agentlakeEscape(project.external_id)} · ${agentlakeEscape(project.department || "No department")}</p>
          </div>
          <div class="agentlake-project-stat"><strong>${assignedCount.toLocaleString()}</strong><span>assigned agents</span></div>
          <div class="agentlake-project-stat"><strong>${Number(project.request_count || 0).toLocaleString()}</strong><span>requests</span></div>
          <div class="agentlake-project-stat"><strong>$${spend.toFixed(2)}</strong><span>total spend</span></div>
          <div class="agentlake-project-stat${risks ? " risk" : ""}"><strong>${risks.toLocaleString()}</strong><span>risk events</span></div>
          <div class="agentlake-project-last"><strong>${agentlakeEscape(agentlakeLastUsed(project.last_activity_at))}</strong><span>last activity</span></div>
          <span class="agentlake-project-chevron">›</span>
        </summary>
        <div class="agentlake-project-detail">
          <div><span>Agent team</span><div class="agentlake-project-agent-list">${agentTeamRows}</div></div>
          <div><span>Platforms</span><strong>${agentlakeEscape(platforms.join(", ") || "—")}</strong></div>
          <div><span>Model tiers</span><strong>${agentlakeEscape(tierNames)}</strong></div>
          <div><span>Owner</span><strong>${agentlakeEscape(project.owner || "Unassigned")}</strong></div>
          <div class="agentlake-project-budget">
            <span>Monthly budget</span>
            <strong>${budget == null ? "Not set" : `$${monthlySpend.toFixed(2)} of $${budget.toFixed(2)}`}</strong>
            ${budgetPct == null ? "" : `<div><i style="width:${budgetPct.toFixed(1)}%"></i></div>`}
          </div>
          <a href="/work-items.html">Manage ${agentlakeEscape(labels.singular.toLowerCase())} →</a>
        </div>
      </details>`;
  }).join("");

  list.innerHTML = unattributed + cards ||
    `<div class="agentlake-empty">No ${agentlakeEscape(labels.plural.toLowerCase())} match these filters. Create or connect ${agentlakeEscape(labels.plural.toLowerCase())} from the Work page.</div>`;
}

function renderAgentlakeCompactList(elementId, rows, formatter, emptyMessage) {
  const element = document.getElementById(elementId);
  if (!element) return;
  if (!rows.length) {
    element.innerHTML = `<div class="agentlake-empty">${agentlakeEscape(emptyMessage)}</div>`;
    return;
  }
  element.innerHTML = rows.map(row => {
    const data = formatter(row);
    return `<div class="agentlake-compact-row">
      <div>
        <span class="agentlake-row-name">${agentlakeEscape(data.name)}</span>
        <span class="agentlake-row-meta">${agentlakeEscape(data.meta)}</span>
      </div>
      <span class="agentlake-row-value">${agentlakeEscape(data.value)}</span>
    </div>`;
  }).join("");
}

function renderAgentlakeDepartments(agents) {
  const container = document.getElementById("agentlakeDepartmentGroups");
  if (!container) return;
  const openDepartments = new Set(
    [...container.querySelectorAll(".agentlake-dept-group[open]")]
      .map(group => group.dataset.department)
      .filter(Boolean)
  );
  const spendById = new Map(_agentSpend.map(row => [row.agent_id, row]));
  const groups = new Map();
  agents.forEach(agent => {
    const department = displayAgentDept(agent) || "Unassigned";
    if (!groups.has(department)) groups.set(department, []);
    groups.get(department).push(agent);
  });
  container.innerHTML = [...groups.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([department, rows]) => {
      const attention = rows.filter(agent => ["locked", "queued"].includes(effectiveAgentStatus(agent))).length;
      const requests = rows.reduce((sum, agent) => sum + Number(spendById.get(agent.id)?.call_count || 0), 0);
      return `<details class="agentlake-dept-group" data-department="${agentlakeEscape(department)}"${openDepartments.has(department) ? " open" : ""}>
        <summary>
          <span class="agentlake-dept-name">${agentlakeEscape(department)}</span>
          <span class="agentlake-dept-stat">${rows.length} agents</span>
          <span class="agentlake-dept-stat">${requests.toLocaleString()} requests</span>
          <span class="agentlake-dept-stat">${attention ? `${attention} need attention` : "No issues"}</span>
        </summary>
        <div class="agentlake-dept-agents">
          ${rows.map(agent => {
            const status = effectiveAgentStatus(agent);
            return `<div class="agent-status-card status-${status}">
              <div class="asc-name">${agentlakeEscape(displayAgentName(agent))}</div>
              <div class="asc-dept">${agentlakeEscape(agent.source_platform || "Custom")}</div>
              <div style="display:flex;align-items:center;justify-content:space-between;margin-top:2px">
                <span class="asc-last">${agentlakeEscape(agentlakeLastUsed(agent.last_used_at))}</span>
                <span class="badge ${status === "active" ? "badge-active" : status === "idle" ? "badge-idle" : "badge-locked"}" style="font-size:9px;padding:1px 6px">${status.toUpperCase()}</span>
              </div>
            </div>`;
          }).join("")}
        </div>
      </details>`;
    }).join("") || '<p class="placeholder">No agents registered yet.</p>';
}

function setSelectOptions(selectId, options, defaultLabel, selectedValue = "") {
  const sel = document.getElementById(selectId);
  if (!sel) return;

  const normalizedSelected = String(selectedValue || "");
  sel.innerHTML = "";

  const allOpt = document.createElement("option");
  allOpt.value = "";
  allOpt.textContent = defaultLabel;
  sel.appendChild(allOpt);

  options.forEach(({ value, label }) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    sel.appendChild(opt);
  });

  sel.value = options.some(o => o.value === normalizedSelected) ? normalizedSelected : "";
}

function populateAgentCardFilters(agents) {
  const deptSelected = document.getElementById("agentCardFilterDept")?.value || "";
  const platformSelected = document.getElementById("agentCardFilterPlatform")?.value || "";

  const deptOptions = [...new Set(agents.map(displayAgentDept).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b))
    .map(d => ({ value: d, label: d }));

  const platformMap = new Map();
  agents.forEach(a => {
    const raw = (a.source_platform || "Custom").trim();
    const key = platformFamily(raw) || "custom";
    if (!platformMap.has(key)) platformMap.set(key, platformFilterLabel(key, raw));
  });
  const platformOptions = [...platformMap.entries()]
    .sort((a, b) => a[1].localeCompare(b[1]))
    .map(([value, label]) => ({ value, label }));

  setSelectOptions("agentCardFilterDept", deptOptions, "All Departments", deptSelected);
  setSelectOptions("agentCardFilterPlatform", platformOptions, "All Platforms", platformFamily(platformSelected));
}

function applyAgentCardFilters() {
  const grid = document.getElementById("agentCardGrid");
  if (!grid) return;

  const activeAgents = _allAgents.filter(a => !a.archived);
  const dept = (document.getElementById("agentCardFilterDept")?.value || "").toLowerCase();
  const platform = platformFamily(document.getElementById("agentCardFilterPlatform")?.value || "");
  const status = (document.getElementById("agentCardFilterStatus")?.value || "").toLowerCase();
  const search = (document.getElementById("agentCardFilterSearch")?.value || "").trim().toLowerCase();

  const filtered = activeAgents.filter(a => {
    const name = displayAgentName(a).toLowerCase();
    const agentDept = displayAgentDept(a).toLowerCase();
    const rawPlatform = (a.source_platform || "Custom").toLowerCase();
    const family = platformFamily(a.source_platform || "Custom");
    const effectiveStatus = effectiveAgentStatus(a);

    if (dept && agentDept !== dept) return false;
    if (platform && family !== platform) return false;
    if (status && effectiveStatus !== status) return false;
    if (search && !name.includes(search) && !agentDept.includes(search) && !rawPlatform.includes(search)) return false;
    return true;
  });

  renderAgentCards(filtered, activeAgents.length);
}

function clearAgentCardFilters() {
  const ids = ["agentCardFilterDept", "agentCardFilterPlatform", "agentCardFilterStatus", "agentCardFilterSearch"];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  applyAgentCardFilters();
}

/** Render live-status agent cards (the visual grid at the top of the registry) */
function renderAgentCards(agents, totalAgents = agents.length) {
  const grid = document.getElementById("agentCardGrid");
  if (!grid) return;

  const count = document.getElementById("agentCardFilterCount");
  if (count) {
    count.textContent = agents.length === totalAgents
      ? `Showing ${totalAgents} agents`
      : `Showing ${agents.length} of ${totalAgents} agents`;
  }

  if (!agents.length) {
    grid.innerHTML = totalAgents
      ? '<p class="placeholder" style="font-size:12px">No agents match these filters.</p>'
      : '<p class="placeholder" style="font-size:12px">No agents registered yet.</p>';
    return;
  }

  const platformColor = p =>
    platformFamily(p) === "salesforce"  ? "var(--accent)" :
    platformFamily(p) === "servicenow"  ? "var(--accent-green)" :
    platformFamily(p) === "hubspot"     ? "var(--accent-yellow)" :
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
    const effectiveStatus = effectiveAgentStatus(a);
    return `
      <div class="agent-status-card status-${effectiveStatus}" title="Click row below to manage">
        <div class="asc-name">${displayAgentName(a)}</div>
        <div class="asc-dept">${displayAgentDept(a)}</div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:2px">
          <span class="asc-platform" style="color:${platformColor(platform)}">${platform}</span>
          <span class="badge ${badgeClass(effectiveStatus)}" style="font-size:9px;padding:1px 6px">${effectiveStatus.toUpperCase()}</span>
        </div>
        <div class="asc-last">${fmtLast(a.last_used_at)}</div>
      </div>`;
  }).join("");
}

function populateDeptFilter(agents) {
  const sel = document.getElementById("filterDept");
  if (!sel) return;
  const existing = new Set(Array.from(sel.options).map(o => o.value));
  const depts = [...new Set(agents.map(displayAgentDept).filter(Boolean))];
  depts.forEach(d => {
    if (!existing.has(d)) {
      const opt = document.createElement("option");
      opt.value = d; opt.textContent = d;
      sel.appendChild(opt);
    }
  });
}

function populatePlatformFilter(agents) {
  const sel = document.getElementById("filterPlatform");
  if (!sel) return;

  const current = platformFamily(sel.value);
  const platforms = new Map([["", "All Platforms"]]);

  agents.forEach(a => {
    const raw = (a.source_platform || "Custom").trim();
    const key = platformFamily(raw) || "custom";
    if (!platforms.has(key)) platforms.set(key, platformFilterLabel(key, raw));
  });

  sel.innerHTML = "";
  platforms.forEach((label, value) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    sel.appendChild(opt);
  });

  sel.value = platforms.has(current) ? current : "";
}

function applyAgentFilters() {
  const platform = platformFamily(document.getElementById("filterPlatform")?.value || "");
  const status   = (document.getElementById("filterStatus")?.value   || "").toLowerCase();
  const dept     = (document.getElementById("filterDept")?.value     || "").toLowerCase();
  const search   = (document.getElementById("filterSearch")?.value   || "").toLowerCase();
  const hasFilter = !!(platform || status || dept || search);

  const filtered = _allAgents.filter(a => {
    if (platform && platformFamily(a.source_platform) !== platform) return false;
    if (status   && effectiveAgentStatus(a) !== status)   return false;
    if (dept     && displayAgentDept(a).toLowerCase() !== dept) return false;
    if (search   && !displayAgentName(a).toLowerCase().includes(search) && !displayAgentDept(a).toLowerCase().includes(search)) return false;
    return true;
  });

  renderAgentTable(filtered);

  // Update count badge without affecting KPI
  const tbody = document.getElementById("agentTableBody");
  if (hasFilter) {
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
