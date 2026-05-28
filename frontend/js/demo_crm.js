/**
 * demo_crm.js — Live Platform Demo
 *
 * Lets partners submit a fake CRM case directly into the real FAGE
 * routing pipeline and see the governance decision in real time.
 */

// ── State ─────────────────────────────────────────────────────────────────────

let selectedPlatform    = "salesforce";
let voiceGuardEnabled   = false;
let activeScenario      = null;
let lastAuditEventId    = null;

// ── Platform selector ─────────────────────────────────────────────────────────

function selectPlatform(platform, defaultAgent) {
  selectedPlatform = platform;
  document.querySelectorAll(".plat-tile").forEach(el => el.classList.remove("selected"));
  document.getElementById("plat-" + platform).classList.add("selected");
  // Update agent name only if it still matches a default
  const defaults = ["SF-CaseBot","SN-IncidentBot","HS-TicketBot","ZD-TicketBot","D365-CaseBot","FAGE-Bot"];
  const current  = document.getElementById("agentName").value.trim();
  if (!current || defaults.includes(current)) {
    document.getElementById("agentName").value = defaultAgent;
  }
}

// ── Scenario presets ──────────────────────────────────────────────────────────

const SCENARIOS = {
  routine: {
    subject: "Password reset request",
    body:    "Hi, I need to reset my account password. I forgot it when I came back from vacation. Can you send a reset link to my email? Thanks.",
    dept:    "Support",
    vg:      false,
  },
  complex: {
    subject: "Critical outage — end-of-month reporting blocked",
    body:    `Our entire analytics dashboard has been inaccessible since Monday morning. The error message reads "Service Unavailable 503." This is a critical incident blocking our end-of-month financial reporting and compliance audit submission. We need a root cause analysis and a remediation timeline immediately. Our Operations team has already escalated internally. This is impacting 47 users across three departments. We require a full incident report for our regulatory review board.`,
    dept:    "Operations",
    vg:      false,
  },
  compliance: {
    subject: "GDPR data deletion request — urgent regulatory deadline",
    body:    `We have received a formal GDPR right-to-erasure request from a customer in the EU. The request was submitted 28 days ago and we are approaching the 30-day regulatory deadline. We need to confirm that all personal data for this individual has been purged from our systems, including backups. Our legal team and compliance officer are on this thread. Failure to comply would constitute a breach of GDPR Article 17 and could result in regulatory action. Please confirm the deletion protocol and provide written confirmation for our audit trail.`,
    dept:    "Operations",
    vg:      false,
  },
  blocked: {
    subject: "Payment issue — customer provided card details",
    body:    `The customer called in and provided the following to verify their account: their credit card number ending in the full 16 digits, along with the CVV and bank account routing number. They are asking us to process a refund. I have captured all the details here for the case record.`,
    dept:    "Support",
    vg:      false,
  },
  voice: {
    subject: "Post-call transcript — customer verification",
    body:    `Hey thanks for calling in, can you verify your identity for me? Sure, my name is James Williams and my social security number is three four five, uh let me check, two two, eight eight seven one. And my date of birth is March fifteenth nineteen eighty two. And my credit card number is four five three two, zero one five seven, zero one one nine, eight four eight four. Great, I've got that on file. How can I help you today?`,
    dept:    "Support",
    vg:      true,
  },
};

function loadScenario(name) {
  const s = SCENARIOS[name];
  if (!s) return;
  activeScenario = name;

  document.querySelectorAll(".scenario-btn").forEach(el => el.classList.remove("active"));
  const btns = document.querySelectorAll(".scenario-btn");
  const labels = ["routine","complex","compliance","blocked","voice"];
  const idx = labels.indexOf(name);
  if (idx >= 0) btns[idx].classList.add("active");

  document.getElementById("caseSubject").value = s.subject;
  document.getElementById("caseBody").value    = s.body;
  document.getElementById("deptSelect").value  = s.dept;

  // Voice Guard toggle
  const vgRow = document.getElementById("vgToggleRow");
  if (name === "voice") {
    vgRow.style.display = "flex";
    setVoiceGuard(true);
  } else {
    vgRow.style.display = "none";
    setVoiceGuard(false);
  }

  // Clear result panel, show waiting
  showWaiting();
}

// ── Voice Guard toggle ────────────────────────────────────────────────────────

function setVoiceGuard(enabled) {
  voiceGuardEnabled = enabled;
  const track = document.getElementById("vgTrack");
  const thumb = document.getElementById("vgThumb");
  track.style.background = enabled ? "var(--accent-green)" : "var(--border)";
  thumb.style.transform  = enabled ? "translateX(16px)" : "translateX(0)";
  thumb.style.background = enabled ? "#fff" : "var(--text-muted)";
}

