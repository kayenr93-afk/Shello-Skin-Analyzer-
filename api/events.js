/**
 * POST /api/events — anonymous, photo-free feedback log.
 * DELETE /api/events?session_id=<uuid> — delete a session's events.
 *
 * Bodies are validated strictly against api/_lib/event_schema.json (shared
 * with server.py) and are never logged. When DATABASE_URL is unset, POST
 * returns 202 {stored:false} — the event is accepted but not stored.
 */

const db = require("./_lib/db");
const { SCHEMA, validateEvent } = require("./_lib/validate");

const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

function noStore(res) {
  res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate");
}

function readRaw(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (c) => {
      size += c.length;
      if (size > SCHEMA.max_body_bytes + 1) {
        resolve(null); // oversized; respond 400 below
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

async function getBody(req) {
  if (req.body !== undefined) {
    const raw = typeof req.body === "string" ? req.body : JSON.stringify(req.body);
    if (Buffer.byteLength(raw) > SCHEMA.max_body_bytes) return { tooBig: true };
    return { body: typeof req.body === "string" ? safeParse(raw) : req.body };
  }
  const raw = await readRaw(req);
  if (raw === null || raw.length > SCHEMA.max_body_bytes) return { tooBig: true };
  return { body: safeParse(raw.toString("utf8")) };
}

function safeParse(s) {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
}

module.exports = async function handler(req, res) {
  noStore(res);
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  if (req.method === "DELETE") {
    const sid = (req.query && req.query.session_id) || "";
    if (!UUID_RE.test(String(sid))) {
      res.status(400).json({ ok: false, error: "invalid session_id" });
      return;
    }
    try {
      const n = await db.deleteSession(sid);
      res.status(200).json({ ok: true, deleted: n });
    } catch {
      res.status(500).json({ ok: false });
    }
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ ok: false });
    return;
  }

  const { body, tooBig } = await getBody(req);
  if (tooBig || body === undefined || typeof body !== "object" || body === null) {
    res.status(400).json({ ok: false });
    return;
  }
  const keys = Object.keys(body);
  if (keys.some((k) => !["type", "payload", "session_id"].includes(k)) || keys.length > 3) {
    res.status(400).json({ ok: false });
    return;
  }
  const { type, payload } = body;
  const sessionId = body.session_id;
  if (typeof type !== "string" || !UUID_RE.test(String(sessionId))) {
    res.status(400).json({ ok: false });
    return;
  }
  const err = validateEvent(type, payload);
  if (err) {
    res.status(400).json({ ok: false });
    return;
  }
  if (!db.configured()) {
    res.status(202).json({ ok: true, stored: false });
    return;
  }
  try {
    await db.insertEvent(sessionId, type, payload);
    res.status(200).json({ ok: true, stored: true });
  } catch {
    res.status(500).json({ ok: false });
  }
};
