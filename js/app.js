(function () {
  const panel = document.getElementById("panel");
  const progressEl = document.getElementById("progress");
  const matcher = window.ShelloMatcher;
  const i18n = window.ShelloI18n;
  const t = function (key, vars) { return i18n.t(key, vars); };

  const QUIZ_TOTAL = 7;
  const STEP = {
    landing: 0,
    skin: 1,
    concerns: 2,
    safety: 3,
    area: 4,
    climate: 5,
    budget: 6,
    selfie: 7,
    confirm: 8,
    result: 9,
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
      { id: "not_sure", title: t("not_sure") },
    ];
  }

  const state = {
    catalog: null,
    step: 0,
    quiz: {
      skin_type: null,
      concerns: [],
      area: null,
      budget: null,
      red_flags: [],
      climate: null,
      outdoors: null,
    },
    selfie: { file: null, dataUrl: null, observations: null, analyzed: false, meta: null },
    quality: { status: "idle", ran: false, pass: null, reasons: [], metrics: {}, retakes: 0, face_check: null },
    confirm: null,
    confirmObs: null,
    feedback: {},
    resultLogged: false,
    deleted: false,
    camera: { phase: "idle", fallback: false, message: null },
    analysisAvailable: false,
    analysisProvider: null,
    deviceReaderPct: 0,
    result: null,
    resultSource: "quiz",
    added: {},
    safetyNone: false,
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
      state.deviceReaderPct = Number(data.device_reader_pct) || 0;
    } catch {
      state.analysisAvailable = false;
    }
  }

  // ?reader=device forces the on-device reader, ?reader=cloud disables it;
  // otherwise a per-tab random bucket (no persistent ID) vs DEVICE_READER_PCT.
  function deviceReaderEnabled() {
    if (!window.ShelloSkinReader) return false;
    const forced = new URLSearchParams(location.search).get("reader");
    if (forced === "device") return true;
    if (forced === "cloud") return false;
    let bucket = Number(sessionStorage.getItem("shello_reader_bucket"));
    if (!(bucket >= 0 && bucket < 100)) {
      bucket = Math.floor(Math.random() * 100);
      try { sessionStorage.setItem("shello_reader_bucket", String(bucket)); } catch (_) {}
    }
    return bucket < state.deviceReaderPct;
  }

  function selfieAnalysisPossible() {
    return state.analysisAvailable || deviceReaderEnabled();
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
    const hint = opts.nextDisabled && opts.hint
      ? `<p class="hint" id="next-hint" role="status">${opts.hint}</p>`
      : "";
    return `<div class="actions">${back}${next}</div>${hint}`;
  }

  function stepKicker(n) {
    return `<p class="step-kicker">${t("step_of", { n: n, total: QUIZ_TOTAL })}</p>`;
  }

  // One plain line; the full notice sits behind "Details".
  function privacyBlock() {
    const full = t(deviceReaderEnabled() ? "privacy_note_device" : "privacy_note");
    return `
      <details class="privacy">
        <summary><span>${t("privacy_short")}</span> <span class="privacy-more">${t("privacy_details")}</span></summary>
        <p>${full}</p>
      </details>`;
  }

  const STEP_NAMES = ["landing", "skin", "concerns", "safety", "area", "climate", "budget", "scan", "confirm", "result"];
  let lastLoggedStep = null;
  function logStepView() {
    if (state.step === lastLoggedStep) return;
    lastLoggedStep = state.step;
    if (window.ShelloEvents) window.ShelloEvents.send("step_view", { step: STEP_NAMES[state.step] });
  }

  function render() {
    renderChrome();
    renderProgress();
    const steps = [
      renderLanding,
      renderSkin,
      renderConcerns,
      renderSafety,
      renderArea,
      renderClimate,
      renderBudget,
      renderSelfie,
      renderConfirm,
      renderResult,
    ];
    panel.innerHTML = steps[Math.min(state.step, STEP.result)]();
    const intro = document.getElementById("intro");
    if (intro) intro.classList.toggle("hidden", state.step !== STEP.landing);
    logStepView();
    bind();
    bindSafeImages(document);
  }

  function renderLanding() {
    const kits = state.catalog ? matcher.readyKits(state.catalog) : [];
    const chips = kits
      .map((kit) => `<button type="button" class="chip btn-view-kit" data-kit="${kit.id}">${t("kit_" + kit.id)} <span class="chip-price">${kit.total_label}</span></button>`)
      .join("");
    return `
      <div class="hero-actions">
        <button type="button" class="btn btn-primary btn-lg" id="btn-start-quiz">${t("start_quiz")}</button>
        ${privacyBlock()}
      </div>
      ${kits.length ? `
      <section class="kits" id="ready-kits" aria-labelledby="kits-title">
        <h2 class="kits-title" id="kits-title">${t("kits_or")}</h2>
        <div class="chip-row">${chips}</div>
      </section>` : ""}
    `;
  }

  function renderSkin() {
    return `
      ${stepKicker(STEP.skin)}
      <h2>${t("skin_title")}</h2>
      <p class="lede">${t("skin_lede")}</p>
      <div class="options two" id="opts">${optionButton(skinTypes(), { selected: state.quiz.skin_type })}</div>
      ${actions({ back: true, backLabel: t("back_to_start"), nextDisabled: !state.quiz.skin_type, hint: t("choose_one") })}
    `;
  }

  function renderConcerns() {
    return `
      ${stepKicker(STEP.concerns)}
      <h2>${t("concerns_title")}</h2>
      <p class="lede">${t("concerns_lede")}</p>
      ${state.concernLimitNote ? `<p class="note" id="concern-limit">${t("concerns_limit")}</p>` : ""}
      <div class="options two" id="opts">${optionButton(concerns(), { selected: state.quiz.concerns, multi: true })}</div>
      ${actions({ back: true, nextLabel: t("continue") })}
    `;
  }

  // Its own step so the safety check is never skimmed past.
  function renderSafety() {
    const selected = state.safetyNone ? ["none"] : state.quiz.red_flags;
    const opts = redFlags().concat([{ id: "none", title: t("safety_none") }]);
    return `
      ${stepKicker(STEP.safety)}
      <h2 id="red-flags-title">${t("red_flags_title")}</h2>
      <p class="lede">${t("red_flags_lede")}</p>
      <div class="options two" id="flags">${optionButton(opts, { selected })}</div>
      ${actions({ back: true, nextDisabled: !state.safetyNone && !state.quiz.red_flags.length, hint: t("choose_one") })}
    `;
  }

  function renderArea() {
    return `
      ${stepKicker(STEP.area)}
      <h2>${t("area_title")}</h2>
      <div class="options" id="opts">${optionButton(areas(), { selected: state.quiz.area })}</div>
      ${actions({ back: true, nextDisabled: !state.quiz.area, hint: t("choose_one") })}
    `;
  }

  function renderClimate() {
    const q = state.quiz;
    const hint = !q.climate && !q.outdoors
      ? t("climate_missing_both")
      : !q.climate ? t("climate_missing_air") : t("climate_missing_sun");
    return `
      ${stepKicker(STEP.climate)}
      <h2>${t("climate_title")}</h2>
      <p class="lede">${t("climate_lede")}</p>
      <h3 class="subq">${t("climate_air_title")}</h3>
      <div class="options two" id="opts-climate">${optionButton(climateAir(), { selected: q.climate })}</div>
      <h3 class="subq">${t("climate_outdoors_title")}</h3>
      <div class="options two" id="opts-outdoors">${optionButton(climateOutdoors(), { selected: q.outdoors })}</div>
      ${actions({ back: true, nextDisabled: !q.climate || !q.outdoors, hint })}
    `;
  }

  function renderBudget() {
    const opts = budgetOptions();
    const notes = opts.filter((o) => o.note).map((o) => `<li><strong>${escapeHtml(o.title)}</strong> — ${o.note}</li>`).join("");
    return `
      ${stepKicker(STEP.budget)}
      <h2>${t("budget_title")}</h2>
      <div class="options" id="opts">${optionButton(opts.map((o) => ({ id: o.id, title: o.title })), { selected: state.quiz.budget })}</div>
      <details class="more">
        <summary>${t("budget_how")}</summary>
        <p>${t("budget_lede")}</p>
        ${notes ? `<ul>${notes}</ul>` : ""}
      </details>
      ${actions({ back: true, nextDisabled: !state.quiz.budget, hint: t("choose_one") })}
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
    const available = selfieAnalysisPossible();
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
          <button type="button" class="btn btn-ghost" id="btn-take-selfie">${t("take_selfie")}</button>
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
        ${qualityStatusHtml()}
        ${
          !available && state.selfie.dataUrl
            ? `<p class="note">${t("photo_device_only")}</p>`
            : ""
        }`;
    }
    return `
      ${stepKicker(STEP.selfie)}
      <h2>${t("selfie_title")}</h2>
      <p class="lede">${copy}</p>
      <div class="selfie-box">
        ${body}
      </div>
      ${inCamera ? "" : actions({ back: true, nextLabel: t("see_routine") })}
    `;
  }

  function qualityStatusHtml() {
    if (!state.selfie.dataUrl) return "";
    const q = state.quality;
    if (q.status === "checking") {
      return `<p class="note" id="quality-status">${t("quality_checking")}</p>`;
    }
    if (q.status === "pass") {
      return `<p class="note quality-ok" id="quality-status">${t("quality_ok")}</p>`;
    }
    if (q.status === "fail") {
      const items = (q.reasons || [])
        .map((r) => `<li>${t("quality_" + r)}</li>`)
        .join("");
      return `
        <div class="alert alert-warn" id="quality-status">
          <strong>${t("quality_fail_title")}</strong>
          <ul>${items}</ul>
          <div class="selfie-actions">
            <button type="button" class="btn btn-ghost" id="btn-retake-quality">${t("retake")}</button>
            <button type="button" class="btn btn-ghost" id="btn-continue-no-photo">${t("continue_without_photo")}</button>
          </div>
        </div>`;
    }
    return "";
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
          : "";
        const reason = s.reason
          ? `<p class="reason"><span class="reason-label">${t("reason_label")}</span> ${s.reason}</p>`
          : "";
        const fb = state.feedback[s.id] || null;
        const add = state.added[s.id]
          ? `<span class="btn btn-added" aria-live="polite">${t("added_to_cart")}</span>`
          : `<a class="btn btn-ghost btn-add-item" data-product-id="${s.id}" href="${matcher.addToCartUrl(s)}" target="shello-cart" rel="noopener">${t("add_to_cart")}</a>`;
        return `
          <article class="product" data-product-id="${s.id}">
            <div class="product-img">${img}</div>
            <div class="product-body">
              <div class="step-name">${s.label}</div>
              <h3>${s.name}</h3>
              <div class="meta">${variantMeta(s)}</div>
              ${fitLine(s.fit)}
              ${reason}
              <div class="product-actions">
                ${add}
                <a class="buy" href="${s.permalink}" target="_blank" rel="noopener" data-product-id="${s.id}">${t("view_product")}</a>
                <span class="feedback">
                  <button type="button" class="thumb" data-pid="${s.id}" data-val="up"
                    aria-pressed="${fb === "up"}" aria-label="${t("thumb_up")}">${ICON_UP}</button>
                  <button type="button" class="thumb" data-pid="${s.id}" data-val="down"
                    aria-pressed="${fb === "down"}" aria-label="${t("thumb_down")}">${ICON_DOWN}</button>
                </span>
              </div>
            </div>
          </article>`;
      })
      .join("");
  }

  // Lucide thumbs-up / thumbs-down (ISC licence), inline so no icon font loads.
  const ICON_UP = '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg>';
  const ICON_DOWN = '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/></svg>';

  function fitLine(fit) {
    if (!fit || !fit.length) return "";
    const bits = fit.map((f) => {
      const [kind, id] = f.split(":");
      return kind === "skin" ? t("fit_skin", { type: t("skin_" + id).toLowerCase() }) : concernLabel(id).toLowerCase();
    });
    return `<p class="fit">${t("fit_label")} ${escapeHtml(bits.join(" · "))}</p>`;
  }

  function resultSummary(quiz) {
    if (!quiz) return "";
    const type = quiz.skin_type ? t("skin_" + quiz.skin_type) : null;
    const cs = (quiz.concerns || []).map((c) => concernLabel(c).toLowerCase()).join(", ");
    const line = type && cs ? t("summary_full", { type, concerns: cs })
      : type ? t("summary_type", { type }) : cs ? t("summary_concerns", { concerns: cs }) : "";
    return line ? `<p class="summary">${escapeHtml(line)}</p>` : "";
  }

  function bundleActions(steps, totalLabel, opts) {
    if (!steps || !steps.length) return "";
    const wa = whatsAppUrl(steps, totalLabel);
    return `
      <div class="total"><span>${opts && opts.kit ? t("kit_total") : t("routine_total")}</span><span>${totalLabel}</span></div>
      <p class="note">${t("cart_note")}</p>
      <div class="bundle-actions" id="bundle-actions">
        <a class="btn btn-whatsapp" id="btn-whatsapp" href="${wa}" target="_blank" rel="noopener">${t("send_whatsapp")}</a>
      </div>
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

  function concernLabel(id) {
    return t("concern_" + id) || id;
  }

  function renderConfirm() {
    const c = state.confirm;
    if (!c) return "";
    if (c.editing) {
      const typeOpts = skinTypes().concat([{ id: "not_sure", title: t("confirm_not_sure"), note: "" }]);
      return `
        <h2>${t("confirm_title")}</h2>
        <h3 class="subq">${t("confirm_change_type")}</h3>
        <div class="options two" id="confirm-types">${optionButton(typeOpts, {
          selected: c.corrected_skin_type || "not_sure",
        })}</div>
        <h3 class="subq">${t("confirm_change_concerns")}</h3>
        ${c.limitNote ? `<p class="note">${t("concerns_limit")}</p>` : ""}
        <div class="options two" id="confirm-concerns">${optionButton(concerns(), {
          selected: c.corrected_concerns,
        })}</div>
        ${actions({ back: true, nextLabel: t("see_routine") })}
      `;
    }
    const typeLabel = c.read_skin_type ? t("skin_" + c.read_skin_type) : null;
    const concernLabels = (c.read_concerns || []).map(concernLabel).join(", ");
    const read =
      typeLabel && concernLabels
        ? t("confirm_read_full", { type: typeLabel, concerns: concernLabels })
        : typeLabel
          ? t("confirm_read_type", { type: typeLabel })
          : t("confirm_read_concerns", { concerns: concernLabels });
    return `
      <h2>${t("confirm_title")}</h2>
      <p class="lede">${read}</p>
      <div class="actions">
        <button type="button" class="btn btn-ghost" id="btn-back">${t("back")}</button>
        <button type="button" class="btn btn-primary" id="btn-confirm-yes">${t("confirm_yes")}</button>
        <button type="button" class="btn btn-ghost" id="btn-confirm-change">${t("confirm_change")}</button>
      </div>
    `;
  }

  function finishToResult(observations) {
    const quiz = matcher.mergeQuizWithObservations(state.quiz, observations);
    state.quiz = quiz;
    const result = attachVariantLabels(matcher.recommend(state.catalog, quiz));
    state.result = result;
    state.resultSource = "quiz";
    state.activeKit = null;
    state.selfie.observations = observations;
    state.step = STEP.result;
    logSessionResult(result, quiz, observations);
    render();
  }

  function readerEvent(observations) {
    const meta = state.selfie.meta || {};
    const obs = observations || state.selfie.observations || {};
    return {
      source: meta.provider || "none",
      provider: meta.provider || undefined,
      model: meta.model || undefined,
      prompt_version: meta.prompt_version || undefined,
      skin_type: obs.skin_type_hint || undefined,
      concerns: obs.concerns || [],
      confidence: obs.confidence || undefined,
      latency_ms: meta.latency_ms || undefined,
      fallback_reason: meta.fallback_reason || undefined,
      device_confidence: meta.confidence == null ? undefined : meta.confidence,
    };
  }

  function confirmEvent() {
    const c = state.confirm;
    if (!c || !c.action) return { action: "skipped" };
    return {
      action: c.action,
      read_skin_type: c.read_skin_type || undefined,
      read_concerns: c.read_concerns || [],
      corrected_skin_type: c.action === "correct" ? c.corrected_skin_type || undefined : undefined,
      corrected_concerns: c.action === "correct" ? c.corrected_concerns || [] : [],
    };
  }

  function qualityEvent() {
    const q = state.quality;
    return {
      ran: q.ran,
      pass: q.pass == null ? undefined : q.pass,
      reasons: q.reasons || [],
      retakes: q.retakes || 0,
      face_check: q.face_check || undefined,
    };
  }

  function stripUndef(obj) {
    const out = {};
    for (const k in obj) if (obj[k] !== undefined) out[k] = obj[k];
    return out;
  }

  function logSessionResult(result, mergedQuiz, observations) {
    if (!window.ShelloEvents || state.resultLogged === result) return;
    state.resultLogged = result;
    const isKit = state.resultSource === "kit";
    window.ShelloEvents.send(
      "session_result",
      stripUndef({
        lang: i18n.getLang(),
        result_source: state.resultSource,
        kit_id: isKit ? state.activeKit : undefined,
        quiz: {
          skin_type: mergedQuiz ? mergedQuiz.skin_type : state.quiz.skin_type,
          concerns: mergedQuiz ? mergedQuiz.concerns : state.quiz.concerns,
          area: state.quiz.area,
          budget: state.quiz.budget,
          climate: state.quiz.climate,
          outdoors: state.quiz.outdoors,
          red_flags: state.quiz.red_flags || [],
        },
        merged_concerns: mergedQuiz ? mergedQuiz.concerns : state.quiz.concerns,
        reader: stripUndef(readerEvent(observations)),
        quality: stripUndef(qualityEvent()),
        confirm: stripUndef(confirmEvent()),
        routine_product_ids: ((result && result.steps) || []).map((s) => s.id).filter((n) => Number.isInteger(n)),
        dermatologist_note: Boolean(result && result.dermatologist_note),
      })
    );
  }

  function renderResult() {
    const raw = state.result;
    if (!raw) return `<p>${t("building")}</p>`;
    const result = localizeResult(raw);
    if (result.dermatologist_note) {
      return `
        <h2>${result.title}</h2>
        <div class="alert alert-danger">${result.message}</div>
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
      ${isKit ? "" : resultSummary(state.quiz)}
      <h2>${isKit && state.activeKit ? t("kit_" + state.activeKit) : result.title}</h2>
      <p class="lede">${isKit && state.activeKit ? t("kit_" + state.activeKit + "_note") : result.message}</p>
      ${obsHtml}
      <div class="routine-list">${productList(result.steps)}</div>
      ${bundleActions(result.steps, result.total_label, { kit: isKit })}
      <div class="anon-footer">
        <span class="anon-code">${t("anonymous_code")}: ${window.ShelloEvents ? window.ShelloEvents.code() : "--------"}</span>
        ${state.deleted ? `<span class="note">${t("delete_answers_done")}</span>` : `<button type="button" class="btn-link" id="btn-delete-answers">${t("delete_answers")}</button>`}
      </div>
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
    state.selfie.meta = null;
    runQualityCheck(scaled);
  }

  function resetQuality(keepRetakes) {
    state.quality = {
      status: "idle",
      ran: false,
      pass: null,
      reasons: [],
      metrics: {},
      retakes: keepRetakes ? state.quality.retakes : 0,
      face_check: null,
    };
  }

  async function runQualityCheck(file) {
    state.quality = {
      status: "checking",
      ran: false,
      pass: null,
      reasons: [],
      metrics: {},
      retakes: state.quality.retakes,
      face_check: null,
    };
    try {
      const bmp = await createImageBitmap(file);
      const canvas = document.createElement("canvas");
      canvas.width = bmp.width;
      canvas.height = bmp.height;
      canvas.getContext("2d").drawImage(bmp, 0, 0);
      // Face detection loads lazily here — only once a photo exists.
      const face = window.ShelloFace ? await window.ShelloFace.detect(canvas) : null;
      if (state.selfie.file !== file) return; // a different photo was picked
      const res = await window.ShelloQuality.check(canvas, face);
      try { bmp.close && bmp.close(); } catch (_) {}
      // Landmarks stay in memory only, for the on-device reader.
      state.selfie.landmarks = face && face.faces === 1 ? face.landmarks : null;
      if (res.pass && deviceReaderEnabled()) window.ShelloSkinReader.load();
      state.quality = {
        status: res.pass ? "pass" : "fail",
        ran: true,
        pass: res.pass,
        reasons: res.reasons,
        metrics: res.metrics || {},
        retakes: state.quality.retakes,
        face_check: (res.metrics || {}).face_check || null,
      };
    } catch (_) {
      if (state.selfie.file !== file) return;
      // A broken gate must not block the photo; checks count as unavailable.
      state.quality = {
        status: "pass",
        ran: true,
        pass: true,
        reasons: [],
        metrics: { face_check: "unavailable" },
        retakes: state.quality.retakes,
        face_check: "unavailable",
      };
    }
    if (state.step === STEP.selfie) render();
  }

  function clearSelfie(keepRetakes) {
    if (state.selfie.dataUrl) URL.revokeObjectURL(state.selfie.dataUrl);
    state.selfie = { file: null, dataUrl: null, observations: null, analyzed: false, meta: null };
    state.analysisNote = null;
    resetQuality(keepRetakes);
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

  function showKit(kitId) {
    const kits = matcher.readyKits(state.catalog);
    const kit = kits.find((k) => k.id === kitId);
    if (!kit) return;
    state.result = attachVariantLabels(kit.result);
    state.resultSource = "kit";
    state.activeKit = kitId;
    state.step = STEP.result;
    logSessionResult(state.result, null, null);
    render();
  }

  function resetQuiz(keepLang) {
    leaveSelfieStep();
    if (state.selfie.dataUrl) URL.revokeObjectURL(state.selfie.dataUrl);
    state.step = STEP.landing;
    state.result = null;
    state.resultSource = "quiz";
    state.activeKit = null;
    state.concernLimitNote = false;
    state.added = {};
    state.safetyNone = false;
    state.selfie = { file: null, dataUrl: null, observations: null, analyzed: false, meta: null };
    resetQuality(false);
    state.confirm = null;
    state.confirmObs = null;
    state.feedback = {};
    state.resultLogged = false;
    state.deleted = false;
    state.analysisNote = null;
    state.quiz = {
      skin_type: null,
      concerns: [],
      area: null,
      budget: null,
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
    panel.querySelectorAll(".btn-add-item").forEach((a) => {
      a.addEventListener("click", () => {
        if (window.ShelloEvents)
          window.ShelloEvents.send("add_to_cart", {
            product_ids: [Number(a.getAttribute("data-product-id"))],
            kind: "add_item",
            source: state.resultSource === "kit" ? "kit" : "quiz",
          });
        state.added[a.getAttribute("data-product-id")] = true;
        setTimeout(render, 0); // let the link open the cart tab first
      });
    });
    panel.querySelectorAll("a.buy").forEach((a) => {
      a.addEventListener("click", () => {
        if (window.ShelloEvents)
          window.ShelloEvents.send("click", {
            product_id: Number(a.getAttribute("data-product-id")),
            kind: "view_product",
          });
      });
    });
    const wa = panel.querySelector("#btn-whatsapp");
    if (wa) wa.addEventListener("click", () => {
      if (window.ShelloEvents) window.ShelloEvents.send("click", { kind: "whatsapp" });
    });
    panel.querySelectorAll(".thumb").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pid = Number(btn.getAttribute("data-pid"));
        const val = btn.getAttribute("data-val");
        state.feedback[pid] = state.feedback[pid] === val ? null : val;
        if (state.feedback[pid] && window.ShelloEvents)
          window.ShelloEvents.send("product_feedback", { product_id: pid, value: val });
        render();
      });
    });
    const delBtn = panel.querySelector("#btn-delete-answers");
    if (delBtn) delBtn.addEventListener("click", async () => {
      if (window.ShelloEvents) await window.ShelloEvents.forget();
      state.deleted = true;
      render();
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
          if (id === "none") {
            arr.length = 0;
            state.safetyNone = !state.safetyNone;
          } else {
            state.safetyNone = false;
            const idx = arr.indexOf(id);
            if (idx >= 0) arr.splice(idx, 1);
            else arr.push(id);
          }
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
            state.step = STEP.landing;
      } else if (state.step === STEP.result) {
        state.step = STEP.budget;
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
        clearSelfie(false);
        render();
      });
    }
    const retakeQ = panel.querySelector("#btn-retake-quality");
    if (retakeQ) {
      retakeQ.addEventListener("click", () => {
        state.quality.retakes += 1;
        clearSelfie(true);
        render();
      });
    }
    const noPhoto = panel.querySelector("#btn-continue-no-photo");
    if (noPhoto) {
      noPhoto.addEventListener("click", () => {
        clearSelfie(true);
        render();
      });
    }
    const confirmYes = panel.querySelector("#btn-confirm-yes");
    if (confirmYes) {
      confirmYes.addEventListener("click", () => {
        if (state.confirm) state.confirm.action = "confirm";
        finishToResult(state.confirmObs);
      });
    }
    const confirmChange = panel.querySelector("#btn-confirm-change");
    if (confirmChange) {
      confirmChange.addEventListener("click", () => {
        if (state.confirm) state.confirm.editing = true;
        render();
      });
    }
    const cTypes = panel.querySelector("#confirm-types");
    if (cTypes) {
      cTypes.querySelectorAll(".choice").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-id");
          state.confirm.corrected_skin_type = id === "not_sure" ? null : id;
          render();
        });
      });
    }
    const cConcerns = panel.querySelector("#confirm-concerns");
    if (cConcerns) {
      cConcerns.querySelectorAll(".choice").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-id");
          const arr = state.confirm.corrected_concerns;
          const idx = arr.indexOf(id);
          if (idx >= 0) {
            arr.splice(idx, 1);
            state.confirm.limitNote = false;
          } else if (arr.length >= 2) {
            state.confirm.limitNote = true;
          } else {
            arr.push(id);
            state.confirm.limitNote = false;
          }
          render();
        });
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

  // Normal path: read on the phone. Gemini only when the device reader fails
  // to load/run or is not confident (and Gemini is configured).
  async function readOnDevice() {
    try {
      const bmp = await createImageBitmap(state.selfie.file);
      const res = await window.ShelloSkinReader.read(bmp, state.selfie.landmarks);
      try { bmp.close && bmp.close(); } catch (_) {}
      return res;
    } catch (_) {
      return null;
    }
  }

  async function maybeAnalyzeSelfie() {
    if (!state.selfie.file || state.selfie.analyzed) return state.selfie.observations;
    let fallbackReason = null;
    if (deviceReaderEnabled()) {
      const dev = await readOnDevice();
      if (dev && (dev.confident || !state.analysisAvailable)) {
        state.selfie.analyzed = true;
        state.selfie.observations = dev.observations;
        state.selfie.meta = dev.meta;
        state.analysisNote = null;
        return dev.observations;
      }
      fallbackReason = dev ? "low_confidence" : "device_failed";
    }
    if (!state.analysisAvailable) return state.selfie.observations;
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
        state.selfie.meta = null;
        state.analysisNote = "fallback";
        return null;
      }
      const data = await resp.json().catch(() => ({}));
      state.selfie.analyzed = true;
      state.selfie.observations = (data && data.observations) || null;
      state.selfie.meta = {
        provider: (data && data.provider) || null,
        model: (data && data.model) || null,
        prompt_version: (data && data.prompt_version) || null,
        latency_ms: (data && data.latency_ms) || null,
        fallback_reason: fallbackReason,
      };
      if (!state.selfie.observations) {
        state.analysisNote = "fallback";
      }
      return state.selfie.observations;
    } catch (_) {
      state.selfie.analyzed = true;
      state.selfie.observations = null;
      state.selfie.meta = null;
      state.analysisNote = "fallback";
      return null;
    }
  }

  async function onNext() {
    if (state.step === STEP.confirm) {
      const c = state.confirm;
      if (c && c.editing) {
        c.action = "correct";
        const corrected = Object.assign({}, state.confirmObs || {}, {
          skin_type_hint: c.corrected_skin_type || null,
          concerns: c.corrected_concerns || [],
        });
        finishToResult(corrected);
      }
      return;
    }
    if (state.step < STEP.selfie) {
      state.step += 1;
      render();
      return;
    }
    if (state.step === STEP.selfie) {
      // A photo that is still being checked or failed the gate blocks
      // progression — the shopper must retake or use "Continue without photo".
      if (
        state.selfie.file &&
        (state.quality.status === "checking" || state.quality.status === "fail")
      ) {
        return;
      }
      panel.querySelector("#btn-next") && (panel.querySelector("#btn-next").disabled = true);
      leaveSelfieStep();
      const observations = state.selfie.file ? await maybeAnalyzeSelfie() : null;
      if (
        observations &&
        !observations.refer_dermatologist &&
        (observations.skin_type_hint || (observations.concerns || []).length)
      ) {
        state.confirm = {
          action: null,
          editing: false,
          limitNote: false,
          read_skin_type: observations.skin_type_hint || null,
          read_concerns: (observations.concerns || []).slice(),
          corrected_skin_type: observations.skin_type_hint || null,
          corrected_concerns: (observations.concerns || []).slice(0, 2),
        };
        state.confirmObs = observations;
        state.step = STEP.confirm;
        render();
        return;
      }
      state.confirm = null;
      state.confirmObs = null;
      finishToResult(observations);
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
