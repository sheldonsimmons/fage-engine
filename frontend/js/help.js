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
    body:  "The total cost of all AI model calls made today across every department. CostPilot tracks every fraction of a cent — so leadership always knows exactly what AI is costing in real time.",
  },
  kpiTokensSaved: {
    title: "Tokens Saved (Pruning)",
    body:  "Before any text reaches an AI model, CostPilot's Context Sweeper strips out junk — email signatures, repeated content, boilerplate noise. Fewer tokens in = lower cost on every call. This counter shows how many tokens have been eliminated.",
  },
  kpiAgents: {
    title: "Active Agents",
    body:  "Every AI digital worker connected to CostPilot is tracked here. Agents auto-register on first contact — no manual setup needed. Status updates in real time: idle, active, locked, or queued.",
  },
  kpiThrottled: {
    title: "Throttled Departments",
    body:  "When a department hits its monthly AI budget cap, CostPilot automatically throttles it — switching to a lighter model so work continues without blowing the budget. A supervisor must grant an override to restore full access.",
  },
  budgetPanel: {
    title: "Department Budgets",
    body:  "Each department gets its own monthly AI spend cap. The bar fills as spend accumulates. Green = healthy, yellow = approaching limit, red = at or over cap. Set the cap, reset the month, or grant an override — all from here.",
  },
  deptHealthPanel: {
    title: "Department Health",
    body:  "This strip summarizes each department's current budget position. Green means healthy, yellow means getting close to the cap, and red means the department is over budget or being throttled. The percentage shows how much of that department's monthly AI budget has been used.",
  },
  tierSplitPanel: {
    title: "Tier Split Today",
    body:  "This shows how today's routed AI calls were divided across model tiers. Scout and Analyst are lower-cost tiers. Advisor and Strategist are stronger, higher-cost tiers. The split helps you see whether routine work is staying inexpensive and complex work is being escalated appropriately.",
  },
  agentPanel: {
    title: "Agentlake Registry",
    body:  "Every AI agent connected to CostPilot appears here automatically on first use. The Traffic Cop monitors all agents in real time — if two agents try to update the same record simultaneously, CostPilot detects the collision and locks both until a supervisor resolves it. No data corruption, no silent overwrites.",
  },
  agentEfficiencyPanel: {
    title: "Agent Efficiency Rank",
    body:  "This ranks agents by usage, cost, routing efficiency, and pruning performance. It helps identify which agents are driving spend, which ones are being routed to lower-cost tiers, and which ones are producing the most context-pruning savings.",
  },
  routingInsightsPanel: {
    title: "Routing Insights",
    body:  "This section explains why requests are becoming complex or high-risk. It highlights the keywords and compliance signals that caused CostPilot to escalate, flag, block, or route requests differently.",
  },
  trendsPanel: {
    title: "30-Day Spend & Activity Trends",
    body:  "These charts show AI spend and model-tier activity over the last 30 days. They help you see whether spend is rising, which departments are driving usage, and whether routing behavior is changing over time.",
  },
  eventStreamPanel: {
    title: "Governance Event Stream",
    body:  "This is the live operational view of CostPilot decisions. Repetitive routine activity is grouped to reduce noise, while blocks, throttles, collisions, and high-capability routing remain individually visible. Open an event to inspect its audit evidence.",
  },
  prunerPanel: {
    title: "Context-Pruning Sweeper",
    body:  "Paste any raw text — a support email, a log, a messy ticket — and CostPilot strips it down to only the meaningful content before sending it to an AI model. The savings are shown instantly: tokens removed, cost avoided for both micro and flagship models.",
  },
  routerPanel: {
    title: "Token Router & Model Cascader",
    body:  "CostPilot scores every payload for complexity and routes it to the right model tier. Routine requests go to a fast, cheap micro model. Complex or high-risk requests escalate to a flagship model. Throttled departments are automatically downgraded. Every routing decision is logged.",
  },
  keywordsPanel: {
    title: "Sensitive Term Library",
    body:  "Add any word or phrase your company considers sensitive — legal terms, HIPAA keywords, financial triggers, or custom terms. When CostPilot detects a match in a payload, it can flag it in the audit log, escalate it to a flagship model for compliance review, or block the request entirely.",
  },
  voiceGuardPanel: {
    title: "Voice Guard — PII Redaction",
    body:  "Intercepts voice call transcripts before they reach any AI model and strips PII — Social Security numbers, credit cards, routing numbers, dates of birth, phone numbers, and bank accounts — even when a caller says them with hesitations, filler words, or interruptions. Powered by a dual-layer engine: trigger-phrase state machine + Presidio AI pattern recognition. The clean transcript is then passed safely to CostPilot for routing.",
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
    title:    "Welcome to CostPilot",
    body:     "Your real-time AI spend dashboard. Every dollar your AI agents spend is tracked here — by department, by day, by month. Let's take a quick tour.",
    position: "bottom",
  },
  {
    target:   "kpiTokensSavedCard",
    title:    "Instant Cost Savings",
    body:     "Before any text reaches an AI model, CostPilot strips out noise — email signatures, reply chains, HTML, boilerplate. Fewer tokens in means lower cost on every call. Savings stack from the very first request.",
    position: "bottom",
  },
  {
    target:   "kpiAgentsCard",
    title:    "AI Agent Tracking",
    body:     "Every AI digital worker connected to CostPilot appears here automatically. Agents self-register on first contact — no manual setup required. Status updates in real time: idle, active, locked, or queued.",
    position: "bottom",
  },
  {
    target:   "kpiThrottleCard",
    title:    "Budget Protection",
    body:     "When a department hits its monthly spend cap, CostPilot auto-throttles it — switching all calls to a lighter model so work keeps running without blowing the budget. A supervisor override restores full access.",
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
    body:     "See what CostPilot is doing right now without reading the full Audit Log. Routine activity is summarized, important exceptions stay prominent, and every event opens to its recorded evidence.",
    position: "left",
    last:     true,
  },
];

