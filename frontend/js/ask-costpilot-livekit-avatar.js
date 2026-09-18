/**
 * ask-costpilot-livekit-avatar.js — shared real-time video avatar
 * connection logic, used by both global-nav.js's drawer and
 * ask-voice.html's mobile page. Keep this here, once, for the same
 * reason ask-costpilot-render.js's own docstring gives for sharing
 * answer-rendering logic: two independent surfaces, one contract, so
 * they can't silently drift apart.
 *
 * Deliberately additive to each page's existing avatar/voice flow, not
 * a replacement of it -- the turn-based record/transcribe/ask/speak
 * pipeline (with its own tuned VAD) stays the default, tested experience;
 * this is an opt-in "talk face-to-face, live" mode a page can offer
 * alongside it. Requires livekit-client (loaded from jsdelivr, already
 * an allowed script-src host) and the backend's POST /api/livekit/token
 * endpoint (api/routes_livekit.py).
 *
 * Reuses the exact same idle/listening/thinking/speaking state
 * vocabulary and CSS the static-image avatar already uses (data-state
 * on the avatar element, driven here by real LiveKit room events instead
 * of manual call-site transitions) -- the video element takes the image
 * element's place, the ring/animation CSS needs no changes at all.
 */

function createAskCostpilotAvatarConnection({ videoEl, onState, onError, onAnswer, onAudioBlocked }) {
  let room = null;
  let connected = false;

  function setState(state) {
    if (typeof onState === "function") onState(state);
  }

  async function connect() {
    if (connected) return;
    if (!window.LivekitClient) {
      onError?.(new Error("Live avatar library didn't load — check your connection and try again."));
      return;
    }
    setState("thinking"); // connecting
    try {
      const tokenRes = await fetch("/api/livekit/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: (localStorage.getItem("cp_workspace_id") || undefined) }),
      });
      const tokenData = await tokenRes.json().catch(() => ({}));
      if (!tokenRes.ok) {
        throw new Error(tokenData.detail || "Couldn't start a live avatar session.");
      }

      const { Room, RoomEvent, Track } = window.LivekitClient;
      room = new Room();

      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Video || track.kind === Track.Kind.Audio) {
          track.attach(videoEl);
        }
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach(videoEl);
      });
      // ActiveSpeakersChanged fires for BOTH the local mic (you talking)
      // and the avatar's own published audio (it talking) -- mapping
      // that directly onto the existing listening/speaking states is
      // what makes the ring genuinely reflect who's talking right now,
      // not just "connected" the whole call.
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const avatarSpeaking = speakers.some((p) => p.identity !== room.localParticipant.identity);
        const youSpeaking = speakers.some((p) => p.identity === room.localParticipant.identity);
        if (avatarSpeaking) setState("speaking");
        else if (youSpeaking) setState("listening");
        else setState("idle");
      });
      room.on(RoomEvent.Disconnected, (reason) => {
        // Surfaced verbatim rather than swallowed -- confirmed live
        // 2026-09-19 a call disconnected itself within ~2ms of the avatar
        // becoming ready with no error ever reaching this module's own
        // try/catch, so whatever is closing the room is happening inside
        // the SDK/server, not this code -- this is the only way to see
        // the real DisconnectReason instead of guessing at it.
        console.warn("[Ask CostPilot avatar] room disconnected, reason:", reason);
        connected = false;
        setState("idle");
        onError?.(new Error(`Live call ended (${reason ?? "unknown reason"}).`));
      });
      // Autoplay can be blocked by the browser the same way it can for
      // plain <audio> (see ask-voice.html's own unlockAudioPlayback
      // comment for the identical iOS Safari gotcha). onAudioBlocked
      // (rather than just onError) is what lets a host page show a
      // dedicated "tap for sound" control that calls retryAudio() below
      // WITHOUT ending the call -- the main avatar tap already means
      // "end call" once connected, so reusing it here would hang up
      // instead of fixing the audio.
      room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
        if (!room.canPlaybackAudio) {
          onAudioBlocked?.();
        }
      });
      // The realtime model only ever SPEAKS an answer -- confirmed live
      // 2026-09-19 that a live call showed nothing on screen at all, no
      // evidence, no table, no proposal card, unlike every other Ask
      // CostPilot surface. agents/livekit_avatar_worker.py publishes the
      // full JSON answer (the same shape /api/reports/bot-efficiency/ask
      // already returns) over this data channel after each tool call;
      // onAnswer is the host page's hook to render it the normal way
      // (renderAskAnswerCard) instead of leaving the screen blank.
      room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
        if (topic !== "ask-costpilot-answer") return;
        try {
          const data = JSON.parse(new TextDecoder().decode(payload));
          onAnswer?.(data);
        } catch (err) {
          console.warn("[Ask CostPilot avatar] couldn't parse published answer:", err);
        }
      });

      console.info("[Ask CostPilot avatar] connecting to room", tokenData.room, "at", tokenData.url);
      await room.connect(tokenData.url, tokenData.token);
      console.info("[Ask CostPilot avatar] room connected, enabling microphone…");
      connected = true;
      setState("listening");
      // LiveKit's own documented unlock for browser autoplay policy (most
      // browsers require a real user interaction before audio plays) --
      // called here, still inside the async chain the original tap
      // started, which is usually still close enough to count. iOS
      // Safari is the strictest about this window closing; when it does,
      // AudioPlaybackStatusChanged (above) fires and onAudioBlocked lets
      // the host page offer a direct retry from a fresh tap instead.
      try {
        await room.startAudio();
      } catch (audioErr) {
        console.warn("[Ask CostPilot avatar] startAudio() blocked, will retry on next tap:", audioErr);
      }
      // Isolated from the room-connect try/catch on purpose: a failure
      // here (no mic device, permission denied, another app has it
      // exclusively) should degrade to "you can hear/see the avatar but
      // it can't hear you," not tear down a room connection that
      // otherwise succeeded.
      try {
        await room.localParticipant.setMicrophoneEnabled(true);
        console.info("[Ask CostPilot avatar] microphone enabled");
      } catch (micErr) {
        console.warn("[Ask CostPilot avatar] microphone enable failed (call still connected):", micErr);
        onError?.(new Error(
          "Connected, but couldn't access your microphone — the avatar can't hear you. " +
          "Check mic permissions and try again."
        ));
      }
    } catch (err) {
      console.error("[Ask CostPilot avatar] connect failed:", err);
      connected = false;
      setState("idle");
      onError?.(err);
      room?.disconnect();
      room = null;
    }
  }

  function disconnect() {
    if (room) {
      room.disconnect();
      room = null;
    }
    connected = false;
    setState("idle");
  }

  function isConnected() {
    return connected;
  }

  // Call from a fresh, real tap (not automatically) when onAudioBlocked
  // has fired -- a later user gesture is exactly what LiveKit's own
  // startAudio() docs say unlocks playback, and unlike retrying the main
  // avatar tap, this never ends the call.
  async function retryAudio() {
    if (!room) return;
    try {
      await room.startAudio();
      console.info("[Ask CostPilot avatar] audio unlocked");
    } catch (err) {
      console.warn("[Ask CostPilot avatar] retryAudio() still blocked:", err);
      onError?.(new Error("Still couldn't enable sound — check your browser's site settings."));
    }
  }

  return { connect, disconnect, isConnected, retryAudio };
}
