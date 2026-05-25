/* voice_guard.js — Voice Guard dashboard panel */

const VG_EXAMPLES = [
  "my social security number is 123 ummm 45 6789",
  "sure, my social is one two three, hold on, forty five, six seven eight nine",
  "the card number is 4532 uh 0157 let me check 0119 8484",
  "date of birth is zero three, um, fifteen, nineteen eighty two",
  "call me back at 555 uh 867 5309",
  "routing number is 0 2 1, 0, 0, 0, 0, 2, 1",
];
let _vgExampleIdx = 0;

async function loadVoiceStats() {
  try {
    const data = await fetch("/api/voice/stats").then(r => r.json());

    document.getElementById("vg-calls-today").textContent     = (data.calls_today || 0).toLocaleString();
    document.getElementById("vg-calls-month").textContent     = (data.calls_month || 0).toLocaleString() + " this month";
    document.getElementById("vg-redactions-today").textContent = (data.redactions_today || 0).toLocaleString();
    document.getElementById("vg-redactions-total").textContent = (data.redactions_total || 0).toLocaleString() + " total";
    document.getElementById("vg-flagged").textContent         = (data.flagged_total || 0).toLocaleString();
    document.getElementById("vg-confidence").textContent      = data.avg_confidence
      ? (data.avg_confidence * 100).toFixed(1) + "%"
      : "—";

    // Update collapsed header summary
    const summaryEl = document.getElementById("vg-header-summary");
    if (summaryEl) {
      const piiToday = data.redactions_today || 0;
      const conf     = data.avg_confidence ? (data.avg_confidence * 100).toFixed(1) + "%" : "—";
      summaryEl.textContent = piiToday
        ? `${piiToday} PII event${piiToday !== 1 ? "s" : ""} today · ${conf} confidence`
        : "No events today";
    }

    // Presidio status badge
    const presidioActive = data.presidio_active;
    const chipEl = document.getElementById("vg-pii-breakdown");

    // PII type chips
    const breakdown = data.pii_breakdown || {};
    // AI layer status chip
    const aiChip = presidioActive
      ? `<div style="background:#0a1f0f;border:1px solid var(--accent-green);border-radius:6px;padding:6px 14px;font-size:12px">
           <span style="color:var(--accent-green);font-weight:700">🤖 Presidio AI</span>
           <span style="color:var(--text-muted);margin-left:6px">Active</span>
         </div>`
      : `<div style="background:var(--bg-panel);border:1px solid var(--accent-yellow);border-radius:6px;padding:6px 14px;font-size:12px">
           <span style="color:var(--accent-yellow);font-weight:700">⚠ Rule Engine Only</span>
           <span style="color:var(--text-muted);margin-left:6px">Presidio loading</span>
         </div>`;

    const fmtType = t => t.replace(/_/g, " ");

    chipEl.innerHTML = aiChip + (Object.keys(breakdown).length === 0
      ? '<span style="font-size:12px;color:var(--text-muted);padding:6px 0">No PII events recorded yet — use the test box below to try it.</span>'
      : Object.entries(breakdown).map(([type, count]) => `
          <div style="background:var(--bg-panel);border:1px solid var(--border);border-radius:6px;padding:6px 14px;font-size:12px">
            <span style="color:var(--accent-red);font-weight:700">${fmtType(type)}</span>
            <span style="color:var(--text-muted);margin-left:6px">${count} event${count !== 1 ? "s" : ""}</span>
          </div>
        `).join(""));
  } catch (e) {
    console.warn("Voice Guard stats unavailable:", e);
  }
}

function loadVoiceExample() {
  const ex = VG_EXAMPLES[_vgExampleIdx % VG_EXAMPLES.length];
  _vgExampleIdx++;
  document.getElementById("vgTestInput").value = ex;
  document.getElementById("vgResult").style.display = "none";
  document.getElementById("vgStatus").textContent = "";
}

