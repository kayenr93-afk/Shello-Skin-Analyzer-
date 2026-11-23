/* Anonymous, photo-free feedback log. window.ShelloEvents:
 *   sessionId()      -> uuid, stable for the tab (sessionStorage)
 *   code()           -> first 8 hex of the session id
 *   send(type, obj)  -> fire-and-forget keepalive POST /api/events; errors
 *                       are swallowed and never block the UI.
 *   forget()         -> DELETE /api/events?session_id=…
 * Only the allowlisted fields are sent — never the photo or anything
 * derived from it.
 */
(function (root) {
  const KEY = "shello-session-id";

  function sessionId() {
    let id = null;
    try {
      id = sessionStorage.getItem(KEY);
      if (!id) {
        id = crypto.randomUUID();
        sessionStorage.setItem(KEY, id);
      }
    } catch (_) {
      id = id || "00000000-0000-4000-8000-000000000000";
    }
    return id;
  }

  function send(type, payload) {
    try {
      fetch("/api/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId(), type, payload }),
        keepalive: true,
      }).catch(() => {});
    } catch (_) {}
  }

  async function forget() {
    try {
      const resp = await fetch(
        "/api/events?session_id=" + encodeURIComponent(sessionId()),
        { method: "DELETE" }
      );
      return resp.ok;
    } catch (_) {
      return false;
    } finally {
      try { sessionStorage.removeItem(KEY); } catch (_) {}
    }
  }

  root.ShelloEvents = {
    sessionId,
    code: () => sessionId().replace(/-/g, "").slice(0, 8),
    send,
    forget,
  };
})(window);