let tourStep     = 0;
let tourOverlay  = null;
let tourBox      = null;
let tourActive   = false;
let _activeTourSteps = null; // set at startTour time — uses PAGE_TOUR_STEPS if defined

const TOUR_TARGET_ALIASES = {
  kpiSpendCard: "ceoSavingsBanner",
  kpiTokensSavedCard: "ceoSavingsBanner",
  kpiAgentsCard: "budgetsAgentsSection",
  kpiThrottleCard: "deptHealthStrip",
  kpiBlockedCard: "auditSection",
};

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
  if (tourActive) {
    endTour();
    return;
  }
  tourStep         = 0;
  tourActive       = true;
  _activeTourSteps = window.PAGE_TOUR_STEPS || TOUR_STEPS;
  setTourButtonState(true);

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

function setTourButtonState(active) {
  document.querySelectorAll(".tour-btn").forEach(btn => {
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
    btn.textContent = active ? "Exit Guide" : "? Guide";
  });
}

function isVisibleTarget(el) {
  if (!el) return false;
  const style = window.getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" && rect.width > 1 && rect.height > 1;
}

function resolveTourTarget(step) {
  if (!step) return null;
  const candidates = [
    step.selector ? document.querySelector(step.selector) : null,
    step.guideId ? document.querySelector(`[data-guide-id="${step.guideId}"]`) : null,
    step.target ? document.querySelector(`[data-guide-id="${step.target}"]`) : null,
    step.target ? document.getElementById(step.target) : null,
    step.target && TOUR_TARGET_ALIASES[step.target] ? document.getElementById(TOUR_TARGET_ALIASES[step.target]) : null,
    step.target && TOUR_TARGET_ALIASES[step.target] ? document.querySelector(`[data-guide-id="${TOUR_TARGET_ALIASES[step.target]}"]`) : null,
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (isVisibleTarget(candidate)) return preferredHighlightTarget(candidate);
    const visibleParent = candidate.closest?.(".dash-section,.panel,.rpt-card,.ws-panel,.kpi-card,.dp-wrap,[data-guide-id]");
    if (isVisibleTarget(visibleParent)) return preferredHighlightTarget(visibleParent);
  }
  return null;
}

function preferredHighlightTarget(el) {
  if (!el) return null;
  if (["TBODY", "THEAD", "TR"].includes(el.tagName)) {
    return el.closest("table,.dash-section,.panel") || el;
  }
  return el;
}

function clearTourHighlights() {
  document.querySelectorAll(".tour-highlight").forEach(el => {
    el.classList.remove("tour-highlight");
    delete el.dataset.guideLabel;
  });
}

function renderTourStep() {
  const steps  = _activeTourSteps || TOUR_STEPS;
  const step   = steps[tourStep];
  const target = resolveTourTarget(step);

  // Scroll target into view
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => positionTourBox(target, step), 350);
  } else {
    setTimeout(() => {
      tourBox.style.top = "84px";
      tourBox.style.left = Math.max(12, window.innerWidth - 380) + "px";
    }, 0);
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
  clearTourHighlights();
  if (target) {
    target.dataset.guideLabel = step.title;
    target.classList.add("tour-highlight");
  }
}

function positionTourBox(target, step) {
  const rect   = target.getBoundingClientRect();
  const boxW   = Math.min(340, window.innerWidth - 24);
  const boxH   = tourBox.offsetHeight || 180;
  const margin = 16;
  let top, left;

  if (step.position === "bottom") {
    top  = rect.bottom + margin;
    left = rect.left + (rect.width / 2) - (boxW / 2);
  } else if (step.position === "top") {
    top  = rect.top - boxH - margin;
    left = rect.left + (rect.width / 2) - (boxW / 2);
  } else if (step.position === "right") {
    top  = rect.top + (rect.height / 2) - (boxH / 2);
    left = rect.right + margin;
  } else {
    top  = rect.top + (rect.height / 2) - (boxH / 2);
    left = rect.left - boxW - margin;
  }

  // Keep within viewport
  left = Math.max(12, Math.min(left, window.innerWidth - boxW - 12));
  top  = Math.max(12, Math.min(top, window.innerHeight - Math.min(boxH, window.innerHeight - 24) - 12));

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
  clearTourHighlights();
  setTourButtonState(false);
  // Remember tour was completed
  try { localStorage.setItem("costpilot_tour_done", "1"); } catch(e) {}
}

// ── Auto-launch tour on first visit ──────────────────────────────────────────
window.addEventListener("load", () => {
  try {
    if (!localStorage.getItem("costpilot_tour_done")) {
      setTimeout(startTour, 1200);
    }
  } catch(e) {}
});
