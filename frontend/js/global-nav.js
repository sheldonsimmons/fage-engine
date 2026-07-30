(function () {
  "use strict";

  const primaryItems = [
    { label: "Executive", href: "/index.html", paths: ["/", "/index.html"] },
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

    document.addEventListener("click", () => closeMenus(nav));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenus(nav);
    });

    updateStatus(nav);
  }

  const ASK_HISTORY_KEY = "cp_ask_costpilot_history";
  const ASK_CONTEXT_KEY = "cp_ask_costpilot_context";

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
            <p>Answers calculated from your governed AI activity.</p>
          </div>
          <button type="button" class="cp-ask-close" id="cpAskClose" aria-label="Close Ask CostPilot">×</button>
        </header>
        <div class="cp-ask-suggestions" aria-label="Suggested questions">
          <button type="button">Who used the most tokens last week?</button>
          <button type="button">Where is our AI spend going?</button>
          <button type="button">How many tokens did pruning remove?</button>
          <button type="button">Show live versus simulator usage.</button>
        </div>
        <div class="cp-ask-messages" id="cpAskMessages" aria-live="polite">
          <div class="cp-ask-welcome">
            <strong>What would you like to know?</strong>
            <span>Ask about people, agents, departments, business contexts, models, spend, tokens, pruning, or risk.</span>
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
    document.getElementById("cpAskForm").addEventListener("submit", submitGlobalAsk);
    document.querySelectorAll(".cp-ask-suggestions button").forEach((button) => {
      button.addEventListener("click", () => {
        document.getElementById("cpAskInput").value = button.textContent.trim();
        submitGlobalAsk();
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAskCostPilot();
      if ((event.metaKey || event.ctrlKey) && event.key === "/") {
        event.preventDefault();
        openAskCostPilot();
      }
    });
  }

  function openAskCostPilot() {
    installAskCostPilot();
    const drawer = document.getElementById("cpAskDrawer");
    const backdrop = document.getElementById("cpAskBackdrop");
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.hidden = false;
    document.body.classList.add("cp-ask-open");
    setTimeout(() => document.getElementById("cpAskInput")?.focus(), 30);
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
    const pageScope = typeof window.getCostPilotAskScope === "function"
      ? window.getCostPilotAskScope()
      : {};
    return {
      days: 30,
      workspace_id: localStorage.getItem("cp_workspace_id") || null,
      ...pageScope,
    };
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

  function renderGlobalEvidence(item) {
    const drill = item.filter_name && item.filter_value !== null && item.filter_value !== undefined
      ? `<button type="button" data-ask-filter="${escapeHtml(item.filter_name)}"
          data-ask-value="${escapeHtml(String(item.filter_value))}">View activity →</button>`
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
    }[provenance.scope] || "Governed activity";
    const evidence = (data.evidence || []).map(renderGlobalEvidence).join("");
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
    return `<article class="cp-ask-answer">
      <div class="cp-ask-answer-head"><span>${escapeHtml(provenance.period_label || "Selected period")}</span><b>${escapeHtml(sourceLabel)} · Calculated</b></div>
      <h3>${escapeHtml(data.title || "CostPilot answer")}</h3>
      <p>${escapeHtml(data.answer || "No answer was returned.")}</p>
      ${activeFilters ? `<div class="cp-ask-active-filters"><strong>Active filters</strong>${activeFilters}</div>` : ""}
      ${evidence ? `<section><h4>Evidence</h4>${evidence}</section>` : ""}
      ${calculation}
      ${recommendations}
      <small>${escapeHtml(data.measurement_note || "Calculated from governed CostPilot activity.")}</small>
    </article>`;
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
    const history = readSession(ASK_HISTORY_KEY, []);
    const context = readSession(ASK_CONTEXT_KEY, null);
    try {
      const response = await fetch("/api/reports/bot-efficiency/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...askScope(),
          question,
          conversation: history.slice(-8),
          context,
        }),
      });
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      const data = await response.json();
      pending.innerHTML = renderGlobalAnswer(data);
      pending.querySelectorAll("[data-ask-filter]").forEach((button) => {
        button.addEventListener("click", () => openAskDrill(button.dataset.askFilter, button.dataset.askValue));
      });
      history.push(
        { role: "user", content: question },
        { role: "assistant", content: data.answer || "" },
      );
      writeSession(ASK_HISTORY_KEY, history.slice(-8));
      writeSession(ASK_CONTEXT_KEY, data.conversation_context || context);
    } catch (error) {
      pending.innerHTML = `<div class="cp-ask-error"><strong>I couldn't calculate that answer.</strong><span>${escapeHtml(error.message || "Please try again.")}</span></div>`;
    } finally {
      send.disabled = false;
      send.textContent = "Ask";
      input.focus();
    }
  }

  function openAskDrill(filterName, filterValue) {
    if (typeof window.drillFromAskCostPilot === "function") {
      closeAskCostPilot();
      window.drillFromAskCostPilot(filterName, filterValue);
      return;
    }
    writeSession("cp_ask_pending_drill", { filterName, filterValue });
    location.href = "/reports.html?tab=contexts";
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
