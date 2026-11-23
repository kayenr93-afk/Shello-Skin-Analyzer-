/**
 * Deterministic quiz-to-catalog matcher.
 * Works entirely in the browser with no API key.
 * Reasons are sentences taken from the product's own page copy only.
 */
(function (root) {
  const CONCERN_LABELS = {
    breakouts: "breakouts",
    dullness: "dullness",
    dryness: "dryness",
    uneven_tone: "uneven tone",
    tan: "tan",
    sensitivity: "sensitivity",
    oil_control: "oil control",
    sun_protection: "sun protection",
    hydration: "hydration",
  };

  const RED_FLAG_LABELS = {
    painful_cysts: "painful cysts",
    rashes: "rashes",
    bleeding: "bleeding",
    sudden_changes: "sudden changes",
    photo_concern: "a concern from your photo",
  };

  const STEP_TYPES = {
    cleanse: {
      face: ["cleanser"],
      body: ["body wash", "shower gel"],
    },
    treat: {
      face: ["serum/treat", "scrub", "sunscreen"],
      body: ["serum/treat", "sunscreen"],
    },
    moisturise: {
      face: ["moisturiser"],
      body: ["body lotion", "moisturiser", "hand cream"],
    },
  };

  function formatRs(amount) {
    if (amount == null || Number.isNaN(Number(amount))) return "";
    const n = Math.round(Number(amount));
    return "Rs " + n.toLocaleString("en-LK");
  }

  function sentences(text) {
    return String(text || "")
      .replace(/\s+/g, " ")
      .trim()
      .split(/(?<=[.!?])\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function areaFits(item, area) {
    if (!area || area === "both") return true;
    return item.area === area || item.area === "both";
  }

  function allowedTypes(step, area) {
    if (area === "both") {
      const set = new Set([
        ...STEP_TYPES[step].face,
        ...STEP_TYPES[step].body,
      ]);
      return [...set];
    }
    return STEP_TYPES[step][area] || STEP_TYPES[step].face;
  }

  function hasSignal(item, name) {
    return (item.copy_signals || []).includes(name);
  }

  function copyHas(item, pattern) {
    const blob = `${item.name || ""} ${item.page_copy || item.short_description || ""}`;
    return pattern.test(blob);
  }

  function scoreItem(item, quiz, step) {
    let score = 0;
    const type = item.product_type;
    const signals = new Set(item.copy_signals || []);
    const skin = quiz.skin_type;
    const concerns = new Set(quiz.concerns || []);
    const area = quiz.area;

    if (item.area === area) score += 6;
    else if (item.area === "both") score += 4;

    if (step === "cleanse") {
      if (area === "face" && type === "cleanser") score += 8;
      if (area === "body" && (type === "body wash" || type === "shower gel")) score += 8;
      if (area === "both") {
        if (type === "cleanser") score += 7;
        if (type === "body wash" || type === "shower gel") score += 5;
      }
      if (skin === "oily" && (signals.has("oily") || signals.has("pimples"))) score += 10;
      if (skin === "dry" && (signals.has("dry") || copyHas(item, /normal to dry/i))) score += 10;
      if (skin === "dry" && copyHas(item, /normal to dry/i)) score += 4;
      if (skin === "sensitive" && (signals.has("sensitive") || signals.has("fragrance_free") || copyHas(item, /sooth|calm|fragrance-free/i))) score += 8;
      if (skin === "normal" && signals.has("normal_skin")) score += 8;
      if (skin === "combination" && signals.has("all_skin_types")) score += 6;
      if (concerns.has("breakouts") && signals.has("pimples")) score += 10;
      if (concerns.has("oil_control") && (signals.has("oily") || signals.has("pimples"))) score += 8;
      if (concerns.has("dullness") && (signals.has("dull") || signals.has("glow") || signals.has("exfoliate"))) score += 6;
      if (concerns.has("tan") && signals.has("exfoliate")) score += 4;
      if (concerns.has("sensitivity") && (signals.has("sensitive") || signals.has("fragrance_free") || copyHas(item, /sooth|calm|fragrance-free/i))) score += 8;
      else if (concerns.has("sensitivity") && signals.has("soap_free")) score += 2;
      if (concerns.has("dryness") || concerns.has("hydration")) {
        if (signals.has("dry") || copyHas(item, /hydrat|moisture/i)) score += 5;
      }
      // Body-specific
      if (area !== "face" && concerns.has("dullness") && (signals.has("glow") || signals.has("exfoliate"))) score += 5;
      if (skin === "sensitive" && signals.has("exfoliate") && type === "body wash") score -= 8;
    }

    if (step === "treat") {
      if (type === "serum/treat") score += 8;
      if (type === "scrub") score += 3;
      if (type === "sunscreen" && concerns.has("sun_protection")) score += 10;
      if (concerns.has("dullness") && (signals.has("dull") || signals.has("glow"))) score += 10;
      if (concerns.has("uneven_tone") && (signals.has("uneven_tone") || signals.has("glow"))) score += 8;
      if (concerns.has("tan") && (signals.has("tan") || type === "scrub")) score += 8;
      if (concerns.has("hydration") && signals.has("lightweight")) score += 3;
      if (concerns.has("sun_protection") && signals.has("sun")) score += 8;
      // Skip scrubs for sensitive skin or active breakouts — they can feel harsh
      if (type === "scrub" && (skin === "sensitive" || concerns.has("sensitivity"))) score -= 20;
      if (type === "scrub" && concerns.has("breakouts")) score -= 8;
      if (skin === "sensitive" && (signals.has("sensitive") || copyHas(item, /sooth|calm/i))) score += 4;
      if (skin === "oily" && signals.has("lightweight")) score += 3;
    }

    if (step === "moisturise") {
      if (area === "face" && type === "moisturiser") score += 8;
      if (area === "body" && type === "body lotion") score += 8;
      if (area === "body" && type === "moisturiser" && item.area === "both") score += 6;
      if (area === "body" && type === "hand cream") score += 2;
      if (skin === "oily" && signals.has("lightweight")) score += 8;
      if ((skin === "oily" || concerns.has("breakouts") || concerns.has("oil_control")) && /gel/i.test(item.name || "")) score += 8;
      if ((skin === "oily" || concerns.has("breakouts")) && /cream/i.test(item.name || "")) score -= 5;
      if (skin === "dry" && (signals.has("dry") || signals.has("rich"))) score += 10;
      if (skin === "sensitive" && (signals.has("sensitive") || signals.has("fragrance_free"))) score += 12;
      if (skin === "normal" && signals.has("lightweight")) score += 3;
      if (concerns.has("dryness") && (signals.has("dry") || signals.has("rich"))) score += 8;
      if ((skin === "dry" || concerns.has("dryness")) && /vitamin e|moisturis/i.test(item.name || "")) score += 4;
      if (concerns.has("hydration") && (signals.has("dry") || copyHas(item, /hydrat|moistur/i))) score += 5;
      if (concerns.has("sensitivity") && (signals.has("sensitive") || signals.has("fragrance_free"))) score += 10;
      if (concerns.has("dullness") && (signals.has("glow") || signals.has("dull") || signals.has("uneven_tone"))) score += 6;
      if (concerns.has("uneven_tone") && signals.has("uneven_tone")) score += 7;
      if (concerns.has("sun_protection") && signals.has("sun")) score += 8;
      if (skin === "oily" && signals.has("rich") && !signals.has("lightweight")) score -= 4;
      if (skin === "sensitive" && type === "hand cream") score -= 2;
    }

    // Climate is a light nudge, never enough to override skin type or safety penalties.
    const climate = quiz.climate;
    const outdoors = quiz.outdoors;
    if (climate === "humid") {
      if (signals.has("lightweight") || /gel/i.test(item.name || "")) score += 3;
      if (signals.has("rich") && !signals.has("lightweight")) score -= 2;
    }
    if (climate === "ac") {
      if (step === "moisturise" && (signals.has("rich") || signals.has("dry") || /cream|lotion/i.test(item.name || ""))) score += 3;
      if (step === "moisturise" && /gel/i.test(item.name || "") && !signals.has("rich")) score -= 1;
    }
    if (outdoors === "often" || outdoors === "sometimes") {
      const boost = outdoors === "often" ? 8 : 5;
      // Only suggest the body sunscreen when the routine is for body (or both).
      // There is no face-specific sunscreen in the current catalog; do not place a body lotion on a face-only routine.
      if (type === "sunscreen" && area !== "face") score += boost;
      if (step === "moisturise" && area !== "body" && signals.has("sun")) score += 3;
    }

    return score;
  }

  // -> { text, fit } where text is a sentence from the product page and fit
  // lists the shopper's own answers ("skin:oily", "concern:breakouts") that
  // the chosen sentence speaks to. fit is empty when the sentence is generic.
  function reasonDetail(item, quiz, step) {
    const text = item.short_description || item.description || item.page_copy || "";
    const lines = sentences(text);
    if (!lines.length) return { text: "As described on the product page.", fit: [] };

    const concerns = new Set(quiz.concerns || []);
    const answered = (skins, cs) => [
      ...skins.filter((s) => quiz.skin_type === s).map((s) => "skin:" + s),
      ...cs.filter((c) => concerns.has(c)).map((c) => "concern:" + c),
    ];
    const groups = [
      [/oily|sebum|pimple|blemish|breakout|shine|excess oil/i, answered(["oily"], ["oil_control", "breakouts"])],
      [/dry|hydrat|moisture|nourish|soft/i, answered(["dry"], ["dryness", "hydration"])],
      [/sensitive|soothe|calm|fragrance-free|gentle|irritat/i, answered(["sensitive"], ["sensitivity"])],
      [/dull|glow|radiant|brighten|tone|dark (spot|mark)|luminous/i, answered([], ["dullness", "uneven_tone"])],
      [/tan|de-tanning|sun tan/i, answered([], ["tan"])],
    ];
    const patterns = groups.filter(([, fit]) => fit.length);
    if (concerns.has("sun_protection") || item.product_type === "sunscreen") {
      patterns.push([/spf|uv|sun protection|sunburn|sun rays/i, answered([], ["sun_protection"])]);
    }
    if (step === "cleanse") patterns.push([/cleanse|wash away|impurit|soap-free/i, []]);
    if (step === "treat") patterns.push([/glow|saffron|exfoliat|dull/i, []]);
    if (item.product_type === "moisturiser" || item.product_type === "body lotion") {
      patterns.push([/moisturis|moisturiz|hydrat|aloe/i, []]);
    }

    for (const [pattern, fit] of patterns) {
      const hit = lines.find((line) => pattern.test(line));
      if (hit) return { text: hit, fit };
    }
    return { text: lines[0], fit: [] };
  }

  function reasonFor(item, quiz, step) {
    return reasonDetail(item, quiz, step).text;
  }

  function groupByParent(items) {
    const map = new Map();
    for (const item of items) {
      const key = item.parent_id || item.id;
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(item);
    }
    for (const list of map.values()) {
      list.sort((a, b) => (a.size_amount || 0) - (b.size_amount || 0) || (a.price_lkr || 0) - (b.price_lkr || 0));
    }
    return map;
  }

  function sizeBandBonus(item, bandId) {
    const amount = Number(item.size_amount) || 0;
    const unit = (item.size_unit || "").toLowerCase();
    const ml = unit === "g" ? amount : amount; // already normalised to ml/g in catalog
    if (bandId === "value") {
      return ml && ml <= 60 ? 2 : 0;
    }
    if (bandId === "everyday") {
      if (ml >= 80 && ml <= 200) return 4;
      if (ml >= 50 && ml < 80) return 1;
      if (ml && ml < 50) return -3;
      return 0;
    }
    if (bandId === "generous") {
      if (ml >= 160) return 3;
      if (ml && ml < 50) return -2;
    }
    return 0;
  }

  function pickSize(family, budgetMax, remaining, bandId) {
    const affordable = family.filter(
      (item) => item.price_lkr != null && item.price_lkr <= remaining
    );
    if (!affordable.length) return null;
    const bySize = [...affordable].sort(
      (a, b) => (a.size_amount || 0) - (b.size_amount || 0) || a.price_lkr - b.price_lkr
    );
    if (bandId === "value") return bySize[0];
    if (bandId === "generous") return bySize[bySize.length - 1];
    // Everyday: prefer a daily-use size (about 80–200ml) when one fits.
    const daily = bySize.filter((item) => {
      const ml = Number(item.size_amount) || 0;
      return ml >= 80 && ml <= 200;
    });
    if (daily.length) return daily[0];
    if (bySize.length === 1) return bySize[0];
    return bySize[Math.floor((bySize.length - 1) / 2)] || bySize[0];
  }

  function bandFor(catalog, budgetId) {
    const bands = (catalog && catalog.budget_bands) || [];
    return bands.find((b) => b.id === budgetId) || bands[0] || { id: "generous", max_lkr: null };
  }

  function recommend(catalog, quiz) {
    const items = (catalog && catalog.included) || [];
    const flags = (quiz.red_flags || []).filter(Boolean);
    const disclaimer =
      (catalog && catalog.disclaimer) ||
      "These are cosmetic suggestions only and are not a medical diagnosis or treatment.";

    if (flags.length) {
      const labels = flags.map((f) => RED_FLAG_LABELS[f] || f);
      return {
        blocked: true,
        dermatologist_note: true,
        title: "Please see a dermatologist",
        message:
          "You mentioned " +
          labels.join(", ") +
          ". That is beyond what a cosmetic routine can safely address. Please see a qualified dermatologist in Sri Lanka before trying new skin-care products. This tool will not recommend products in this situation.",
        steps: [],
        total_lkr: 0,
        disclaimer,
      };
    }

    const band = bandFor(catalog, quiz.budget);
    const budgetMax = band.max_lkr == null ? Infinity : Number(band.max_lkr);
    let remaining = budgetMax;
    const usedParents = new Set();
    const steps = [];
    const area = quiz.area || "face";

    const pickOrder = ["cleanse", "moisturise", "treat"];
    const labels = {
      cleanse: "Cleanse",
      treat: "Treat / glow",
      moisturise: "Moisturise",
    };

    function pickForStep(step, remainingBudget) {
      const types = new Set(allowedTypes(step, area));
      const candidates = items.filter(
        (item) =>
          types.has(item.product_type) &&
          areaFits(item, area) &&
          item.in_stock !== false &&
          item.price_lkr != null &&
          !usedParents.has(item.parent_id || item.id)
      );
      if (!candidates.length) return null;

      const families = groupByParent(candidates);
      const ranked = [];
      for (const [parentId, family] of families) {
        const bestInFamily = [...family].sort(
          (a, b) => scoreItem(b, quiz, step) - scoreItem(a, quiz, step)
        )[0];
        const familyScore = scoreItem(bestInFamily, quiz, step);
        if (familyScore <= -10) continue;
        const sized = pickSize(family, budgetMax, remainingBudget, band.id);
        const fit = sized ? sizeBandBonus(sized, band.id) : 0;
        ranked.push({ parentId, family, score: familyScore + fit, base: familyScore, pick: sized });
      }
      ranked.sort((a, b) => b.score - a.score || ((a.pick && a.pick.price_lkr) || 0) - ((b.pick && b.pick.price_lkr) || 0));
      if (!ranked.length) return null;
      if (step === "treat" && ranked[0].score < 4) return null;

      for (const entry of ranked) {
        const pick = entry.pick || pickSize(entry.family, budgetMax, remainingBudget, band.id);
        if (pick) return pick;
      }
      return null;
    }

    for (const step of pickOrder) {
      const chosen = pickForStep(step, remaining);
      if (!chosen) continue;
      usedParents.add(chosen.parent_id || chosen.id);
      remaining -= chosen.price_lkr;
      steps.push({
        step,
        label: labels[step],
        id: chosen.id,
        parent_id: chosen.parent_id,
        name: chosen.name,
        size: chosen.size || "",
        price_lkr: chosen.price_lkr,
        price_label: formatRs(chosen.price_lkr),
        permalink: chosen.permalink,
        image: chosen.image || "",
        image_width: chosen.image_width || 300,
        image_height: chosen.image_height || 300,
        product_type: chosen.product_type,
        area: chosen.area,
        ...(({ text, fit }) => ({ reason: text, fit }))(reasonDetail(chosen, quiz, step)),
        cart: cartFields(chosen),
      });
    }

    const stepRank = { cleanse: 1, treat: 2, moisturise: 3 };
    steps.sort((a, b) => stepRank[a.step] - stepRank[b.step]);

        const total = steps.reduce((sum, s) => sum + (s.price_lkr || 0), 0);
    return {
      blocked: false,
      dermatologist_note: false,
      title: steps.length ? "Your Shello routine" : "No matching routine",
      message: steps.length
        ? `A ${steps.length}-step routine within the ${band.label} budget band${
            band.max_lkr ? ` (up to ${formatRs(band.max_lkr)})` : ""
          }.`
        : "No in-stock Shello skin products fitted this combination of area and budget. Try a higher budget band or a different area.",
      steps,
      total_lkr: total,
      total_label: formatRs(total),
      budget_band: band,
      disclaimer,
    };
  }

  function mergeQuizWithObservations(quiz, observations) {
    const next = {
      skin_type: quiz.skin_type,
      concerns: [...(quiz.concerns || [])],
      area: quiz.area,
      budget: quiz.budget,
      red_flags: [...(quiz.red_flags || [])],
    };
    next.climate = quiz.climate || null;
    next.outdoors = quiz.outdoors || null;
    if (!observations) {
      next.concerns = next.concerns.slice(0, 2);
      return next;
    }
    if (observations.refer_dermatologist) {
      if (!next.red_flags.includes("photo_concern")) next.red_flags.push("photo_concern");
    }
    // Quiz answers win. Selfie may fill leftover concern slots only, up to two.
    for (const c of observations.concerns || []) {
      if (next.concerns.length >= 2) break;
      if (!next.concerns.includes(c)) next.concerns.push(c);
    }
    next.concerns = next.concerns.slice(0, 2);
    return next;
  }


  function cartFields(item) {
    const attrs = {};
    try {
      const url = new URL(item.permalink, "https://shellocosmetics.com");
      url.searchParams.forEach((value, key) => {
        if (/^attribute_/i.test(key)) attrs[key] = value;
      });
    } catch (_) {}
    return {
      id: item.id,
      parent_id: item.parent_id || item.id,
      is_variation: Boolean(item.parent_id && item.parent_id !== item.id),
      attributes: attrs,
    };
  }

  function addToCartUrl(step) {
    const cart = step.cart || cartFields(step);
    const params = new URLSearchParams();
    if (cart.is_variation) {
      params.set("add-to-cart", String(cart.parent_id));
      params.set("variation_id", String(cart.id));
      const attrs = cart.attributes || {};
      Object.keys(attrs).forEach((key) => params.set(key, attrs[key]));
    } else {
      params.set("add-to-cart", String(cart.id || step.id));
    }
    params.set("quantity", "1");
    return "https://shellocosmetics.com/cart/?" + params.toString();
  }

  const KIT_DEFS = [
    { id: "oily_breakouts", quiz: { skin_type: "oily", concerns: ["breakouts"], area: "face", budget: "everyday", red_flags: [] } },
    { id: "dry_dull", quiz: { skin_type: "dry", concerns: ["dullness"], area: "face", budget: "everyday", red_flags: [] } },
    { id: "sensitive", quiz: { skin_type: "sensitive", concerns: ["sensitivity"], area: "face", budget: "everyday", red_flags: [] } },
    { id: "glow", quiz: { skin_type: "normal", concerns: ["dullness", "uneven_tone"], area: "face", budget: "everyday", red_flags: [] } },
    { id: "body_care", quiz: { skin_type: "normal", concerns: ["hydration", "sun_protection"], area: "body", budget: "everyday", red_flags: [] } },
  ];

  function readyKits(catalog) {
    return KIT_DEFS.map((def) => {
      const result = recommend(catalog, def.quiz);
      return {
        id: def.id,
        quiz: def.quiz,
        result,
        total_lkr: result.total_lkr,
        total_label: result.total_label,
        steps: result.steps || [],
      };
    }).filter((kit) => kit.steps.length);
  }

  root.ShelloMatcher = {
    CONCERN_LABELS,
    RED_FLAG_LABELS,
    KIT_DEFS,
    formatRs,
    recommend,
    mergeQuizWithObservations,
    scoreItem,
    reasonFor,
    cartFields,
    addToCartUrl,
    readyKits,
  };
})(typeof window !== "undefined" ? window : globalThis);
