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
    if (!terms || terms.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" class="placeholder">No terms yet. Add one above.</td></tr>`;
      return;
    }

    const actionBadge = (action) => {
      const cls = { flag: "kw-badge-flag", escalate: "kw-badge-escalate", block: "kw-badge-block" }[action] || "";
      const label = { flag: "Flag", escalate: "Escalate", block: "Block" }[action] || action;
      return `<span class="kw-badge ${cls}">${label}</span>`;
    };

    const categoryBadge = (cat) => {
      const labels = { legal: "⚖ Legal", hipaa: "🏥 HIPAA", financial: "💰 Financial", hr: "👤 HR", custom: "★ Custom" };
      return `<span class="kw-cat">${labels[cat] || cat}</span>`;
    };

    tbody.innerHTML = terms.map(t => `
      <tr>
        <td class="kw-term-cell"><code class="kw-term">${t.term}</code></td>
        <td>${categoryBadge(t.category)}</td>
        <td>${actionBadge(t.action)}</td>
        <td class="kw-scope">${t.department || "Global"}</td>
        <td><button class="btn-deregister" onclick="removeKeyword(${t.id}, '${t.term}')">Remove</button></td>
      </tr>
    `).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="placeholder">Error loading terms.</td></tr>`;
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

async function removeKeyword(id, term) {
  const status = document.getElementById("kwStatus");
  try {
    await apiDelete(`/api/keywords/${id}`);
    status.textContent = `✓ "${term}" removed.`;
    status.style.color = "var(--accent-green)";
    await loadKeywords();
    setTimeout(() => status.textContent = "", 3000);
  } catch (e) {
    status.textContent = "Error removing term.";
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
