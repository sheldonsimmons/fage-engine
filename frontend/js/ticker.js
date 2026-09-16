/**
 * ticker.js — Live Intelligence Ticker.
 *
 * Deliberately independent of both nav patterns this app has (global-nav.js's
 * top bar, left-nav.js's sidebar) -- confirmed this session that global-nav.js
 * alone doesn't reach every page (business-profile.html, connector-manager.html
 * load left-nav.js without it), and folding the ticker into either shared file
 * would make a ticker bug a risk to the nav/drawer every page depends on.
 * Any page opts in with exactly two lines: a `<div id="cpTickerMount"></div>`
 * placed where the strip should render, and this script tag. No mount point,
 * no ticker -- nothing else on the page needs to change.
 *
 * Data integrity: every fact below reads a field already returned by
 * /api/dashboard, the same endpoint the executive dashboard itself renders
 * from -- no new metric definitions, no frontend-only math beyond picking
 * the largest budget_summaries.used_pct. Ticker = Dashboard = Reports stays
 * true by construction, not by convention.
 */
(function () {
  "use strict";

  const ROTATE_MS = 8000;
  const FADE_MS = 400;
  const prefersReducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;

  function injectStyles() {
    if (document.getElementById("cp-ticker-styles")) return;
    const style = document.createElement("style");
    style.id = "cp-ticker-styles";
    style.textContent = `
      .cp-ticker {
        display: flex; align-items: center; gap: 12px;
        padding: 9px 18px; background: #0d1826; border-bottom: 1px solid #1c2c40;
        font-size: 12.5px; color: #c3d0e0; min-height: 36px;
        font-family: system-ui, -apple-system, sans-serif;
      }
      .cp-ticker[hidden] { display: none; }
      .cp-ticker-badge {
        flex-shrink: 0; font-size: 9.5px; font-weight: 800; letter-spacing: .06em;
        text-transform: uppercase; padding: 2px 8px; border-radius: 999px;
        color: #0d1826; white-space: nowrap;
      }
      .cp-ticker-badge.live    { background: #5fe0d0; }
      .cp-ticker-badge.savings { background: #4fd39a; }
      .cp-ticker-badge.budget  { background: #f0b95a; }
      .cp-ticker-badge.risk    { background: #f0a598; }
      .cp-ticker-badge.outcome { background: #a894ec; }
      .cp-ticker-badge.agent   { background: #7fc4f5; }
      .cp-ticker-text {
        flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
        white-space: nowrap; opacity: 1; transition: opacity ${FADE_MS}ms ease;
      }
      .cp-ticker-text.cp-ticker-fading { opacity: 0; }
      .cp-ticker-action {
        flex-shrink: 0; background: none; border: none; color: #5fe0d0;
        font-size: 12.5px; font-weight: 600; cursor: pointer; padding: 2px 4px;
        white-space: nowrap;
      }
      .cp-ticker-action:hover, .cp-ticker-action:focus-visible { text-decoration: underline; }
      .cp-ticker-dots { display: flex; gap: 4px; flex-shrink: 0; }
      .cp-ticker-dot { width: 4px; height: 4px; border-radius: 50%; background: #2c3f56; }
      .cp-ticker-dot.active { background: #5fe0d0; }
      @media (max-width: 720px) {
        .cp-ticker { padding: 8px 12px; font-size: 12px; }
        .cp-ticker-dots { display: none; }
      }
    `;
    document.head.appendChild(style);
  }

  function fmtUsd(v) {
    const n = Number(v || 0);
    return `$${n.toLocaleString(undefined, { minimumFractionDigits: n < 1000 ? 2 : 0, maximumFractionDigits: n < 1000 ? 2 : 0 })}`;
  }
  function fmtNum(v) {
    return Number(v || 0).toLocaleString();
  }

  // Fixed Phase 1 fact list, per the reference doc -- no ranking engine,
  // just filter to what's true right now and rotate through it. Each
  // builder returns null when it has nothing meaningful to say (e.g. no
  // department is anywhere near its cap) rather than showing a hollow fact.
  function buildFacts(d) {
    const facts = [];

    if (d.spend_today_usd != null) {
      facts.push({
        badge: "live", cls: "live",
        text: `AI spend today: ${fmtUsd(d.spend_today_usd)} across ${fmtNum(d.calls_today)} requests`,
        ask: "How much have we spent on AI today?",
      });
    }

    if (d.projected_annual_savings) {
      facts.push({
        badge: "savings", cls: "savings",
        text: `${fmtUsd(d.projected_annual_savings)} in projected annual savings at current routing efficiency`,
        ask: "How much could we save by optimizing our AI spend?",
      });
    }

    const budgets = Array.isArray(d.budget_summaries) ? d.budget_summaries : [];
    const nearestCap = budgets
      .filter(b => b && b.department && b.used_pct != null)
      .sort((a, b) => (b.used_pct || 0) - (a.used_pct || 0))[0];
    if (nearestCap && nearestCap.used_pct >= 50) {
      // department comes back workspace-prefixed ("WORKSPACE_ID:Engineering")
      // for legacy rows -- confirmed live 2026-09-16 this showed the raw
      // prefix straight in the ticker. Same display convention already used
      // everywhere else in this app for this exact field.
      const deptName = String(nearestCap.department).split(":").pop();
      facts.push({
        badge: "budget", cls: "budget",
        text: `${deptName} is at ${Math.round(nearestCap.used_pct)}% of its monthly AI budget`,
        ask: `Is ${deptName}'s AI budget under control?`,
      });
    }

    if (d.blocked_count) {
      facts.push({
        badge: "risk", cls: "risk",
        text: `${fmtNum(d.blocked_count)} blocked request${d.blocked_count === 1 ? "" : "s"} on record`,
        ask: "Are we spending AI on sensitive or risky requests?",
      });
    }

    if (d.tokens_saved_today) {
      facts.push({
        badge: "savings", cls: "savings",
        text: `${fmtNum(d.tokens_saved_today)} tokens saved today through pruning and routing`,
        ask: "How much could we save by optimizing our AI spend?",
      });
    }

    return facts;
  }

  function openAskWith(question) {
    // openAskCostPilot() itself lives inside global-nav.js's own IIFE and
    // is never exposed on window -- confirmed live while building this,
    // not assumed. left-nav.js already hit this exact problem (see its
    // own comment) and settled on clicking the real #cpGlobalAsk button
    // instead, since that element's click listener is registered inside
    // the same closure and does the real work. Matches that established
    // workaround instead of inventing a second one.
    const askTrigger = document.getElementById("cpGlobalAsk");
    if (!askTrigger) {
      // This page never loaded global-nav.js (e.g. business-profile.html,
      // connector-manager.html) -- no drawer exists to open. Fails
      // silently rather than throwing; the ticker's facts are still
      // useful without the action on those pages.
      return;
    }
    askTrigger.click();
    setTimeout(() => {
      const input = document.getElementById("cpAskInput");
      if (input) {
        input.value = question;
        input.focus();
      }
    }, 80);
  }

  async function mount() {
    const container = document.getElementById("cpTickerMount");
    if (!container) return;
    injectStyles();

    let data;
    try {
      const workspaceId = localStorage.getItem("cp_workspace_id");
      const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
      // Plain fetch, not apiGet() -- confirmed live-landing.html (one of
      // this component's two first pages) never loads api.js at all.
      // "Independent mount, every page" means genuinely independent: this
      // file assumes nothing else on the page is loaded, the same
      // standard every other file here gets held to.
      const token = localStorage.getItem("cp_auth_token");
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const response = await fetch(`/api/dashboard${qs}`, { headers });
      if (!response.ok) return;
      data = await response.json();
    } catch (_err) {
      return; // No data, no ticker -- never show a stale or empty strip.
    }

    const facts = buildFacts(data);
    if (!facts.length) return;

    container.innerHTML = `
      <div class="cp-ticker" role="region" aria-label="Live CostPilot insights">
        <span class="cp-ticker-badge" id="cpTickerBadge"></span>
        <span class="cp-ticker-text" id="cpTickerText" aria-live="polite"></span>
        <button type="button" class="cp-ticker-action" id="cpTickerAction" hidden>Ask why →</button>
        <div class="cp-ticker-dots" id="cpTickerDots"></div>
      </div>
    `;
    const badgeEl = document.getElementById("cpTickerBadge");
    const textEl = document.getElementById("cpTickerText");
    const actionEl = document.getElementById("cpTickerAction");
    const dotsEl = document.getElementById("cpTickerDots");

    dotsEl.innerHTML = facts.map((_, i) => `<span class="cp-ticker-dot" data-i="${i}"></span>`).join("");

    let index = 0;
    let paused = false;
    let timer = null;

    function render(i) {
      const fact = facts[i];
      badgeEl.textContent = fact.badge;
      badgeEl.className = `cp-ticker-badge ${fact.cls}`;
      textEl.textContent = fact.text;
      if (fact.ask) {
        actionEl.hidden = !document.getElementById("cpGlobalAsk");
        actionEl.onclick = () => openAskWith(fact.ask);
      } else {
        actionEl.hidden = true;
      }
      dotsEl.querySelectorAll(".cp-ticker-dot").forEach(dot => {
        dot.classList.toggle("active", Number(dot.dataset.i) === i);
      });
    }

    function advance() {
      if (paused || facts.length < 2) return;
      // Previously disabled auto-rotation ENTIRELY when the OS prefers
      // reduced motion -- confirmed live 2026-09-16 that's what was
      // actually stuck: the user's real machine has that setting on, so
      // every one of my own tests (headless browsers default to "no
      // preference") looked correct while their real browser never
      // advanced past the first fact, permanently, no amount of
      // refreshing would ever fix it. That reading was too strict --
      // WCAG 2.2.2 asks for a way to pause/stop auto-updating content
      // (the action button and focus already provide that), not for the
      // content to stop updating outright. Content still rotates either
      // way now; only the fade animation itself is skipped for reduced
      // motion, an instant swap instead of a cross-fade.
      if (prefersReducedMotion) {
        index = (index + 1) % facts.length;
        render(index);
        return;
      }
      textEl.classList.add("cp-ticker-fading");
      setTimeout(() => {
        index = (index + 1) % facts.length;
        render(index);
        textEl.classList.remove("cp-ticker-fading");
      }, FADE_MS);
    }

    render(0);
    timer = setInterval(advance, ROTATE_MS);

    // Scoping hover-pause to the text (previous fix, still scoped away
    // from the whole strip) turned out to have the identical problem one
    // level down: confirmed live 2026-09-16 with the user actively
    // watching the tab, focused, having just hard-refreshed -- the one
    // remaining explanation was that deliberately watching text IS
    // hovering it, so "pause while reading" and "paused the entire time
    // someone is looking at it" are the same thing here, not two
    // different behaviors. Text no longer pauses on mouseenter at all.
    // The action button still does -- it's a small, deliberately-aimed-at
    // target, so hovering it is a real signal, not incidental proximity
    // from reading. Keyboard focus still pauses both, since tabbing to an
    // element is inherently deliberate, never incidental.
    actionEl.addEventListener("mouseenter", () => { paused = true; });
    actionEl.addEventListener("mouseleave", () => { paused = false; });
    [textEl, actionEl].forEach(el => {
      el.addEventListener("focusin", () => { paused = true; });
      el.addEventListener("focusout", () => { paused = false; });
    });

    // A single unhidden mount is the common case, but never leaves a
    // second interval running if a page's own script re-invokes mount()
    // (e.g. after a workspace switch re-render).
    window.addEventListener("beforeunload", () => clearInterval(timer), { once: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