async function testVoiceGuard() {
  const input = document.getElementById("vgTestInput").value.trim();
  if (!input) {
    document.getElementById("vgStatus").textContent = "Enter a transcript first.";
    return;
  }

  const statusEl = document.getElementById("vgStatus");
  statusEl.textContent = "Processing...";
  statusEl.style.color = "var(--text-muted)";

  try {
    const res = await fetch("/api/voice/transcript", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transcript: input,
        platform: "Dashboard Test",
        department: "Test",
      }),
    });

    const data = await res.json();

    // Show result
    const resultEl = document.getElementById("vgResult");
    resultEl.style.display = "block";

    // Highlight redacted spans
    let display = data.clean_transcript
      .replace(/\[REDACTED-([^\]]+)\]/g, (_, type) =>
        `<span style="background:#2d0a0a;color:var(--accent-red);border:1px solid #5a1a1a;border-radius:4px;padding:1px 6px;font-weight:700;font-family:var(--font-mono);font-size:12px">[REDACTED-${type}]</span>`
      );
    document.getElementById("vgCleanText").innerHTML = display;

    // Meta row
    const statusColor = data.status === "redacted" ? "var(--accent-green)"
      : data.status === "flagged" ? "var(--accent-yellow)"
      : "var(--text-muted)";

    const fmtPii  = t => t.replace(/_/g, " ");
    const piiList = data.pii_types_found.length
      ? data.pii_types_found.map(fmtPii).join(", ")
      : "none";

    const methodLabel = data.detection_method === "both" ? "🤖+📏 Rule + AI"
      : data.detection_method === "ai"   ? "🤖 Presidio AI"
      : data.detection_method === "rule" ? "📏 Rule Engine"
      : "—";

    document.getElementById("vgMeta").innerHTML = `
      <span>Status: <strong style="color:${statusColor}">${data.status.toUpperCase()}</strong></span>
      <span>Redactions: <strong style="color:var(--accent-red)">${data.redactions_count}</strong></span>
      <span>PII types: <strong>${piiList}</strong></span>
      <span>Detected by: <strong>${methodLabel}</strong></span>
      <span>Confidence: <strong>${data.redactions_count ? (data.confidence_score * 100).toFixed(1) + "%" : "—"}</strong></span>
      <span>Processed in: <strong>${data.processing_ms}ms</strong></span>
      ${data.flagged_for_review ? '<span style="color:var(--accent-yellow)">⚠ Flagged for review</span>' : ""}
    `;

    if (data.status === "clean") {
      statusEl.textContent = "✓ No PII detected — transcript is clean";
      statusEl.style.color = "var(--accent-green)";
    } else if (data.status === "flagged") {
      statusEl.textContent = "⚠ PII detected — flagged for human review";
      statusEl.style.color = "var(--accent-yellow)";
    } else {
      statusEl.textContent = `✓ ${data.redactions_count} PII event(s) redacted`;
      statusEl.style.color = "var(--accent-green)";
    }

    // Detection breakdown table
    const detailEl  = document.getElementById("vgDetectionDetails");
    const details   = data.detection_details || [];
    const fmtMethod = m => m === "both" ? "Rule + AI" : m === "ai" ? "Presidio AI" : "Rule Engine";
    if (details.length > 0) {
      detailEl.style.display = "block";
      detailEl.innerHTML = `
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-muted);margin-bottom:8px">
          Detection Breakdown
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead>
            <tr style="color:var(--text-muted);text-align:left;border-bottom:1px solid var(--border)">
              <th style="padding:5px 8px;font-weight:600">PII Type</th>
              <th style="padding:5px 8px;font-weight:600">Trigger Phrase</th>
              <th style="padding:5px 8px;font-weight:600">Confidence</th>
              <th style="padding:5px 8px;font-weight:600">Method</th>
            </tr>
          </thead>
          <tbody>
            ${details.map(d => `
              <tr style="border-bottom:1px solid var(--border)">
                <td style="padding:5px 8px;color:var(--accent-red);font-weight:700;font-family:var(--font-mono)">${fmtPii(d.pii_type)}</td>
                <td style="padding:5px 8px;color:var(--text-primary);font-style:${d.trigger_phrase ? "normal" : "italic"}">${d.trigger_phrase ? `"${d.trigger_phrase}"` : "—"}</td>
                <td style="padding:5px 8px;color:${d.confidence >= 0.9 ? "var(--accent-green)" : d.confidence >= 0.7 ? "var(--accent-yellow)" : "var(--accent-red)"};font-weight:600">${(d.confidence * 100).toFixed(1)}%</td>
                <td style="padding:5px 8px;color:var(--text-muted)">${fmtMethod(d.detection_method)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    } else {
      detailEl.style.display = "none";
    }

    // Refresh stats and audit log
    loadVoiceStats();
    loadVoiceAuditLog();

  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
    statusEl.style.color = "var(--accent-red)";
  }
}

