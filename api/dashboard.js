/**
 * GET  /api/dashboard?days=N   — raw events (admin; Bearer DASHBOARD_TOKEN).
 * DELETE /api/dashboard?code=<8hex> — delete one session by id prefix.
 *
 * 503 when DASHBOARD_TOKEN is unset; 401 on a wrong token (constant-time
 * compare). Aggregation happens client-side in js/dashboard.js. Event bodies
 * are never logged.
 */

const crypto = require("crypto");
const db = require("./_lib/db");

function noStore(res) {
  res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate");
}

function tokenOk(req) {
  const token = process.env.DASHBOARD_TOKEN;
  if (!token) return "unset";
  const header = String((req.headers && (req.headers.authorization || req.headers.Authorization)) || "");
  const got = header.replace(/^Bearer\s+/i, "");
  const a = Buffer.from(got);
  const b = Buffer.from(token);
  return a.length === b.length && crypto.timingSafeEqual(a, b) ? "ok" : "bad";
}

module.exports = async function handler(req, res) {
  noStore(res);
  const auth = tokenOk(req);
  if (auth === "unset") {
    res.status(503).json({ ok: false, error: "dashboard not configured" });
    return;
  }
  if (auth !== "ok") {
    res.status(401).json({ ok: false });
    return;
  }

  if (req.method === "DELETE") {
    const code = String((req.query && req.query.code) || "");
    if (!/^[0-9a-fA-F]{8}$/.test(code)) {
      res.status(400).json({ ok: false, error: "code must be 8 hex chars" });
      return;
    }
    try {
      const n = await db.deleteSession(code);
      res.status(200).json({ ok: true, deleted: n });
    } catch {
      res.status(500).json({ ok: false });
    }
    return;
  }
  if (req.method !== "GET") {
    res.status(405).json({ ok: false });
    return;
  }
  if (!db.configured()) {
    res.status(200).json({ ok: true, stored: false, events: [] });
    return;
  }
  let days = parseInt(String((req.query && req.query.days) || "30"), 10);
  if (!Number.isFinite(days) || days < 1) days = 30;
  days = Math.min(days, 180);
  const since = new Date(Date.now() - days * 86400000);
  try {
    const events = await db.listEvents({ since, limit: 50000 });
    res.status(200).json({ ok: true, events, days });
  } catch {
    res.status(500).json({ ok: false });
  }
};
