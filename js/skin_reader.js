/* On-device skin reader (ONNX Runtime Web, vendored, lazy-loaded).
 *
 * Mirrors ml/face/preprocess.py + ml/eval/readers/onnx_reader.py using the
 * shared models/skin_reader/face_regions.json geometry, so the phone sees the
 * same masked face crop and skin patches the model was trained on.
 *
 * window.ShelloSkinReader:
 *   load()  -> Promise<boolean>; fetches runtime + model only when called.
 *   read(source, landmarks) -> Promise<result|null>
 *     result = { observations, confident, meta, probs }
 *     observations has the same shape /api/analyze returns.
 *   status() -> "idle"|"loading"|"ready"|"failed"
 * The photo never leaves the device on this path.
 */
(function (root) {
  const ORT_URL = "vendor/onnxruntime-web/1.31.1/ort.wasm.bundle.min.mjs";
  const ORT_WASM_DIR = "vendor/onnxruntime-web/1.31.1/";
  const ACTIVE_URL = "models/skin_reader/active.json";
  const REGIONS_URL = "models/skin_reader/face_regions.json";
  const MAX_SIDE = 1024;

  let state = "idle";
  let loading = null;
  let ctx = null; // { ort, session, meta, regions, base }

  const abs = (p) => new URL(p, document.baseURI).href;

  async function fetchJson(url) {
    const r = await fetch(abs(url), { cache: "no-cache" });
    if (!r.ok) throw new Error("fetch " + url);
    return r.json();
  }

  async function load() {
    if (ctx) return true;
    if (loading) return loading;
    state = "loading";
    loading = (async () => {
      try {
        const active = await fetchJson(ACTIVE_URL);
        const base = "models/skin_reader/" + active.version + "/";
        const [ort, meta, regions] = await Promise.all([
          import(abs(ORT_URL)),
          fetchJson(base + "meta.json"),
          fetchJson(REGIONS_URL),
        ]);
        ort.env.wasm.wasmPaths = abs(ORT_WASM_DIR);
        ort.env.wasm.numThreads = 1; // no SharedArrayBuffer / COOP-COEP needed
        ort.env.wasm.proxy = false;
        const session = await ort.InferenceSession.create(abs(base + "model.onnx"), {
          executionProviders: ["wasm"],
        });
        ctx = { ort, session, meta, regions };
        state = "ready";
        return true;
      } catch (_) {
        ctx = null;
        state = "failed";
        return false;
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  // ---------- geometry (same maths as preprocess.py) ----------
  function bbox(marks, w, h) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of marks) {
      x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y);
      x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y);
    }
    return [x0 * w, y0 * h, x1 * w, y1 * h];
  }

  function convexHull(points) {
    const pts = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    if (pts.length <= 2) return pts;
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lower = [], upper = [];
    for (const p of pts) {
      while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
      lower.push(p);
    }
    for (let i = pts.length - 1; i >= 0; i--) {
      const p = pts[i];
      while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
      upper.push(p);
    }
    return lower.slice(0, -1).concat(upper.slice(0, -1));
  }

  function poly(g, pts) {
    g.beginPath();
    pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    g.closePath();
  }

  // Paint the skin mask (face oval minus dilated eyes/brows/lips) in `color`
  // onto g, in image pixel coordinates (caller sets the transform).
  function paintMask(g, marks, w, h, R, holeColor) {
    const [x0, , x1] = bbox(marks, w, h);
    const r = Math.round(R.mask_dilate_frac * (x1 - x0));
    const P = (i) => [marks[i].x * w, marks[i].y * h];
    poly(g, R.face_oval.map(P));
    g.fill();
    g.fillStyle = holeColor;
    g.strokeStyle = holeColor;
    g.lineJoin = "round";
    g.lineCap = "round";
    g.lineWidth = 2 * r + 1;
    for (const name of R.skin_mask.exclude) {
      let pts = R.polygons[name].map(P);
      if (R.convex_hull_features.includes(name)) pts = convexHull(pts);
      poly(g, pts);
      g.fill();
      if (r > 0) g.stroke();
    }
  }

  function cropBox(marks, w, h, R) {
    const [x0, y0, x1, y1] = bbox(marks, w, h);
    const side = Math.max(x1 - x0, y1 - y0) * (1 + R.crop.expand_frac);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    return [cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2].map(Math.round);
  }

  function patchBoxes(marks, w, h, R) {
    const [x0, y0, x1, y1] = bbox(marks, w, h);
    const fw = x1 - x0, fh = y1 - y0;
    const out = {};
    for (const [name, s] of Object.entries(R.patches)) {
      let cx, cy;
      if (s.centroid_of) {
        cx = (s.centroid_of.reduce((a, i) => a + marks[i].x, 0) / s.centroid_of.length) * w;
        cy = (s.centroid_of.reduce((a, i) => a + marks[i].y, 0) / s.centroid_of.length) * h;
      } else {
        cx = marks[s.center].x * w;
        cy = marks[s.center].y * h;
      }
      cx += (s.offset_x_face_w || 0) * fw;
      cy += (s.offset_y_face_h || 0) * fh;
      const side = s.side_frac * fw;
      out[name] = [cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2].map(Math.round);
    }
    return out;
  }

  function canvas2d(w, h) {
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    return [c, c.getContext("2d", { willReadFrequently: true })];
  }

  // Binary skin mask at image resolution -> Uint8Array (1 = skin).
  function maskArray(marks, w, h, R) {
    const [, g] = canvas2d(w, h);
    g.fillStyle = "#fff";
    paintMask(g, marks, w, h, R, "#000");
    const d = g.getImageData(0, 0, w, h).data;
    const m = new Uint8Array(w * h);
    for (let i = 0; i < m.length; i++) m[i] = d[i * 4] > 127 ? 1 : 0;
    return m;
  }

  function maskFrac(m, w, h, b) {
    const xa = Math.max(0, b[0]), ya = Math.max(0, b[1]);
    const xb = Math.min(w, b[2]), yb = Math.min(h, b[3]);
    if (xb <= xa || yb <= ya) return 0;
    let s = 0;
    for (let y = ya; y < yb; y++) for (let x = xa; x < xb; x++) s += m[y * w + x];
    return s / ((xb - xa) * (yb - ya));
  }

  function drawCrop(src, b, size, fill) {
    const [c, g] = canvas2d(size, size);
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = "high";
    if (fill) {
      g.fillStyle = fill;
      g.fillRect(0, 0, size, size);
    }
    g.drawImage(src, b[0], b[1], b[2] - b[0], b[3] - b[1], 0, 0, size, size);
    return [c, g];
  }

  function faceCrop(src, marks, w, h, R) {
    const size = R.crop.output_size;
    const fill = "rgb(" + R.crop.mask_fill_rgb.join(",") + ")";
    const b = cropBox(marks, w, h, R);
    // Skin pixels from the photo; everything else (outside oval, features,
    // out-of-frame) becomes the fill colour, as in preprocess.face_crop.
    const [mc, mg] = canvas2d(w, h);
    mg.fillStyle = "#fff";
    paintMask(mg, marks, w, h, R, "#000");
    // Turn the white mask into alpha, then keep photo pixels under it.
    const md = mg.getImageData(0, 0, w, h);
    for (let i = 0; i < md.data.length; i += 4) md.data[i + 3] = md.data[i];
    mg.putImageData(md, 0, 0);
    mg.globalCompositeOperation = "source-in";
    mg.drawImage(src, 0, 0, w, h);
    return drawCrop(mc, b, size, fill)[0];
  }

  function toTensorData(canvases, meta) {
    const S = meta.input_size;
    const n = canvases.length;
    const out = new Float32Array(n * 3 * S * S);
    canvases.forEach((c, k) => {
      const d = c.getContext("2d").getImageData(0, 0, S, S).data;
      for (let i = 0; i < S * S; i++) {
        for (let ch = 0; ch < 3; ch++) {
          out[k * 3 * S * S + ch * S * S + i] = (d[i * 4 + ch] / 255 - meta.mean[ch]) / meta.std[ch];
        }
      }
    });
    return out;
  }

  async function run(canvases) {
    const { ort, session, meta } = ctx;
    const S = meta.input_size;
    const x = new ort.Tensor("float32", toTensorData(canvases, meta), [canvases.length, 3, S, S]);
    const out = await session.run({ input: x });
    return [out.skin_type_logits, out.concern_logits];
  }

  function toCanvas(source) {
    const sw = source.naturalWidth || source.videoWidth || source.width;
    const sh = source.naturalHeight || source.videoHeight || source.height;
    const k = Math.min(1, MAX_SIDE / Math.max(sw, sh));
    const w = Math.max(1, Math.round(sw * k)), h = Math.max(1, Math.round(sh * k));
    const [c, g] = canvas2d(w, h);
    g.drawImage(source, 0, 0, w, h);
    return c;
  }

  async function read(source, landmarks) {
    if (!(await load())) return null;
    const t0 = performance.now();
    try {
      const { meta, regions: R } = ctx;
      const img = toCanvas(source);
      const w = img.width, h = img.height;
      let probs = null, skinType = null, top = 0;
      let patchCanvases = [];
      if (landmarks && landmarks.length >= 468) {
        const mask = maskArray(landmarks, w, h, R);
        const b = cropBox(landmarks, w, h, R);
        const side = Math.max(1, b[2] - b[0]);
        const visW = Math.min(b[2], w) - Math.max(b[0], 0);
        const visH = Math.min(b[3], h) - Math.max(b[1], 0);
        const clip = 1 - Math.min(1, (visW * visH) / (side * side));
        const partial = clip > R.partial_face.crop_clip_frac ||
          maskFrac(mask, w, h, b) < R.partial_face.min_skin_fraction_of_crop;
        if (!partial) {
          const [sl] = await run([faceCrop(img, landmarks, w, h, R)]);
          const l = Array.from(sl.data);
          const mx = Math.max(...l);
          const e = l.map((v) => Math.exp(v - mx));
          const z = e.reduce((a, v) => a + v, 0);
          const p = e.map((v) => v / z);
          probs = {};
          meta.skin_labels.forEach((n, i) => (probs[n] = p[i]));
          top = Math.max(...p);
          if (meta.tau != null && top >= meta.tau) skinType = meta.skin_labels[p.indexOf(top)];
          const v = R.patch_validity;
          for (const pb of Object.values(patchBoxes(landmarks, w, h, R))) {
            const inside = pb[0] >= 0 && pb[1] >= 0 && pb[2] <= w && pb[3] <= h;
            if ((!v.must_fit_inside_image || inside) && maskFrac(mask, w, h, pb) >= v.min_skin_fraction) {
              patchCanvases.push(drawCrop(img, pb, R.patch_output_size)[0]);
            }
          }
        }
      }
      if (!patchCanvases.length) {
        const s = Math.min(w, h);
        const x = Math.floor((w - s) / 2), y = Math.floor((h - s) / 2);
        patchCanvases = [drawCrop(img, [x, y, x + s, y + s], R.patch_output_size)[0]];
      }
      const [, cl] = await run(patchCanvases);
      const nC = meta.concern_labels.length;
      const scores = meta.concern_labels.map((name, c) => {
        let m = -Infinity;
        for (let k = 0; k < patchCanvases.length; k++) m = Math.max(m, cl.data[k * nC + c]);
        return { name, p: 1 / (1 + Math.exp(-m)) };
      });
      const hits = scores
        .filter((s) => s.p >= (meta.thresholds[s.name] ?? 1))
        .sort((a, b) => b.p - a.p);
      const concerns = [];
      for (const s of hits) {
        const app = meta.app_concern_map[s.name];
        if (app && !concerns.includes(app)) concerns.push(app);
      }
      const confident = skinType != null;
      return {
        confident,
        probs,
        concern_scores: Object.fromEntries(scores.map((s) => [s.name, s.p])),
        observations: {
          skin_type_hint: skinType,
          concerns,
          notes: "",
          refer_dermatologist: false,
          confidence: !confident ? "low" : top >= 0.85 ? "high" : "medium",
        },
        meta: {
          provider: "device",
          model: meta.version,
          prompt_version: null,
          latency_ms: Math.round(performance.now() - t0),
          confidence: probs ? Math.round(top * 1000) / 1000 : null,
        },
      };
    } catch (_) {
      return null;
    }
  }

  root.ShelloSkinReader = { load, read, status: () => state };
})(window);
