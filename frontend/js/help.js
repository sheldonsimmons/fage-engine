/**
 * help.js — Contextual Help & Guided Tour  [Step 11]
 *
 * Two modes:
 *   1. Contextual tooltips — ⓘ icons on each panel, click for explanation
 *   2. Guided tour — step-by-step walkthrough of the full dashboard
 */

// ── Contextual tooltip content ────────────────────────────────────────────────
const HELP_CONTENT = {
  kpiSpend: {
    title: "Total Spend Today",
    body:  "The total cost of all AI model calls made today across every department. FAGE tracks every fraction of a cent — so leadership always knows exactly what AI is costing in real time.",
  },
  kpiTokensSaved: {
    title: "Tokens Saved (Pruning)",
    body:  "Before any text reaches an AI model, FAGE's Context Sweeper strips out junk — email signatures, repeated content, boilerplate noise. Fewer tokens in = lower cost on every call. This counter shows how many tokens have been eliminated.",
  },
  kpiAgents: {
    title: "Active Agents",
    body:  "Every AI digital worker connected to FAGE is tracked here. Agents auto-register on first contact — no manual setup needed. Status updates in real time: idle, active, locked, or queued.",
  },
  kpiThrottled: {
    title: "Throttled Departments",
    body:  "When a department hits its monthly AI budget cap, FAGE automatically throttles it — switching to a lighter model so work continues without blowing the budget. A supervisor must grant an override to restore full access.",
  },
  budgetPanel: {
    title: "Department Budgets",
    body:  "Each department gets its own monthly AI spend cap. The bar fills as spend accumulates. Green = healthy, yellow = approaching limit, red = at or over cap. Set the cap, reset the month, or grant an override — all from here.",
  },
  agentPanel: {
    title: "Agentlake Registry",
    body:  "Every AI agent connected to FAGE appears here automatically on first use. The Traffic Cop monitors all agents in real time — if two agents try to update the same record simultaneously, FAGE detects the collision and locks both until a supervisor resolves it. No data corruption, no silent overwrites.",
  },
  prunerPanel: {
    title: "Context-Pruning Sweeper",
    body:  "Paste any raw text — a support email, a log, a messy ticket — and FAGE strips it down to only the meaningful content before sending it to an AI model. The savings are shown instantly: tokens removed, cost avoided for both micro and flagship models.",
  },
  routerPanel: {
    title: "Token Router & Model Cascader",
    body:  "FAGE scores every payload for complexity and routes it to the right model tier. Routine requests go to a fast, cheap micro model. Complex or high-risk requests escalate to a flagship model. Throttled departments are automatically downgraded. Every routing decision is logged.",
  },
  keywordsPanel: {
    title: "Sensitive Term Library",
    body:  "Add any word or phrase your company considers sensitive — legal terms, HIPAA keywords, financial triggers, or custom terms. When FAGE detects a match in a payload, it can flag it in the audit log, escalate it to a flagship model for compliance review, or block the request entirely.",
  },
  voiceGuardPanel: {
    title: "Voice Guard — PII Redaction",
    body:  "Intercepts voice call transcripts before they reach any AI model and strips PII — Social Security numbers, credit cards, routing numbers, dates of birth, phone numbers, and bank accounts — even when a caller says them with hesitations, filler words, or interruptions. Powered by a dual-layer engine: trigger-phrase state machine + Presidio AI pattern recognition. The clean transcript is then passed safely to FAGE for routing.",
  },
  auditPanel: {
    title: "AI Decision Audit Log",
    body:  "Every high-stakes AI decision is written here once and never modified — an immutable black box. If your company faces a legal dispute, compliance review, or audit, you have a timestamped record of every AI decision: what was routed, what model was used, why, and what it cost.",
  },
};

