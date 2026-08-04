(function () {
  "use strict";

  const primaryItems = [
    { label: "Overview", href: "/index.html", paths: ["/", "/index.html"] },
    { label: "Operate", href: "/operate.html", paths: ["/operate.html"] },
    { label: "Projects", href: "/work-items.html", paths: ["/work-items.html"] },
    { label: "Reports", href: "/reports.html", paths: ["/reports.html"] },
  ];

  const manageItems = [
    { label: "Connect & Setup", href: "/onboarding.html" },
    { label: "Policy & Rules", href: "/policy.html" },
    { label: "Models", href: "/models.html" },
    { label: "Administration", href: "/admin.html" },
  ];

  const toolItems = [
    { label: "Sandbox", href: "/sandbox.html" },
    { label: "Savings Calculator", href: "/savings.html" },
    { label: "Live Demo", href: "/demo-crm.html" },
    { label: "Live Monitor", href: "/live-landing.html" },
  ];

  const workspaceOptions = [
    { id: "4BE43240A6674314", label: "Salesforce Pilot", kind: "Live workspace" },
    { id: "SIM-HISTORICAL-2Y", label: "Historical Demo", kind: "Simulated history" },
  ];

  const currentPath = location.pathname || "/";
  const isCurrent = (item) => (item.paths || [item.href]).includes(currentPath);
  const escapeHtml = (value) => String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

  function linkMarkup(item, className) {
    const active = isCurrent(item);
    return `<a href="${escapeHtml(item.href)}" class="${className}${active ? " active" : ""}"${active ? ' aria-current="page"' : ""}>${escapeHtml(item.label)}</a>`;
  }

  function dropdownMarkup(id, label, items) {
    const active = items.some(isCurrent);
    return `
      <div class="cp-global-nav__dropdown" data-cp-dropdown>
        <button class="cp-global-nav__trigger${active ? " active" : ""}" type="button"
          aria-expanded="false" aria-controls="${id}">${escapeHtml(label)}</button>
        <div class="cp-global-nav__menu" id="${id}" role="menu">
          <div class="cp-global-nav__menu-label">${escapeHtml(label)}</div>
          ${items.map((item) => linkMarkup(item, "")).join("")}
        </div>
      </div>`;
  }

  function activeWorkspace() {
    const id = localStorage.getItem("cp_workspace_id") || workspaceOptions[0].id;
    return workspaceOptions.find((workspace) => workspace.id === id)
      || { id, label: localStorage.getItem("cp_workspace_name") || "Current workspace", kind: "Workspace" };
  }

  function workspaceMarkup() {
    const active = activeWorkspace();
    const options = workspaceOptions.some((workspace) => workspace.id === active.id)
      ? workspaceOptions
      : [active, ...workspaceOptions];
    return `
      <label class="cp-workspace-switcher" title="Choose which isolated workspace CostPilot should display">
        <span class="cp-workspace-switcher__label">Workspace</span>
        <select id="cpWorkspaceSwitcher" aria-label="Active CostPilot workspace">
          ${options.map((workspace) => `<option value="${escapeHtml(workspace.id)}"${workspace.id === active.id ? " selected" : ""}>${escapeHtml(workspace.label)}</option>`).join("")}
        </select>
      </label>`;
  }

  function switchWorkspace(workspaceId) {
    const selected = workspaceOptions.find((workspace) => workspace.id === workspaceId);
    if (!selected || selected.id === activeWorkspace().id) return;
    localStorage.setItem("cp_workspace_id", selected.id);
    localStorage.setItem("cp_workspace_name", selected.label);
    localStorage.removeItem("costpilot_exec_filters");
    const url = new URL(location.href);
    url.searchParams.delete("workspace_id");
    location.assign(url.href);
  }

  function findHeader() {
    return document.querySelector(".exec-header")
      || document.querySelector(".wa-header")
      || document.querySelector("header.header");
  }

  function findExistingNavigation(header) {
    return header.querySelector(".exec-header-right")
      || header.querySelector(".wa-nav")
      || header.querySelector(".header-nav")
      || Array.from(header.children).find((child) => child.querySelector && child.querySelector(".header-nav-link"));
  }

  function buildNavigation() {
    const header = findHeader();
    if (!header || header.querySelector(".cp-global-nav")) return;

    const existingNavigation = findExistingNavigation(header);
    if (existingNavigation) existingNavigation.classList.add("cp-global-nav-source");
    header.querySelectorAll(".header-status").forEach((status) => status.classList.add("cp-global-nav-source"));
    const legacyStatusLabel = header.querySelector("#statusLabel");
    if (legacyStatusLabel?.parentElement) legacyStatusLabel.parentElement.classList.add("cp-global-nav-source");

    const nav = document.createElement("nav");
    nav.className = "cp-global-nav";
    nav.setAttribute("aria-label", "CostPilot navigation");
    nav.innerHTML = `
      <button class="cp-global-nav__trigger cp-global-nav__mobile-trigger" type="button"
        aria-expanded="false" aria-label="Open navigation">Menu</button>
      <div class="cp-global-nav__primary">
        ${primaryItems.map((item) => linkMarkup(item, "cp-global-nav__link")).join("")}
        ${dropdownMarkup("cpManageMenu", "Manage", manageItems)}
        ${dropdownMarkup("cpToolsMenu", "Tools", toolItems)}
      </div>
      <div class="cp-global-nav__utilities">
        ${workspaceMarkup()}
        <button class="cp-global-nav__ask" id="cpGlobalAsk" type="button"
          aria-label="Ask CostPilot about this workspace" title="Ask CostPilot">Ask CostPilot</button>
        <a class="cp-global-nav__status" id="cpGlobalStatus" href="/live-landing.html" title="Open live system monitor">
          <span class="cp-global-nav__status-dot" aria-hidden="true"></span>
          <span class="cp-global-nav__status-label">Checking</span>
        </a>
        <button class="cp-global-nav__help" id="cpGlobalHelp" type="button" aria-label="Open page guide" title="Help and page guide">?</button>
      </div>`;
    header.appendChild(nav);

    nav.querySelectorAll("[data-cp-dropdown] > button").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const dropdown = button.closest("[data-cp-dropdown]");
        const willOpen = !dropdown.classList.contains("open");
        closeMenus(nav);
        dropdown.classList.toggle("open", willOpen);
        button.setAttribute("aria-expanded", String(willOpen));
      });
    });

    const mobileButton = nav.querySelector(".cp-global-nav__mobile-trigger");
    mobileButton.addEventListener("click", (event) => {
      event.stopPropagation();
      const willOpen = !nav.classList.contains("mobile-open");
      closeMenus(nav);
      nav.classList.toggle("mobile-open", willOpen);
      mobileButton.setAttribute("aria-expanded", String(willOpen));
    });

    nav.querySelector("#cpGlobalHelp").addEventListener("click", () => {
      if (typeof window.startTour === "function" && Array.isArray(window.PAGE_TOUR_STEPS)) {
        window.startTour();
      } else {
        location.href = "/getting-started.html";
      }
    });
    nav.querySelector("#cpGlobalAsk").addEventListener("click", openAskCostPilot);
    nav.querySelector("#cpWorkspaceSwitcher").addEventListener("change", (event) => {
      switchWorkspace(event.target.value);
    });

    document.addEventListener("click", () => closeMenus(nav));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenus(nav);
    });

    updateStatus(nav);
  }

  function readSession(key, fallback) {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(key) || "null");
      return parsed === null ? fallback : parsed;
    } catch (_error) {
      return fallback;
    }
  }

  function writeSession(key, value) {
    try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (_error) {}
  }

  function askActorScope() {
    return localStorage.getItem("cp_user_external_id")
      || localStorage.getItem("cp_user_email")
      || localStorage.getItem("cp_actor_id")
      || "anonymous";
  }

  function askStorageKey(kind) {
    const workspace = localStorage.getItem("cp_workspace_id") || "default";
    return `cp_ask_costpilot_${kind}:${workspace}:${askActorScope()}`;
  }

  function readAskStorage(kind, fallback) {
    try {
      const parsed = JSON.parse(localStorage.getItem(askStorageKey(kind)) || "null");
      return parsed === null ? fallback : parsed;
    } catch (_error) {
      return fallback;
    }
  }

  function writeAskStorage(kind, value) {
    try { localStorage.setItem(askStorageKey(kind), JSON.stringify(value)); } catch (_error) {}
  }

  function clearAskStorage() {
    try {
      localStorage.removeItem(askStorageKey("history"));
      localStorage.removeItem(askStorageKey("context"));
    } catch (_error) {}
  }

  function installAskCostPilot() {
    if (document.getElementById("cpAskDrawer")) return;
    const root = document.createElement("div");
    root.innerHTML = `
      <div class="cp-ask-backdrop" id="cpAskBackdrop" hidden></div>
      <aside class="cp-ask-drawer" id="cpAskDrawer" aria-hidden="true" aria-labelledby="cpAskTitle">
        <header class="cp-ask-header">
          <div>
            <span class="cp-ask-kicker">Workspace intelligence</span>
            <h2 id="cpAskTitle">Ask CostPilot</h2>
            <p>Answers calculated from <strong id="cpAskWorkspaceName">this workspace</strong>.</p>
          </div>
          <div class="cp-ask-header-actions">
            <button type="button" class="cp-ask-clear" id="cpAskClear">Clear chat</button>
            <button type="button" class="cp-ask-close" id="cpAskClose" aria-label="Close Ask CostPilot">×</button>
          </div>
        </header>
        <div class="cp-ask-scope-summary">
          <span><b>Scope</b><strong id="cpAskScopeWorkspace">Current workspace</strong></span>
          <span><b>Date range</b><strong id="cpAskScopeDate">Last 30 days</strong></span>
          <span><b>Data</b><strong id="cpAskScopeData">Checking…</strong></span>
        </div>
        <div class="cp-ask-suggestions" aria-label="Suggested questions">
          <button type="button" data-question="Why did our AI spend increase compared with the previous period?">Why did AI spend increase?</button>
          <button type="button" data-requires="people" data-question="Who used the most tokens this month?">Who used the most tokens?</button>
          <button type="button" data-requires="agents" data-question="Which agents have not been used in this period?">Which agents are inactive?</button>
          <button type="button" data-question="Compare our token usage and AI spend this month with last month.">Compare with last month</button>
          <button type="button" data-requires="accounts" data-question="Which accounts generated the most AI activity this month?">Top accounts by activity</button>
        </div>
        <div class="cp-ask-messages" id="cpAskMessages" aria-live="polite">
          <div class="cp-ask-welcome">
            <strong>What would you like to know?</strong>
            <span>Ask how CostPilot works, what this page means, or about people, agents, models, spend, pruning, and risk.</span>
          </div>
        </div>
        <form class="cp-ask-composer" id="cpAskForm">
          <label class="cp-sr-only" for="cpAskInput">Ask CostPilot a question</label>
          <textarea id="cpAskInput" rows="2" maxlength="500"
            placeholder="Ask about AI spend, usage, pruning, people, agents, or accounts…"></textarea>
          <button type="submit" id="cpAskSend">Ask</button>
        </form>
        <footer class="cp-ask-footer">
          <span>Calculations include evidence and data source labels.</span>
          <a href="/reports.html?tab=efficiency">Open full analysis →</a>
        </footer>
      </aside>`;
    document.body.appendChild(root);

    document.getElementById("cpAskBackdrop").addEventListener("click", closeAskCostPilot);
    document.getElementById("cpAskClose").addEventListener("click", closeAskCostPilot);
    document.getElementById("cpAskClear").addEventListener("click", clearGlobalAskConversation);
    document.getElementById("cpAskForm").addEventListener("submit", submitGlobalAsk);
    document.getElementById("cpAskInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        document.getElementById("cpAskForm")?.requestSubmit();
      }
    });
    document.querySelector(".cp-ask-suggestions").addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      document.getElementById("cpAskInput").value = button.dataset.question || button.textContent.trim();
      submitGlobalAsk();
    });
    document.getElementById("cpAskMessages").addEventListener("click", (event) => {
      const followUp = event.target.closest("[data-ask-question]");
      if (!followUp) return;
      document.getElementById("cpAskInput").value = followUp.dataset.askQuestion;
      submitGlobalAsk();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAskCostPilot();
      if ((event.metaKey || event.ctrlKey) && event.key === "/") {
        event.preventDefault();
        openAskCostPilot();
      }
    });
    restoreGlobalAskConversation();
  }

  function openAskCostPilot() {
    installAskCostPilot();
    const workspaceName = document.getElementById("cpAskWorkspaceName");
    if (workspaceName) workspaceName.textContent = activeWorkspace().label;
    const drawer = document.getElementById("cpAskDrawer");
    const backdrop = document.getElementById("cpAskBackdrop");
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.hidden = false;
    document.body.classList.add("cp-ask-open");
    refreshGlobalAskExperience();
    setTimeout(() => document.getElementById("cpAskInput")?.focus(), 30);
  }

  function compactDateLabel(value) {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric",
    });
  }

  async function refreshGlobalAskExperience() {
    const scope = askScope();
    const workspace = activeWorkspace();
    const workspaceNode = document.getElementById("cpAskScopeWorkspace");
    const dateNode = document.getElementById("cpAskScopeDate");
    const dataNode = document.getElementById("cpAskScopeData");
    if (workspaceNode) workspaceNode.textContent = workspace.label;
    if (dateNode) {
      const from = compactDateLabel(scope.date_from);
      const to = compactDateLabel(scope.date_to);
      dateNode.textContent = from && to ? `${from} – ${to}` : `Last ${Number(scope.days) || 30} days`;
    }
    const params = new URLSearchParams({
      days: String(Math.max(1, Math.min(365, Number(scope.days) || 30))),
      activity_limit: "1",
    });
    if (scope.workspace_id) params.set("workspace_id", scope.workspace_id);
    if (scope.date_from) params.set("date_from", scope.date_from);
    if (scope.date_to) params.set("date_to", scope.date_to);
    try {
      const response = await fetch(`/api/work-items/activity-report?${params.toString()}`);
      if (!response.ok) throw new Error("Availability check failed");
      const data = await response.json();
      const summary = data.summary || {};
      const live = Number(summary.live_count || 0);
      const simulated = Number(summary.simulation_count || 0);
      if (dataNode) dataNode.textContent = live && simulated
        ? `${live.toLocaleString()} live · ${simulated.toLocaleString()} sample`
        : live ? `${live.toLocaleString()} live`
          : simulated ? `${simulated.toLocaleString()} sample`
            : "No matching activity";
      const availability = {
        people: Number(summary.people_count || 0) > 0,
        agents: (data.filter_options?.agents || []).length > 0,
        accounts: (data.filter_options?.accounts || []).length > 0,
      };
      document.querySelectorAll(".cp-ask-suggestions [data-requires]").forEach((button) => {
        button.hidden = !availability[button.dataset.requires];
      });
    } catch (_error) {
      if (dataNode) dataNode.textContent = "Could not verify";
    }
  }

  function closeAskCostPilot() {
    const drawer = document.getElementById("cpAskDrawer");
    const backdrop = document.getElementById("cpAskBackdrop");
    if (!drawer || !backdrop) return;
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    backdrop.hidden = true;
    document.body.classList.remove("cp-ask-open");
  }

  function askScope() {
    const query = new URLSearchParams(location.search);
    const pageScope = typeof window.getCostPilotAskScope === "function"
      ? window.getCostPilotAskScope()
      : {};
    return {
      days: 30,
      workspace_id: localStorage.getItem("cp_workspace_id") || null,
      governed_request_id: query.get("governed_request_id"),
      audit_event_id: query.get("audit_event_id"),
      ...pageScope,
    };
  }

  function cleanAskString(value) {
    if (value === null || value === undefined) return null;
    const cleaned = String(value).trim();
    return cleaned || null;
  }

  function cleanAskDate(value) {
    const cleaned = cleanAskString(value);
    if (!cleaned || Number.isNaN(Date.parse(cleaned))) return null;
    return cleaned;
  }

  function currentAskScreenContext() {
    const activeSection = document.querySelector(
      "[aria-current='page'], .rpt-tab.active, .cp-exec-subnav a.active, [role='tab'][aria-selected='true']"
    );
    const visibleHeading = document.querySelector(
      "main h1, main h2, .exec-body h1, .exec-body h2, .page-title"
    );
    return {
      page_path: `${location.pathname}${location.search}`.slice(0, 500),
      page_title: (document.title || "CostPilot").slice(0, 200),
      section: (activeSection?.textContent || visibleHeading?.textContent || "").trim().slice(0, 200) || null,
      visible_metric: document.activeElement?.dataset?.metric || null,
      selected_label: document.activeElement?.getAttribute?.("aria-label") || null,
    };
  }

  function normalizedAskPayload(question, history, context, includeConversation = true) {
    const scope = askScope() || {};
    const daysValue = Number(scope.days);
    const payload = {
      question,
      days: Number.isFinite(daysValue) ? Math.max(1, Math.min(365, Math.round(daysValue))) : 30,
      timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      screen_context: currentAskScreenContext(),
    };
    [
      "workspace_id", "project_id", "user_external_id", "account_id",
      "source_platform", "record_type", "model_tier", "charged_unit", "business_purpose",
      "governed_request_id",
    ].forEach((key) => {
      const value = cleanAskString(scope[key]);
      if (value !== null) payload[key] = value;
    });
    const agentId = Number(scope.agent_id);
    if (Number.isInteger(agentId) && agentId > 0) payload.agent_id = agentId;
    const auditEventId = Number(scope.audit_event_id);
    if (Number.isInteger(auditEventId) && auditEventId > 0) payload.audit_event_id = auditEventId;
    const dateFrom = cleanAskDate(scope.date_from);
    const dateTo = cleanAskDate(scope.date_to);
    if (dateFrom) payload.date_from = dateFrom;
    if (dateTo) payload.date_to = dateTo;

    if (!includeConversation) return payload;
    payload.conversation = (Array.isArray(history) ? history : [])
      .filter((item) => item && (item.role === "user" || item.role === "assistant"))
      .map((item) => ({ role: item.role, content: cleanAskString(item.content) }))
      .filter((item) => item.content)
      .slice(-12);
    if (context && typeof context === "object" && !Array.isArray(context)) {
      const normalizedContext = {};
      [
        "intent", "entity", "metric", "direction", "source_platform", "subject_entity",
        "subject_filter_name", "subject_filter_value", "model_tier", "period_key",
        "comparison_key", "usage_status", "budget_scope",
      ].forEach((key) => {
        const value = cleanAskString(context[key]);
        if (value !== null) normalizedContext[key] = value;
      });
      ["days", "result_limit", "usage_threshold"].forEach((key) => {
        const value = Number(context[key]);
        if (Number.isInteger(value) && value > 0) normalizedContext[key] = value;
      });
      if (Object.keys(normalizedContext).length) payload.context = normalizedContext;
    }
    return payload;
  }

  async function postGlobalAsk(payload) {
    return fetch("/api/reports/bot-efficiency/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  function addAskMessage(role, content) {
    const messages = document.getElementById("cpAskMessages");
    const node = document.createElement("div");
    node.className = `cp-ask-message ${role}`;
    node.innerHTML = content;
    messages.appendChild(node);
    messages.scrollTop = messages.scrollHeight;
    return node;
  }

  const GLOBAL_ASK_DRILL_KEYS = [
    "date_from", "date_to", "project_id", "user_external_id", "account_id", "agent_id",
    "source_platform", "record_type", "charged_unit", "business_purpose", "audit_event_id",
  ];

  function normalizeGlobalAskDrillScope(scopeOrName, filterValue) {
    let source = scopeOrName;
    if (typeof source === "string" && source.trim().startsWith("{")) {
      try { source = JSON.parse(source); } catch (_) { source = {}; }
    } else if (typeof source === "string") {
      source = { [source]: filterValue };
    }
    if (source?.scope) source = source.scope;
    if (source?.filterName) source = { [source.filterName]: source.filterValue };
    const normalized = {};
    GLOBAL_ASK_DRILL_KEYS.forEach((key) => {
      const value = source?.[key];
      if (value !== null && value !== undefined && String(value).trim() !== "") {
        normalized[key] = key === "date_from" || key === "date_to"
          ? String(value).trim().slice(0, 10)
          : String(value).trim();
      }
    });
    return normalized;
  }

  function globalAskDrillScope(data, item) {
    const provenance = data?.data_provenance || {};
    const scope = normalizeGlobalAskDrillScope({
      ...(data?.filters || {}),
      ...(provenance.active_filters || {}),
      date_from: data?.period?.date_from,
      date_to: data?.period?.date_to,
    });
    if (item?.filter_name && item.filter_value !== null && item.filter_value !== undefined) {
      scope[item.filter_name] = String(item.filter_value);
    }
    return normalizeGlobalAskDrillScope(scope);
  }

  function globalAskDrillUrl(scope) {
    const params = new URLSearchParams({ tab: "contexts" });
    Object.entries(scope).forEach(([key, value]) => {
      if (key !== "audit_event_id") params.set(key, value);
    });
    return `/reports.html?${params.toString()}`;
  }

  function renderGlobalEvidence(item, data) {
    const scope = globalAskDrillScope(data, item);
    const drill = item.filter_name && item.filter_value !== null && item.filter_value !== undefined
      ? `<button type="button" data-ask-scope="${escapeHtml(encodeURIComponent(JSON.stringify(scope)))}">View activity →</button>`
      : "";
    return `<div class="cp-ask-evidence">
      <div><strong>${escapeHtml(item.label || "Unknown")}</strong><span>${escapeHtml(item.detail || "")}</span></div>
      <div><strong>${escapeHtml(item.value || "—")}</strong><span>${escapeHtml(item.metric_label || "")}</span>${drill}</div>
    </div>`;
  }

  function renderGlobalAnswer(data) {
    const provenance = data.data_provenance || {};
    const liveRequests = Number(provenance.live_requests || 0);
    const simulatorRequests = Number(provenance.simulator_requests || 0);
    const sourceLabel = {
      live: `Live data · ${liveRequests} requests`,
      simulator: `Simulator data · ${simulatorRequests} requests`,
      mixed: `Live + simulator · ${liveRequests} live / ${simulatorRequests} simulated`,
      no_activity: "No matching activity",
      product_knowledge: "CostPilot product knowledge",
      clarification_required: "Answer withheld for verification",
    }[provenance.scope] || "Governed activity";
    const clarification = provenance.scope === "clarification_required" || data.intent === "clarification";
    const evidence = (data.evidence || []).map((item) => renderGlobalEvidence(item, data)).join("");
    const activeFilters = Object.entries(provenance.active_filters || {})
      .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "")
      .map(([key, value]) => `<span>${escapeHtml(key.replaceAll("_", " "))}: ${escapeHtml(String(value))}</span>`)
      .join("");
    const calculation = data.calculation
      ? `<div class="cp-ask-calculation"><strong>Calculation</strong><span>${escapeHtml(data.calculation.formula || "")} across ${escapeHtml(String(data.calculation.row_count || 0))} matching requests.</span></div>`
      : "";
    const recommendations = (data.recommendations || []).length
      ? `<section><h4>Recommended next steps</h4><div class="cp-ask-recommendations">${data.recommendations.map((item) => `
          <div><strong>${escapeHtml(item.title || "Review opportunity")}</strong><span>${escapeHtml(item.body || "")}</span></div>
        `).join("")}</div></section>`
      : "";
    const rowCount = Number(data.calculation?.row_count || data.summary?.request_count || 0);
    const answerScope = globalAskDrillScope(data, null);
    const supportingRecords = rowCount > 0
      ? `<button type="button" class="cp-ask-supporting" data-ask-scope="${escapeHtml(encodeURIComponent(JSON.stringify(answerScope)))}">View ${rowCount.toLocaleString()} supporting ${rowCount === 1 ? "record" : "records"} →</button>`
      : "";
    const followUps = globalAskFollowUps(data);
    return `<article class="cp-ask-answer">
      <div class="cp-ask-answer-head"><span>${escapeHtml(provenance.period_label || "Selected period")}</span><b class="${clarification ? "clarification" : ""}">${clarification ? "Needs clarification" : "Calculated"}</b></div>
      <h3>${escapeHtml(data.title || "CostPilot answer")}</h3>
      ${data.interpreted_as ? `<div class="cp-ask-interpretation"><strong>Interpreted as</strong><span>${escapeHtml(data.interpreted_as)}</span></div>` : ""}
      <div class="cp-ask-answer-scope"><span><b>Date range</b>${escapeHtml(provenance.period_label || "Selected period")}</span><span><b>Scope</b>${escapeHtml(sourceLabel)}</span></div>
      <p>${escapeHtml(data.answer || "No answer was returned.")}</p>
      ${activeFilters ? `<div class="cp-ask-active-filters"><strong>Active filters</strong>${activeFilters}</div>` : ""}
      ${evidence ? `<section><h4>Evidence</h4>${evidence}</section>` : ""}
      ${calculation}
      ${supportingRecords}
      ${recommendations}
      ${followUps.length ? `<section><h4>You might also ask</h4><div class="cp-ask-followups">${followUps.map(question => `<button type="button" data-ask-question="${escapeHtml(question)}">${escapeHtml(question)}</button>`).join("")}</div></section>` : ""}
      <small>${escapeHtml(data.measurement_note || "Calculated from governed CostPilot activity.")}</small>
    </article>`;
  }

  function globalAskFollowUps(data) {
    if (Array.isArray(data.suggested_questions) && data.suggested_questions.length) {
      return data.suggested_questions.slice(0, 2).map(String);
    }
    if (data.intent === "clarification") return [];
    if (data.intent === "comparison" || data.intent === "drivers") {
      return ["Which department contributed most to the change?", "Was the change within budget?"];
    }
    if (data.entity === "people") return ["Compare the top people with the previous period."];
    if (data.entity === "agents") return ["Which models did the top agents use?"];
    return ["What changed compared with the previous period?"];
  }

  function bindGlobalAskDrills(container) {
    container?.querySelectorAll("[data-ask-scope],[data-ask-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        const scope = button.dataset.askScope
          ? decodeURIComponent(button.dataset.askScope)
          : { [button.dataset.askFilter]: button.dataset.askValue };
        openAskDrill(scope);
      });
    });
  }

  function restoreGlobalAskConversation() {
    const history = readAskStorage("history", []);
    if (!Array.isArray(history) || !history.length) return;
    const messages = document.getElementById("cpAskMessages");
    if (!messages) return;
    messages.innerHTML = "";
    history.slice(-12).forEach((item) => {
      if (!item || !item.content) return;
      if (item.role === "user") {
        addAskMessage("user", `<p>${escapeHtml(item.content)}</p>`);
      } else if (item.role === "assistant") {
        const node = addAskMessage(
          "assistant",
          item.data ? renderGlobalAnswer(item.data) : `<p>${escapeHtml(item.content)}</p>`,
        );
        bindGlobalAskDrills(node);
      }
    });
  }

  function clearGlobalAskConversation() {
    clearAskStorage();
    const messages = document.getElementById("cpAskMessages");
    if (!messages) return;
    messages.innerHTML = `<div class="cp-ask-welcome">
      <strong>What would you like to know?</strong>
      <span>Ask how CostPilot works, what this page means, or about people, agents, models, spend, pruning, and risk.</span>
    </div>`;
  }

  async function submitGlobalAsk(event) {
    event?.preventDefault();
    const input = document.getElementById("cpAskInput");
    const send = document.getElementById("cpAskSend");
    const question = input?.value.trim();
    if (!question || send?.disabled) return;
    addAskMessage("user", `<p>${escapeHtml(question)}</p>`);
    input.value = "";
    send.disabled = true;
    send.textContent = "Checking…";
    const pending = addAskMessage("assistant", `<div class="cp-ask-thinking">Calculating from governed activity…</div>`);
    const history = readAskStorage("history", []);
    const context = readAskStorage("context", null);
    try {
      let response = await postGlobalAsk(normalizedAskPayload(question, history, context));
      // Old browser sessions can contain conversation state from a previous
      // response contract. A valid question must not fail because that optional
      // context is stale, so retry once with the clean reporting scope only.
      if (response.status === 422) {
        clearAskStorage();
        response = await postGlobalAsk(normalizedAskPayload(question, [], null, false));
      }
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      const data = await response.json();
      pending.innerHTML = renderGlobalAnswer(data);
      bindGlobalAskDrills(pending);
      history.push(
        { role: "user", content: question },
        { role: "assistant", content: data.answer || "", data },
      );
      writeAskStorage("history", history.slice(-12));
      writeAskStorage("context", data.conversation_context || context);
    } catch (error) {
      const statusMessage = /503/.test(error.message || "")
        ? "The analytics service is temporarily unavailable. No answer was generated."
        : error.message || "CostPilot could not safely calculate this answer.";
      pending.innerHTML = `<div class="cp-ask-error"><strong>CostPilot did not generate an answer.</strong><span>${escapeHtml(statusMessage)}</span><small>Your question and existing data were not changed.</small></div>`;
    } finally {
      send.disabled = false;
      send.textContent = "Ask";
      input.focus();
    }
  }

  function openAskDrill(scopeOrName, filterValue) {
    const scope = normalizeGlobalAskDrillScope(scopeOrName, filterValue);
    if (scope.audit_event_id) {
      closeAskCostPilot();
      writeSession("cp_audit_pending_event", scope.audit_event_id);
      location.href = "/operate.html#audit";
      return;
    }
    if (typeof window.drillFromAskCostPilot === "function") {
      closeAskCostPilot();
      window.drillFromAskCostPilot(scope);
      return;
    }
    writeSession("cp_ask_pending_drill", { scope });
    location.href = globalAskDrillUrl(scope);
  }

  function closeMenus(nav) {
    nav.classList.remove("mobile-open");
    nav.querySelector(".cp-global-nav__mobile-trigger")?.setAttribute("aria-expanded", "false");
    nav.querySelectorAll("[data-cp-dropdown]").forEach((dropdown) => {
      dropdown.classList.remove("open");
      dropdown.querySelector(":scope > button")?.setAttribute("aria-expanded", "false");
    });
  }

  async function updateStatus(nav) {
    const status = nav.querySelector("#cpGlobalStatus");
    const label = status.querySelector(".cp-global-nav__status-label");
    try {
      const response = await fetch("/health", { cache: "no-store" });
      if (!response.ok) throw new Error("Health check failed");
      status.classList.add("online");
      status.classList.remove("offline");
      label.textContent = "Online";
      status.title = "CostPilot is online — open live monitor";
    } catch (_error) {
      status.classList.add("offline");
      status.classList.remove("online");
      label.textContent = "Offline";
      status.title = "CostPilot health check failed — open live monitor";
    }
  }

  function initialize() {
    installAskCostPilot();
    buildNavigation();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize);
  } else {
    initialize();
  }
})();
