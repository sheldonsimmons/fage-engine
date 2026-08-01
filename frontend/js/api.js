/**
 * api.js — Shared fetch wrapper for all CostPilot backend calls.
 *
 * All frontend modules import from this file so the backend URL
 * is configured in exactly one place.
 */

// Empty string = same origin (frontend is now served by FastAPI)
const CostPilot_API = "";

/**
 * GET a JSON endpoint from the CostPilot backend.
 * @param {string} path - e.g. "/health" or "/api/budget"
 * @returns {Promise<any>} parsed JSON response
 */
async function apiGet(path) {
  const response = await fetch(`${CostPilot_API}${path}`);
  if (!response.ok) throw new Error(`GET ${path} failed: ${response.status}`);
  return response.json();
}

/**
 * POST JSON data to a CostPilot backend endpoint.
 * @param {string} path  - e.g. "/api/prune"
 * @param {object} body  - data to send as JSON
 * @returns {Promise<any>} parsed JSON response
 */
async function apiPost(path, body) {
  const response = await fetch(`${CostPilot_API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const j = await response.json(); detail = j.detail || j.message || detail; } catch(_) {}
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

/**
 * PUT JSON data to a CostPilot backend endpoint.
 */
async function apiPut(path, body) {
  const response = await fetch(`${CostPilot_API}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`PUT ${path} failed: ${response.status}`);
  return response.json();
}

/**
 * PATCH a CostPilot backend endpoint.
 */
async function apiPatch(path, body) {
  const response = await fetch(`${CostPilot_API}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let detail = `PATCH ${path} failed: ${response.status}`;
    try { const payload = await response.json(); detail = payload.detail || payload.message || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

/**
 * DELETE a CostPilot backend resource.
 * @param {string} path - e.g. "/api/agents/7"
 * @returns {Promise<any>} parsed JSON response
 */
async function apiDelete(path) {
  const response = await fetch(`${CostPilot_API}${path}`, { method: "DELETE" });
  if (!response.ok) {
    let detail = `DELETE ${path} failed: ${response.status}`;
    try { const payload = await response.json(); detail = payload.detail || payload.message || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

// ── Export utilities (shared across all pages) ────────────────────────────────

/**
 * Download an array of objects as a CSV file.
 * @param {string}   filename  - e.g. "fage_audit.csv"
 * @param {string[]} headers   - column header labels
 * @param {Array}    rows      - array of arrays (each inner array = one row of values)
 */
function downloadCsv(filename, headers, rows) {
  const escape = v => {
    const s = (v === null || v === undefined) ? "" : String(v);
    return s.includes(",") || s.includes('"') || s.includes("\n")
      ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [headers.map(escape).join(",")]
    .concat(rows.map(r => r.map(escape).join(",")))
    .join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Print a section of the page to PDF via the browser's native print dialog.
 * @param {string} sectionId   - ID of the element to print
 * @param {string} title       - document title shown in print header
 */
function printSection(sectionId, title) {
  const el = document.getElementById(sectionId);
  if (!el) return;
  const prev = document.title;
  document.title = title || "CostPilot Export";
  // Clone element into a print-only overlay
  const overlay = document.createElement("div");
  overlay.id    = "printOverlay";
  overlay.innerHTML = el.outerHTML;
  document.body.appendChild(overlay);
  document.body.classList.add("printing");
  window.print();
  document.body.classList.remove("printing");
  overlay.remove();
  document.title = prev;
}
