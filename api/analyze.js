/**
 * POST /api/analyze
 * Optional selfie analysis. The image is read from the request body only and
 * is never written to disk, cache, or logs.
 *
 * Prefers GEMINI_API_KEY; falls back to OPENAI_API_KEY.
 * When no key is set, returns analysis_available: false so the quiz can
 * still build a routine on the client.
 *
 * GET /api/analyze reports whether analysis is configured (no secrets).
 */

const GEMINI_MODELS = [
  "gemini-3.5-flash-lite",
  "gemini-3.1-flash-lite",
  "gemini-flash-lite-latest",
  "gemini-3.8-flash",
  "gemini-3.5-flash",
  "gemini-flash-latest",
];

const OPENAI_MODEL = "gpt-4o-mini";

const SYSTEM_PROMPT = `You are a cosmetic skin-care assistant for Shello Cosmetics.
You look at an optional selfie and return JSON only.

Rules:
- Cosmetic appearance notes only. Never diagnose, never name a medical condition, never suggest prescription treatment.
- If the photo appears to show painful-looking cysts, a widespread rash, bleeding, or a sudden / severe change, set refer_dermatologist to true and keep observations conservative.
- Do not invent product names or ingredients. Do not recommend any product.
- If the image is not a face or body skin photo, say so in notes and leave hints empty.
- Concerns must be chosen only from this list: breakouts, dullness, dryness, uneven_tone, tan, sensitivity, oil_control, sun_protection, hydration.
- skin_type_hint must be one of: oily, dry, combination, normal, sensitive, or null if unsure.

Return a JSON object with this exact shape:
{
  "skin_type_hint": "oily" | "dry" | "combination" | "normal" | "sensitive" | null,
  "concerns": string[],
  "notes": string,
  "refer_dermatologist": boolean,
  "confidence": "low" | "medium" | "high"
}`;

function noStore(res) {
  res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate");
  res.setHeader("Pragma", "no-cache");
}

function availability() {
  const gemini = Boolean(process.env.GEMINI_API_KEY && process.env.GEMINI_API_KEY.trim());
  const openai = Boolean(process.env.OPENAI_API_KEY && process.env.OPENAI_API_KEY.trim());
  return {
    ok: true,
    analysis_available: gemini || openai,
    provider: gemini ? "gemini" : openai ? "openai" : null,
    message: gemini || openai
      ? "Optional selfie analysis is available. Shello does not store photos."
      : "Selfie analysis is not configured. Your quiz answers will still be used to build a routine.",
  };
}

function parseBody(req) {
  if (!req.body) return {};
  if (typeof req.body === "string") {
    try {
      return JSON.parse(req.body);
    } catch {
      return {};
    }
  }
  return req.body;
}

function normalizeImage(body) {
  let b64 = (body.image_base64 || body.image || "").toString().trim();
  let mime = (body.mime_type || body.mimeType || "image/jpeg").toString().trim();
  if (b64.startsWith("data:")) {
    const match = /^data:([^;]+);base64,(.+)$/s.exec(b64);
    if (match) {
      mime = match[1];
      b64 = match[2];
    }
  }
  b64 = b64.replace(/\s+/g, "");
  return { b64, mime };
}

function safeParseModelJson(text) {
  if (!text) return null;
  const trimmed = text.trim();
  const fence = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/);
  const raw = fence ? fence[1] : trimmed;
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start === -1 || end === -1) return null;
  try {
    return JSON.parse(raw.slice(start, end + 1));
  } catch {
    return null;
  }
}

function sanitizeObservations(parsed) {
  const types = new Set(["oily", "dry", "combination", "normal", "sensitive"]);
  const concernSet = new Set([
    "breakouts",
    "dullness",
    "dryness",
    "uneven_tone",
    "tan",
    "sensitivity",
    "oil_control",
    "sun_protection",
    "hydration",
  ]);
  const hint = types.has(parsed && parsed.skin_type_hint) ? parsed.skin_type_hint : null;
  const concerns = Array.isArray(parsed && parsed.concerns)
    ? parsed.concerns.filter((c) => concernSet.has(c))
    : [];
  const confidence = ["low", "medium", "high"].includes(parsed && parsed.confidence)
    ? parsed.confidence
    : "low";
  const notes = typeof (parsed && parsed.notes) === "string"
    ? parsed.notes.slice(0, 400)
    : "";
  return {
    skin_type_hint: hint,
    concerns,
    notes,
    refer_dermatologist: Boolean(parsed && parsed.refer_dermatologist),
    confidence,
  };
}

