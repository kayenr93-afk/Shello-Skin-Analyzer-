# Vendored models and runtimes

Self-hosted copies of the face-quality-gate assets. Regenerate with:

```bash
node scripts/vendor_mediapipe.mjs
```

## @mediapipe/tasks-vision 1.2.3 (npm, published 2026-11-11)

Copied from `node_modules/@mediapipe/tasks-vision` into `vendor/mediapipe/1.2.3/`.
`FilesetResolver.forVisionTasks()` uses the plain wasm build by default, so the
`vision_wasm_module_internal.*` variant is intentionally not vendored.

| file | bytes | gz bytes | sha256 |
|---|---|---|---|
| vision_bundle.mjs | 155990 | 44958 | fb0c02c296f44ce0cc324ba16bd646d8773eb4a7847db4019cc4d0430db7a9b5 |
| wasm/vision_wasm_internal.js | 336624 | - | 2aa5fd3fdd3d8208cd7587df0bb19fb6925c4ea16a76dd025aedc1f64d6a6309 |
| wasm/vision_wasm_internal.wasm | 14665716 | 4165875 | 225bc06c0f51a9cf31fea0e9e0b0243e9e01fb5ef332cc1a7fa6656dbdfdaabf |

## face_landmarker.task (float16, MediaPipe model bundle v1)

Downloaded from
`https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`
into `models/face_landmarker/1/face_landmarker.task`:

| file | bytes | gz bytes | sha256 |
|---|---|---|---|
| face_landmarker.task | 3758596 | 3332893 | 64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff |
