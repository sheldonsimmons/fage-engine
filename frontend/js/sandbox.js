/**
 * sandbox.js — FAGE Sandbox / Isolated Test Environment
 *
 * All calls pass is_test: true to the backend — no transactions, budgets,
 * or audit events are written. Results are session-only.
 */

// ── Session counters (in-memory only, cleared on page reload) ─────────────────
let _sbCalls    = 0;
let _sbTokens   = 0;
let _sbCost     = 0;
let _sbSaved    = 0;
let _sbLastTier = "—";
let _sbPrunedText = "";   // Buffer: pruner → router send

function _sbUpdateStrip() {
  document.getElementById("sb-stat-calls").textContent  = _sbCalls.toLocaleString();
  document.getElementById("sb-stat-tokens").textContent = _sbTokens.toLocaleString();
  document.getElementById("sb-stat-cost").textContent   = "$" + _sbCost.toFixed(4);
  document.getElementById("sb-stat-saved").textContent  = _sbSaved.toLocaleString();
  document.getElementById("sb-stat-tier").textContent   = _sbLastTier;
}


// ── Microphone / Speech Recognition ──────────────────────────────────────────

let _sbRecognition = null;
let _sbMicActive   = false;
let _sbFinalText   = "";

function sbToggleMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const status = document.getElementById("sb-vg-status");
  if (!SR) {
    status.textContent = "Speech recognition not supported — use Chrome or Edge.";
    status.style.color = "var(--accent-yellow)";
    return;
  }
  if (_sbMicActive) {
    if (_sbRecognition) _sbRecognition.stop();
    return;
  }
  _sbRecognition = _sbInitSpeech();
  if (_sbRecognition) _sbRecognition.start();
}

function _sbInitSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return null;

  const rec           = new SR();
  rec.continuous      = true;
  rec.interimResults  = true;
  rec.lang            = "en-US";
  rec.maxAlternatives = 1;

  rec.onstart = () => {
    _sbMicActive = true;
    _sbFinalText = "";
    const btn     = document.getElementById("sb-vg-mic-btn");
    const preview = document.getElementById("sb-vg-live-preview");
    if (btn)     { btn.innerHTML = "⏹ Stop"; btn.style.background = "rgba(248,81,73,0.15)"; }
    if (preview) { preview.style.display = "block"; document.getElementById("sb-vg-live-text").textContent = "Listening..."; }
    document.getElementById("sb-vg-result").classList.remove("visible");
    document.getElementById("sb-vg-status").textContent = "";
    document.getElementById("sb-vg-input").value = "";
  };

  rec.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) { _sbFinalText += chunk + " "; }
      else { interim = chunk; }
    }
    const liveEl = document.getElementById("sb-vg-live-text");
    if (liveEl) {
      liveEl.innerHTML =
        (_sbFinalText ? `<span style="color:var(--text-primary)">${_sbFinalText}</span>` : "") +
        (interim      ? `<span style="color:var(--text-muted);font-style:italic">${interim}</span>` : "");
    }
  };

  rec.onerror = (event) => {
    const status = document.getElementById("sb-vg-status");
    status.style.color = "var(--accent-red)";
    if (event.error === "not-allowed") {
      status.textContent = "Microphone access denied — check browser permissions.";
    } else if (event.error === "no-speech") {
      status.textContent = "No speech detected. Try again.";
    } else {
      status.textContent = "Mic error: " + event.error;
    }
    _sbStopMic();
  };

  rec.onend = () => {
    _sbStopMic();
    const text = _sbFinalText.trim();
    if (text) {
      document.getElementById("sb-vg-input").value = text;
      document.getElementById("sb-vg-status").textContent = "Transcript captured — scanning for PII...";
      document.getElementById("sb-vg-status").style.color = "var(--text-muted)";
      setTimeout(() => sbRunVoiceGuard(), 300);
    }
  };

  return rec;
}

function _sbStopMic() {
  _sbMicActive = false;
  const btn     = document.getElementById("sb-vg-mic-btn");
  const preview = document.getElementById("sb-vg-live-preview");
  if (btn)     { btn.innerHTML = "🎙 Speak"; btn.style.background = "var(--bg-panel)"; }
  if (preview) preview.style.display = "none";
  if (_sbRecognition) { try { _sbRecognition.stop(); } catch (_) {} }
}


// ── Voice Guard ───────────────────────────────────────────────────────────────

async function sbRunVoiceGuard() {
  const text   = document.getElementById("sb-vg-input").value.trim();
  const status = document.getElementById("sb-vg-status");
  const result = document.getElementById("sb-vg-result");

  if (!text) { status.textContent = "Paste a transcript first."; return; }

  status.textContent = "Scanning...";
  result.classList.remove("visible");

  try {
    const data = await apiPost("/api/voice/redact", { transcript: text, department: "Sandbox" });

    document.getElementById("sb-vg-count").textContent      = data.redactions_count || 0;
    document.getElementById("sb-vg-confidence").textContent = data.confidence_score != null
      ? Math.round(data.confidence_score * 100) + "%" : "—";
    document.getElementById("sb-vg-method").textContent     = data.detection_method || "—";
    document.getElementById("sb-vg-clean").textContent      = data.clean_transcript || text;

    const types = data.pii_types_found
      ? (Array.isArray(data.pii_types_found)
          ? data.pii_types_found
          : JSON.parse(data.pii_types_found))
      : [];
    document.getElementById("sb-vg-types").textContent = types.length
      ? "PII types detected: " + types.join(", ")
      : "No PII detected.";

    result.classList.add("visible");
    status.textContent = data.redactions_count > 0
      ? `✓ ${data.redactions_count} PII item(s) redacted`
      : "✓ No PII found";
  } catch (e) {
    status.textContent = "Error: " + e.message;
  }
}

