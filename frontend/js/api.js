/**
 * api.js — Shared fetch wrapper for all FAGE backend calls.
 *
 * All frontend modules import from this file so the backend URL
 * is configured in exactly one place.
 */

// Empty string = same origin (frontend is now served by FastAPI)
const FAGE_API = "";

/**
 * GET a JSON endpoint from the FAGE backend.
 * @param {string} path - e.g. "/health" or "/api/budget"
 * @returns {Promise<any>} parsed JSON response
 */
async function apiGet(path) {
  const response = await fetch(`${FAGE_API}${path}`);
  if (!response.ok) throw new Error(`GET ${path} failed: ${response.status}`);
  return response.json();
}

/**
 * POST JSON data to a FAGE backend endpoint.
 * @param {string} path  - e.g. "/api/prune"
 * @param {object} body  - data to send as JSON
 * @returns {Promise<any>} parsed JSON response
 */
async function apiPost(path, body) {
  const response = await fetch(`${FAGE_API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`POST ${path} failed: ${response.status}`);
  return response.json();
}

/**
 * DELETE a FAGE backend resource.
 * @param {string} path - e.g. "/api/agents/7"
 * @returns {Promise<any>} parsed JSON response
 */
async function apiDelete(path) {
  const response = await fetch(`${FAGE_API}${path}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`DELETE ${path} failed: ${response.status}`);
  return response.json();
}
