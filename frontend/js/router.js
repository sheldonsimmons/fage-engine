/**
 * router.js — Token Router & Model Cascader UI logic  [Step 3]
 */

/** Send the RAW input text to the router with auto-prune ON.
 *  This lets the router run the pruner itself so it can calculate
 *  the real token savings and show the cost difference correctly. */
function sendPrunedText() {
  const raw = document.getElementById("prunerInput").value.trim();
  if (!raw) {
    document.getElementById("routerStatus").textContent =
      "Paste a payload into the Sweeper first.";
    return;
  }
  document.getElementById("routerInput").value = raw;
  // Keep auto-prune ON — router will prune and track savings
  document.getElementById("routerAutoPrune").checked = true;
  document.getElementById("routerStatus").textContent =
    "Raw payload loaded with auto-prune enabled. Router will prune and calculate savings.";
}

/** Submit a payload to POST /api/route and render the result card */
async function runRouter() {
  const text      = document.getElementById("routerInput").value.trim();
  const dept      = document.getElementById("routerDept").value;
  const autoPrune = document.getElementById("routerAutoPrune").checked;
  const status    = document.getElementById("routerStatus");
  const result    = document.getElementById("routerResult");

  if (!text) {
    status.textContent = "Paste a payload first.";
    return;
  }

  status.textContent = "Routing...";
  result.style.display = "none";

  try {
    const data = await apiPost("/api/route", {
      text:       text,
      department: dept,
      auto_prune: autoPrune,
    });

    // ── Complexity badge ──────────────────────────────────────────────────────
    const complexityBadge = document.getElementById("routerComplexityBadge");
    complexityBadge.textContent  = data.complexity;
    complexityBadge.className    = "badge " + (
      data.routing_decision === "THROTTLED" ? "badge-locked" :
      data.complexity === "COMPLEX"         ? "badge-advisor" : "badge-scout"
    );

    // ── Model tier badge ──────────────────────────────────────────────────────
    const modelBadge = document.getElementById("routerModelBadge");
    modelBadge.textContent = data.model_name;
    modelBadge.className   = "badge " + getTierBadgeClassByName(data.model_tier || "");

    // ── Throttle badge (only shown when throttled) ────────────────────────────
    const throttleBadge = document.getElementById("routerThrottleBadge");
    if (data.was_throttled) {
      throttleBadge.textContent   = "THROTTLED";
      throttleBadge.className     = "badge badge-locked";
      throttleBadge.style.display = "inline-block";
    } else {
      throttleBadge.style.display = "none";
    }

    // ── Routing reason ────────────────────────────────────────────────────────
    document.getElementById("routerReason").textContent = data.routing_reason;

    // ── Cost grid ─────────────────────────────────────────────────────────────
    document.getElementById("routerInputTokens").textContent  = data.input_tokens.toLocaleString();
    document.getElementById("routerOutputTokens").textContent = data.output_tokens.toLocaleString();
    document.getElementById("routerCost").textContent         = "$" + data.cost_usd.toFixed(6);
    document.getElementById("routerCostFull").textContent     = "$" + data.total_cost_without_pruning.toFixed(6);
    document.getElementById("routerPruningSaved").textContent = "$" + data.pruning_cost_saved_usd.toFixed(6);
    document.getElementById("routerBudgetPct").textContent    = data.budget_used_pct + "%";

    // ── Simulated response ────────────────────────────────────────────────────
    document.getElementById("routerResponse").textContent = data.simulated_response;

    result.style.display = "block";
    status.textContent   = "";

  } catch (err) {
    status.textContent = "Error: " + err.message;
    status.style.color = "var(--accent-red)";
  }
}