// ── Tour steps — Main Dashboard ───────────────────────────────────────────────
const TOUR_STEPS = [
  {
    target:   "kpiSpendCard",
    title:    "Welcome to FAGE",
    body:     "Your real-time AI spend dashboard. Every dollar your AI agents spend is tracked here — by department, by day, by month. Let's take a quick tour.",
    position: "bottom",
  },
  {
    target:   "kpiTokensSavedCard",
    title:    "Instant Cost Savings",
    body:     "Before any text reaches an AI model, FAGE strips out noise — email signatures, reply chains, HTML, boilerplate. Fewer tokens in means lower cost on every call. Savings stack from the very first request.",
    position: "bottom",
  },
  {
    target:   "kpiAgentsCard",
    title:    "AI Agent Tracking",
    body:     "Every AI digital worker connected to FAGE appears here automatically. Agents self-register on first contact — no manual setup required. Status updates in real time: idle, active, locked, or queued.",
    position: "bottom",
  },
  {
    target:   "kpiThrottleCard",
    title:    "Budget Protection",
    body:     "When a department hits its monthly spend cap, FAGE auto-throttles it — switching all calls to a lighter model so work keeps running without blowing the budget. A supervisor override restores full access.",
    position: "bottom",
  },
  {
    target:   "kpiBlockedCard",
    title:    "Blocked Requests",
    body:     "Every request stopped before reaching an AI model is counted here. PII, sensitive terms, or policy violations — blocked at the gate, zero tokens consumed, full audit trail written.",
    position: "bottom",
  },
  {
    target:   "budgetsAgentsSection",
    title:    "Agentlake Registry & Department Budgets",
    body:     "Live agent status and budget utilization in one place. See every connected AI agent and their real-time status, plus how much of each department's monthly budget has been consumed.",
    position: "bottom",
  },
  {
    target:   "routingFeedSection",
    title:    "Routing Decision Feed",
    body:     "Every AI call scored and routed in real time. Routine requests go to Scout (fast, cheap). Complex or sensitive requests escalate to Advisor. Blocked events are flagged in red. Click any row to jump to its full audit entry.",
    position: "top",
  },
  {
    target:   "auditSection",
    title:    "AI Decision Audit Log",
    body:     "Every high-stakes AI decision is written here once and never modified — an immutable black box. Click the header to expand it. Export to CSV or PDF for compliance reviews and legal holds.",
    position: "top",
  },
  {
    target:   "eventStreamSection",
    title:    "Governance Event Stream",
    body:     "Live feed of every governance event across all departments — blocks, escalations, throttles, and routine calls. Filter by type or department. This is your real-time view of everything FAGE is doing right now.",
    position: "left",
    last:     true,
  },
];

let tourStep     = 0;
let tourOverlay  = null;
let tourBox      = null;
let tourActive   = false;
let _activeTourSteps = null; // set at startTour time — uses PAGE_TOUR_STEPS if defined

// ── Contextual tooltip ────────────────────────────────────────────────────────
function showHelp(panelId) {
  const info = HELP_CONTENT[panelId];
  if (!info) return;

  // Remove any existing tooltip
  const existing = document.getElementById("helpTooltip");
  if (existing) existing.remove();

  const icon = document.querySelector(`[data-help="${panelId}"]`);
  if (!icon) return;

  const tooltip = document.createElement("div");
  tooltip.id        = "helpTooltip";
  tooltip.className = "help-tooltip";
  tooltip.innerHTML = `
    <div class="help-tooltip-title">${info.title}</div>
    <div class="help-tooltip-body">${info.body}</div>
    <button class="help-tooltip-close" onclick="document.getElementById('helpTooltip').remove()">✕</button>
  `;
  document.body.appendChild(tooltip);

  // Position near the icon
  const rect = icon.getBoundingClientRect();
  const top  = rect.bottom + window.scrollY + 8;
  const left = Math.min(rect.left + window.scrollX, window.innerWidth - 320);
  tooltip.style.top  = top  + "px";
  tooltip.style.left = Math.max(left, 12) + "px";

  // Close on outside click
  setTimeout(() => {
    document.addEventListener("click", function handler(e) {
      if (!tooltip.contains(e.target) && e.target !== icon) {
        tooltip.remove();
        document.removeEventListener("click", handler);
      }
    });
  }, 100);
}

