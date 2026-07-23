/**
 * keywords.js — Sensitive Term Library UI
 *
 * Loads, renders, adds, and removes sensitive terms.
 * Auto-refreshes every 30 seconds alongside the rest of the dashboard.
 */

// ── Protected PII terms — locked in UI, cannot be removed ────────────────────

const _LOCKED_TERMS = new Set([
  "ssn", "social security", "social security number",
  "credit card", "card number", "cvv",
  "routing number", "bank account", "passport number", "date of birth",
]);

function _isLockedTerm(t) {
  return (t.category === "hipaa" || t.category === "pii" || t.category === "financial") &&
         t.action === "block" &&
         _LOCKED_TERMS.has(t.term.toLowerCase());
}

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

    tbody.innerHTML = filtered.map(t => {
      const safeTerm = JSON.stringify(t.term);
      return `
      <tr>
        <td class="kw-term-cell"><code class="kw-term">${t.term}</code></td>
        <td>${categoryBadge(t.category)}</td>
        <td>${actionBadge(t.action)}</td>
        <td class="kw-scope">${t.department || "Global"}</td>
        <td>${_isLockedTerm(t)
          ? `<span style="font-size:11px;color:var(--text-muted)" title="Core PII compliance term — cannot be removed">🔒 Protected</span>`
          : `<button class="btn-deregister" onclick="removeKeyword(${t.id}, ${safeTerm})">Remove</button>`
        }</td>
      </tr>
    `}).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="placeholder">Error rendering terms.</td></tr>`;
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
  if (!confirm(`Remove the sensitive term "${term}"? This policy change takes effect immediately.`)) return;
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
