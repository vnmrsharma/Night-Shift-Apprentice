/* ElevenLabs agents. The engine writes the line. The agent only speaks it, in one warm voice. */
(function () {
  let conv = null;
  let role = null;
  let starting = null;
  let speaking = false;
  const client = import("https://esm.sh/@elevenlabs/client@0.14.0");

  function messageText(m) {
    if (!m) return "";
    if (typeof m === "string") return m;
    return m.message || m.text || "";
  }
  function messageSource(m) {
    if (!m || typeof m === "string") return "";
    return m.source || m.role || "";
  }

  const API = String(window.APPRENTICE_API || "").replace(/\/$/, "");

  async function stop() {
    const current = conv;
    conv = null;
    role = null;
    speaking = false;
    if (current) {
      try { await current.endSession(); } catch (e) { /* already closed */ }
    }
  }

  function start(nextRole) {
    nextRole = nextRole === "tutor" ? "tutor" : "interviewer";
    if (conv && role === nextRole) return Promise.resolve(true);
    if (starting) return starting.then(() => start(nextRole));
    starting = (async () => {
      await stop();
      const res = await fetch(API + "/voice/session?role=" + nextRole);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.signed_url) throw new Error(data.detail || "Voice is not available");
      await navigator.mediaDevices.getUserMedia({ audio: true });
      const { Conversation } = await client;
      const session = await Conversation.startSession({
        signedUrl: data.signed_url,
        connectionType: "websocket",
        onMessage(m) {
          const text = messageText(m).trim();
          if (messageSource(m) === "user" && text && text[0] !== "[") {
            speaking = false;
            if (window.ApprenticeVoice.onUser) window.ApprenticeVoice.onUser(text);
          }
        },
        onModeChange(m) {
          const mode = m && (m.mode || m);
          speaking = mode === "speaking";
        },
        onError() { speaking = false; }
      });
      conv = session;
      role = nextRole;
      return true;
    })();
    return starting.finally(() => { starting = null; });
  }

  function say(nextRole, tag, text) {
    if (!text) return Promise.resolve(false);
    return start(nextRole).then(() => {
      if (!conv) return false;
      if (tag === "CONTEXT") conv.sendContextualUpdate(String(text));
      else conv.sendUserMessage("[" + tag + "] " + text);
      return true;
    });
  }

  window.ApprenticeVoice = {
    start, stop, say,
    onUser: null,
    get speaking() { return speaking; },
    get role() { return role; },
    get connected() { return !!conv; }
  };
})();