// ── Guided tour ───────────────────────────────────────────────────────────────
function startTour() {
  tourStep         = 0;
  tourActive       = true;
  _activeTourSteps = window.PAGE_TOUR_STEPS || TOUR_STEPS;

  // Overlay
  tourOverlay = document.createElement("div");
  tourOverlay.id        = "tourOverlay";
  tourOverlay.className = "tour-overlay";
  document.body.appendChild(tourOverlay);

  // Tour box
  tourBox = document.createElement("div");
  tourBox.id        = "tourBox";
  tourBox.className = "tour-box";
  document.body.appendChild(tourBox);

  renderTourStep();
}

function renderTourStep() {
  const steps  = _activeTourSteps || TOUR_STEPS;
  const step   = steps[tourStep];
  const target = document.getElementById(step.target);

  // Scroll target into view
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => positionTourBox(target, step), 350);
  }

  tourBox.innerHTML = `
    <div class="tour-step-count">Step ${tourStep + 1} of ${steps.length}</div>
    <div class="tour-title">${step.title}</div>
    <div class="tour-body">${step.body}</div>
    <div class="tour-actions">
      <button class="tour-btn-skip" onclick="endTour()">Skip Tour</button>
      <div style="display:flex; gap:8px">
        ${tourStep > 0 ? `<button class="tour-btn-prev" onclick="prevTourStep()">← Back</button>` : ""}
        ${step.last
          ? `<button class="tour-btn-next" onclick="endTour()">Done</button>`
          : `<button class="tour-btn-next" onclick="nextTourStep()">Next →</button>`
        }
      </div>
    </div>
  `;

  // Highlight target
  document.querySelectorAll(".tour-highlight").forEach(el => el.classList.remove("tour-highlight"));
  if (target) target.classList.add("tour-highlight");
}

function positionTourBox(target, step) {
  const rect   = target.getBoundingClientRect();
  const boxW   = 340;
  const boxH   = tourBox.offsetHeight || 180;
  const margin = 16;
  let top, left;

  if (step.position === "bottom") {
    top  = rect.bottom + window.scrollY + margin;
    left = rect.left   + window.scrollX + (rect.width / 2) - (boxW / 2);
  } else if (step.position === "top") {
    top  = rect.top  + window.scrollY - boxH - margin;
    left = rect.left + window.scrollX + (rect.width / 2) - (boxW / 2);
  } else if (step.position === "right") {
    top  = rect.top  + window.scrollY + (rect.height / 2) - (boxH / 2);
    left = rect.right + window.scrollX + margin;
  } else {
    top  = rect.top  + window.scrollY + (rect.height / 2) - (boxH / 2);
    left = rect.left + window.scrollX - boxW - margin;
  }

  // Keep within viewport
  left = Math.max(12, Math.min(left, window.innerWidth - boxW - 12));
  top  = Math.max(12 + window.scrollY, top);

  tourBox.style.top  = top  + "px";
  tourBox.style.left = left + "px";
}

function nextTourStep() {
  const steps = _activeTourSteps || TOUR_STEPS;
  if (tourStep < steps.length - 1) {
    tourStep++;
    renderTourStep();
  }
}

function prevTourStep() {
  if (tourStep > 0) {
    tourStep--;
    renderTourStep();
  }
}

function endTour() {
  tourActive = false;
  if (tourOverlay) { tourOverlay.remove(); tourOverlay = null; }
  if (tourBox)     { tourBox.remove();     tourBox     = null; }
  document.querySelectorAll(".tour-highlight").forEach(el => el.classList.remove("tour-highlight"));
  // Remember tour was completed
  try { localStorage.setItem("fage_tour_done", "1"); } catch(e) {}
}

// ── Auto-launch tour on first visit ──────────────────────────────────────────
window.addEventListener("load", () => {
  try {
    if (!localStorage.getItem("fage_tour_done")) {
      setTimeout(startTour, 1200);
    }
  } catch(e) {}
});
