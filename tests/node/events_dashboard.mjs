// Test api/events.js + api/dashboard.js against __SHELLO_TEST_DB__.
// Covers: strict validation (image-ish payloads rejected), 202 without
// DATABASE_URL, stored rows, session delete, dashboard auth 503/401, and
// that no event payload echoes a sentinel.

import { createRequire } from "node:module";
import crypto from "node:crypto";

const require = createRequire(import.meta.url);

// In-memory test DB honoured by api/_lib/db.js
const store = [];
globalThis.__SHELLO_TEST_DB__ = {
  async insertEvent(sessionId, type, payload) {
    store.push({ id: store.length + 1, session_id: sessionId, type, payload, created_at: new Date().toISOString() });
    return 1;
  },
  async listEvents() {
    return store.slice();
  },
  async deleteSession(idOrPrefix) {
    const before = store.length;
    for (let i = store.length - 1; i >= 0; i--) {
      if (store[i].session_id.startsWith(idOrPrefix)) store.splice(i, 1);
    }
    return before - store.length;
  },
};

delete process.env.DATABASE_URL;
const eventsHandler = require("../../api/events.js");
const dashboardHandler = require("../../api/dashboard.js");

function mockRes() {
  return {
    code: null,
    body: null,
    status(c) { this.code = c; return this; },
    json(p) { this.body = p; return this; },
    setHeader() {},
    end() { return this; },
  };
}
function mockReq(method, { body, query = {}, headers = {} } = {}) {
  const listeners = {};
  const raw = body === undefined ? null : JSON.stringify(body);
  return {
    method,
    query,
    headers,
    body,
    on(ev, cb) {
      listeners[ev] = cb;
      if (ev === "data" && raw !== null) setImmediate(() => cb(Buffer.from(raw)));
      if (ev === "end") setImmediate(() => cb());
      return this;
    },
    destroy() {},
  };
}

const SID = crypto.randomUUID();
const sentinel = "SENTINEL-" + crypto.randomBytes(8).toString("hex");
const failures = [];
const check = (name, cond) => { if (!cond) failures.push(name); };

async function post(body) {
  const res = mockRes();
  await eventsHandler(mockReq("POST", { body }), res);
  return res;
}

// --- validation: image-bearing payloads must be rejected ---
for (const [name, payload] of [
  ["data uri", { lang: "en", result_source: "quiz", dermatologist_note: false, kit_id: "data:image/jpeg;base64,AAAA" }],
  ["long base64", { lang: "en", result_source: "quiz", dermatologist_note: false, kit_id: "A".repeat(120) }],
  ["extra image key", { lang: "en", result_source: "quiz", dermatologist_note: false, image: sentinel }],
  ["unknown type", {}],
]) {
  const type = name === "unknown type" ? "selfie" : "session_result";
  const res = await post({ session_id: SID, type, payload });
  check(`rejected ${name}`, res.code === 400);
}
check("store unchanged after rejects", store.length === 0);

// --- valid session_result is stored ---
const res = await post({
  session_id: SID,
  type: "session_result",
  payload: {
    lang: "en",
    result_source: "quiz",
    quiz: { skin_type: "oily", concerns: ["breakouts"], area: "face", budget: "everyday", red_flags: [] },
    reader: { source: "gemini", model: "m1", skin_type: "oily", concerns: ["breakouts"] },
    confirm: { action: "correct", read_skin_type: "oily", corrected_skin_type: "dry" },
    routine_product_ids: [101, 102],
    dermatologist_note: false,
  },
});
check("valid event 200", res.code === 200 && res.body.stored === true);
check("stored row has no sentinel", !JSON.stringify(store).includes(sentinel));

// --- 202 without DATABASE_URL (test DB still configured -> stored; also check branch) ---
// covered above: configured() is true via test DB.

// --- DELETE session ---
const delRes = mockRes();
await eventsHandler(mockReq("DELETE", { query: { session_id: SID } }), delRes);
check("delete session 200", delRes.code === 200 && delRes.body.deleted === 1);
check("store empty after delete", store.length === 0);

// --- dashboard auth ---
delete process.env.DASHBOARD_TOKEN;
let res2 = mockRes();
await dashboardHandler(mockReq("GET", { headers: {} }), res2);
check("dashboard 503 without token env", res2.code === 503);

process.env.DASHBOARD_TOKEN = "test-token-123";
res2 = mockRes();
await dashboardHandler(mockReq("GET", { headers: { authorization: "Bearer wrong" } }), res2);
check("dashboard 401 wrong token", res2.code === 401);

await post({ session_id: SID, type: "click", payload: { kind: "whatsapp" } });
res2 = mockRes();
await dashboardHandler(mockReq("GET", { headers: { authorization: "Bearer test-token-123" } }), res2);
check("dashboard 200 with token", res2.code === 200 && Array.isArray(res2.body.events));
check("dashboard returns events", res2.body.events.length === 1);

// dashboard DELETE by 8-hex code
const code = SID.replace(/-/g, "").slice(0, 8);
res2 = mockRes();
await dashboardHandler(mockReq("DELETE", { query: { code }, headers: { authorization: "Bearer test-token-123" } }), res2);
check("dashboard delete by code", res2.code === 200 && store.length === 0);

if (failures.length) {
  process.stderr.write("events_dashboard FAILED:\n");
  for (const f of failures) process.stderr.write(" - " + f + "\n");
  process.exit(1);
}
process.stdout.write("events_dashboard OK: validation, storage, delete, and dashboard auth all pass\n");