async function clearVoiceGuardData() {
  if (!confirm("Clear all Voice Guard data? This cannot be undone.")) return;
  try {
    const res = await fetch("/api/voice/events", { method: "DELETE" });
    const data = await res.json();
    if (data.status === "ok") {
      document.getElementById("vgResult").style.display = "none";
      document.getElementById("vgStatus").textContent = `✓ Cleared ${data.deleted} event${data.deleted !== 1 ? "s" : ""}`;
      document.getElementById("vgStatus").style.color = "var(--accent-green)";
      loadVoiceStats();
      loadVoiceAuditLog();
    }
  } catch (e) {
    alert("Failed to clear data: " + e.message);
  }
}

// ── Voice Guard Audit Log ─────────────────────────────────────────────────────

let _vgAuditEvents = [];   // kept in memory for export

async function loadVoiceAuditLog() {
  const container = document.getElementById("vgAuditLog");
  if (!container) return;

  try {
    const events = await fetch("/api/voice/events?limit=50").then(r => r.json());
    _vgAuditEvents = events;

    if (!events.length) {
      container.innerHTML = '<p style="font-size:12px;color:var(--text-muted);padding:8px 0">No voice events recorded yet.</p>';
      return;
    }

    const fmtTs = iso => {
      if (!iso) return "—";
      return new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("en-US", {
        month: "numeric", day: "numeric", year: "2-digit",
        hour: "numeric", minute: "2-digit", hour12: true,
      });
    };

    const fmtPiiType = t => t.replace(/_/g, " ");
    const fmtMethod  = m => m === "both" ? "Rule + AI" : m === "ai" ? "Presidio AI" : m === "rule" ? "Rule Engine" : m || "—";

    const statusColor = s => s === "redacted" ? "var(--accent-green)"
      : s === "flagged" ? "var(--accent-yellow)"
      : "var(--text-muted)";

    const methodColor = m => m === "both" ? "var(--accent-purple)"
      : m === "ai" ? "var(--accent)"
      : "var(--text-muted)";

    const confColor = c => c >= 0.9 ? "var(--accent-green)"
      : c >= 0.7 ? "var(--accent-yellow)"
      : "var(--accent-red)";

    const rows = events.map((e, idx) => {
      const status    = e.flagged_for_review ? "flagged" : e.redactions_count > 0 ? "redacted" : "clean";
      const conf      = e.confidence_score ? (e.confidence_score * 100).toFixed(1) + "%" : "—";
      const details   = e.detection_details || [];
      const hasDetail = details.length > 0;
      const rowId     = `vg-audit-row-${e.id}`;

      const detailRows = details.map(d => `
        <tr style="background:var(--bg-base)">
          <td style="padding:5px 10px 5px 32px;font-family:var(--font-mono);font-size:11px;font-weight:700;color:var(--accent-red)">${fmtPiiType(d.pii_type)}</td>
          <td style="padding:5px 10px;font-size:11px;color:var(--text-primary);font-style:${d.trigger_phrase ? "normal" : "italic"}">
            ${d.trigger_phrase ? `"${d.trigger_phrase}"` : "— no trigger (direct pattern)"}
          </td>
          <td style="padding:5px 10px;font-size:11px;font-weight:700;color:${confColor(d.confidence)}">${(d.confidence * 100).toFixed(1)}%</td>
          <td style="padding:5px 10px;font-size:11px;color:${methodColor(d.detection_method)}">${fmtMethod(d.detection_method)}</td>
          <td></td>
        </tr>
      `).join("");

      return `
        <tr class="vg-audit-main-row" style="border-bottom:1px solid var(--border);cursor:${hasDetail ? "pointer" : "default"}"
            ${hasDetail ? `onclick="toggleVgAuditRow('${rowId}')"` : ""}>
          <td style="padding:8px 10px;font-size:11px;color:var(--text-muted);white-space:nowrap">${fmtTs(e.timestamp)}</td>
          <td style="padding:8px 10px;font-size:11px;color:var(--text-primary);font-family:var(--font-mono)">${e.call_id || "—"}</td>
          <td style="padding:8px 10px;font-size:11px;color:var(--text-muted)">${e.department || "—"}</td>
          <td style="padding:8px 10px;font-size:11px;font-weight:700;color:${statusColor(status)}">${status.toUpperCase()}</td>
          <td style="padding:8px 10px;font-size:11px;color:var(--accent-red);font-weight:700">${e.redactions_count}</td>
          <td style="padding:8px 10px;font-size:11px;font-weight:700;color:${confColor(e.confidence_score || 0)}">${conf}</td>
          <td style="padding:8px 10px;font-size:11px;color:${methodColor(e.detection_method)}">${fmtMethod(e.detection_method)}</td>
          <td style="padding:8px 10px;font-size:11px;color:var(--text-muted);text-align:right">${hasDetail ? '<span style="color:var(--accent);font-size:10px">▸ details</span>' : ""}</td>
        </tr>
        ${hasDetail ? `
        <tr id="${rowId}" style="display:none">
          <td colspan="8" style="padding:0 0 4px">
            <table style="width:100%;border-collapse:collapse">
              <thead>
                <tr style="background:var(--bg-base);border-bottom:1px solid var(--border)">
                  <th style="padding:5px 10px 5px 32px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">PII Type</th>
                  <th style="padding:5px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Trigger Phrase</th>
                  <th style="padding:5px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Confidence</th>
                  <th style="padding:5px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Method</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>${detailRows}</tbody>
            </table>
          </td>
        </tr>` : ""}
      `;
    }).join("");

    container.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead>
          <tr style="border-bottom:1px solid var(--border)">
            <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Timestamp</th>
            <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Call ID</th>
            <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Dept</th>
            <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Status</th>
            <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Redactions</th>
            <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Avg Confidence</th>
            <th style="padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);text-align:left">Method</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  } catch (e) {
    container.innerHTML = `<p style="font-size:12px;color:var(--accent-red)">Failed to load audit log: ${e.message}</p>`;
  }
}

