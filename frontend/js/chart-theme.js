/**
 * Shared CostPilot Chart.js theme.
 * Loaded after Chart.js and before page chart configuration. Page-specific
 * options still win, preserving every existing dashboard chart behavior.
 */
(function initializeCostPilotChartTheme(global) {
  "use strict";

  const ChartJS = global.Chart;
  if (!ChartJS) return;

  const css = getComputedStyle(document.documentElement);
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const colors = {
    text: token("--cp-color-text", "#edf2f7"),
    muted: token("--cp-color-text-secondary", "#a7b2c0"),
    grid: token("--cp-color-border", "#293442"),
    canvas: token("--cp-color-canvas", "#0b0f14"),
    surface: token("--cp-color-surface-1", "#111820"),
    primary: token("--cp-color-primary", "#4c9aff"),
    positive: token("--cp-color-positive", "#39b980"),
    warning: token("--cp-color-warning", "#e5a83b"),
    danger: token("--cp-color-danger", "#ef6461"),
  };

  const formatCurrency = (value, options) => new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: Math.abs(Number(value) || 0) < 1 ? 4 : 2,
    ...options,
  }).format(Number(value) || 0);

  const formatCompactNumber = value => new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(Number(value) || 0);

  ChartJS.defaults.color = colors.muted;
  ChartJS.defaults.borderColor = colors.grid;
  ChartJS.defaults.font.family = token(
    "--cp-font-sans",
    'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
  );
  ChartJS.defaults.font.size = 11;
  ChartJS.defaults.animation.duration = 280;

  ChartJS.defaults.plugins.legend.labels.color = colors.muted;
  ChartJS.defaults.plugins.legend.labels.boxWidth = 10;
  ChartJS.defaults.plugins.legend.labels.boxHeight = 10;
  ChartJS.defaults.plugins.legend.labels.padding = 16;

  Object.assign(ChartJS.defaults.plugins.tooltip, {
    backgroundColor: colors.canvas,
    borderColor: colors.grid,
    borderWidth: 1,
    titleColor: colors.text,
    bodyColor: colors.muted,
    padding: 12,
    cornerRadius: 6,
    displayColors: true,
    boxPadding: 4,
  });

  global.CostPilotChartTheme = Object.freeze({
    colors: Object.freeze(colors),
    formatCurrency,
    formatCompactNumber,
    currencyTick: value => formatCurrency(value, { maximumFractionDigits: 0 }),
  });
})(window);
