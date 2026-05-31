/**
 * tier-utils.js — Centralised tier name resolution
 *
 * Loads custom tier names from /api/routing-config on every page boot.
 * All JS files use getTierName(tier) instead of hardcoded strings so
 * admins can rename tiers without any code changes.
 *
 * Must be included AFTER api.js, BEFORE page-specific scripts.
 */

// ── Defaults (shown until API responds, and as fallback) ──────────────────────
const _TIER_DEFAULTS = { 1: "Scout", 2: "Analyst", 3: "Advisor", 4: "Strategist" };
const _TIER_ICONS    = { 1: "⚡",    2: "🔍",      3: "💡",      4: "🎯" };
const _TIER_COLORS   = { 1: "#3fb950", 2: "#58a6ff", 3: "#d29922", 4: "#f85149" };
const _TIER_CLASSES  = { 1: "badge-scout", 2: "badge-analyst", 3: "badge-advisor", 4: "badge-strategist" };
const _TIER_CSS_VARS = { 1: "var(--tier-scout)", 2: "var(--tier-analyst)", 3: "var(--tier-advisor)", 4: "var(--tier-strategist)" };

// Live names — starts as defaults, overwritten by loadTierNames()
window.TIER_NAMES = { ...Object.fromEntries(Object.entries(_TIER_DEFAULTS).map(([k,v])=>[parseInt(k),v])) };

// ── Public helpers ────────────────────────────────────────────────────────────

/** Return the display name for a tier integer (1–4). */
window.getTierName = function(tier) {
  const t = parseInt(tier);
  return (window.TIER_NAMES[t]) || _TIER_DEFAULTS[t] || `Tier ${t}`;
};

/** Return the CSS badge class for a tier integer (1–4). */
window.getTierBadgeClass = function(tier) {
  return _TIER_CLASSES[parseInt(tier)] || "badge-scout";
};

/**
 * Return the CSS badge class for a tier name string.
 * Handles legacy "micro"/"flagship" and current/custom names.
 */
window.getTierBadgeClassByName = function(name) {
  if (!name) return "badge-scout";
  const n = name.toLowerCase().trim();
  if (n === "micro")    return "badge-scout";
  if (n === "flagship") return "badge-advisor";
  // Match against current custom names (case-insensitive)
  for (const [tier, label] of Object.entries(window.TIER_NAMES)) {
    if (label.toLowerCase() === n) return _TIER_CLASSES[parseInt(tier)] || "badge-scout";
  }
  // Fallback: match against built-in defaults
  for (const [tier, label] of Object.entries(_TIER_DEFAULTS)) {
    if (label.toLowerCase() === n) return _TIER_CLASSES[parseInt(tier)] || "badge-scout";
  }
  return "badge-scout";
};

/** Return the emoji icon for a tier integer. */
window.getTierIcon = function(tier) {
  return _TIER_ICONS[parseInt(tier)] || "◈";
};

/** Return the hex color for a tier integer. */
window.getTierColor = function(tier) {
  return _TIER_COLORS[parseInt(tier)] || "#58a6ff";
};

/** Return the CSS variable string for a tier integer. */
window.getTierCssVar = function(tier) {
  return _TIER_CSS_VARS[parseInt(tier)] || "var(--tier-scout)";
};

/**
 * Return the fixed lowercase CSS key for a tier (e.g. "scout", "analyst").
 * Accepts tier integer OR any display name (current custom or default).
 * Used as a CSS class suffix and color lookup key.
 */
const _TIER_CSS_KEYS = { 1: "scout", 2: "analyst", 3: "advisor", 4: "strategist" };
window.getTierCssKey = function(tierOrName) {
  const asInt = parseInt(tierOrName);
  if (!isNaN(asInt) && _TIER_CSS_KEYS[asInt]) return _TIER_CSS_KEYS[asInt];
  const n = String(tierOrName).toLowerCase().trim();
  if (n === "micro")    return "scout";
  if (n === "flagship") return "advisor";
  for (const [t, label] of Object.entries(window.TIER_NAMES || {})) {
    if (label.toLowerCase() === n) return _TIER_CSS_KEYS[parseInt(t)] || "scout";
  }
  for (const [t, label] of Object.entries(_TIER_DEFAULTS)) {
    if (label.toLowerCase() === n) return _TIER_CSS_KEYS[parseInt(t)] || "scout";
  }
  return "scout";
};

/**
 * Resolve a tier name string (including legacy) to its display name.
 * e.g. "micro" → current Tier 1 name, "flagship" → current Tier 3 name
 */
window.normaliseTierName = function(name) {
  if (!name) return "—";
  const n = name.toLowerCase().trim();
  if (n === "micro")    return getTierName(1);
  if (n === "flagship") return getTierName(3);
  // If it already matches a current name, return it as-is
  for (const label of Object.values(window.TIER_NAMES)) {
    if (label.toLowerCase() === n) return label;
  }
  return name; // unknown — return as-is
};

// ── Loader ────────────────────────────────────────────────────────────────────

/**
 * Fetch tier names from the API and update window.TIER_NAMES.
 * Returns a promise that resolves when done (or on error — never rejects).
 */
window.loadTierNames = async function() {
  try {
    // Works whether api.js (apiGet) is loaded or not
    const config = typeof apiGet === "function"
      ? await apiGet("/api/routing-config")
      : await fetch("/api/routing-config").then(r => r.json());
    if (config && config.tier_names) {
      window.TIER_NAMES = Object.fromEntries(
        Object.entries(config.tier_names).map(([k, v]) => [parseInt(k), v])
      );
    }
  } catch (e) {
    // silently use defaults
  }
};