function toggleVgAuditRow(rowId) {
  const row = document.getElementById(rowId);
  if (!row) return;
  const isOpen = row.style.display !== "none";
  row.style.display = isOpen ? "none" : "table-row";
  // Flip the arrow on the parent row's last cell
  const mainRow = row.previousElementSibling;
  if (mainRow) {
    const arrow = mainRow.querySelector("td:last-child span");
    if (arrow) arrow.textContent = isOpen ? "▸ details" : "▾ details";
  }
}

// ── Voice Guard export functions ──────────────────────────────────────────────

function exportVgSummaryCsv() {
  if (!_vgAuditEvents.length) { alert("No Voice Guard events to export."); return; }
  const fmtTs = iso => iso
    ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("en-US", { hour12: true })
    : "";
  const headers = ["Timestamp", "Call ID", "Platform", "Department", "Status", "Redactions", "Avg Confidence", "Detection Method", "Flagged for Review", "Processing (ms)"];
  const rows = _vgAuditEvents.map(e => {
    const status = e.flagged_for_review ? "flagged" : e.redactions_count > 0 ? "redacted" : "clean";
    return [
      fmtTs(e.timestamp),
      e.call_id || "",
      e.platform || "",
      e.department || "",
      status,
      e.redactions_count,
      e.confidence_score != null ? (e.confidence_score * 100).toFixed(1) + "%" : "",
      e.detection_method || "",
      e.flagged_for_review ? "Yes" : "No",
      e.processing_ms || "",
    ];
  });
  const date = new Date().toISOString().slice(0, 10);
  downloadCsv(`fage_voice_guard_summary_${date}.csv`, headers, rows);
}

function exportVgDetailedCsv() {
  if (!_vgAuditEvents.length) { alert("No Voice Guard events to export."); return; }
  const fmtTs = iso => iso
    ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("en-US", { hour12: true })
    : "";
  const headers = ["Call ID", "Timestamp", "Platform", "Department", "PII Type", "Trigger Phrase", "Confidence %", "Detection Method", "Call Status"];
  const rows = [];
  _vgAuditEvents.forEach(e => {
    const status = e.flagged_for_review ? "flagged" : e.redactions_count > 0 ? "redacted" : "clean";
    const details = e.detection_details || [];
    if (details.length === 0) {
      // Event with no detections — still include as one clean row
      rows.push([e.call_id || "", fmtTs(e.timestamp), e.platform || "", e.department || "", "", "", "", "", status]);
    } else {
      details.forEach(d => {
        rows.push([
          e.call_id || "",
          fmtTs(e.timestamp),
          e.platform || "",
          e.department || "",
          (d.pii_type || "").replace(/_/g, " "),
          d.trigger_phrase || "",
          d.confidence != null ? (d.confidence * 100).toFixed(1) + "%" : "",
          d.detection_method || "",
          status,
        ]);
      });
    }
  });
  const date = new Date().toISOString().slice(0, 10);
  downloadCsv(`fage_voice_guard_detailed_${date}.csv`, headers, rows);
}

