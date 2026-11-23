/* Client-side aggregation for /api/dashboard. The token stays in
 * sessionStorage only; event payloads are rendered as tables and can be
 * exported to CSV. No external scripts. */
(function () {
  const tokenInput = document.getElementById("token");
  const btnLoad = document.getElementById("btn-load");
  const errEl = document.getElementById("err");
  const dash = document.getElementById("dash");
  const tablesEl = document.getElementById("tables");
  const summaryEl = document.getElementById("summary");
  let events = [];
  let catalogNames = {};

  try {
    tokenInput.value = sessionStorage.getItem("shello-dash-token") || "";
  } catch (_) {}

  function table(title, headers, rows) {
    const head = headers.map((h) => `<th>${h}</th>`).join("");
    const body = rows
      .map((r) => `<tr>${r.map((c) => `<td>${c == null ? "" : c}</td>`).join("")}</tr>`)
      .join("");
    return `<h2>${title}</h2><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function pct(n, d) {
    return d ? ((100 * n) / d).toFixed(1) + "%" : "-";
  }

  function percentile(arr, p) {
    if (!arr.length) return null;
    const s = arr.slice().sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  }

  const STEP_ORDER = ["landing", "skin", "concerns", "safety", "area", "climate", "budget", "scan", "confirm", "result"];

  function aggregate(evs) {
    const sessions = {};
    for (const e of evs) {
      sessions[e.session_id] = sessions[e.session_id] || [];
      sessions[e.session_id].push(e);
    }
    const results = evs.filter((e) => e.type === "session_result");
    const feedback = evs.filter((e) => e.type === "product_feedback");
    const clicks = evs.filter((e) => e.type === "click");
    const carts = evs.filter((e) => e.type === "add_to_cart");

    // sessions per day
    const perDay = {};
    for (const id of Object.keys(sessions)) {
      const day = (sessions[id][0].created_at || "").slice(0, 10);
      perDay[day] = (perDay[day] || 0) + 1;
    }

    const withPhoto = results.filter((e) => ["gemini", "openai", "device"].includes((e.payload.reader || {}).source)).length;
    const qRun = results.filter((e) => (e.payload.quality || {}).ran);
    const qFail = qRun.filter((e) => e.payload.quality.pass === false);
    const reasonCount = {};
    for (const e of qFail) for (const r of (e.payload.quality || {}).reasons || []) {
      reasonCount[r] = (reasonCount[r] || 0) + 1;
    }

    const actions = { confirm: 0, correct: 0, skipped: 0 };
    const byModel = {};
    for (const e of results) {
      const a = (e.payload.confirm || {}).action || "skipped";
      actions[a] = (actions[a] || 0) + 1;
      const model = (e.payload.reader || {}).model || "none";
      byModel[model] = byModel[model] || { confirm: 0, correct: 0, skipped: 0 };
      byModel[model][a] = (byModel[model][a] || 0) + 1;
    }

    const matrix = {};
    for (const e of results) {
      const c = e.payload.confirm || {};
      if (c.action === "correct") {
        const k = `${c.read_skin_type || "-"} -> ${c.corrected_skin_type || "not sure"}`;
        matrix[k] = (matrix[k] || 0) + 1;
      }
    }

    const perProduct = {};
    for (const e of feedback) {
      const p = e.payload.product_id;
      perProduct[p] = perProduct[p] || { up: 0, down: 0, view: 0, wa: 0, cart: 0 };
      perProduct[p][e.payload.value]++;
    }
    for (const e of clicks) {
      const p = e.payload.product_id;
      if (p == null) continue;
      perProduct[p] = perProduct[p] || { up: 0, down: 0, view: 0, wa: 0, cart: 0 };
      if (e.payload.kind === "view_product") perProduct[p].view++;
      if (e.payload.kind === "whatsapp") perProduct[p].wa++;
    }
    for (const e of carts) {
      for (const p of e.payload.product_ids || []) {
        perProduct[p] = perProduct[p] || { up: 0, down: 0, view: 0, wa: 0, cart: 0 };
        perProduct[p].cart++;
      }
    }

    const latencies = results
      .map((e) => (e.payload.reader || {}).latency_ms)
      .filter((v) => typeof v === "number");

    const langs = {};
    for (const e of results) langs[e.payload.lang || "?"] = (langs[e.payload.lang || "?"] || 0) + 1;

    // Step drop-off: distinct sessions that reached each quiz step.
    const reached = {};
    for (const e of evs) {
      if (e.type !== "step_view") continue;
      (reached[e.payload.step] = reached[e.payload.step] || new Set()).add(e.session_id);
    }
    const funnel = STEP_ORDER.filter((s) => reached[s]).map((s) => [s, reached[s].size]);

    return { sessions, results, perDay, withPhoto, qRun, qFail, reasonCount, actions, byModel, matrix, perProduct, latencies, langs, funnel };
  }

  function render(evs) {
    const a = aggregate(evs);
    const nSessions = Object.keys(a.sessions).length;
    summaryEl.textContent = `${evs.length} events · ${nSessions} sessions · ${a.results.length} results`;

    const name = (p) => esc(catalogNames[p] || ("#" + p));
    let html = "";
    html += table("Sessions per day", ["day", "sessions"],
      Object.keys(a.perDay).sort().map((d) => [d, a.perDay[d]]));
    html += table("Usage", ["metric", "value"], [
      ["sessions", nSessions],
      ["selfie (reader) used", `${a.withPhoto} / ${a.results.length} (${pct(a.withPhoto, a.results.length)})`],
      ["quality gate ran", a.qRun.length],
      ["quality gate failed", `${a.qFail.length} (${pct(a.qFail.length, a.qRun.length)})`],
      ["reader latency p50 (ms)", percentile(a.latencies, 50)],
      ["reader latency p95 (ms)", percentile(a.latencies, 95)],
    ]);
    const top = a.funnel.length ? a.funnel[0][1] : 0;
    html += table("Quiz drop-off (sessions reaching each step)", ["step", "sessions", "of first step"],
      a.funnel.map(([s, n]) => [s, n, pct(n, top)]));
    html += table("Quality fail reasons", ["reason", "count"],
      Object.entries(a.reasonCount).sort((x, y) => y[1] - x[1]));
    html += table("Confirm actions", ["action", "count"], Object.entries(a.actions));
    html += table("Confirm actions by reader model", ["model", "confirm", "correct", "skipped"],
      Object.entries(a.byModel).map(([m, r]) => [esc(m), r.confirm || 0, r.correct || 0, r.skipped || 0]));
    html += table("Read -> corrected skin type", ["transition", "count"],
      Object.entries(a.matrix).sort((x, y) => y[1] - x[1]));
    html += table("Per product", ["product", "up", "down", "view", "whatsapp", "cart"],
      Object.entries(a.perProduct).map(([p, r]) => [name(p), r.up, r.down, r.view, r.wa, r.cart]));
    html += table("Languages", ["lang", "results"], Object.entries(a.langs));
    tablesEl.innerHTML = html;
  }

  async function load() {
    errEl.textContent = "";
    const token = tokenInput.value.trim();
    try {
      sessionStorage.setItem("shello-dash-token", token);
    } catch (_) {}
    let names = {};
    try {
      const c = await fetch("catalog.json", { cache: "no-store" }).then((r) => r.json());
      for (const p of c.included || []) names[p.id] = p.name;
    } catch (_) {}
    catalogNames = names;
    try {
      const resp = await fetch("/api/dashboard?days=30", {
        headers: { Authorization: "Bearer " + token },
      });
      if (!resp.ok) {
        errEl.textContent = "HTTP " + resp.status;
        return;
      }
      const data = await resp.json();
      events = data.events || [];
      dash.hidden = false;
      render(events);
    } catch (e) {
      errEl.textContent = "request failed";
    }
  }

  btnLoad.addEventListener("click", load);
  if (tokenInput.value) load();

  document.getElementById("btn-csv").addEventListener("click", () => {
    const rows = [["id", "session_id", "type", "created_at", "payload"]];
    for (const e of events) {
      rows.push([e.id, e.session_id, e.type, e.created_at, JSON.stringify(e.payload)]);
    }
    const csv = rows
      .map((r) => r.map((c) => `"${String(c == null ? "" : c).replace(/"/g, '""')}"`).join(","))
      .join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = "shello-events.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  });
})();