function sbLoadVgExample() {
  document.getElementById("sb-vg-input").value =
    "Hi, this is Sarah calling about my account. My social security number is 532-11-9876 " +
    "and my credit card ending in 4532 is what I used. Date of birth is January 14th 1985. " +
    "Can someone please help me reset my online banking access?";
}


// ── Context Pruner ────────────────────────────────────────────────────────────

async function sbRunPruner() {
  const text   = document.getElementById("sb-pruner-input").value.trim();
  const status = document.getElementById("sb-pruner-status");
  const result = document.getElementById("sb-pruner-result");

  if (!text) { status.textContent = "Paste a payload first."; return; }

  status.textContent = "Pruning...";
  result.classList.remove("visible");

  try {
    const data = await apiPost("/api/prune", { text, department: "Sandbox" });

    document.getElementById("sb-pruner-before").textContent = (data.raw_tokens || 0).toLocaleString();
    document.getElementById("sb-pruner-after").textContent  = (data.clean_tokens || 0).toLocaleString();
    document.getElementById("sb-pruner-saved").textContent  = (data.tokens_saved || 0).toLocaleString();
    document.getElementById("sb-pruner-pct").textContent    = (data.compression_pct || 0) + "%";
    document.getElementById("sb-pruner-cost-line").textContent =
      `Cost avoided → Micro: $${(data.micro_cost_saved_usd || 0).toFixed(6)}  ·  Flagship: $${(data.flagship_cost_saved_usd || 0).toFixed(6)}`;
    document.getElementById("sb-pruner-output").textContent = data.cleaned_text || "";

    _sbSaved += data.tokens_saved || 0;
    _sbUpdateStrip();

    _sbPrunedText = data.cleaned_text || "";
    result.classList.add("visible");
    status.textContent = `✓ ${data.compression_pct || 0}% compressed`;
  } catch (e) {
    status.textContent = "Error: " + e.message;
  }
}

function sbSendPrunedToRouter() {
  if (!_sbPrunedText) {
    document.getElementById("sb-pruner-status").textContent = "Run the sweeper first.";
    return;
  }
  document.getElementById("sb-router-input").value = _sbPrunedText;
  // Uncheck auto-prune since it's already pruned
  document.getElementById("sb-router-prune").checked = false;
  document.getElementById("sb-router-status").textContent = "← Pruned text loaded";
}


// ── Token Router ──────────────────────────────────────────────────────────────

const TIER_CLASSES = {
  Scout: "tier-Scout", Analyst: "tier-Analyst",
  Advisor: "tier-Advisor", Strategist: "tier-Strategist",
  micro: "tier-Scout", flagship: "tier-Advisor",
};

async function sbRunRouter() {
  const text   = document.getElementById("sb-router-input").value.trim();
  const dept   = document.getElementById("sb-router-dept").value;
  const prune  = document.getElementById("sb-router-prune").checked;
  const status = document.getElementById("sb-router-status");
  const result = document.getElementById("sb-router-result");

  if (!text) { status.textContent = "Paste a payload first."; return; }

  status.textContent = "Routing...";
  result.classList.remove("visible");

  try {
    const data = await apiPost("/api/route", {
      text,
      department:  dept,
      auto_prune:  prune,
      is_test:     true,   // ← sandbox flag — no DB writes
    });

    // Tier badge
    const tierBadge = document.getElementById("sb-router-tier-badge");
    tierBadge.textContent = data.model_tier || "—";
    tierBadge.className   = "tier-badge " + (TIER_CLASSES[data.model_tier] || "");

    // Decision badge
    const decBadge = document.getElementById("sb-router-decision-badge");
    decBadge.textContent = data.routing_decision || "—";

    // Sensitive term badge
    const senseBadge = document.getElementById("sb-router-sensitive-badge");
    if (data.sensitive_term_triggered) {
      senseBadge.textContent  = `⚠ Sensitive: ${data.sensitive_term_action?.toUpperCase()}`;
      senseBadge.style.display = "inline-block";
    } else {
      senseBadge.style.display = "none";
    }

    document.getElementById("sb-router-reason").textContent  = data.routing_reason || "";
    document.getElementById("sb-router-model").textContent   = data.model_name || "—";
    document.getElementById("sb-router-in").textContent      = (data.input_tokens || 0).toLocaleString();
    document.getElementById("sb-router-out").textContent     = (data.output_tokens || 0).toLocaleString();
    document.getElementById("sb-router-cost").textContent    = "$" + (data.cost_usd || 0).toFixed(6);
    document.getElementById("sb-router-saved").textContent   = "$" + (data.pruning_cost_saved_usd || 0).toFixed(6);
    document.getElementById("sb-router-response").textContent = data.simulated_response || "";

    // Update session stats
    _sbCalls   += 1;
    _sbTokens  += (data.input_tokens || 0) + (data.output_tokens || 0);
    _sbCost    += data.cost_usd || 0;
    _sbLastTier = data.model_tier || "—";
    _sbUpdateStrip();

    result.classList.add("visible");
    status.textContent = `✓ Routed → ${data.model_tier}`;
  } catch (e) {
    status.textContent = "Error: " + e.message;
  }
}
