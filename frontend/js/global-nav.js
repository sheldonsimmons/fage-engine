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

    document.addEventListener("click", () => closeMenus(nav));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenus(nav);
    });

    updateStatus(nav);
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildNavigation);
  } else {
    buildNavigation();
  }
})();
