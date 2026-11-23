// Verify api/analyze.js never writes the image to disk or logs it.
//
// Imports the handler, points it at a local stub "Gemini" server via
// GEMINI_API_BASE, monkeypatches console.* and fs write functions, then POSTs
// a body carrying a unique sentinel. Asserts no fs writes happened and no
// logged/response string contains the sentinel or the base64 prefix.

import http from "node:http";
import { createRequire } from "node:module";
import fs from "node:fs";
import crypto from "node:crypto";

const sentinel = `SENTINEL-${crypto.randomBytes(16).toString("hex")}`;
const imageBytes = Buffer.concat([Buffer.from([0xff, 0xd8]), Buffer.from(sentinel), Buffer.from([0xff, 0xd9])]);
const b64 = imageBytes.toString("base64");
const b64Prefix = b64.slice(0, 40);

let stubSawImage = false;
const stub = http.createServer((req, res) => {
  let raw = "";
  req.on("data", (c) => (raw += c));
  req.on("end", () => {
    try {
      const body = JSON.parse(raw || "{}");
      const parts = (body.contents && body.contents[0] && body.contents[0].parts) || [];
      for (const p of parts) {
        if (p.inlineData && p.inlineData.data === b64) stubSawImage = true;
      }
    } catch {}
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        candidates: [
          {
            content: {
              parts: [
                {
                  text: JSON.stringify({
                    skin_type_hint: "normal",
                    concerns: ["breakouts"],
                    notes: "stub",
                    refer_dermatologist: false,
                    confidence: "low",
                  }),
                },
              ],
            },
          },
        ],
      })
    );
  });
});
await new Promise((r) => stub.listen(0, "127.0.0.1", r));
const stubPort = stub.address().port;

// Env must be set before the handler module is required: it reads the API
// base override at load time.
process.env.GEMINI_API_KEY = "dummy-key-for-tests";
process.env.GEMINI_API_BASE = `http://127.0.0.1:${stubPort}`;
delete process.env.OPENAI_API_KEY;

const require = createRequire(import.meta.url);
const handler = require("../../api/analyze.js");

const logged = [];
for (const name of ["log", "error", "warn", "info", "debug"]) {
  console[name] = (...args) => logged.push(args.map(String).join(" "));
}

const writes = [];
const writeFns = ["writeFileSync", "writeFile", "appendFileSync", "appendFile", "createWriteStream"];
const originals = {};
for (const fn of writeFns) {
  originals[fn] = fs[fn];
  fs[fn] = (...args) => {
    writes.push([fn, String(args[0])]);
    if (fn === "createWriteStream") throw new Error("fs write blocked in privacy test");
    if (typeof args[args.length - 1] === "function") args[args.length - 1](null);
    return undefined;
  };
}

const resCalls = [];
const res = {
  _status: 0,
  status(code) {
    this._status = code;
    return this;
  },
  setHeader() {},
  json(payload) {
    resCalls.push(payload);
    return this;
  },
  end() {},
};

const req = {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: { image_base64: b64, mime_type: "image/jpeg" },
};

let failed = null;
try {
  await handler(req, res);
} catch (err) {
  failed = err;
}

for (const fn of writeFns) fs[fn] = originals[fn];
stub.close();

const leak = (s) => s.includes(sentinel) || s.includes(b64Prefix);
const failures = [];
if (failed) failures.push(`handler threw: ${failed}`);
if (!stubSawImage) failures.push("stub provider did not receive the image body");
if (writes.length) failures.push(`fs writes attempted: ${JSON.stringify(writes)}`);
const leakedLogs = logged.filter(leak);
if (leakedLogs.length) failures.push(`image leaked into console output: ${leakedLogs.length} line(s)`);
if (resCalls.some((p) => leak(JSON.stringify(p)))) failures.push("image leaked into the response body");

if (failures.length) {
  process.stderr.write("analyze_privacy FAILED:\n");
  for (const f of failures) process.stderr.write(" - " + f + "\n");
  process.exit(1);
}
process.stdout.write(
  "analyze_privacy OK: no fs writes, no image in logs or response, stub received image\n"
);
