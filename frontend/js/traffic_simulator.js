(() => {
  const profileMap = {
    enterpriseSaas: {
      label: "Enterprise SaaS",
      platforms: ["Salesforce Agentforce", "Salesforce Service Cloud", "Zendesk", "Slack"],
      departments: {
        Support: ["Support Resolution Agent", "Case Triage Agent"],
        Sales: ["Renewal Assist Agent", "Pipeline Follow-up Agent"],
        Operations: ["Workflow Ops Agent", "Integration Monitor Agent"],
        Finance: ["Invoice Review Agent", "Spend Review Agent"],
        Marketing: ["Campaign Brief Agent", "Content QA Agent"],
      },
    },
    retailServices: {
      label: "Retail Services",
      platforms: ["Salesforce Commerce", "Zendesk", "Shopify", "ServiceNow"],
      departments: {
        Support: ["Returns Agent", "Customer Care Agent"],
        Sales: ["Loyalty Offer Agent", "Store Assist Agent"],
        Operations: ["Fulfillment Agent", "Inventory Agent"],
        Finance: ["Refund Review Agent", "Chargeback Agent"],
        Marketing: ["Promotion Agent", "Customer Segment Agent"],
      },
    },
    manufacturing: {
      label: "Manufacturing",
      platforms: ["ServiceNow", "IoT Ops", "SAP", "Salesforce Field Service"],
      departments: {
        Support: ["Warranty Agent", "Field Support Agent"],
        Sales: ["Distributor Quote Agent", "Renewal Quote Agent"],
        Operations: ["Maintenance Planning Agent", "Production Schedule Agent"],
        Finance: ["Vendor Invoice Agent", "Capex Review Agent"],
        Engineering: ["Quality Review Agent", "Sensor Summary Agent"],
      },
    },
    professionalServices: {
      label: "Professional Services",
      platforms: ["Salesforce", "HubSpot", "Microsoft Teams", "NetSuite"],
      departments: {
        Support: ["Client Request Agent", "Delivery Desk Agent"],
        Sales: ["Proposal Agent", "Account Planning Agent"],
        Operations: ["Staffing Agent", "Project Risk Agent"],
        Finance: ["Billing Agent", "Revenue Forecast Agent"],
        Legal: ["Contract Intake Agent", "Engagement Terms Agent"],
      },
    },
  };

  const styleLabels = {
    balanced: "Balanced",
    savings: "Savings-Heavy",
    risk: "Risk-Heavy",
    pruning: "Pruning Showcase",
  };

  const styleMix = {
    balanced: ["routine", "routine", "moderate", "pruning", "pruning", "risk", "blocked"],
    savings: ["routine", "routine", "routine", "routine", "moderate", "pruning", "pruning"],
    risk: ["moderate", "risk", "risk", "risk", "blocked", "blocked", "pruning"],
    pruning: ["pruning", "pruning", "pruning", "pruning", "routine", "moderate", "risk"],
  };

  const subjects = {
    routine: [
      "Update customer record notes",
      "Summarize support conversation",
      "Draft routine follow-up",
      "Classify renewal request",
    ],
    moderate: [
      "Prepare account summary",
      "Review quote request",
      "Summarize incident history",
      "Create customer response plan",
    ],
    pruning: [
      "Clean long customer email thread",
      "Summarize repeated case history",
      "Review forwarded renewal chain",
      "Extract action items from noisy thread",
    ],
    risk: [
      "Review contract language",
      "Summarize confidential vendor concern",
      "Assess legal escalation request",
      "Review liability question",
    ],
    blocked: [
      "Payment verification request",
      "Identity data in support case",
      "Banking details in refund request",
      "Sensitive account recovery note",
    ],
  };

  const state = {
    size: 50,
    style: "balanced",
    running: false,
    stopRequested: false,
    totals: null,
  };

  const $ = id => document.getElementById(id);
  const moneyFmt = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  });
  const intFmt = new Intl.NumberFormat("en-US");

  function resetTotals() {
    state.totals = {
      completed: 0,
      sent: 0,
      success: 0,
      blocked: 0,
      scout: 0,
      analyst: 0,
      advisor: 0,
      strategist: 0,
      pruned: 0,
      tokensSaved: 0,
      spend: 0,
      errors: 0,
    };
  }

  function pick(list) {
    return list[Math.floor(Math.random() * list.length)];
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function normalizedTier(value) {
    const tier = String(value || "").toLowerCase();
    if (tier.includes("scout") || tier.includes("micro")) return "scout";
    if (tier.includes("analyst")) return "analyst";
    if (tier.includes("advisor") || tier.includes("flagship")) return "advisor";
    if (tier.includes("strategist")) return "strategist";
    if (tier.includes("none")) return "blocked";
    return "scout";
  }

  function deptAgent(profile) {
    const departments = Object.keys(profile.departments);
    const department = pick(departments);
    return {
      department,
      agent: pick(profile.departments[department]),
      platform: pick(profile.platforms),
    };
  }

  function buildRoutine({ department }) {
    return [
      "Customer request:",
      `Department: ${department}`,
      "Task: Draft a short, friendly status update confirming that the request was received.",
      "Keep the answer brief. No policy exception requested. No contract review requested.",
    ].join("\n");
  }

  function buildModerate({ department }) {
    return [
      `A ${department} team member needs a customer-ready summary and next-step recommendation.`,
      "Review the open notes, summarize the customer's goal, identify missing information, and recommend a response plan.",
      "The customer asked for timing, ownership, dependencies, and whether an internal review is needed before the team replies.",
      "Keep the answer structured with summary, next steps, owner, and customer response.",
    ].join("\n");
  }

  function buildPruning({ department, agent, platform, index }) {
    const header = [
      `From: taylor.casey${index}@example.com`,
      `To: ${agent.toLowerCase().replaceAll(" ", ".")}@example.com`,
      `CC: operations.team@example.com; support.queue@example.com; updates@example.com`,
      "Date: Wednesday, May 27, 2026, 8:22 AM",
      `Subject: RE: RE: RE: RE: ${department} customer request - follow up needed`,
      "X-Mailer: Microsoft Outlook 16.0",
      "Importance: High",
      "Thread-ID: demo-thread-93A7-FAKE-ONLY",
      "",
    ].join("\n");

    const body = [
      "Hi team, following up on the customer request below. The customer wants a concise summary, next steps, and a clear explanation of what we can do by Friday.",
      "Please ignore duplicate signatures, previous routing notes, old disclaimers, tracking lines, and repeated headers. The useful business request is only the current ask plus the latest customer constraints.",
      "Latest request: summarize the status, identify open questions, and draft a short response that a manager can approve.",
      "",
      "--",
      "Taylor Casey",
      `${platform} Operations`,
      "This message may contain internal business information. If you received this in error, delete it.",
      "",
      "-----Original Message-----",
    ].join("\n");

    return Array.from({ length: 5 }, (_, i) => {
      return [
        header,
        body,
        `Prior note ${i + 1}: repeating old context, boilerplate, routing metadata, blank lines, and signatures that should not be needed for the AI answer.`,
        "Status: copied from earlier message. Owner: copied from earlier message. Deadline: copied from earlier message.",
        "Confidentiality footer: This is a synthetic demo footer repeated to test context pruning. ".repeat(6),
      ].join("\n");
    }).join("\n\n");
  }

  function buildRisk({ department }) {
    return [
      `${department} request for review: customer is asking about contract language, legal responsibility, liability, and confidential renewal terms.`,
      "Summarize the business risk in plain English, identify whether this should be reviewed before response, and recommend an escalation path.",
      "The request references an NDA, a contract exception, liability concerns, and confidential pricing discussion.",
      "Do not make a legal decision. Prepare a business summary for review.",
    ].join("\n");
  }

  function buildBlocked({ department, index }) {
    return [
      `${department} case note: customer pasted sensitive payment and account recovery details into the support conversation.`,
      `Synthetic test card number 4111 1111 1111 ${String(index).padStart(4, "0")}.`,
      "The message also mentions a bank account, routing number, credit card number, and account verification information.",
      "This is fake demo data only, but it should demonstrate blocked payload behavior before any AI model receives it.",
    ].join("\n");
  }

  function buildText(type, context) {
    if (type === "routine") return buildRoutine(context);
    if (type === "moderate") return buildModerate(context);
    if (type === "pruning") return buildPruning(context);
    if (type === "risk") return buildRisk(context);
    return buildBlocked(context);
  }

  function buildPlan() {
    const profile = profileMap[$("companyProfile").value] || profileMap.enterpriseSaas;
    const mix = styleMix[state.style] || styleMix.balanced;
    return Array.from({ length: state.size }, (_, index) => {
      const context = { ...deptAgent(profile), index: index + 1 };
      const type = pick(mix);
      const subject = pick(subjects[type]);
      return {
        type,
        subject,
        ...context,
        text: `Subject: ${subject}\n\n${buildText(type, context)}`,
      };
    });
  }

  async function postRoute(item) {
    const response = await fetch("/api/route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: item.text,
        department: item.department,
        auto_prune: true,
        agent_name: item.agent,
        source_platform: item.platform,
        voice_guard_processed: false,
        is_test: false,
        payload_type: "text",
      }),
    });

    const rawText = await response.text();
    let body = {};
    try {
      body = rawText ? JSON.parse(rawText) : {};
    } catch (_) {
      body = { detail: rawText };
    }

    if (!response.ok) {
      return {
        ok: false,
        blocked: response.status === 451 || /block|sensitive/i.test(body.detail || body.message || rawText),
        status: response.status,
        body,
      };
    }

    return { ok: true, blocked: false, status: response.status, body };
  }

  function updateCounters() {
    const t = state.totals;
    const economy = t.scout + t.analyst;
    const progress = state.size > 0 ? Math.round((t.completed / state.size) * 100) : 0;

    $("sentCount").textContent = intFmt.format(t.sent);
    $("targetCount").textContent = `of ${state.size} requests`;
    $("economyCount").textContent = intFmt.format(economy);
    $("prunedCount").textContent = intFmt.format(t.pruned);
    $("tokensSavedCount").textContent = intFmt.format(t.tokensSaved);
    $("blockedCount").textContent = intFmt.format(t.blocked);
    $("scoutCount").textContent = intFmt.format(t.scout);
    $("analystCount").textContent = intFmt.format(t.analyst);
    $("advisorCount").textContent = intFmt.format(t.advisor);
    $("strategistCount").textContent = intFmt.format(t.strategist);
    $("spendCount").textContent = moneyFmt.format(t.spend);
    $("errorCount").textContent = intFmt.format(t.errors);
    $("progressBar").style.width = `${progress}%`;
    $("progressText").textContent = `${progress}%`;
  }

  function setStatus(label, subtext) {
    $("runStatus").textContent = label;
    $("runStatusSub").textContent = subtext;
  }

  function clearLog() {
    $("activityLog").innerHTML = `
      <div class="empty-state">
        No simulated requests yet. Start a run to send synthetic AI traffic through CostPilot.
      </div>
    `;
  }

  function addLog({ item, result, tier, blocked, error }) {
    const log = $("activityLog");
    const empty = log.querySelector(".empty-state");
    if (empty) empty.remove();

    const body = result?.body || {};
    const cost = Number(body.cost_usd || 0);
    const saved = Number(body.tokens_saved_by_pruning || 0);
    const label = blocked ? "Blocked" : error ? "Error" : (body.model_tier || tier || "Scout");
    const cls = blocked ? "blocked" : error ? "error" : normalizedTier(label);
    const outcome = blocked
      ? (body.detail || "Sensitive payload stopped")
      : error
        ? (body.detail || body.message || "Request failed")
        : `${body.routing_decision || "Routed"}${saved > 0 ? ` - ${intFmt.format(saved)} tokens saved` : ""}`;

    const node = document.createElement("div");
    node.className = `activity-item ${cls}`;
    node.innerHTML = `
      <div class="activity-pill ${cls}">${escapeHtml(label)}</div>
      <div class="activity-main">
        <div class="activity-title">${escapeHtml(item.subject)}</div>
        <div class="activity-meta">${escapeHtml(item.agent)} · ${escapeHtml(item.department)} · ${escapeHtml(item.platform)} · ${escapeHtml(outcome)}</div>
      </div>
      <div class="activity-cost">${blocked || error ? "-" : moneyFmt.format(cost)}</div>
    `;
    log.prepend(node);

    while (log.children.length > 75) {
      log.removeChild(log.lastElementChild);
    }
  }

  async function sendOne(item) {
    const t = state.totals;
    try {
      const result = await postRoute(item);
      t.completed += 1;

      if (result.blocked) {
        t.blocked += 1;
        t.sent += 1;
        addLog({ item, result, blocked: true });
        updateCounters();
        return;
      }

      if (!result.ok) {
        t.errors += 1;
        addLog({ item, result, error: true });
        updateCounters();
        return;
      }

      t.sent += 1;
      t.success += 1;

      const body = result.body;
      const tier = normalizedTier(body.model_tier);
      if (tier === "scout") t.scout += 1;
      if (tier === "analyst") t.analyst += 1;
      if (tier === "advisor") t.advisor += 1;
      if (tier === "strategist") t.strategist += 1;

      const saved = Number(body.tokens_saved_by_pruning || 0);
      if (body.was_pruned && saved > 0) {
        t.pruned += 1;
        t.tokensSaved += saved;
      }
      t.spend += Number(body.cost_usd || 0);
      addLog({ item, result, tier });
      updateCounters();
    } catch (err) {
      t.completed += 1;
      t.errors += 1;
      addLog({ item, result: { body: { detail: err.message } }, error: true });
      updateCounters();
    }
  }

  function setRunning(isRunning) {
    state.running = isRunning;
    $("runSimulationBtn").disabled = isRunning;
    $("stopSimulationBtn").disabled = !isRunning;
    $("companyProfile").disabled = isRunning;
    document.querySelectorAll(".choice-btn").forEach(btn => {
      btn.disabled = isRunning;
    });
  }

  async function runSimulation() {
    if (state.running) return;

    resetTotals();
    clearLog();
    updateCounters();
    state.stopRequested = false;
    setRunning(true);
    setStatus("Running", `Sending ${state.size} synthetic requests in small waves.`);

    const plan = buildPlan();
    const waveSize = 5;

    for (let start = 0; start < plan.length; start += waveSize) {
      if (state.stopRequested) break;
      const wave = plan.slice(start, start + waveSize);
      setStatus("Running", `Processing requests ${start + 1}-${Math.min(start + waveSize, plan.length)}.`);
      await Promise.all(wave.map(sendOne));
      if (start + waveSize < plan.length) {
        await delay(550 + Math.floor(Math.random() * 350));
      }
    }

    const stopped = state.stopRequested;
    setRunning(false);
    setStatus(stopped ? "Stopped" : "Complete", stopped ? "Run stopped by user." : "Open dashboard or reports to view the roll-up.");
  }

  function updateChoice(groupId, attr, value) {
    document.querySelectorAll(`#${groupId} .choice-btn`).forEach(btn => {
      btn.classList.toggle("active", btn.dataset[attr] === String(value));
    });
  }

  function updateLabels() {
    const profile = profileMap[$("companyProfile").value] || profileMap.enterpriseSaas;
    $("styleLabel").textContent = styleLabels[state.style] || "Balanced";
    $("profileLabel").textContent = profile.label;
    $("targetCount").textContent = `of ${state.size} requests`;
  }

  function bindEvents() {
    $("sizeChoices").addEventListener("click", event => {
      const btn = event.target.closest("[data-size]");
      if (!btn || state.running) return;
      state.size = Number(btn.dataset.size || 50);
      updateChoice("sizeChoices", "size", state.size);
      updateLabels();
      updateCounters();
    });

    $("styleChoices").addEventListener("click", event => {
      const btn = event.target.closest("[data-style]");
      if (!btn || state.running) return;
      state.style = btn.dataset.style || "balanced";
      updateChoice("styleChoices", "style", state.style);
      updateLabels();
    });

    $("companyProfile").addEventListener("change", updateLabels);
    $("runSimulationBtn").addEventListener("click", runSimulation);
    $("stopSimulationBtn").addEventListener("click", () => {
      state.stopRequested = true;
      $("stopSimulationBtn").disabled = true;
      setStatus("Stopping", "Finishing the current wave.");
    });
    $("clearLogBtn").addEventListener("click", clearLog);
  }

  document.addEventListener("DOMContentLoaded", () => {
    resetTotals();
    bindEvents();
    updateLabels();
    updateCounters();
  });
})();
