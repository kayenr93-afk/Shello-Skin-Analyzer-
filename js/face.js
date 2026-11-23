/* Lazy face detection via the vendored MediaPipe tasks-vision bundle.
 *
 * window.ShelloFace:
 *   load()    -> Promise<landmarker|null>; starts loading only when called
 *              (i.e. when the shopper taps take/upload selfie). Resolves to
 *              null if anything fails — never throws into app flow.
 *   status()  -> "idle"|"loading"|"ready"|"failed"
 *   onStatus(cb) -> notified on status changes (for loading UI)
 *   detect(image) -> Promise<{faces, box, landmarks}|null>
 *              box is the landmark bounding box, normalized 0..1.
 */
(function (root) {
  const BUNDLE_URL = "vendor/mediapipe/1.2.3/vision_bundle.mjs";
  const WASM_URL = "vendor/mediapipe/1.2.3/wasm";
  const MODEL_URL = "models/face_landmarker/1/face_landmarker.task";

  let landmarker = null;
  let loading = null;
  let state = "idle";
  const listeners = [];

  function setState(s) {
    state = s;
    listeners.forEach((cb) => {
      try {
        cb(s);
      } catch (_) {}
    });
  }

  async function load() {
    if (landmarker) return landmarker;
    if (loading) return loading;
    setState("loading");
    loading = (async () => {
      try {
        const abs = (p) => new URL(p, document.baseURI).href;
        const vision = await import(abs(BUNDLE_URL));
        const fileset = await vision.FilesetResolver.forVisionTasks(abs(WASM_URL));
        landmarker = await vision.FaceLandmarker.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: abs(MODEL_URL) },
          runningMode: "IMAGE",
          numFaces: 2,
        });
        setState("ready");
        return landmarker;
      } catch (_) {
        landmarker = null;
        setState("failed");
        return null;
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  async function detect(image) {
    const lm = await load();
    if (!lm) return null;
    try {
      const res = lm.detect(image);
      const faces = (res && res.faceLandmarks && res.faceLandmarks.length) || 0;
      if (!faces) return { faces: 0, box: null, landmarks: [] };
      const marks = res.faceLandmarks[0];
      let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
      for (const p of marks) {
        if (p.x < x0) x0 = p.x;
        if (p.y < y0) y0 = p.y;
        if (p.x > x1) x1 = p.x;
        if (p.y > y1) y1 = p.y;
      }
      return {
        faces,
        box: { x: x0, y: y0, width: x1 - x0, height: y1 - y0 },
        landmarks: marks,
      };
    } catch (_) {
      return null;
    }
  }

  root.ShelloFace = {
    load,
    detect,
    status: () => state,
    onStatus(cb) {
      listeners.push(cb);
    },
  };
})(window);
