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
      `<tr><td colspan="5" class="placeholder" style="color:var(--accent-red)">Failed to load agents: ${err.message}</td></tr>`;
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
    tbody.innerHTML = '<tr><td colspan="6" class="placeholder">No agents registered.</td></tr>';
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
      : `<button class="btn-deregister" onclick="deregisterAgent(${a.id}, '${a.name}')">Remove</button>`;

    const lockTip = a.lock_reason
      ? `title="${a.lock_reason}"`
      : "";

    const policyLabel = a.collision_policy === "queue" ? "QUEUE"
                      : a.collision_policy === "skip"  ? "SKIP"
                      : "LOCK";
    const policyColor = a.collision_policy === "queue" ? "var(--accent-yellow)"
                      : a.collision_policy === "skip"  ? "var(--text-muted)"
                      : "var(--accent-red)";

    return `
      <tr id="agent-row-${a.id}" ${a.status === "locked" ? 'class="row-locked"' : ""}>
        <td ${lockTip}>${a.name}</td>
        <td>${a.department}</td>
        <td style="font-family:var(--font-mono); font-size:11px">${target}</td>
        <td style="font-size:11px; font-weight:600; color:${policyColor}">${policyLabel}</td>
        <td><span class="badge ${badgeClass}">${a.status.toUpperCase()}</span></td>
        <td>${actionBtn}</td>
      </tr>
    `;
  }).join("");
}

function updateKpiAgents(agents) {
  const active = agents.filter(a => a.status !== "idle").length;
  document.getElementById("kpiAgents").textContent = agents.length;
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

/** Remove an agent from the registry */
async function deregisterAgent(agentId, agentName) {
  if (!confirm(`Remove "${agentName}" from the registry?`)) return;
  try {
    await apiDelete(`/api/agents/${agentId}`);
    await loadAgents();
  } catch (err) {
    alert("Failed to remove agent: " + err.message);
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

// Load on page ready, refresh every 10 seconds
loadAgents();
setInterval(loadAgents, 10000);
