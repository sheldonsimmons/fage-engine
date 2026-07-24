/**
 * keywords.js — Sensitive Term Library UI
 *
 * Loads, renders, adds, and removes sensitive terms.
 * Auto-refreshes every 30 seconds alongside the rest of the dashboard.
 */

// ── Load & render ─────────────────────────────────────────────────────────────

async function loadKeywords() {
  const tbody = document.getElementById("kwTableBody");
  try {
    const terms = await apiGet("/api/keywords");
    window.POLICY_SENSITIVE_TERMS = terms || [];
    renderKeywordTable(terms || []);
    if (typeof updatePolicyOverview === "function") updatePolicyOverview();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="placeholder">Error loading terms.</td></tr>`;
  }
}

function renderKeywordTable(terms = window.POLICY_SENSITIVE_TERMS || []) {
  const tbody = document.getElementById("kwTableBody");
  const search = (document.getElementById("policyTermSearch")?.value || "").trim().toLowerCase();
  const category = document.getElementById("policyCategoryFilter")?.value || "";
  const action = document.getElementById("policyActionFilter")?.value || "";
  const filtered = terms.filter(term => {
    if (search && !String(term.term || "").toLowerCase().includes(search)) return false;
    if (category && term.category !== category) return false;
    if (action && term.action !== action) return false;
    return true;
  });

  try {
    if (!filtered.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="placeholder">No terms yet. Add one above.</td></tr>`;
      return;
    }

    const categoryBadge = (cat) => {
      const labels = {
        legal: "⚖ Legal", hipaa: "🏥 HIPAA", financial: "💰 Financial",
        hr: "👤 HR", pii: "◆ PII", code: "⌘ Code", custom: "★ Custom"
      };
      return `<span class="kw-cat">${labels[cat] || cat}</span>`;
    };

    tbody.innerHTML = filtered.map(t => {
      const safeTerm = JSON.stringify(t.term);
      return `
      <tr class="${t.enabled ? "" : "kw-disabled-row"}">
        <td class="kw-term-cell">
          <code class="kw-term">${keywordEscape(t.term)}</code>
          ${t.is_recommended ? '<span class="kw-recommended">Recommended default</span>' : ""}
        </td>
        <td>${categoryBadge(t.category)}</td>
        <td><select class="kw-action-select" onchange="updateKeywordAction(${t.id}, this.value)">
          <option value="flag"${t.action === "flag" ? " selected" : ""}>Flag</option>
          <option value="escalate"${t.action === "escalate" ? " selected" : ""}>Escalate</option>
          <option value="block"${t.action === "block" ? " selected" : ""}>Block</option>
        </select></td>
        <td class="kw-scope">${keywordEscape(t.department || "Global")}</td>
        <td><button class="kw-toggle ${t.enabled ? "enabled" : ""}" type="button"
          onclick="toggleKeyword(${t.id}, ${!t.enabled}, ${safeTerm}, ${Boolean(t.is_recommended)})"
          aria-pressed="${Boolean(t.enabled)}">${t.enabled ? "Enabled" : "Disabled"}</button></td>
        <td><button class="btn-deregister" onclick="removeKeyword(${t.id}, ${safeTerm}, ${Boolean(t.is_recommended)})">Delete</button></td>
      </tr>
    `}).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="placeholder">Error rendering terms.</td></tr>`;
  }
}

function keywordEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

async function toggleKeyword(id, enabled, term, recommended) {
  const status = document.getElementById("kwStatus");
  if (!enabled && recommended && !confirm(
    `Disable the recommended term "${term}"?\n\nThe phrase will stop triggering this policy. Automatic PII pattern detectors are configured separately.`
  )) return;
  try {
    await apiPatch(`/api/keywords/${id}`, { enabled });
    status.textContent = `✓ "${term}" ${enabled ? "enabled" : "disabled"}.`;
    status.style.color = "var(--accent-green)";
    await loadKeywords();
  } catch (error) {
    status.textContent = "Error updating term: " + error.message;
    status.style.color = "var(--accent-red)";
  }
}

async function updateKeywordAction(id, action) {
  const status = document.getElementById("kwStatus");
  try {
    await apiPatch(`/api/keywords/${id}`, { action });
    status.textContent = `✓ Enforcement action changed to ${action}.`;
    status.style.color = "var(--accent-green)";
    await loadKeywords();
  } catch (error) {
    status.textContent = "Error updating action: " + error.message;
    status.style.color = "var(--accent-red)";
  }
}

async function restoreRecommendedKeywords() {
  if (!confirm("Restore and enable CostPilot's recommended sensitive-term defaults? Your custom terms will remain unchanged.")) return;
  const status = document.getElementById("kwStatus");
  try {
    await apiPost("/api/keywords/restore-defaults", {});
    status.textContent = "✓ Recommended defaults restored.";
    status.style.color = "var(--accent-green)";
    await loadKeywords();
  } catch (error) {
    status.textContent = "Error restoring defaults: " + error.message;
    status.style.color = "var(--accent-red)";
  }
}

// ── Add term ──────────────────────────────────────────────────────────────────

async function addKeyword() {
  const term     = document.getElementById("kwTerm").value.trim();
  const category = document.getElementById("kwCategory").value;
  const action   = document.getElementById("kwAction").value;
  const status   = document.getElementById("kwStatus");

  if (!term) {
    status.textContent = "Please enter a term or phrase.";
    status.style.color = "var(--accent-red)";
    return;
  }

  status.textContent = "Adding...";
  status.style.color = "var(--text-muted)";

  try {
    await apiPost("/api/keywords", { term, category, action });
    document.getElementById("kwTerm").value = "";
    status.textContent = `✓ "${term}" added.`;
    status.style.color = "var(--accent-green)";
    await loadKeywords();
    setTimeout(() => status.textContent = "", 3000);
  } catch (e) {
    if (e.message.includes("409")) {
      status.textContent = `"${term}" already exists in the library.`;
    } else {
      status.textContent = "Error: " + e.message;
    }
    status.style.color = "var(--accent-red)";
  }
}

// ── Remove term ───────────────────────────────────────────────────────────────

async function removeKeyword(id, term, recommended = false) {
  const status = document.getElementById("kwStatus");
  const warning = recommended
    ? `Delete the recommended term "${term}"?\n\nIt will not return automatically. You can restore recommended defaults later.`
    : `Delete the sensitive term "${term}"? This policy change takes effect immediately.`;
  if (!confirm(warning)) return;
  try {
    await apiDelete(`/api/keywords/${id}`);
    status.textContent = `✓ "${term}" removed.`;
    status.style.color = "var(--accent-green)";
    await loadKeywords();
    setTimeout(() => status.textContent = "", 3000);
  } catch (e) {
    status.textContent = "Error removing term: " + e.message;
    status.style.color = "var(--accent-red)";
  }
}

// ── Allow Enter key in the term input ─────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("kwTerm");
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") addKeyword();
    });
  }
  loadKeywords();
});
