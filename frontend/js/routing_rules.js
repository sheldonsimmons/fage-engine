/**
 * routing_rules.js — Routing Rules configuration panel
 *
 * GET    /api/routing-config                    — load threshold + keywords
 * PATCH  /api/routing-config/threshold          — save threshold
 * POST   /api/routing-config/keywords           — add keyword
 * DELETE /api/routing-config/keywords/{keyword} — remove keyword
 */

const _RR_PROTECTED = new Set(["fraud", "breach", "gdpr", "hipaa"]);

// ── Load & render ─────────────────────────────────────────────────────────────

async function loadRoutingRules() {
  try {
    const cfg = await apiGet("/api/routing-config");
    window.POLICY_ROUTING_CONFIG = cfg;
    _rrRenderSlider(cfg);
    _rrRenderKeywords(cfg);
    if (typeof updatePolicyOverview === "function") updatePolicyOverview();
  } catch (e) {
    console.warn("Routing config load failed:", e.message);
  }
}

function _rrRenderSlider(cfg) {
  const slider  = document.getElementById("rrThresholdSlider");
  const valueEl = document.getElementById("rrThresholdValue");
  if (!slider || !valueEl) return;
  slider.value        = cfg.complexity_token_threshold;
  valueEl.textContent = cfg.complexity_token_threshold;
}

function _rrRenderKeywords(cfg) {
  const container = document.getElementById("rrKeywordList");
  if (!container) return;

  const serverProtected = new Set(cfg.protected_keywords || []);
  const allProtected    = new Set([..._RR_PROTECTED, ...serverProtected]);
  const kws             = cfg.complexity_keywords || [];

  if (kws.length === 0) {
    container.innerHTML = `<span style="color:var(--text-muted);font-size:12px">No keywords configured.</span>`;
    return;
  }

  container.innerHTML = kws.map(kw => {
    const locked = allProtected.has(kw);
    return `<span class="rr-kw-chip${locked ? " rr-kw-locked" : ""}">
      ${locked ? "🔒 " : ""}<code>${kw}</code>
      ${locked ? "" : `<button class="rr-kw-remove" onclick="removeRoutingKeyword('${kw.replace(/'/g, "\\'")}')">✕</button>`}
    </span>`;
  }).join("");
}

// ── Threshold ─────────────────────────────────────────────────────────────────

async function saveThreshold() {
  const slider = document.getElementById("rrThresholdSlider");
  const status = document.getElementById("rrThresholdStatus");
  const val    = parseInt(slider.value, 10);
  status.textContent = "Saving...";
  status.style.color = "var(--text-muted)";
  try {
    const cfg = await apiPatch("/api/routing-config/threshold", { threshold: val });
    window.POLICY_ROUTING_CONFIG = cfg;
    status.textContent = `✓ Saved — payloads over ${val} tokens will escalate (with a keyword match)`;
    status.style.color = "var(--accent-green)";
    if (typeof updatePolicyOverview === "function") updatePolicyOverview();
    setTimeout(() => status.textContent = "", 4000);
  } catch (e) {
    status.textContent = "Error: " + e.message;
    status.style.color = "var(--accent-red)";
  }
}

// ── Add keyword ───────────────────────────────────────────────────────────────

async function addRoutingKeyword() {
  const input  = document.getElementById("rrNewKeyword");
  const status = document.getElementById("rrKeywordStatus");
  const kw     = input.value.trim().toLowerCase();
  if (!kw) {
    status.textContent = "Enter a keyword first.";
    status.style.color = "var(--accent-red)";
    return;
  }
  status.textContent = "Adding...";
  status.style.color = "var(--text-muted)";
  try {
    const cfg = await apiPost("/api/routing-config/keywords", { keyword: kw });
    window.POLICY_ROUTING_CONFIG = cfg;
    input.value        = "";
    status.textContent = `✓ "${kw}" added.`;
    status.style.color = "var(--accent-green)";
    _rrRenderKeywords(cfg);
    if (typeof updatePolicyOverview === "function") updatePolicyOverview();
    setTimeout(() => status.textContent = "", 3000);
  } catch (e) {
    status.textContent = e.message.includes("409") ? `"${kw}" already exists.` : "Error: " + e.message;
    status.style.color = "var(--accent-red)";
  }
}

// ── Remove keyword ────────────────────────────────────────────────────────────

async function removeRoutingKeyword(kw) {
  const status = document.getElementById("rrKeywordStatus");
  try {
    const cfg = await apiDelete(`/api/routing-config/keywords/${encodeURIComponent(kw)}`);
    window.POLICY_ROUTING_CONFIG = cfg;
    status.textContent = `✓ "${kw}" removed.`;
    status.style.color = "var(--accent-green)";
    _rrRenderKeywords(cfg);
    if (typeof updatePolicyOverview === "function") updatePolicyOverview();
    setTimeout(() => status.textContent = "", 3000);
  } catch (e) {
    status.textContent = e.message.includes("403")
      ? `"${kw}" is a protected keyword and cannot be removed.`
      : "Error: " + e.message;
    status.style.color = "var(--accent-red)";
  }
}

// ── Boot ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  const slider = document.getElementById("rrThresholdSlider");
  if (slider) {
    slider.addEventListener("input", () => {
      document.getElementById("rrThresholdValue").textContent = slider.value;
    });
  }
  const kwInput = document.getElementById("rrNewKeyword");
  if (kwInput) {
    kwInput.addEventListener("keydown", e => { if (e.key === "Enter") addRoutingKeyword(); });
  }
  loadRoutingRules();
});
