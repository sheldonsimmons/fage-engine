/**
 * mobile-guard.js
 * Injects the mobile/tablet overlay on dashboard pages.
 * Include this script on any page that requires desktop.
 * Pages designed for mobile (savings, live-landing) should NOT include this.
 */
(function () {
  const el = document.createElement("div");
  el.id = "mobile-guard";
  el.innerHTML = `
    <div class="mg-logo">◈ Cost<span>Pilot</span></div>
    <div class="mg-icon">🖥</div>
    <div class="mg-title">Desktop required</div>
    <div class="mg-sub">
      CostPilot's dashboard is designed for desktop and laptop screens.
      Open it on a larger device for the full experience.
    </div>
    <div class="mg-links">
      <a href="/live-landing.html" class="mg-btn-primary">📊 View Executive Summary</a>
      <a href="/savings.html" class="mg-btn-secondary">💰 Calculate My Savings</a>
    </div>
    <div class="mg-rotate">↻ Or rotate your tablet to landscape</div>
  `;
  document.body.appendChild(el);
})();
