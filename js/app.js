(function () {
  const panel = document.getElementById("panel");
  const progressEl = document.getElementById("progress");
  const matcher = window.ShelloMatcher;
  const i18n = window.ShelloI18n;
  const t = function (key, vars) { return i18n.t(key, vars); };

  const QUIZ_TOTAL = 6;
  const STEP = {
    landing: 0,
    skin: 1,
    concerns: 2,
    area: 3,
    climate: 4,
    budget: 5,
    selfie: 6,
    result: 7,
  };

  function skinTypes() {
    return [
      { id: "oily", title: t("skin_oily"), note: t("skin_oily_note") },
      { id: "dry", title: t("skin_dry"), note: t("skin_dry_note") },
      { id: "combination", title: t("skin_combination"), note: t("skin_combination_note") },
      { id: "normal", title: t("skin_normal"), note: t("skin_normal_note") },
      { id: "sensitive", title: t("skin_sensitive"), note: t("skin_sensitive_note") },
    ];
  }

  function concerns() {
    return [
      { id: "breakouts", title: t("concern_breakouts") },
      { id: "oil_control", title: t("concern_oil_control") },
      { id: "dryness", title: t("concern_dryness") },
      { id: "hydration", title: t("concern_hydration") },
      { id: "dullness", title: t("concern_dullness") },
      { id: "uneven_tone", title: t("concern_uneven_tone") },
      { id: "tan", title: t("concern_tan") },
      { id: "sensitivity", title: t("concern_sensitivity") },
      { id: "sun_protection", title: t("concern_sun_protection") },
    ];
  }

  function areas() {
    return [
      { id: "face", title: t("area_face"), note: t("area_face_note") },
      { id: "body", title: t("area_body"), note: t("area_body_note") },
      { id: "both", title: t("area_both"), note: t("area_both_note") },
    ];
  }

  function redFlags() {
    return [
      { id: "painful_cysts", title: t("flag_painful_cysts") },
      { id: "rashes", title: t("flag_rashes") },
      { id: "bleeding", title: t("flag_bleeding") },
      { id: "sudden_changes", title: t("flag_sudden_changes") },
    ];
  }

  function climateAir() {
    return [
      { id: "humid", title: t("climate_humid"), note: t("climate_humid_note") },
      { id: "ac", title: t("climate_ac"), note: t("climate_ac_note") },
      { id: "mixed", title: t("climate_mixed"), note: t("climate_mixed_note") },
    ];
  }

  function climateOutdoors() {
    return [
      { id: "often", title: t("climate_outdoors_often") },
      { id: "sometimes", title: t("climate_outdoors_sometimes") },
      { id: "rarely", title: t("climate_outdoors_rarely") },
    ];
  }

  const state = {
    catalog: null,
    step: 0,
    quiz: {
      skin_type: null,
      concerns: [],
      area: "face",
      budget: "everyday",
      red_flags: [],
      climate: null,
      outdoors: null,
    },
    selfie: { file: null, dataUrl: null, observations: null, analyzed: false },
    camera: { phase: "idle", fallback: false, message: null },
    analysisAvailable: false,
    analysisProvider: null,
    result: null,
    resultSource: "quiz",
    cartFollowup: [],
    concernLimitNote: false,
    analysisNote: null,
  };

  function budgetOptions() {
    const bands = (state.catalog && state.catalog.budget_bands) || [];
    return bands.map((b) => {
      const cap = b.max_lkr == null ? t("budget_no_cap") : t("budget_up_to", { amount: matcher.formatRs(b.max_lkr) });
      return { id: b.id, title: `${b.label} · ${cap}`, note: b.note, max_lkr: b.max_lkr };
    });
  }

  async function loadCatalog() {
    const res = await fetch("catalog.json", { cache: "no-store" });
    if (!res.ok) throw new Error("Could not load catalog.json");
    state.catalog = await res.json();
  }

  async function loadAnalysisStatus() {
    try {
      const res = await fetch("/api/analyze", { method: "GET", cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      state.analysisAvailable = Boolean(data.analysis_available);
      state.analysisProvider = data.provider || null;
    } catch {
      state.analysisAvailable = false;
    }
  }

  function renderChrome() {
    const eyebrow = document.querySelector("header .eyebrow");
    if (eyebrow) eyebrow.textContent = t("skin_analyzer");
    const introEyebrow = document.querySelector("#intro .eyebrow");
    if (introEyebrow) introEyebrow.textContent = t("intro_eyebrow");
    const introTitle = document.querySelector("#intro h1");
    if (introTitle) introTitle.textContent = t("intro_title");
    const introLede = document.querySelector("#intro .lede");
    if (introLede) introLede.textContent = t("intro_lede");
    const footer = document.querySelector("footer p");
    if (footer) footer.innerHTML = "<strong>" + t("footer").split(". ")[0] + ".</strong> " + t("footer").split(". ").slice(1).join(". ");
    // Simpler: just set the whole footer text, first sentence is already "Cosmetic suggestions only."
    if (footer) footer.textContent = t("footer");
    renderLangToggle();
  }

  function renderLangToggle() {
    let nav = document.getElementById("lang-toggle");
    if (!nav) {
      nav = document.createElement("nav");
      nav.id = "lang-toggle";
      nav.className = "lang-toggle";
      nav.setAttribute("aria-label", "Language");
      const header = document.querySelector(".header");
      if (header) header.appendChild(nav);
    }
    const lang = i18n.getLang();
    nav.innerHTML = ["en", "si", "ta"].map((code) => {
      const label = t("lang_" + code);
      const on = lang === code ? " aria-current=\"true\" class=\"on\"" : "";
      return `<button type="button" data-lang="${code}" lang="${code}"${on}>${label}</button>`;
    }).join("");
    nav.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (i18n.getLang() === btn.getAttribute("data-lang")) return;
        i18n.setLang(btn.getAttribute("data-lang"));
        render();
      });
    });
  }

  function renderProgress() {
    const inQuiz = state.step >= STEP.skin && state.step <= STEP.selfie;
    const html = Array.from({ length: QUIZ_TOTAL }, (_, i) => {
      const quizStep = state.step; // 1..6
      const cls = i + 1 < quizStep ? "done" : i + 1 === quizStep ? "on" : "";
      return `<span class="${cls}"></span>`;
    }).join("");
    progressEl.innerHTML = html;
    progressEl.classList.toggle("hidden", !inQuiz);
    if (inQuiz) {
      progressEl.setAttribute("role", "progressbar");
      progressEl.setAttribute("aria-label", t("quiz_progress"));
      progressEl.setAttribute("aria-valuemin", "1");
      progressEl.setAttribute("aria-valuemax", String(QUIZ_TOTAL));
      progressEl.setAttribute("aria-valuenow", String(state.step));
      progressEl.removeAttribute("aria-hidden");
    } else {
      progressEl.removeAttribute("role");
      progressEl.removeAttribute("aria-label");
      progressEl.removeAttribute("aria-valuemin");
      progressEl.removeAttribute("aria-valuemax");
      progressEl.removeAttribute("aria-valuenow");
      progressEl.setAttribute("aria-hidden", "true");
    }
  }

  function escapeHtml(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function bindSafeImages(root) {
    const scope = root || document;
    scope.querySelectorAll("img[data-hide-on-error]").forEach((img) => {
      if (img.dataset.boundError) return;
      img.dataset.boundError = "1";
      img.addEventListener("error", () => {
        img.style.display = "none";
      });
    });
  }

  function optionButton(opts, { selected }) {
    const sel = Array.isArray(selected) ? selected : selected ? [selected] : [];
    return opts
      .map(
        (o) => `
      <button type="button" class="choice ${sel.includes(o.id) ? "selected" : ""}"
        data-id="${o.id}" aria-pressed="${sel.includes(o.id)}">
        <span>
          <span class="title">${o.title}</span>
          ${o.note ? `<small>${o.note}</small>` : ""}
        </span>
      </button>`
      )
      .join("");
  }

  function actions(opts) {
    const back = opts.back
      ? `<button type="button" class="btn btn-ghost" id="btn-back">${opts.backLabel || t("back")}</button>`
      : "";
    const next = `<button type="button" class="btn btn-primary" id="btn-next" ${
      opts.nextDisabled ? "disabled" : ""
    }>${opts.nextLabel || t("continue")}</button>`;
    return `<div class="actions">${back}${next}</div>`;
  }

  function stepKicker(n) {
    return `<p class="step-kicker">${t("step_of", { n: n, total: QUIZ_TOTAL })}</p>`;
  }

  function privacyBlock() {
    return `<p class="privacy-note">${t("privacy_note")}</p>`;
  }

  function render() {
    renderChrome();
    renderProgress();
    const steps = [
      renderLanding,
      renderSkin,
      renderConcerns,
      renderArea,
      renderClimate,
      renderBudget,
      renderSelfie,
      renderResult,
    ];
    panel.innerHTML = steps[Math.min(state.step, STEP.result)]();
    bind();
    bindSafeImages(document);
  }

  function renderLanding() {
    const kits = state.catalog ? matcher.readyKits(state.catalog) : [];
    const kitHtml = kits
      .map((kit) => {
        const items = kit.steps
          .map((s) => {
            const bits = [s.name];
            if (s.size) bits.push(s.size);
            bits.push(s.price_label);
            return `<li>${bits.join(" · ")}</li>`;
          })
          .join("");
        return `
          <article class="kit-card" data-kit="${kit.id}">
            <h3>${t("kit_" + kit.id)}</h3>
            <p class="lede">${t("kit_" + kit.id + "_note")}</p>
            <ul class="kit-items">${items}</ul>
            <div class="kit-total"><span>${t("kit_total")}</span><span>${kit.total_label}</span></div>
            <div class="kit-actions">
              <button type="button" class="btn btn-ghost btn-view-kit" data-kit="${kit.id}">${t("view_kit")}</button>
              ${kit.steps.length ? `<button type="button" class="btn btn-primary btn-add-all" data-kit="${kit.id}">${t("add_all")}</button>` : ""}
            </div>
          </article>`;
      })
      .join("");
    return `
      ${privacyBlock()}
      <div class="actions">
        <button type="button" class="btn btn-primary" id="btn-start-quiz">${t("start_quiz")}</button>
      </div>
      <section class="kits" id="ready-kits">
        <h2>${t("kits_title")}</h2>
        <p class="lede">${t("kits_lede")}</p>
        <div class="kit-grid">${kitHtml}</div>
      </section>
    `;
  }

  function renderSkin() {
    return `
      ${stepKicker(1)}
      <h2>${t("skin_title")}</h2>
      <p class="lede">${t("skin_lede")}</p>
      <div class="options two" id="opts">${optionButton(skinTypes(), { selected: state.quiz.skin_type })}</div>
      ${actions({ back: true, backLabel: t("back_to_start"), nextDisabled: !state.quiz.skin_type })}
    `;
  }

  function renderConcerns() {
    return `
      ${stepKicker(2)}
      <h2>${t("concerns_title")}</h2>
      <p class="lede">${t("concerns_lede")}</p>
      ${state.concernLimitNote ? `<p class="note" id="concern-limit">${t("concerns_limit")}</p>` : ""}
      <div class="options two" id="opts">${optionButton(concerns(), { selected: state.quiz.concerns, multi: true })}</div>
      <div id="red-flags">
        <h3>${t("red_flags_title")}</h3>
        <p class="lede">${t("red_flags_lede")}</p>
        <div class="options two" id="flags">${optionButton(redFlags(), { selected: state.quiz.red_flags, multi: true })}</div>
      </div>
      ${actions({ back: true, nextLabel: t("continue") })}
    `;
  }

  function renderArea() {
    return `
      ${stepKicker(3)}
      <h2>${t("area_title")}</h2>
      <div class="options" id="opts">${optionButton(areas(), { selected: state.quiz.area })}</div>
      ${actions({ back: true, nextDisabled: !state.quiz.area })}
    `;
  }

  function renderClimate() {
    return `
      ${stepKicker(4)}
      <h2>${t("climate_title")}</h2>
      <p class="lede">${t("climate_lede")}</p>
      <h3 class="subq">${t("climate_air_title")}</h3>
      <div class="options" id="opts-climate">${optionButton(climateAir(), { selected: state.quiz.climate })}</div>
      <h3 class="subq">${t("climate_outdoors_title")}</h3>
      <div class="options" id="opts-outdoors">${optionButton(climateOutdoors(), { selected: state.quiz.outdoors })}</div>
      ${actions({ back: true, nextDisabled: !state.quiz.climate || !state.quiz.outdoors })}
    `;
  }

  function renderBudget() {
    return `
      ${stepKicker(5)}
      <h2>${t("budget_title")}</h2>
      <p class="lede">${t("budget_lede")}</p>
      <div class="options" id="opts">${optionButton(budgetOptions(), { selected: state.quiz.budget })}</div>
      ${actions({ back: true, nextDisabled: !state.quiz.budget })}
    `;
  }

  function selfieTips() {
    return `
      <ul class="selfie-tips">
        <li><strong>${t("selfie_tips_title")}</strong></li>
        <li>${t("selfie_tip_window")}</li>
        <li>${t("selfie_tip_glasses")}</li>
        <li>${t("selfie_tip_hair")}</li>
        <li>${t("selfie_tip_bare")}</li>
      </ul>`;
  }

  function renderSelfie() {
    const available = state.analysisAvailable;
    const phase = state.camera.phase;
    const inCamera = phase === "live" || phase === "review";
    const copy = available ? t("selfie_lede") : t("selfie_lede_unavailable");
    let body = "";
    if (phase === "live") {
      body = `
        <div class="camera-stage" id="camera-stage">
          <video id="selfie-video" class="camera-preview" autoplay playsinline muted></video>
          <div class="camera-controls">
            <button type="button" class="btn btn-primary" id="btn-capture">${t("capture")}</button>
            <button type="button" class="btn btn-ghost" id="btn-cancel-camera">${t("cancel")}</button>
          </div>
        </div>`;
    } else if (phase === "review") {
      body = `
        <div class="camera-stage" id="camera-stage">
          <img id="camera-still" class="camera-still" alt="${t("captured_selfie")}" ${
            cameraReviewUrl ? `src="${cameraReviewUrl}"` : ""
          } />
          <div class="camera-controls">
            <button type="button" class="btn btn-ghost" id="btn-retake">${t("retake")}</button>
            <button type="button" class="btn btn-primary" id="btn-use-photo">${t("use_photo")}</button>
            <button type="button" class="btn btn-ghost" id="btn-cancel-camera">${t("cancel")}</button>
          </div>
        </div>`;
    } else {
      body = `
        ${privacyBlock()}
        ${selfieTips()}
        <div class="selfie-actions">
          <button type="button" class="btn btn-primary" id="btn-take-selfie">${t("take_selfie")}</button>
          <button type="button" class="btn btn-ghost" id="btn-upload-photo">${t("upload_photo")}</button>
        </div>
        <input id="selfie-upload" class="visually-hidden" type="file" accept="image/*" />
        <input id="selfie-fallback" class="visually-hidden" type="file" accept="image/*" capture="user" />
        ${
          state.camera.fallback && state.camera.message
            ? `<p class="note" id="camera-fallback-note">${state.camera.message}</p>`
            : ""
        }
        ${
          state.selfie.dataUrl
            ? `<div class="selfie-thumb">
                <img id="selfie-preview" class="selfie-preview on" alt="${t("chosen_photo")}" src="${state.selfie.dataUrl}" />
                <button type="button" class="btn-link" id="btn-clear-selfie">${t("remove_photo")}</button>
              </div>`
            : ""
        }
        ${
          !available && state.selfie.dataUrl
            ? `<p class="note">${t("photo_device_only")}</p>`
            : ""
        }`;
    }
    return `
      ${stepKicker(6)}
      <h2>${t("selfie_title")}</h2>
      <p class="lede">${copy}</p>
      <div class="selfie-box">
        ${body}
      </div>
      ${inCamera ? "" : actions({ back: true, nextLabel: t("see_routine") })}
    `;
  }

  function variantMeta(step) {
    const bits = [];
    if (step.size) bits.push(step.size);
    if (step.variant) bits.push(step.variant);
    bits.push(step.price_label);
    return bits.join(" · ");
  }

  function localizeResult(result) {
    if (!result) return result;
    const copy = Object.assign({}, result);
    if (copy.dermatologist_note) {
      const flags = (state.quiz.red_flags || []).map((f) => t("flag_" + f + "_lower") || f);
      copy.title = t("derm_title");
      copy.message = t("derm_message", { flags: flags.join(", ") });
      return copy;
    }
    const steps = (copy.steps || []).map((s) =>
      Object.assign({}, s, { label: t("step_" + s.step) || s.label })
    );
    copy.steps = steps;
    copy.title = t("result_title");
    const band = copy.budget_band || {};
    const cap = band.max_lkr == null ? "" : t("result_cap", { amount: matcher.formatRs(band.max_lkr) });
    copy.message = steps.length
      ? t("result_message", { n: steps.length, band: band.label || "", cap: cap })
      : t("result_empty");
    return copy;
  }

  function productList(steps) {
    if (!steps || !steps.length) return `<p>${t("no_matches")}</p>`;
    return steps
      .map((s) => {
        const alt = escapeHtml(s.name || "Shello product");
        const w = Number(s.image_width) || 300;
        const h = Number(s.image_height) || 300;
        const img = s.image
          ? `<img src="${escapeHtml(s.image)}" alt="${alt}" width="${w}" height="${h}" loading="lazy" referrerpolicy="no-referrer-when-downgrade" data-hide-on-error="1" />`
          : `<div></div>`;
        const reason = s.reason
          ? `<p class="reason"><span class="reason-label">${t("reason_label")}</span> ${s.reason}</p>`
          : "";
        return `
          <article class="product" data-product-id="${s.id}">
            ${img}
            <div>
              <div class="step-name">${s.label}</div>
              <h3>${s.name}</h3>
              <div class="meta">${variantMeta(s)}</div>
              ${reason}
              <a class="buy" href="${s.permalink}" target="_blank" rel="noopener">${t("view_product")}</a>
            </div>
          </article>`;
      })
      .join("");
  }

  function bundleActions(steps, totalLabel, opts) {
    if (!steps || !steps.length) return "";
    const follow = state.cartFollowup || [];
    const followHtml = follow.length
      ? `<div class="cart-followup">
          <p class="note">${t("add_remaining")}</p>
          ${follow
            .map(
              (s) =>
                `<a class="btn btn-ghost" href="${matcher.addToCartUrl(s)}" target="shello-cart" rel="noopener">${t("add_item", { name: s.name + (s.size ? " · " + s.size : "") })}</a>`
            )
            .join("")}
        </div>`
      : `<p class="note">${t("add_all_note")}</p>`;
    const wa = whatsAppUrl(steps, totalLabel);
    return `
      <div class="total"><span>${opts && opts.kit ? t("kit_total") : t("routine_total")}</span><span>${totalLabel}</span></div>
      <div class="bundle-actions" id="bundle-actions">
        <button type="button" class="btn btn-primary" id="btn-add-all">${t("add_all")}</button>
        <a class="btn btn-whatsapp" id="btn-whatsapp" href="${wa}" target="_blank" rel="noopener">${t("send_whatsapp")}</a>
      </div>
      ${followHtml}
    `;
  }

  function whatsAppUrl(steps, totalLabel) {
    const lines = [t("wa_intro"), ""];
    (steps || []).forEach((s, i) => {
      const bits = [s.name];
      if (s.size) bits.push(s.size);
      bits.push(s.price_label);
      lines.push(`${i + 1}. ${bits.join(" · ")}`);
      if (s.permalink) lines.push(s.permalink);
    });
    lines.push("");
    lines.push(`${t("wa_total")}: ${totalLabel}`);
    lines.push(t("wa_footer"));
    return "https://wa.me/94741169600?text=" + encodeURIComponent(lines.join("\n"));
  }

  function renderResult() {
    const raw = state.result;
    if (!raw) return `<p>${t("building")}</p>`;
    const result = localizeResult(raw);
    if (result.dermatologist_note) {
      return `
        <h2>${result.title}</h2>
        <div class="alert alert-danger">${result.message}</div>
        <p class="disclaimer">${t("footer")}</p>
        <div class="actions">
          <button type="button" class="btn btn-ghost" id="btn-restart">${t("start_again")}</button>
        </div>
      `;
    }
    const obs = state.selfie.observations;
    const obsHtml = obs
      ? `<div class="alert alert-warn">
          <strong>${t("from_photo")}</strong>
          ${obs.notes ? `<div>${escapeHtml(obs.notes)}</div>` : ""}
          ${obs.refer_dermatologist ? `<div>${t("photo_derm")}</div>` : ""}
        </div>`
      : state.analysisNote
        ? `<div class="alert alert-warn">${t("selfie_analyze_fallback")}</div>`
        : "";
    const isKit = state.resultSource === "kit";
    return `
      <h2>${isKit && state.activeKit ? t("kit_" + state.activeKit) : result.title}</h2>
      <p class="lede">${isKit && state.activeKit ? t("kit_" + state.activeKit + "_note") : result.message}</p>
      ${obsHtml}
      <div class="routine-list">${productList(result.steps)}</div>
      ${bundleActions(result.steps, result.total_label, { kit: isKit })}
      <p class="disclaimer">${t("footer")}</p>
      <div class="actions">
        ${
          isKit
            ? `<button type="button" class="btn btn-ghost" id="btn-back">${t("back_to_start")}</button>`
            : `<button type="button" class="btn btn-ghost" id="btn-back">${t("adjust_answers")}</button>`
        }
        <button type="button" class="btn btn-primary" id="btn-restart">${t("start_again")}</button>
      </div>
    `;
  }

  let cameraStream = null;
  let cameraReviewUrl = null;
  let cameraReviewFile = null;

  function stopCameraTracks() {
    if (cameraStream) {
      cameraStream.getTracks().forEach((track) => {
        try { track.stop(); } catch (_) {}
      });
      cameraStream = null;
    }
    const video = panel.querySelector("#selfie-video");
    if (video) video.srcObject = null;
  }

  function closeCameraUi() {
    stopCameraTracks();
    if (cameraReviewUrl) {
      URL.revokeObjectURL(cameraReviewUrl);
      cameraReviewUrl = null;
    }
    cameraReviewFile = null;
    state.camera.phase = "idle";
  }

  function leaveSelfieStep() {
    closeCameraUi();
    state.camera.fallback = false;
    state.camera.message = null;
  }

  function attachLivePreview() {
    const video = panel.querySelector("#selfie-video");
    if (!video || !cameraStream || state.camera.phase !== "live") return;
    video.srcObject = cameraStream;
    const play = () => video.play().catch(() => {});
    if (video.readyState >= 2) play();
    else video.onloadedmetadata = play;
  }

  async function startCamera() {
    stopCameraTracks();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      enterCameraFallback();
      return;
    }
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user" },
        audio: false,
      });
      if (state.step !== STEP.selfie) {
        stopCameraTracks();
        return;
      }
      state.camera.phase = "live";
      state.camera.fallback = false;
      state.camera.message = null;
      render();
    } catch (_) {
      enterCameraFallback();
    }
  }

  function enterCameraFallback() {
    stopCameraTracks();
    state.camera.phase = "idle";
    state.camera.fallback = true;
    state.camera.message = t("camera_fallback");
    render();
    const fallback = panel.querySelector("#selfie-fallback");
    if (fallback) fallback.click();
  }

  function captureFrame() {
    const video = panel.querySelector("#selfie-video");
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob(async (blob) => {
      if (!blob) return;
      try {
        cameraReviewFile = await downscaleToJpeg(blob, 1024);
        if (cameraReviewUrl) URL.revokeObjectURL(cameraReviewUrl);
        cameraReviewUrl = URL.createObjectURL(cameraReviewFile);
        state.camera.phase = "review";
        render();
      } catch (_) {
        enterCameraFallback();
      }
    }, "image/jpeg", 0.9);
  }

  async function useCapturedPhoto() {
    if (!cameraReviewFile) return;
    await setSelfieFile(cameraReviewFile, cameraReviewUrl);
    cameraReviewFile = null;
    cameraReviewUrl = null;
    stopCameraTracks();
    state.camera.phase = "idle";
    state.camera.fallback = false;
    state.camera.message = null;
    render();
  }

  async function setSelfieFile(file, existingUrl) {
    if (state.selfie.dataUrl && state.selfie.dataUrl !== existingUrl) {
      URL.revokeObjectURL(state.selfie.dataUrl);
    }
    const scaled = file.type === "image/jpeg" && file.name === "selfie.jpg"
      ? file
      : await downscaleToJpeg(file, 1024);
    state.selfie.file = scaled;
    state.selfie.dataUrl = existingUrl && scaled === file
      ? existingUrl
      : URL.createObjectURL(scaled);
    if (existingUrl && existingUrl !== state.selfie.dataUrl) {
      URL.revokeObjectURL(existingUrl);
    }
    state.selfie.observations = null;
    state.selfie.analyzed = false;
  }

  function downscaleToJpeg(blobOrFile, maxSize, quality) {
    maxSize = maxSize || 1024;
    quality = quality == null ? 0.85 : quality;
    return new Promise((resolve, reject) => {
      const img = new Image();
      const url = URL.createObjectURL(blobOrFile);
      img.onload = () => {
        let width = img.naturalWidth || img.width;
        let height = img.naturalHeight || img.height;
        const scale = Math.min(1, maxSize / Math.max(width, height));
        width = Math.max(1, Math.round(width * scale));
        height = Math.max(1, Math.round(height * scale));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        canvas.getContext("2d").drawImage(img, 0, 0, width, height);
        URL.revokeObjectURL(url);
        canvas.toBlob((blob) => {
          if (!blob) {
            reject(new Error("Could not write the photo"));
            return;
          }
          resolve(new File([blob], "selfie.jpg", { type: "image/jpeg" }));
        }, "image/jpeg", quality);
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("Could not read the photo"));
      };
      img.src = url;
    });
  }

  function attachVariantLabels(result) {
    if (!result || !result.steps) return result;
    const byId = new Map((state.catalog.included || []).map((p) => [p.id, p]));
    for (const step of result.steps) {
      const src = byId.get(step.id);
      if (src && src.variation_label && src.variation_label !== src.size) {
        const label = String(src.variation_label)
          .replace(/^(variant|size|sizes)\s*:\s*/i, "")
          .trim();
        if (label && !/^\d/.test(label) && label.toLowerCase() !== String(step.size || "").toLowerCase()) {
          step.variant = label;
        }
      }
    }
    return result;
  }

  function openAddAll(steps) {
    if (!steps || !steps.length) return;
    const first = matcher.addToCartUrl(steps[0]);
    window.open(first, "shello-cart");
    state.cartFollowup = steps.slice(1);
    render();
  }

  function showKit(kitId) {
    const kits = matcher.readyKits(state.catalog);
    const kit = kits.find((k) => k.id === kitId);
    if (!kit) return;
    state.result = attachVariantLabels(kit.result);
    state.resultSource = "kit";
    state.activeKit = kitId;
    state.cartFollowup = [];
    state.step = STEP.result;
    render();
  }

  function resetQuiz(keepLang) {
    leaveSelfieStep();
    if (state.selfie.dataUrl) URL.revokeObjectURL(state.selfie.dataUrl);
    state.step = STEP.landing;
    state.result = null;
    state.resultSource = "quiz";
    state.activeKit = null;
    state.cartFollowup = [];
    state.concernLimitNote = false;
    state.selfie = { file: null, dataUrl: null, observations: null, analyzed: false };
    state.analysisNote = null;
    state.quiz = {
      skin_type: null,
      concerns: [],
      area: "face",
      budget: "everyday",
      red_flags: [],
      climate: null,
      outdoors: null,
    };
  }

  function bind() {
    const start = panel.querySelector("#btn-start-quiz");
    if (start) start.addEventListener("click", () => {
      state.step = STEP.skin;
      render();
    });
    panel.querySelectorAll(".btn-view-kit").forEach((btn) => {
      btn.addEventListener("click", () => showKit(btn.getAttribute("data-kit")));
    });
    panel.querySelectorAll(".btn-add-all").forEach((btn) => {
      btn.addEventListener("click", () => {
        const kitId = btn.getAttribute("data-kit");
        const kits = matcher.readyKits(state.catalog);
        const kit = kits.find((k) => k.id === kitId);
        if (kit) openAddAll(kit.steps);
      });
    });
    const addAll = panel.querySelector("#btn-add-all");
    if (addAll) addAll.addEventListener("click", () => {
      const steps = (state.result && state.result.steps) || [];
      openAddAll(steps);
    });

    const opts = panel.querySelector("#opts");
    if (opts) {
      opts.querySelectorAll(".choice").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-id");
          if (state.step === STEP.skin) state.quiz.skin_type = id;
          else if (state.step === STEP.area) state.quiz.area = id;
          else if (state.step === STEP.budget) state.quiz.budget = id;
          else if (state.step === STEP.concerns) {
            const arr = state.quiz.concerns;
            const idx = arr.indexOf(id);
            if (idx >= 0) {
              arr.splice(idx, 1);
              state.concernLimitNote = false;
            } else if (arr.length >= 2) {
              state.concernLimitNote = true;
            } else {
              arr.push(id);
              state.concernLimitNote = false;
            }
          }
          render();
        });
      });
    }
    const flags = panel.querySelector("#flags");
    if (flags) {
      flags.querySelectorAll(".choice").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-id");
          const arr = state.quiz.red_flags;
          const idx = arr.indexOf(id);
          if (idx >= 0) arr.splice(idx, 1);
          else arr.push(id);
          render();
        });
      });
    }
    const climateOpts = panel.querySelector("#opts-climate");
    if (climateOpts) {
      climateOpts.querySelectorAll(".choice").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.quiz.climate = btn.getAttribute("data-id");
          render();
        });
      });
    }
    const outdoorsOpts = panel.querySelector("#opts-outdoors");
    if (outdoorsOpts) {
      outdoorsOpts.querySelectorAll(".choice").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.quiz.outdoors = btn.getAttribute("data-id");
          render();
        });
      });
    }

    const back = panel.querySelector("#btn-back");
    if (back) back.addEventListener("click", () => {
      if (state.step === STEP.selfie) leaveSelfieStep();
      if (state.step === STEP.result && state.resultSource === "kit") {
        state.result = null;
        state.resultSource = "quiz";
        state.activeKit = null;
        state.cartFollowup = [];
        state.step = STEP.landing;
      } else if (state.step === STEP.result) {
        state.step = STEP.budget;
        state.cartFollowup = [];
      } else if (state.step > 0) {
        state.step -= 1;
      }
      render();
    });
    const next = panel.querySelector("#btn-next");
    if (next) next.addEventListener("click", onNext);
    const restart = panel.querySelector("#btn-restart");
    if (restart) restart.addEventListener("click", () => {
      resetQuiz();
      render();
    });
    const take = panel.querySelector("#btn-take-selfie");
    if (take) take.addEventListener("click", () => {
      if (state.camera.fallback) {
        const fallback = panel.querySelector("#selfie-fallback");
        if (fallback) fallback.click();
        else startCamera();
      } else {
        startCamera();
      }
    });
    const uploadBtn = panel.querySelector("#btn-upload-photo");
    const uploadInput = panel.querySelector("#selfie-upload");
    if (uploadBtn && uploadInput) {
      uploadBtn.addEventListener("click", () => uploadInput.click());
    }
    const onPickedFile = async (input) => {
      const file = input.files && input.files[0];
      input.value = "";
      if (!file) return;
      try {
        await setSelfieFile(file);
        state.camera.phase = "idle";
        render();
      } catch (_) {
        state.camera.message = t("photo_unread");
        render();
      }
    };
    if (uploadInput) uploadInput.addEventListener("change", () => onPickedFile(uploadInput));
    const fallbackInput = panel.querySelector("#selfie-fallback");
    if (fallbackInput) fallbackInput.addEventListener("change", () => onPickedFile(fallbackInput));
    const capture = panel.querySelector("#btn-capture");
    if (capture) capture.addEventListener("click", captureFrame);
    const retake = panel.querySelector("#btn-retake");
    if (retake) retake.addEventListener("click", () => {
      if (cameraReviewUrl) {
        URL.revokeObjectURL(cameraReviewUrl);
        cameraReviewUrl = null;
      }
      cameraReviewFile = null;
      if (cameraStream && cameraStream.getTracks().some((tr) => tr.readyState === "live")) {
        state.camera.phase = "live";
        render();
      } else {
        startCamera();
      }
    });
    const usePhoto = panel.querySelector("#btn-use-photo");
    if (usePhoto) usePhoto.addEventListener("click", () => { useCapturedPhoto(); });
    const cancelCam = panel.querySelector("#btn-cancel-camera");
    if (cancelCam) cancelCam.addEventListener("click", () => {
      closeCameraUi();
      render();
    });
    const clear = panel.querySelector("#btn-clear-selfie");
    if (clear) {
      clear.addEventListener("click", () => {
        if (state.selfie.dataUrl) URL.revokeObjectURL(state.selfie.dataUrl);
        state.selfie = { file: null, dataUrl: null, observations: null, analyzed: false };
        state.analysisNote = null;
        render();
      });
    }
    attachLivePreview();
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || "");
        const comma = result.indexOf(",");
        resolve(comma >= 0 ? result.slice(comma + 1) : result);
      };
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function postAnalyze(image_base64) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 25000);
    try {
      return await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_base64,
          mime_type: "image/jpeg",
        }),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  function shouldRetryAnalyze(err, resp) {
    if (err) return true;
    if (!resp) return true;
    if (resp.status === 429 || resp.status >= 500) return true;
    return false;
  }

  async function maybeAnalyzeSelfie() {
    if (!state.analysisAvailable || !state.selfie.file || state.selfie.analyzed) {
      return state.selfie.observations;
    }
    state.analysisNote = null;
    try {
      let file = state.selfie.file;
      if (file.type !== "image/jpeg") {
        file = await downscaleToJpeg(file, 1024);
        state.selfie.file = file;
      }
      const image_base64 = await fileToBase64(file);
      let resp = null;
      let lastErr = null;
      try {
        resp = await postAnalyze(image_base64);
      } catch (err) {
        lastErr = err;
      }
      if (shouldRetryAnalyze(lastErr, resp)) {
        await sleep(1200);
        lastErr = null;
        try {
          resp = await postAnalyze(image_base64);
        } catch (err) {
          lastErr = err;
          resp = null;
        }
      }
      if (lastErr || !resp || !resp.ok) {
        state.selfie.analyzed = true;
        state.selfie.observations = null;
        state.analysisNote = "fallback";
        return null;
      }
      const data = await resp.json().catch(() => ({}));
      state.selfie.analyzed = true;
      state.selfie.observations = (data && data.observations) || null;
      if (!state.selfie.observations) {
        state.analysisNote = "fallback";
      }
      return state.selfie.observations;
    } catch (_) {
      state.selfie.analyzed = true;
      state.selfie.observations = null;
      state.analysisNote = "fallback";
      return null;
    }
  }

  async function onNext() {
    if (state.step < STEP.selfie) {
      state.step += 1;
      render();
      return;
    }
    if (state.step === STEP.selfie) {
      panel.querySelector("#btn-next") && (panel.querySelector("#btn-next").disabled = true);
      leaveSelfieStep();
      const observations = await maybeAnalyzeSelfie();
      const quiz = matcher.mergeQuizWithObservations(state.quiz, observations);
      state.quiz = quiz;
      const result = attachVariantLabels(matcher.recommend(state.catalog, quiz));
      state.result = result;
      state.resultSource = "quiz";
      state.activeKit = null;
      state.cartFollowup = [];
      state.step = STEP.result;
      render();
    }
  }

  async function start() {
    i18n.loadSaved();
    const logo = document.getElementById("brand-logo");
    const brand = document.getElementById("brand");
    if (logo && brand) {
      logo.addEventListener("error", () => brand.classList.add("no-logo"));
    }
    try {
      await loadCatalog();
      await loadAnalysisStatus();
      render();
    } catch (err) {
      renderChrome();
      panel.innerHTML = `<div class="alert alert-danger">${t("catalog_error")}</div>`;
    }
  }

  window.addEventListener("pagehide", leaveSelfieStep);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && state.step !== STEP.selfie) leaveSelfieStep();
  });
  start();
})();
