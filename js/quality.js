/* Photo quality gate. Thresholds live in models/quality.json (fetched once,
 * with a baked-in copy as fallback) so Python can mirror them later.
 *
 * window.ShelloQuality.check(canvas, faceResult)
 *   -> { pass, reasons[], metrics{} }
 * reasons are stable keys: no_face | multiple_faces | face_too_small |
 * too_dark | too_bright | blurry.
 *
 * When face detection is unavailable (faceResult null), face-count/size
 * checks are skipped, exposure/sharpness run on the whole image, and
 * metrics.face_check is "unavailable". The dark check is deliberately
 * lenient — darker skin must not be failed for being darker.
 */
(function (root) {
  const FALLBACK = {
    face: { required_count: 1, min_height_frac_of_short_side: 0.3, min_height_px: 180 },
    exposure: {
      face_luma_min: 40,
      image_luma_min: 50,
      bright_pixel_luma: 250,
      bright_pixel_frac_max: 0.05,
    },
    sharpness: { resize_width: 256, laplacian_var_min: 40 },
  };

  let thresholdsPromise = null;
  function thresholds() {
    if (!thresholdsPromise) {
      thresholdsPromise = fetch("models/quality.json", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : FALLBACK))
        .catch(() => FALLBACK);
    }
    return thresholdsPromise;
  }

  function lumaArray(ctx, x, y, w, h) {
    const data = ctx.getImageData(x, y, w, h).data;
    const n = w * h;
    const luma = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const o = i * 4;
      luma[i] = 0.2126 * data[o] + 0.7152 * data[o + 1] + 0.0722 * data[o + 2];
    }
    return luma;
  }

  function regionFromBox(canvas, box) {
    if (!box) return { x: 0, y: 0, w: canvas.width, h: canvas.height };
    const x = Math.max(0, Math.floor(box.x * canvas.width));
    const y = Math.max(0, Math.floor(box.y * canvas.height));
    const w = Math.min(canvas.width - x, Math.ceil(box.width * canvas.width));
    const h = Math.min(canvas.height - y, Math.ceil(box.height * canvas.height));
    return w > 0 && h > 0 ? { x, y, w, h } : { x: 0, y: 0, w: canvas.width, h: canvas.height };
  }

  function laplacianVariance(ctx, x, y, w, h, resizeW) {
    // Grayscale, resized to resizeW width, then variance of the Laplacian.
    const scale = Math.min(1, resizeW / w);
    const rw = Math.max(3, Math.round(w * scale));
    const rh = Math.max(3, Math.round(h * scale));
    const tmp = document.createElement("canvas");
    tmp.width = rw;
    tmp.height = rh;
    tmp.getContext("2d").drawImage(ctx.canvas, x, y, w, h, 0, 0, rw, rh);
    const luma = lumaArray(tmp.getContext("2d"), 0, 0, rw, rh);
    let sum = 0;
    let sumSq = 0;
    let count = 0;
    for (let j = 1; j < rh - 1; j++) {
      for (let i = 1; i < rw - 1; i++) {
        const v =
          luma[j * rw + i - 1] +
          luma[j * rw + i + 1] +
          luma[(j - 1) * rw + i] +
          luma[(j + 1) * rw + i] -
          4 * luma[j * rw + i];
        sum += v;
        sumSq += v * v;
        count++;
      }
    }
    if (!count) return 0;
    const mean = sum / count;
    return sumSq / count - mean * mean;
  }

  async function check(canvas, faceResult) {
    const cfg = await thresholds();
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const reasons = [];
    const metrics = {};

    const faceOk = faceResult && typeof faceResult.faces === "number";
    metrics.face_check = faceOk ? "ran" : "unavailable";
    metrics.faces = faceOk ? faceResult.faces : null;

    let region = { x: 0, y: 0, w: canvas.width, h: canvas.height };
    if (faceOk) {
      if (faceResult.faces !== cfg.face.required_count) {
        reasons.push(faceResult.faces === 0 ? "no_face" : "multiple_faces");
      } else if (faceResult.box) {
        const shortSide = Math.min(canvas.width, canvas.height);
        const boxH = faceResult.box.height * canvas.height;
        const need = Math.max(
          cfg.face.min_height_frac_of_short_side * shortSide,
          Math.min(cfg.face.min_height_px, shortSide)
        );
        metrics.face_box_height_px = Math.round(boxH);
        metrics.face_box_min_px = Math.round(need);
        if (boxH < need) reasons.push("face_too_small");
        region = regionFromBox(canvas, faceResult.box);
      }
    }

    // Exposure: face (or whole) mean luma vs whole-image mean luma.
    const faceLuma = lumaArray(ctx, region.x, region.y, region.w, region.h);
    let faceMean = 0;
    let bright = 0;
    for (const v of faceLuma) {
      faceMean += v;
      if (v >= cfg.exposure.bright_pixel_luma) bright++;
    }
    faceMean /= faceLuma.length || 1;
    const imgLuma = faceOk
      ? lumaArray(ctx, 0, 0, canvas.width, canvas.height)
      : faceLuma;
    let imgMean = 0;
    for (const v of imgLuma) imgMean += v;
    imgMean /= imgLuma.length || 1;
    metrics.face_luma_mean = Math.round(faceMean);
    metrics.image_luma_mean = Math.round(imgMean);
    metrics.bright_frac = bright / (faceLuma.length || 1);
    if (faceMean < cfg.exposure.face_luma_min && imgMean < cfg.exposure.image_luma_min) {
      reasons.push("too_dark");
    }
    if (metrics.bright_frac > cfg.exposure.bright_pixel_frac_max) {
      reasons.push("too_bright");
    }

    metrics.laplacian_var = Math.round(
      laplacianVariance(ctx, region.x, region.y, region.w, region.h, cfg.sharpness.resize_width)
    );
    if (metrics.laplacian_var < cfg.sharpness.laplacian_var_min) {
      reasons.push("blurry");
    }

    return { pass: reasons.length === 0, reasons, metrics };
  }

  root.ShelloQuality = { check, thresholds };
})(window);
