(function () {
  "use strict";

  const primaryItems = [
    { label: "Overview", href: "/index.html", paths: ["/", "/index.html"] },
    { label: "Operate", href: "/operate.html", paths: ["/operate.html"] },
    { label: "Business Profiles", href: "/business-profile.html", paths: ["/business-profile.html"] },
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
    { label: "Live Demo", href: "/demo-crm.html" },
    { label: "Live Monitor", href: "/live-landing.html" },
  ];

  // Fallback only — used if GET /api/workspaces fails or hasn't resolved
  // yet. The real list now comes from the workspaces table (Phase 1); this
  // hardcoded array used to be the *only* source, which meant any
  // workspace not listed here was invisible and unreachable the moment a
  // user touched the switcher.
  const fallbackWorkspaceOptions = [
    { id: "4BE43240A6674314", label: "Production", kind: "Live workspace" },
    { id: "SIM-HISTORICAL-2Y", label: "Simulated", kind: "Simulated history" },
  ];
  let dynamicWorkspaceOptions = null;
  const workspaceOptionsList = () => dynamicWorkspaceOptions || fallbackWorkspaceOptions;

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
    const options = workspaceOptionsList();
    const id = localStorage.getItem("cp_workspace_id") || options[0].id;
    return options.find((workspace) => workspace.id === id)
      || { id, label: localStorage.getItem("cp_workspace_name") || "Current workspace", kind: "Workspace" };
  }

  function workspaceMarkup() {
    const active = activeWorkspace();
    const base = workspaceOptionsList();
    const options = base.some((workspace) => workspace.id === active.id)
      ? base
      : [active, ...base];
    return `
      <label class="cp-workspace-switcher" title="Choose which isolated workspace CostPilot should display">
        <span class="cp-workspace-switcher__label">Workspace</span>
        <select id="cpWorkspaceSwitcher" aria-label="Active CostPilot workspace">
          ${options.map((workspace) => `<option value="${escapeHtml(workspace.id)}"${workspace.id === active.id ? " selected" : ""}>${escapeHtml(workspace.label)}</option>`).join("")}
        </select>
      </label>`;
  }

  function accountMarkup() {
    // Security architecture assessment, Phase 1: a real login now exists
    // (login.html), but no route requires a session yet -- this is a
    // visibility/entry-point affordance only, not an access-control UI.
    const token = localStorage.getItem("cp_auth_token");
    const email = localStorage.getItem("cp_user_email");
    if (token && email) {
      return `
        <span class="cp-global-nav__account" id="cpAccountEmail" title="Signed in as ${escapeHtml(email)}">${escapeHtml(email)}</span>
        <button class="cp-global-nav__help" id="cpSignOut" type="button" title="Sign out">Sign out</button>`;
    }
    return `<a class="cp-global-nav__help" href="/login.html" title="Sign in">Sign in</a>`;
  }

  function bindAccountControls(nav) {
    const signOut = nav.querySelector("#cpSignOut");
    if (!signOut) return;
    signOut.addEventListener("click", async () => {
      try {
        await fetch("/api/auth/logout", {
          method: "POST",
          headers: { "Authorization": `Bearer ${localStorage.getItem("cp_auth_token") || ""}` },
        });
      } catch (_err) { /* revoke best-effort -- clear local state regardless */ }
      localStorage.removeItem("cp_auth_token");
      location.assign("/login.html");
    });
  }

  async function refreshWorkspaceOptions() {
    try {
      const response = await fetch("/api/workspaces");
      if (!response.ok) return;
      const data = await response.json();
      const fetched = (data.workspaces || []).map((w) => ({
        id: w.workspace_id,
        label: w.name,
        kind: w.workspace_type === "production" ? "Live workspace" : w.workspace_type,
      }));
      if (!fetched.length) return;
      dynamicWorkspaceOptions = fetched;
      const select = document.getElementById("cpWorkspaceSwitcher");
      if (!select) return;
      const active = activeWorkspace();
      const options = fetched.some((w) => w.id === active.id) ? fetched : [active, ...fetched];
      select.innerHTML = options.map((workspace) =>
        `<option value="${escapeHtml(workspace.id)}"${workspace.id === active.id ? " selected" : ""}>${escapeHtml(workspace.label)}</option>`
      ).join("");
    } catch (_err) {
      // Fall back silently to fallbackWorkspaceOptions — never a hard dependency.
    }
  }

  function switchWorkspace(workspaceId) {
    const selected = workspaceOptionsList().find((workspace) => workspace.id === workspaceId);
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
    if (!header) {
      // Safety net for global-nav.css's default-hide rule: if this page's
      // header markup doesn't match any known selector, the legacy nav
      // would otherwise stay hidden forever with nothing to replace it.
      document.body.classList.add("cp-global-nav-unavailable");
      return;
    }
    if (header.querySelector(".cp-global-nav")) return;

    const existingNavigation = findExistingNavigation(header);
    if (existingNavigation) existingNavigation.classList.add("cp-global-nav-source");
    header.querySelectorAll(".header-status").forEach((status) => status.classList.add("cp-global-nav-source"));
    const legacyStatusLabel = header.querySelector("#statusLabel");
    if (legacyStatusLabel?.parentElement) legacyStatusLabel.parentElement.classList.add("cp-global-nav-source");
    // The "AI Cost Control · CRM · Support · Engineering · Finance" tagline
    // sits next to the logo in .header-brand, a sibling of the nav-links
    // wrapper above -- never hidden by that selector, so it stayed visible
    // and collided with this injected nav bar (confirmed live: "Overview"
    // rendering on top of "Engineering · Finance" on admin.html and every
    // other page sharing this header markup). The real global nav's
    // workspace switcher already conveys equivalent context, so hiding
    // this redundant tagline is safe, not just a visual patch.
    header.querySelectorAll(".header-sub").forEach((sub) => sub.classList.add("cp-global-nav-source"));

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
        ${accountMarkup()}
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
    bindAccountControls(nav);
    refreshWorkspaceOptions();

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
        <div class="cp-ask-resize-handle" id="cpAskResizeHandle" role="separator" aria-orientation="vertical" aria-label="Resize Ask CostPilot panel"></div>
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
        <div class="cp-ask-voice-status" id="cpAskVoiceStatus" hidden></div>
        <label class="cp-ask-wake-toggle" id="cpAskWakeToggleLabel" title="Chrome only">
          <input type="checkbox" id="cpWakeToggle">
          <span>"Hey CostPilot" voice trigger</span>
        </label>
        <p class="cp-ask-wake-disclosure" id="cpAskWakeDisclosure">
          When on, your browser continuously listens for "hey CostPilot." While listening, audio is sent to your
          browser's built-in speech recognition service (e.g. Google, in Chrome) — not to CostPilot — until you turn
          this off. Works best in Chrome.
        </p>
        <form class="cp-ask-composer" id="cpAskForm">
          <label class="cp-sr-only" for="cpAskInput">Ask CostPilot a question</label>
          <textarea id="cpAskInput" rows="2" maxlength="500"
            placeholder="Ask about AI spend, usage, pruning, people, agents, or accounts…"></textarea>
          <button type="button" class="cp-ask-mic" id="cpAskMic" aria-label="Ask by voice" title="Ask by voice">🎤</button>
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
      if (followUp) {
        document.getElementById("cpAskInput").value = followUp.dataset.askQuestion;
        submitGlobalAsk();
        return;
      }
      const speakBtn = event.target.closest("[data-ask-speak]");
      if (speakBtn) speakAskAnswer(speakBtn.dataset.askSpeak, speakBtn);
    });
    document.getElementById("cpAskMic").addEventListener("click", toggleAskVoiceRecording);
    const wakeToggle = document.getElementById("cpWakeToggle");
    if (!wakeWordSupported()) {
      wakeToggle.disabled = true;
      document.getElementById("cpAskWakeToggleLabel").classList.add("cp-ask-wake-toggle--unsupported");
      document.getElementById("cpAskWakeDisclosure").textContent = "Voice wake word isn't supported in this browser — try Chrome.";
    } else {
      wakeToggle.checked = readAskStorage("wake_enabled", false);
      wakeToggle.addEventListener("change", () => setWakeWordEnabled(wakeToggle.checked));
    }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAskCostPilot();
      if ((event.metaKey || event.ctrlKey) && event.key === "/") {
        event.preventDefault();
        openAskCostPilot();
      }
    });
    installAskCostPilotResize();
    restoreGlobalAskConversation();
  }

  const ASK_DRAWER_MIN_WIDTH = 360;
  const ASK_DRAWER_MAX_WIDTH = 960;

  function installAskCostPilotResize() {
    const drawer = document.getElementById("cpAskDrawer");
    const handle = document.getElementById("cpAskResizeHandle");
    if (!drawer || !handle) return;

    const savedWidth = Number(readAskStorage("panel_width", 0));
    if (savedWidth) {
      drawer.style.width = `${Math.min(ASK_DRAWER_MAX_WIDTH, Math.max(ASK_DRAWER_MIN_WIDTH, savedWidth))}px`;
    }

    let dragging = false;

    function pointerX(event) {
      return event.touches && event.touches.length ? event.touches[0].clientX : event.clientX;
    }

    function onMove(event) {
      if (!dragging) return;
      const newWidth = Math.min(
        ASK_DRAWER_MAX_WIDTH,
        Math.max(ASK_DRAWER_MIN_WIDTH, window.innerWidth - pointerX(event)),
      );
      drawer.style.width = `${newWidth}px`;
      event.preventDefault();
    }

    function onEnd() {
      if (!dragging) return;
      dragging = false;
      drawer.classList.remove("cp-ask-resizing");
      document.body.style.userSelect = "";
      writeAskStorage("panel_width", Math.round(drawer.getBoundingClientRect().width));
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onEnd);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onEnd);
    }

    function onStart(event) {
      dragging = true;
      drawer.classList.add("cp-ask-resizing");
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onEnd);
      window.addEventListener("touchmove", onMove, { passive: false });
      window.addEventListener("touchend", onEnd);
      event.preventDefault();
    }

    handle.addEventListener("mousedown", onStart);
    handle.addEventListener("touchstart", onStart, { passive: false });
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
    // Never leave a mic hot or audio playing behind a closed drawer.
    // Deliberately does NOT stop wake-word listening -- the whole point
    // of the wake word is re-engaging Ask CostPilot hands-free, so it
    // stays armed across drawer open/close and only stops via its own
    // toggle or the tab being backgrounded.
    if (typeof _askRecording !== "undefined" && _askRecording) _askMediaRecorder?.stop();
    if (typeof stopAskSpeaking === "function") stopAskSpeaking();
    resumeWakeWordListenerIfEnabled();
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

  function normalizedAskPayload(question, history, context, includeConversation = true, voiceMeta = null) {
    const scope = askScope() || {};
    const daysValue = Number(scope.days);
    const payload = {
      question,
      days: Number.isFinite(daysValue) ? Math.max(1, Math.min(365, Math.round(daysValue))) : 30,
      timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      screen_context: currentAskScreenContext(),
    };
    // CostPilot Voice (Phase 1): purely observational tagging -- Ask
    // CostPilot's answer pipeline is identical either way (see
    // backend/api/routes_ask_voice.py's docstring). modality defaults to
    // "text" server-side when omitted, so a typed question sends nothing
    // extra here.
    if (voiceMeta && voiceMeta.modality === "voice") {
      payload.modality = "voice";
      if (typeof voiceMeta.confidence === "number") payload.transcription_confidence = voiceMeta.confidence;
    }
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

  // Shared with the Overview page's inline Ask box — see
  // js/ask-costpilot-render.js for renderAskAnswerCard/askDrillUrl/etc.,
  // so the two surfaces render identical answer cards from one place.

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
          item.data ? renderAskAnswerCard(item.data) : `<p>${escapeHtml(item.content)}</p>`,
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

  // ── CostPilot Voice (Phase 1) ──────────────────────────────────────────
  // Speech-to-text/text-to-speech for Ask CostPilot -- NOT Voice Guard (a
  // completely different, PII-redaction feature; see
  // backend/api/routes_ask_voice.py's own docstring on why the two names
  // are easy to conflate). A voice question is sent through this exact
  // same submitGlobalAsk()/postGlobalAsk() path as a typed one, tagged
  // modality="voice" -- Ask CostPilot itself never knows or cares how the
  // question arrived. Phase 1 is push-to-talk (click to start, click to
  // stop), not continuous listening, per the feasibility assessment's
  // privacy recommendation. Phase 2 (below) adds an opt-in, off-by-default
  // "Hey CostPilot" wake word -- the one continuous-listening exception,
  // gated entirely behind the user explicitly turning it on.

  let _askMediaRecorder = null;
  let _askAudioChunks = [];
  let _askRecording = false;
  let _askPendingVoiceMeta = null; // {modality:"voice", confidence} for the next submit only
  let _askCurrentAudio = null;

  // "Hey CostPilot" wake word (opt-in, off by default) -- automates
  // starting the exact same push-to-talk capture above; it never bypasses
  // Whisper transcription, PII redaction, confidence gating, or the
  // manual "Ask" confirm step. Uses the browser's own SpeechRecognition
  // purely to detect the trigger phrase locally in the tab; in Chrome
  // that API streams audio to Google's speech servers while armed (never
  // to CostPilot) -- disclosed to the user in the drawer's toggle copy.
  let _wakeRecognition = null;
  let _wakeEnabled = false;
  let _wakeListening = false;
  let _wakeSuspended = false;
  let _wakeStopRequested = false;
  let _wakeTriggerInFlight = false;
  let _wakeVisibilityBound = false;
  let _wakeAutoStopArmed = false;
  let _wakeAutoSubmitArmed = false;

  function wakeWordSupported() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  function getWakeRecognition() {
    if (_wakeRecognition) return _wakeRecognition;
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new Ctor();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    recognition.onresult = handleWakeResult;
    recognition.onerror = handleWakeError;
    recognition.onend = handleWakeEnd;
    _wakeRecognition = recognition;
    return recognition;
  }

  function startWakeWordListener() {
    if (!_wakeEnabled || _wakeListening || _askRecording || _wakeSuspended || document.hidden || !wakeWordSupported()) return;
    _wakeStopRequested = false;
    try {
      getWakeRecognition().start();
      _wakeListening = true;
      document.getElementById("cpAskMic")?.classList.add("wake-armed");
    } catch (_error) {
      // start() throws if already started -- state flags above make this rare.
    }
  }

  function stopWakeWordListener() {
    if (!_wakeListening) return;
    _wakeStopRequested = true;
    try { _wakeRecognition?.stop(); } catch (_error) {}
    _wakeListening = false;
    document.getElementById("cpAskMic")?.classList.remove("wake-armed");
  }

  function pauseWakeWordListener() {
    _wakeSuspended = true;
    stopWakeWordListener();
  }

  function resumeWakeWordListenerIfEnabled() {
    _wakeSuspended = false;
    startWakeWordListener();
  }

  function setWakeWordEnabled(enabled) {
    _wakeEnabled = enabled;
    writeAskStorage("wake_enabled", enabled);
    const toggle = document.getElementById("cpWakeToggle");
    if (toggle) toggle.checked = enabled;
    if (enabled) {
      startWakeWordListener();
      askVoiceStatus("Wake-word listening is on.", "ok");
    } else {
      stopWakeWordListener();
      askVoiceStatus("Wake-word listening is off.", "ok");
    }
  }

  function handleWakeResult(event) {
    if (_askRecording || _wakeTriggerInFlight) return;
    // Scan every result accumulated so far this listening session, not
    // just the delta since the last event (event.resultIndex onward) --
    // Chrome often finalizes "Hey" and "CostPilot" as two separate result
    // chunks, so the phrase only ever appears whole across the full
    // transcript, never in a single new chunk on its own.
    let transcript = "";
    for (let i = 0; i < event.results.length; i++) {
      transcript += event.results[i][0].transcript;
    }
    if (window.location.search.includes("wakeDebug")) {
      console.debug("[wake]", JSON.stringify(transcript));
    }
    // Deliberately tolerant: Chrome's speech recognizer doesn't have
    // "CostPilot" as a known word and often mishears or mis-segments it
    // (e.g. "cost pilot", "cost-pilot", a stray filler word after "hey").
    // A tight exact-phrase match was missing real, audible attempts.
    if (!/\bhey\b[\s,]{0,15}cost[\s-]{0,3}pilot\b/i.test(transcript)) return;
    _wakeTriggerInFlight = true;
    _wakeAutoStopArmed = true;
    _wakeAutoSubmitArmed = true;
    pauseWakeWordListener();
    openAskCostPilot();
    askVoiceStatus("Heard “Hey CostPilot” — listening for your question…", "listening");
    toggleAskVoiceRecording().finally(() => { _wakeTriggerInFlight = false; });
  }

  // Push-to-talk (manual mic click) intentionally requires a second click
  // to stop -- that's an existing, documented UX choice. A wake-triggered
  // capture has no second click coming, so it needs its own end-of-speech
  // signal: a lightweight volume-based silence detector on the same
  // stream, armed only for wake-triggered captures via _wakeAutoStopArmed.
  function armWakeSilenceAutoStop(stream) {
    let audioCtx;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (_error) {
      return;
    }
    const SPEECH_RMS_THRESHOLD = 12;
    const SILENCE_MS_TO_STOP = 1000;
    const MAX_CAPTURE_MS = 12000;
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    const source = audioCtx.createMediaStreamSource(stream);
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    const startedAt = Date.now();
    let heardSpeech = false;
    let silenceStartedAt = null;
    const cleanup = () => {
      clearInterval(timer);
      try { source.disconnect(); } catch (_error) {}
      try { audioCtx.close(); } catch (_error) {}
    };
    const timer = setInterval(() => {
      if (!_askRecording) { cleanup(); return; }
      analyser.getByteTimeDomainData(data);
      let sumSquares = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sumSquares += v * v;
      }
      const rms = Math.sqrt(sumSquares / data.length) * 100;
      if (rms > SPEECH_RMS_THRESHOLD) {
        heardSpeech = true;
        silenceStartedAt = null;
      } else if (heardSpeech) {
        if (silenceStartedAt === null) silenceStartedAt = Date.now();
        if (Date.now() - silenceStartedAt > SILENCE_MS_TO_STOP) {
          cleanup();
          _askMediaRecorder?.stop();
          return;
        }
      }
      if (Date.now() - startedAt > MAX_CAPTURE_MS) {
        cleanup();
        _askMediaRecorder?.stop();
      }
    }, 200);
  }

  function handleWakeError(event) {
    switch (event.error) {
      case "not-allowed":
      case "permission-denied":
        askVoiceStatus("Mic access is blocked — allow microphone access in your browser to use the wake word.", "error");
        setWakeWordEnabled(false);
        break;
      case "audio-capture":
        askVoiceStatus("No microphone was found — the wake word can't listen without one.", "error");
        setWakeWordEnabled(false);
        break;
      case "network":
        askVoiceStatus("Wake-word listening lost its connection and stopped — turn it back on to retry.", "warn");
        setWakeWordEnabled(false);
        break;
      default:
        // "no-speech" and "aborted" fire routinely during idle listening
        // and intentional stop() calls -- no user-facing message needed.
        break;
    }
  }

  function handleWakeEnd() {
    _wakeListening = false;
    document.getElementById("cpAskMic")?.classList.remove("wake-armed");
    if (_wakeEnabled && !_wakeSuspended && !document.hidden && !_wakeStopRequested) {
      // Chrome periodically stops a "continuous" recognizer after
      // silence -- restart it so listening stays effectively continuous.
      setTimeout(startWakeWordListener, 250);
    }
  }

  function askVoiceStatus(text, tone) {
    const el = document.getElementById("cpAskVoiceStatus");
    if (!el) return;
    if (!text) { el.hidden = true; el.textContent = ""; return; }
    el.hidden = false;
    el.textContent = text;
    el.className = `cp-ask-voice-status${tone ? " cp-ask-voice-status--" + tone : ""}`;
  }

  async function toggleAskVoiceRecording() {
    pauseWakeWordListener();
    if (_askRecording) {
      _askMediaRecorder?.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      _wakeAutoStopArmed = false;
      _wakeAutoSubmitArmed = false;
      askVoiceStatus("Voice isn't supported in this browser — try typing instead.", "error");
      resumeWakeWordListenerIfEnabled();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      _askAudioChunks = [];
      _askMediaRecorder = new MediaRecorder(stream);
      _askMediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) _askAudioChunks.push(e.data); };
      _askMediaRecorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        _askRecording = false;
        document.getElementById("cpAskMic")?.classList.remove("recording");
        transcribeAskRecording().finally(resumeWakeWordListenerIfEnabled);
      };
      _askMediaRecorder.start();
      _askRecording = true;
      document.getElementById("cpAskMic")?.classList.add("recording");
      if (_wakeAutoStopArmed) {
        _wakeAutoStopArmed = false;
        armWakeSilenceAutoStop(stream);
        askVoiceStatus("Listening for your question…", "listening");
      } else {
        askVoiceStatus("Listening… click the mic again to stop.", "listening");
      }
    } catch (err) {
      _wakeAutoStopArmed = false;
      _wakeAutoSubmitArmed = false;
      askVoiceStatus("Couldn't access your microphone — check your browser permissions.", "error");
      resumeWakeWordListenerIfEnabled();
    }
  }

  async function transcribeAskRecording() {
    if (!_askAudioChunks.length) { askVoiceStatus(""); return; }
    askVoiceStatus("Transcribing…", "listening");
    const blob = new Blob(_askAudioChunks, { type: _askMediaRecorder?.mimeType || "audio/webm" });
    const form = new FormData();
    form.append("audio", blob, "question.webm");
    try {
      const res = await fetch("/api/ask-voice/transcribe", { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not transcribe that clip.");
      const input = document.getElementById("cpAskInput");
      if (input) { input.value = data.transcript || ""; input.focus(); }
      const confidence = typeof data.confidence === "number" ? data.confidence : null;
      _askPendingVoiceMeta = { modality: "voice", confidence };
      const lowConfidence = confidence !== null && confidence < 0.5;
      const autoSubmit = _wakeAutoSubmitArmed && !lowConfidence && input?.value.trim();
      _wakeAutoSubmitArmed = false;
      if (autoSubmit) {
        submitGlobalAsk();
      } else {
        askVoiceStatus(
          lowConfidence
            ? "Not fully sure I caught that — check the text below before sending."
            : "Review your question, then hit Ask.",
          lowConfidence ? "warn" : "ok",
        );
      }
    } catch (err) {
      _wakeAutoSubmitArmed = false;
      _askPendingVoiceMeta = null;
      askVoiceStatus(err.message || "Could not transcribe that clip. Try typing instead.", "error");
    }
  }

  async function speakAskAnswer(text, triggerButton) {
    if (!text) return;
    if (window.location.search.includes("wakeDebug")) {
      console.debug("[speak] text sent to TTS:", JSON.stringify(text));
    }
    // The wake engine must not be armed while CostPilot's own voice is
    // playing -- the answer can contain "CostPilot" and misfire itself.
    // Paused before stopAskSpeaking() (which resumes it) so the net
    // effect of replacing an in-progress answer is "stay paused," not a
    // spurious resume-then-immediately-pause.
    pauseWakeWordListener();
    stopAskSpeaking();
    if (triggerButton) { triggerButton.disabled = true; triggerButton.textContent = "…"; }
    try {
      const res = await fetch("/api/ask-voice/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error("Speech unavailable");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      _askCurrentAudio = new Audio(url);
      _askCurrentAudio.onended = () => {
        askVoiceStatus("");
        renderAskStopSpeakingControl(false);
        resumeWakeWordListenerIfEnabled();
      };
      askVoiceStatus("Speaking…", "listening");
      renderAskStopSpeakingControl(true);
      await _askCurrentAudio.play();
    } catch (err) {
      askVoiceStatus("Couldn't play that answer aloud.", "error");
      resumeWakeWordListenerIfEnabled();
    } finally {
      if (triggerButton) { triggerButton.disabled = false; triggerButton.textContent = "🔊"; }
    }
  }

  function stopAskSpeaking() {
    if (_askCurrentAudio) {
      _askCurrentAudio.pause();
      _askCurrentAudio = null;
    }
    askVoiceStatus("");
    renderAskStopSpeakingControl(false);
  }

  function renderAskStopSpeakingControl(show) {
    let btn = document.getElementById("cpAskStopSpeaking");
    if (show) {
      if (!btn) {
        btn = document.createElement("button");
        btn.type = "button";
        btn.id = "cpAskStopSpeaking";
        btn.className = "cp-ask-stop-speaking";
        btn.textContent = "⏹ Stop Speaking";
        btn.addEventListener("click", () => { stopAskSpeaking(); resumeWakeWordListenerIfEnabled(); });
        document.getElementById("cpAskVoiceStatus")?.after(btn);
      }
      btn.hidden = false;
    } else if (btn) {
      btn.hidden = true;
    }
  }

  async function submitGlobalAsk(event) {
    event?.preventDefault();
    const input = document.getElementById("cpAskInput");
    const send = document.getElementById("cpAskSend");
    const question = input?.value.trim();
    if (!question || send?.disabled) return;
    // Captured once per submit, then cleared -- a voice-originated
    // question only stays "voice" for the send that follows recording;
    // any later edit/re-ask from the same textarea is typed again.
    const voiceMeta = _askPendingVoiceMeta;
    _askPendingVoiceMeta = null;
    addAskMessage("user", `<p>${escapeHtml(question)}</p>`);
    input.value = "";
    send.disabled = true;
    send.textContent = "Checking…";
    const pending = addAskMessage("assistant", `<div class="cp-ask-thinking">Calculating from governed activity…</div>`);
    const history = readAskStorage("history", []);
    const context = readAskStorage("context", null);
    try {
      let response = await postGlobalAsk(normalizedAskPayload(question, history, context, true, voiceMeta));
      // Old browser sessions can contain conversation state from a previous
      // response contract. A valid question must not fail because that optional
      // context is stale, so retry once with the clean reporting scope only.
      if (response.status === 422) {
        clearAskStorage();
        response = await postGlobalAsk(normalizedAskPayload(question, [], null, false, voiceMeta));
      }
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      const data = await response.json();
      pending.innerHTML = renderAskAnswerCard(data);
      bindGlobalAskDrills(pending);
      appendAskSpeakControl(pending, data.answer || "");
      history.push(
        { role: "user", content: question },
        { role: "assistant", content: data.answer || "", data },
      );
      writeAskStorage("history", history.slice(-12));
      writeAskStorage("context", data.conversation_context || context);
      // Voice in, voice back out -- a typed question never auto-plays
      // audio (the written answer stays the primary surface per the
      // Voice feasibility assessment's UI requirement); a spoken one does,
      // since the user was talking to CostPilot, not reading it.
      if (voiceMeta && voiceMeta.modality === "voice" && data.answer) {
        speakAskAnswer(data.answer, pending.querySelector("[data-ask-speak]"));
      }
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

  function appendAskSpeakControl(messageNode, answerText) {
    if (!answerText || !messageNode) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cp-ask-speak-btn";
    btn.textContent = "🔊";
    btn.title = "Hear this answer";
    btn.setAttribute("data-ask-speak", answerText);
    messageNode.appendChild(btn);
  }

  function openAskDrill(scopeOrName, filterValue) {
    const scope = normalizeAskRenderDrillScope(scopeOrName, filterValue);
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
    location.href = askDrillUrl(scope);
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
    // The legacy header nav is hidden by default via CSS now (see
    // global-nav.css), not just once this JS replaces it -- so if
    // anything in here throws before buildNavigation() runs (or
    // buildNavigation itself can't find a header), the fallback class
    // must still get applied, or the page is left with no visible nav
    // at all instead of just the old FOUC flash this was meant to fix.
    try {
      installAskCostPilot();
      buildNavigation();
      if (wakeWordSupported()) {
        _wakeEnabled = readAskStorage("wake_enabled", false);
        if (_wakeEnabled) startWakeWordListener();
        if (!_wakeVisibilityBound) {
          _wakeVisibilityBound = true;
          document.addEventListener("visibilitychange", () => {
            if (document.hidden) stopWakeWordListener();
            else resumeWakeWordListenerIfEnabled();
          });
        }
      }
    } catch (_error) {
      document.body.classList.add("cp-global-nav-unavailable");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize);
  } else {
    initialize();
  }
})();
