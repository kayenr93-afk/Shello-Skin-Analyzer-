// Storage for the anonymous feedback log. Uses Neon via DATABASE_URL in
// production; honours globalThis.__SHELLO_TEST_DB__ (an in-memory fake with
// the same interface) in tests. Never logs event bodies.

let _sql = null;
async function sql() {
  if (!_sql) {
    const { neon } = await import("@neondatabase/serverless");
    _sql = neon(process.env.DATABASE_URL);
  }
  return _sql;
}

function testDb() {
  return globalThis.__SHELLO_TEST_DB__ || null;
}

function configured() {
  return Boolean(testDb()) || Boolean(process.env.DATABASE_URL);
}

async function insertEvent(sessionId, type, payload) {
  const t = testDb();
  if (t) return t.insertEvent(sessionId, type, payload);
  const q = await sql();
  await q`insert into events (session_id, type, payload) values (${sessionId}::uuid, ${type}, ${JSON.stringify(payload)}::jsonb)`;
}

async function listEvents({ since, limit } = {}) {
  const t = testDb();
  if (t) return t.listEvents({ since, limit });
  const q = await sql();
  const rows = await q`select id, session_id, type, payload, created_at from events where created_at >= ${since} order by created_at desc limit ${limit}`;
  return rows;
}

// idOrPrefix: full uuid deletes exactly; a shorter hex prefix deletes all
// sessions whose id starts with it (used by the dashboard code deletion).
async function deleteSession(idOrPrefix) {
  const t = testDb();
  if (t) return t.deleteSession(idOrPrefix);
  const q = await sql();
  const s = String(idOrPrefix);
  if (/^[0-9a-fA-F-]{36}$/.test(s)) {
    const rows = await q`delete from events where session_id = ${s}::uuid returning id`;
    return rows.length;
  }
  const rows = await q`delete from events where session_id::text like ${s + "%"} returning id`;
  return rows.length;
}

module.exports = { configured, insertEvent, listEvents, deleteSession };
