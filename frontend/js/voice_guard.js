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

    chipEl.innerHTML = aiChip + (Object.keys(breakdown).length === 0
      ? '<span style="font-size:12px;color:var(--text-muted);padding:6px 0">No PII events recorded yet — use the test box below to try it.</span>'
      : Object.entries(breakdown).map(([type, count]) => `
          <div style="background:var(--bg-panel);border:1px solid var(--border);border-radius:6px;padding:6px 14px;font-size:12px">
            <span style="color:var(--accent-red);font-weight:700">${type}</span>
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

    const piiList = data.pii_types_found.length
      ? data.pii_types_found.join(", ")
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

    // Refresh stats
    loadVoiceStats();

  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
    statusEl.style.color = "var(--accent-red)";
  }
}

// Load stats on page load and refresh every 30 seconds
document.addEventListener("DOMContentLoaded", () => {
  loadVoiceStats();
  setInterval(loadVoiceStats, 30000);
});