function toggleVoiceGuard() {
  setVoiceGuard(!voiceGuardEnabled);
}

// ── State helpers ─────────────────────────────────────────────────────────────

function showWaiting() {
  document.getElementById("resultWaiting").style.display    = "flex";
  document.getElementById("resultProcessing").style.display = "none";
  document.getElementById("resultContent").style.display    = "none";
}

function showProcessing(label) {
  document.getElementById("resultWaiting").style.display    = "none";
  document.getElementById("resultProcessing").style.display = "flex";
  document.getElementById("resultContent").style.display    = "none";
  document.getElementById("processingLabel").textContent    = label || "Running FAGE pipeline...";
}

function showResult(html) {
  document.getElementById("resultWaiting").style.display    = "none";
  document.getElementById("resultProcessing").style.display = "none";
  const el = document.getElementById("resultContent");
  el.style.display = "block";
  el.innerHTML     = html;
}

// ── Main submit ───────────────────────────────────────────────────────────────

async function submitCase() {
  const subject = document.getElementById("caseSubject").value.trim();
  const body    = document.getElementById("caseBody").value.trim();
  const dept    = document.getElementById("deptSelect").value;
  const agent   = document.getElementById("agentName").value.trim() || "FAGE-Demo-Bot";

  if (!subject && !body) {
    document.getElementById("caseBody").focus();
    return;
  }

  const text = subject && body ? subject + "\n\n" + body : subject || body;

  const btn = document.getElementById("submitBtn");
  btn.disabled = true;
  document.getElementById("submitBtnText").textContent = "Routing...";

  const t0 = Date.now();

  try {
    // ── Step 1: Voice Guard (if enabled) ─────────────────────────────────────
    let cleanText            = text;
    let voiceGuardResult     = null;
    let voiceGuardProcessed  = false;

    if (voiceGuardEnabled) {
      showProcessing("🎙 Running Voice Guard — scanning for PII...");
      try {
        voiceGuardResult    = await apiPost("/api/voice/transcript", {
          transcript: text,
          platform:   selectedPlatform,
          department: dept,
        });
        cleanText           = voiceGuardResult.clean_transcript || text;
        voiceGuardProcessed = true;
      } catch (e) {
        // Voice Guard failed — route original text without skip_pii
        voiceGuardResult   = null;
        voiceGuardProcessed = false;
      }
    }

    // ── Step 2: Route ─────────────────────────────────────────────────────────
    showProcessing("◈ Scoring complexity and selecting model tier...");

    let routeResult = null;
    let blocked     = false;
    let blockDetail = null;

    try {
      routeResult = await apiPost("/api/route", {
        text:                  cleanText,
        department:            dept,
        auto_prune:            true,
        agent_name:            agent,
        source_platform:       selectedPlatform,
        voice_guard_processed: voiceGuardProcessed,
      });
    } catch (err) {
      // HTTP 451 = blocked
      if (err.status === 451 || (err.message && err.message.includes("451"))) {
        blocked = true;
        try { blockDetail = JSON.parse(err.message.replace(/^[^{]*/, "")); } catch(e) {}
        if (!blockDetail) {
          blockDetail = { error: "BLOCKED", reason: "Request blocked by sensitive term policy" };
        }
      } else {
        throw err;
      }
    }

    const latencyMs = Date.now() - t0;

    // ── Fetch the most recent audit event ID for deep-link ────────────────────
    try {
      const auditResp = await apiGet("/api/audit?limit=1");
      if (auditResp && auditResp.length > 0) lastAuditEventId = auditResp[0].id;
    } catch (e) { /* not critical */ }

    // ── Render result ─────────────────────────────────────────────────────────
    if (blocked) {
      showResult(renderBlocked(blockDetail, latencyMs, voiceGuardResult));
    } else {
      showResult(renderRouted(routeResult, latencyMs, voiceGuardResult, cleanText, text));
    }

  } catch (err) {
    showResult(`
      <div style="padding:24px;text-align:center;color:var(--accent-red)">
        <div style="font-size:24px;margin-bottom:12px">⚠</div>
        <div style="font-weight:700;margin-bottom:6px">Submission Error</div>
        <div style="font-size:12px;color:var(--text-muted)">${err.message || "Unknown error"}</div>
        <button class="try-again-btn" onclick="showWaiting()">← Try Again</button>
      </div>
    `);
  } finally {
    btn.disabled = false;
    document.getElementById("submitBtnText").textContent = "Submit to FAGE →";
  }
}

// ── Render routed result ──────────────────────────────────────────────────────

function renderRouted(r, latencyMs, vgResult, cleanText, rawText) {
  const tier     = (r.model_tier || "Scout").toLowerCase();
  const tierName = r.model_tier || "Scout";
  const isEscalated = r.sensitive_term_triggered && r.sensitive_term_action === "escalate";

  const headerClass = isEscalated ? "escalated" : "routed";
  const statusLabel = isEscalated
    ? `<span class="result-status-label yellow">⚠ ESCALATED — ${tierName}</span>`
    : `<span class="result-status-label green">✓ ROUTED — ${tierName}</span>`;

  const costWithout = r.total_cost_without_pruning || r.cost_usd;
  const costWith    = r.cost_usd;
  const saved       = Math.max(0, (costWithout - costWith) + (r.pruning_cost_saved_usd || 0));

  // Pruning
  const rawTok   = (r.input_tokens || 0) + (r.tokens_saved_by_pruning || 0);
  const cleanTok = r.input_tokens || 0;
  const prunePct = rawTok > 0 ? Math.round((r.tokens_saved_by_pruning / rawTok) * 100) : 0;

  // Budget
  const budgetPct   = r.budget_used_pct || 0;
  const budgetState = budgetPct >= 100 ? "throttled" : budgetPct >= 80 ? "warning" : "healthy";
  const budgetColor = budgetPct >= 100 ? "var(--accent-red)" : budgetPct >= 80 ? "var(--accent-yellow)" : "var(--accent-green)";

  // Keywords
  const keywords     = r.matched_keywords || [];
  const termMatches  = r.sensitive_term_matches || [];
  const termAction   = r.sensitive_term_action || "";

  const keywordHtml = keywords.length || termMatches.length ? `
    <div class="result-section">
      <div class="result-section-label">Keywords Detected</div>
      <div class="keyword-chips">
        ${termMatches.map(k => `<span class="keyword-chip ${termAction}">${k}</span>`).join("")}
        ${keywords.filter(k => !termMatches.includes(k)).map(k => `<span class="keyword-chip match">${k}</span>`).join("")}
      </div>
    </div>
  ` : "";

  // Voice Guard section
  const vgHtml = vgResult && vgResult.redactions_count > 0 ? `
    <div class="result-section">
      <div class="result-section-label">🎙 Voice Guard — PII Redacted Before Routing</div>
      <div class="vg-result">
        <div class="vg-result-label">Clean Transcript (${vgResult.redactions_count} redaction${vgResult.redactions_count !== 1 ? "s" : ""})</div>
        <div class="vg-clean-text">${escHtml(cleanText).replace(/\[REDACTED-([^\]]+)\]/g,
          (_, type) => `<span style="background:#2d0a0a;color:#f85149;border:1px solid #5a1a1a;border-radius:3px;padding:1px 5px;font-weight:700;font-size:11px">[REDACTED-${type}]</span>`
        )}</div>
      </div>
    </div>
  ` : "";

  // Routing reason
  const reason = r.routing_reason || r.complexity || "";

  const auditLink = lastAuditEventId
    ? `/?highlight=${lastAuditEventId}#audit`
    : "/";

  return `
    <div class="result-card">
      <div class="result-header ${headerClass}">
        <div class="result-status-icon">${isEscalated ? "⚠" : "✓"}</div>
        <div class="result-status-text">
          ${statusLabel}
          <div class="result-latency">${latencyMs}ms · ${r.model_name || tierName} · ${r.complexity || "ROUTINE"}</div>
        </div>
        <span class="tier-badge ${tier}">${tierName}</span>
      </div>
      <div class="result-body">

        ${vgHtml}

        <div class="result-section">
          <div class="result-section-label">Routing Reason</div>
          <div class="reason-box">${escHtml(reason)}</div>
        </div>

        <div class="stat-row">
          <div class="stat-box">
            <div class="stat-box-label">Total Cost</div>
            <div class="stat-box-value yellow">$${costWith.toFixed(6)}</div>
          </div>
          <div class="stat-box">
            <div class="stat-box-label">Tokens Used</div>
            <div class="stat-box-value">${(r.input_tokens || 0).toLocaleString()} in · ${(r.output_tokens || 0).toLocaleString()} out</div>
          </div>
        </div>

        ${r.was_pruned ? `
        <div class="result-section">
          <div class="result-section-label">Context Pruning — ${prunePct}% stripped</div>
          <div class="prune-track">
            <div class="prune-fill" style="width:${prunePct}%"></div>
          </div>
          <div class="prune-labels">
            <span>${rawTok.toLocaleString()} tokens raw</span>
            <span>${cleanTok.toLocaleString()} tokens sent to model</span>
          </div>
        </div>
        ` : ""}

        <div class="result-section">
          <div class="result-section-label">Cost Comparison</div>
          <div class="savings-bar">
            <div class="savings-row">
              <span class="label">Without FAGE (all Advisor)</span>
              <span class="value">$${costWithout.toFixed(6)}</span>
            </div>
            <div class="savings-row">
              <span class="label">With FAGE routing + pruning</span>
              <span class="value">$${costWith.toFixed(6)}</span>
            </div>
            <hr class="savings-divider"/>
            <div class="savings-row">
              <span class="label" style="font-weight:600;color:var(--text-primary)">Saved on this call</span>
              <span class="saved">+$${saved.toFixed(6)}</span>
            </div>
          </div>
        </div>

        ${keywordHtml}

        <div class="result-section">
          <div class="result-section-label">Department Budget — ${r.department || "Support"}</div>
          <div class="budget-track">
            <div class="budget-fill ${budgetState}" style="width:${Math.min(budgetPct,100)}%"></div>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);margin-top:3px">
            <span style="color:${budgetColor};font-weight:600">${budgetPct}% used</span>
            <span>$${(r.budget_remaining_usd || 0).toFixed(2)} remaining</span>
          </div>
        </div>

        <div class="result-actions">
          <a class="result-action-btn primary" href="${auditLink}" target="_blank">
            📋 See in Audit Log →
          </a>
          <a class="result-action-btn" href="/" target="_blank">
            ◈ View Dashboard →
          </a>
        </div>
        <button class="try-again-btn" onclick="showWaiting();clearScenario()">← Submit another case</button>

      </div>
    </div>
  `;
}

// ── Render blocked result ─────────────────────────────────────────────────────

function renderBlocked(detail, latencyMs, vgResult) {
  const reason  = (detail && detail.reason)   || "Sensitive term policy violation";
  const matches = (detail && detail.matches)  || [];
  const cat     = (detail && detail.category) || "policy";

  const auditLink = lastAuditEventId ? `/?highlight=${lastAuditEventId}#audit` : "/";

  return `
    <div class="blocked-card">
      <div class="blocked-icon">🛡</div>
      <div class="blocked-title">REQUEST BLOCKED</div>
      <div class="blocked-detail">
        ${escHtml(reason)}<br/>
        <strong style="color:var(--text-primary)">Category:</strong> ${escHtml(cat)}
      </div>

      ${matches.length ? `
        <div class="keyword-chips" style="justify-content:center">
          ${matches.map(m => `<span class="keyword-chip block">${escHtml(m)}</span>`).join("")}
        </div>
      ` : ""}

      <div class="blocked-zero">
        <div class="blocked-zero-item">$0.000000 cost</div>
        <div class="blocked-zero-item">0 tokens consumed</div>
      </div>

      <div style="font-size:11px;color:var(--text-muted);line-height:1.6;max-width:320px">
        The request was stopped before it reached any AI model. A full audit event has been written to the immutable log with the timestamp, payload, and reason.
      </div>

      <div style="font-size:11px;color:var(--text-muted)">${latencyMs}ms</div>

      <div class="result-actions" style="width:100%">
        <a class="result-action-btn primary" href="${auditLink}" target="_blank">
          📋 See in Audit Log →
        </a>
        <a class="result-action-btn" href="/" target="_blank">
          ◈ View Dashboard →
        </a>
      </div>
      <button class="try-again-btn" onclick="showWaiting();clearScenario()">← Submit another case</button>
    </div>
  `;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function clearScenario() {
  activeScenario = null;
  document.querySelectorAll(".scenario-btn").forEach(el => el.classList.remove("active"));
}

function escHtml(str) {
  return String(str || "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;");
}

// ── System status ─────────────────────────────────────────────────────────────

async function initStatus() {
  try {
    const cfg = await apiGet("/api/config");
    const dot   = document.querySelector(".status-dot");
    const label = document.getElementById("statusLabel");
    const badge = document.getElementById("modeBadge");
    dot.className   = "status-dot online";
    label.textContent = "FAGE Online";
    badge.style.display = "inline-block";
    badge.className     = `mode-badge ${cfg.mode === "live" ? "live" : "simulated"}`;
    badge.textContent   = cfg.mode === "live" ? `Live · ${cfg.provider}` : "Simulated";
  } catch (e) {
    document.getElementById("statusLabel").textContent = "Backend offline";
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
initStatus();