function exportVgPdf() {
  printSection("voiceGuardBody", "FAGE — Voice Guard Audit Log");
}

// ── Live Microphone / Speech Recognition ──────────────────────────────────────

let _vgRecognition  = null;   // SpeechRecognition instance
let _vgMicActive    = false;  // true while recording
let _vgFinalText    = "";     // accumulated final transcript chunks

function _initSpeechRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return null;

  const rec          = new SR();
  rec.continuous     = true;    // keep going through pauses
  rec.interimResults = true;    // show words as they come in
  rec.lang           = "en-US";
  rec.maxAlternatives = 1;

  rec.onstart = () => {
    _vgMicActive  = true;
    _vgFinalText  = "";
    const btn     = document.getElementById("vgMicBtn");
    const badge   = document.getElementById("vgMicBadge");
    const preview = document.getElementById("vgLivePreview");
    if (btn)     { btn.textContent = "⏹ Stop"; btn.classList.add("vg-mic-active"); }
    if (badge)   badge.style.display = "flex";
    if (preview) { preview.style.display = "block"; document.getElementById("vgLiveText").textContent = "Listening..."; }
    // Clear old result
    document.getElementById("vgResult").style.display = "none";
    document.getElementById("vgStatus").textContent = "";
    document.getElementById("vgTestInput").value = "";
  };

  rec.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        _vgFinalText += chunk + " ";
      } else {
        interim = chunk;
      }
    }
    // Show live preview: confirmed text + grey interim
    const liveEl = document.getElementById("vgLiveText");
    if (liveEl) {
      liveEl.innerHTML = (_vgFinalText
        ? `<span style="color:var(--text-primary)">${_vgFinalText}</span>`
        : "") +
        (interim
        ? `<span style="color:var(--text-muted);font-style:italic">${interim}</span>`
        : "");
    }
  };

  rec.onerror = (event) => {
    const statusEl = document.getElementById("vgStatus");
    if (event.error === "not-allowed") {
      statusEl.textContent = "Microphone access denied — check browser permissions.";
    } else if (event.error === "no-speech") {
      statusEl.textContent = "No speech detected. Try again.";
    } else {
      statusEl.textContent = "Mic error: " + event.error;
    }
    statusEl.style.color = "var(--accent-red)";
    _stopMic();
  };

  rec.onend = () => {
    _stopMic();
    // Auto-submit if we captured something
    const text = _vgFinalText.trim();
    if (text) {
      document.getElementById("vgTestInput").value = text;
      document.getElementById("vgStatus").textContent = "Transcript captured — scanning for PII...";
      document.getElementById("vgStatus").style.color = "var(--text-muted)";
      // Short delay so user sees the textarea populate before processing
      setTimeout(() => testVoiceGuard(), 300);
    }
  };

  return rec;
}

function _stopMic() {
  _vgMicActive = false;
  const btn     = document.getElementById("vgMicBtn");
  const badge   = document.getElementById("vgMicBadge");
  const preview = document.getElementById("vgLivePreview");
  if (btn)     { btn.textContent = "🎙 Speak"; btn.classList.remove("vg-mic-active"); }
  if (badge)   badge.style.display = "none";
  if (preview) preview.style.display = "none";
  if (_vgRecognition) {
    try { _vgRecognition.stop(); } catch (_) {}
  }
}

function toggleVoiceMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    const statusEl = document.getElementById("vgStatus");
    statusEl.textContent = "Speech recognition not supported — use Chrome or Edge.";
    statusEl.style.color = "var(--accent-yellow)";
    return;
  }

  if (_vgMicActive) {
    // User hit Stop — finalize and submit
    if (_vgRecognition) _vgRecognition.stop();
    return;
  }

  _vgRecognition = _initSpeechRecognition();
  if (_vgRecognition) _vgRecognition.start();
}

// Load stats on page load and refresh every 30 seconds
document.addEventListener("DOMContentLoaded", () => {
  loadVoiceStats();
  loadVoiceAuditLog();
  setInterval(loadVoiceStats, 30000);
  setInterval(loadVoiceAuditLog, 30000);
});