async function analyzeWithGemini(b64, mime) {
  const key = process.env.GEMINI_API_KEY.trim();
  let lastError = null;
  for (const model of GEMINI_MODELS) {
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(key)}`;
    const payload = {
      systemInstruction: { parts: [{ text: SYSTEM_PROMPT }] },
      contents: [
        {
          role: "user",
          parts: [
            {
              text: "Review this selfie for cosmetic skin-appearance notes only. Return JSON.",
            },
            { inlineData: { mimeType: mime, data: b64 } },
          ],
        },
      ],
      generationConfig: {
        temperature: 0.2,
        responseMimeType: "application/json",
      },
    };
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      lastError = (json.error && json.error.message) || `Gemini HTTP ${resp.status}`;
      // Try the next model on 404 / not found / unsupported
      if (
        resp.status === 404
        || resp.status === 503
        || /not found|not supported|unknown model|high demand|unavailable|no longer available/i.test(String(lastError))
      ) {
        continue;
      }
      throw new Error(lastError);
    }
    const text = (((json.candidates || [])[0] || {}).content || {}).parts
      ? json.candidates[0].content.parts.map((p) => p.text || "").join("\n")
      : "";
    const parsed = safeParseModelJson(text) || (typeof json === "object" ? null : null);
    const observations = sanitizeObservations(parsed || safeParseModelJson(JSON.stringify(json)));
    // If the model already returned parsed JSON via responseMimeType, candidates text is JSON
    return { provider: "gemini", model, observations };
  }
  throw new Error(lastError || "No Gemini model accepted the request");
}

async function analyzeWithOpenAI(b64, mime) {
  const key = process.env.OPENAI_API_KEY.trim();
  const resp = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: OPENAI_MODEL,
      temperature: 0.2,
      response_format: { type: "json_object" },
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        {
          role: "user",
          content: [
            {
              type: "text",
              text: "Review this selfie for cosmetic skin-appearance notes only. Return JSON.",
            },
            {
              type: "image_url",
              image_url: { url: `data:${mime};base64,${b64}` },
            },
          ],
        },
      ],
    }),
  });
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error((json.error && json.error.message) || `OpenAI HTTP ${resp.status}`);
  }
  const text = (((json.choices || [])[0] || {}).message || {}).content || "";
  return {
    provider: "openai",
    model: OPENAI_MODEL,
    observations: sanitizeObservations(safeParseModelJson(text)),
  };
}

module.exports = async function handler(req, res) {
  noStore(res);
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  if (req.method === "GET") {
    res.status(200).json(availability());
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ ok: false, error: "Method not allowed" });
    return;
  }

  const available = availability();
  const body = parseBody(req);
  const { b64, mime } = normalizeImage(body);

  if (!available.analysis_available) {
    res.status(200).json({
      ...available,
      observations: null,
    });
    return;
  }

  if (!b64) {
    res.status(200).json({
      ...available,
      observations: null,
      message: "No selfie was sent. Your quiz answers will still be used to build a routine.",
    });
    return;
  }

  if (!/^image\/(jpeg|jpg|png|webp|gif|heic|heif)$/i.test(mime)) {
    res.status(400).json({
      ok: false,
      analysis_available: true,
      provider: available.provider,
      error: "Please upload a JPEG, PNG, or WebP photo.",
    });
    return;
  }

  // Rough size cap (~8MB decoded) to protect the function. Body is not stored.
  if (b64.length > 11_000_000) {
    res.status(413).json({
      ok: false,
      analysis_available: true,
      provider: available.provider,
      error: "That photo is too large. Please try a smaller snapshot.",
    });
    return;
  }

  try {
    let result;
    if (process.env.GEMINI_API_KEY && process.env.GEMINI_API_KEY.trim()) {
      result = await analyzeWithGemini(b64, mime);
    } else {
      result = await analyzeWithOpenAI(b64, mime);
    }
    res.status(200).json({
      ok: true,
      analysis_available: true,
      provider: result.provider,
      model: result.model,
      observations: result.observations,
      message: "Cosmetic appearance notes only — not a diagnosis. Shello does not store the photo.",
    });
  } catch (err) {
    // Never include the image or key in the response or a custom log.
    res.status(200).json({
      ok: true,
      analysis_available: true,
      provider: available.provider,
      observations: null,
      message:
        "Selfie analysis could not be completed just now. Your quiz answers will still be used to build a routine.",
    });
  }
};
