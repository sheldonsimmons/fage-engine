(function () {
  "use strict";

  // Vertical left-nav sidebar -- first page on this pattern is
  // business-profile.html (see global-nav.js for the horizontal top-nav
  // pattern still used everywhere else). Deliberately smaller in scope
  // than global-nav.js: no Manage/Tools dropdowns, no mobile drawer yet --
  // just the items that map to a real page in this app. "Alerts" is
  // omitted rather than pointed at something that isn't actually an
  // alerts feature -- there's no alerting page in this app yet. There is
  // also no separate "Dashboards" item: workspace.html looked like a fit
  // at first but is actually a trial-onboarding-only page (its own
  // workspace-stats call 404s for any non-trial workspace) -- Overview
  // and Operate already cover general/live dashboards, so nothing was
  // dropped by removing it.
  const items = [
    { label: "Home", href: "/index.html", icon: "home" },
    { label: "Executive Dashboard", href: "/cockpit/", icon: "dashboard" },
    { label: "AI Activity", href: "/operate.html", icon: "pulse" },
    { label: "Ask CostPilot", href: "#", icon: "ask", id: "cpLeftNavAsk" },
    { label: "Business Profiles", href: "/business-profile.html", icon: "building" },
    { label: "Optimization", href: "/operate.html", icon: "target" },
    { label: "Reports", href: "/reports.html", icon: "doc" },
    { label: "Integrations", href: "/onboarding.html", icon: "plug" },
    { label: "Connectors", href: "/connector-manager.html", icon: "grid" },
    { label: "Settings", href: "/admin.html", icon: "gear" },
  ];

  // All stroke-based (fill="none") for a consistent line-icon look.
  const icons = {
    home: '<path d="M3 9l7-6 7 6v9a1 1 0 0 1-1 1h-4v-6H8v6H4a1 1 0 0 1-1-1V9z"/>',
    dashboard: '<path d="M3 15V9l4-3 4 3v6M3 15h14M3 15V6a1 1 0 0 1 1-1h1M13 15V4a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v11"/>',
    ask: '<path d="M10 2a8 8 0 1 0 4.9 14.3L18 17l-.7-3.1A8 8 0 0 0 10 2z"/><path d="M10 12.5v-.1c0-.9.5-1.4 1.3-1.9.7-.5 1-.8 1-1.4 0-.7-.6-1.1-1.4-1.1-.6 0-1.1.2-1.5.7"/><path d="M10.4 14.6h-.1"/>',
    grid: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="3" width="6" height="6" rx="1"/><rect x="3" y="11" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/>',
    building: '<rect x="4" y="2" width="9" height="16" rx="1"/><path d="M13 7h3v11h-3M7 6h1M7 9h1M7 12h1M10 6h1M10 9h1M10 12h1"/>',
    pulse: '<path d="M2 10h4l2-6 3 12 2-8 1.5 2H18"/>',
    target: '<circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="3.4"/>',
    doc: '<path d="M5 2h7l3 3v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><path d="M12 2v3h3M7 10h6M7 13h6"/>',
    plug: '<path d="M7 3v4M13 3v4M5 7h10v3a5 5 0 0 1-5 5 5 5 0 0 1-5-5V7zM10 15v3"/>',
    gear: '<circle cx="10" cy="10" r="2.6"/><path d="M10 2.5v2M10 15.5v2M17.5 10h-2M4.5 10h-2M15.4 4.6l-1.4 1.4M6 12.6l-1.4 1.4M15.4 15.4l-1.4-1.4M6 7.4L4.6 6"/>',
  };

  function svgIcon(name) {
    const inner = icons[name] || icons.doc;
    return `<svg class="cp-left-nav__icon" width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  }

  const currentPath = location.pathname || "/";

  function linkMarkup(item) {
    const active = item.href === currentPath;
    return `<a href="${item.href}" ${item.id ? `id="${item.id}"` : ""} class="cp-left-nav__link${active ? " active" : ""}"${active ? ' aria-current="page"' : ""}>
      ${svgIcon(item.icon)}<span>${item.label}</span>
    </a>`;
  }

  function userBlockMarkup() {
    const email = localStorage.getItem("cp_user_email") || "";
    const name = email ? email.split("@")[0].replace(/[._]/g, " ") : "Workspace user";
    return `<div class="cp-left-nav__user">
      <div class="cp-left-nav__user-avatar" aria-hidden="true">${(name[0] || "U").toUpperCase()}</div>
      <div class="cp-left-nav__user-meta">
        <div class="cp-left-nav__user-name">${name.replace(/\b\w/g, (c) => c.toUpperCase())}</div>
        ${email ? `<div class="cp-left-nav__user-email">${email}</div>` : ""}
      </div>
    </div>`;
  }

  function workspaceSwitcherMarkup() {
    return `<label class="cp-left-nav__workspace" title="Choose which isolated workspace CostPilot should display">
      <span>Workspace</span>
      <select id="cpLeftNavWorkspace" aria-label="Active CostPilot workspace"><option>Loading…</option></select>
    </label>`;
  }

  async function populateWorkspaceSwitcher() {
    const select = document.getElementById("cpLeftNavWorkspace");
    if (!select) return;
    const fallback = [
      { id: "4BE43240A6674314", label: "Production" },
      { id: "SIM-HISTORICAL-2Y", label: "Simulated" },
    ];
    let options = fallback;
    try {
      const response = await fetch("/api/workspaces");
      if (response.ok) {
        const data = await response.json();
        const fetched = (data.workspaces || []).map((w) => ({ id: w.workspace_id, label: w.name }));
        if (fetched.length) options = fetched;
      }
    } catch (_err) {
      // fall back silently, same as global-nav.js
    }
    const activeId = localStorage.getItem("cp_workspace_id") || options[0].id;
    if (!options.some((o) => o.id === activeId)) {
      options = [{ id: activeId, label: localStorage.getItem("cp_workspace_name") || "Current workspace" }, ...options];
    }
    select.innerHTML = options.map((o) => `<option value="${o.id}"${o.id === activeId ? " selected" : ""}>${o.label}</option>`).join("");
    select.addEventListener("change", (event) => {
      const chosen = options.find((o) => o.id === event.target.value);
      if (!chosen) return;
      localStorage.setItem("cp_workspace_id", chosen.id);
      localStorage.setItem("cp_workspace_name", chosen.label);
      const url = new URL(location.href);
      url.searchParams.delete("workspace_id");
      location.assign(url.href);
    });
  }

  function build() {
    const mount = document.getElementById("cpLeftNavMount");
    if (!mount) return;
    mount.innerHTML = `
      <div class="cp-left-nav__brand">
        <img src="/assets/costpilot-logo-transparent.svg" alt="CostPilot" />
      </div>
      <nav class="cp-left-nav__links" aria-label="Primary navigation">
        ${items.map(linkMarkup).join("")}
      </nav>
      <div class="cp-left-nav__footer">
        ${workspaceSwitcherMarkup()}
        ${userBlockMarkup()}
      </div>`;
    mount.classList.add("cp-left-nav");
    populateWorkspaceSwitcher();

    const askLink = document.getElementById("cpLeftNavAsk");
    askLink?.addEventListener("click", (event) => {
      event.preventDefault();
      // No global drawer-open hook is exposed by global-nav.js, so this
      // focuses the real on-page Ask input rather than pointing at
      // something that doesn't do anything.
      const topBarInput = document.getElementById("bpAskInput");
      if (topBarInput) {
        topBarInput.scrollIntoView({ behavior: "smooth", block: "center" });
        topBarInput.focus();
        return;
      }
      document.getElementById("cpGlobalAsk")?.click();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
