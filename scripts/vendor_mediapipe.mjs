// Reproduce the vendored MediaPipe assets:
//   node scripts/vendor_mediapipe.mjs
//
// Copies vision_bundle.mjs + wasm/ from node_modules/@mediapipe/tasks-vision
// into vendor/mediapipe/<version>/ and downloads face_landmarker.task into
// models/face_landmarker/<model-version>/ if missing. Prints sha256 sums for
// models/MODELS.md.

import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { cp, mkdir, readFile, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const pkg = require("@mediapipe/tasks-vision/package.json");
const VERSION = pkg.version;
const MODEL_VERSION = "1"; // float16 bundle revision in the storage bucket
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")), "..");
const pkgDir = path.dirname(require.resolve("@mediapipe/tasks-vision/package.json"));
const vendorDir = path.join(root, "vendor", "mediapipe", VERSION);
const modelDir = path.join(root, "models", "face_landmarker", MODEL_VERSION);
const modelPath = path.join(modelDir, "face_landmarker.task");

// FilesetResolver.forVisionTasks() defaults to the plain wasm build; the
// *_module_internal.* variant is only used with useModule:true, so it is not
// vendored.
const WASM_FILES = ["vision_wasm_internal.js", "vision_wasm_internal.wasm"];
await mkdir(path.join(vendorDir, "wasm"), { recursive: true });
await cp(path.join(pkgDir, "vision_bundle.mjs"), path.join(vendorDir, "vision_bundle.mjs"));
for (const f of WASM_FILES) {
  await cp(path.join(pkgDir, "wasm", f), path.join(vendorDir, "wasm", f));
}
console.log(`copied vision_bundle.mjs + wasm/{${WASM_FILES.join(", ")}} -> vendor/mediapipe/${VERSION}/`);

if (!existsSync(modelPath)) {
  await mkdir(modelDir, { recursive: true });
  const resp = await fetch(MODEL_URL);
  if (!resp.ok) throw new Error(`model download failed: HTTP ${resp.status}`);
  const buf = Buffer.from(await resp.arrayBuffer());
  await import("node:fs/promises").then((fs) => fs.writeFile(modelPath, buf));
  console.log(`downloaded ${MODEL_URL} -> ${path.relative(root, modelPath)}`);
} else {
  console.log("model already present, skipping download");
}

async function sha256(p) {
  return createHash("sha256").update(await readFile(p)).digest("hex");
}

const files = [
  path.join(vendorDir, "vision_bundle.mjs"),
  path.join(vendorDir, "wasm", "vision_wasm_internal.js"),
  path.join(vendorDir, "wasm", "vision_wasm_internal.wasm"),
  modelPath,
];
for (const f of files) {
  const s = await stat(f);
  console.log(`${path.relative(root, f)}\t${s.size}\t${await sha256(f)}`);
}
