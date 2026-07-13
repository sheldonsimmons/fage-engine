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
    populateAgentCardFilters(agents.filter(a => !a.archived));
    applyAgentCardFilters();
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
