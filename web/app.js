// docex review desk. Behaviour is specified in design.md.
"use strict";

const LOW_CONF = 0.8;
const APPROACHES = {
  A: { name: "OCR + rules", note: "Runs locally with Tesseract and regex rules. No model key needed." },
  B: { name: "OCR + LLM", note: "Tesseract text is sent to the language model, which fills the schema." },
  C: { name: "Vision LLM", note: "The page image is sent to a vision model, which fills the schema." },
};
const DOC_TYPES = { faktur_pajak: "Faktur pajak", invoice: "Invoice", receipt: "Receipt" };
const DECISIONS = { approve: "DISETUJUI", correct: "DIKOREKSI", reject: "DITOLAK" };

// Field layout per document type, in the order the paper document uses (design.md, "The workspace").
const party = (key, title) => ({
  title, items: [
    [`${key}.name`, "Name", "Nama", "text"],
    [`${key}.npwp`, "NPWP", "Tax ID", "npwp"],
    [`${key}.nik`, "NIK", "National ID", "nik"],
    [`${key}.address`, "Address", "Alamat", "long"],
  ],
});
const LAYOUT = {
  faktur_pajak: [
    { title: "Document", items: [
      ["tax_invoice_number", "Invoice serial", "Nomor seri faktur pajak", "id"],
      ["date", "Date", "Tanggal", "date"],
    ] },
    party("seller", "Seller · Penjual"),
    party("buyer", "Buyer · Pembeli"),
    { title: "Amounts", items: [
      ["total", "Sale price", "Harga jual / penggantian", "money"],
      ["discount", "Discount", "Potongan harga", "money"],
      ["dpp", "Tax base", "DPP", "money"],
      ["ppn", "VAT", "PPN", "money"],
      ["ppnbm", "Luxury goods tax", "PPnBM", "money"],
    ] },
  ],
  invoice: [
    { title: "Document", items: [
      ["invoice_number", "Invoice number", "Nomor invoice", "text"],
      ["date", "Issue date", "Tanggal", "date"],
      ["due_date", "Due date", "Jatuh tempo", "date"],
      ["currency", "Currency", "Mata uang", "text"],
    ] },
    party("vendor", "Vendor"),
    party("customer", "Customer"),
    { title: "Amounts", items: [
      ["subtotal", "Subtotal", "", "money"],
      ["discount", "Discount", "Diskon", "money"],
      ["tax", "Tax", "PPN", "money"],
      ["total", "Total", "", "money"],
    ] },
    { title: "Payment", items: [
      ["bank_account.bank_name", "Bank", "", "text"],
      ["bank_account.account_number", "Account number", "Nomor rekening", "id"],
      ["bank_account.account_name", "Account name", "Atas nama", "text"],
    ] },
  ],
  receipt: [
    { title: "Store", items: [
      ["store_name", "Store", "Nama toko", "text"],
      ["store_address", "Address", "Alamat", "long"],
      ["date", "Date", "Tanggal", "date"],
      ["time", "Time", "Jam", "text"],
      ["payment_method", "Payment", "Pembayaran", "text"],
    ] },
    { title: "Amounts", items: [
      ["subtotal", "Subtotal", "", "money"],
      ["discount", "Discount", "Diskon", "money"],
      ["tax", "Tax", "Pajak / PB1", "money"],
      ["service_charge", "Service charge", "Servis", "money"],
      ["total", "Total", "", "money"],
    ] },
  ],
};
const ITEM_COLS = [
  ["description", "Description", "text"],
  ["quantity", "Qty", "num"],
  ["unit_price", "Unit price", "money"],
  ["discount", "Discount", "money"],
  ["total", "Total", "money"],
];

const state = { health: null, approach: null, queue: [], doc: null, initial: {}, polling: null };
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------------------------------------------------------------- formatting
const idNum = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 });
const idr = (v) => (v == null ? "–" : `Rp ${idNum.format(v)}`);
function fmtValue(kind, v) {
  if (v == null || v === "") return "";
  if (kind === "money" || kind === "num") return idNum.format(Number(v));
  if (kind === "npwp") {
    const d = String(v);
    if (d.length === 15) return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}.${d.slice(8, 9)}-${d.slice(9, 12)}.${d.slice(12)}`;
    if (d.length === 16) return d.replace(/(\d{4})(?=\d)/g, "$1 ");
  }
  return String(v);
}
function longDate(iso) {
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso || "") ? new Date(`${iso}T00:00:00`) : null;
  return d && !isNaN(d) ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
}
function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
const confLevel = (c) => (c >= 0.95 ? 4 : c >= 0.8 ? 3 : c >= 0.5 ? 2 : 1);
function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function setPath(obj, path, value) {
  const parts = path.split(".");
  let o = obj;
  for (const p of parts.slice(0, -1)) o = o[p] ??= {};
  o[parts.at(-1)] = value;
}

// ---------------------------------------------------------------- api
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, opts);
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) throw new Error(errorText(r.status, data));
  return data;
}
function errorText(status, data) {
  const detail = typeof data?.detail === "string" ? data.detail : data?.error;
  if (status === 413) return `${detail || "File too large"}. Try a smaller scan or a single page.`;
  if (status === 415) return "That file type is not supported. Use a PDF, PNG, JPG or WEBP file.";
  if (status === 429) return "Too many requests from this address. Wait a minute and try again.";
  if (status === 400 && detail?.includes("not available")) return `${detail}. Try approach A, which runs without a model key.`;
  return detail ? `${detail} (HTTP ${status})` : `The server returned HTTP ${status}.`;
}
let toastTimer;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast${isErr ? " err" : ""}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isErr ? 7000 : 3500);
}

// ---------------------------------------------------------------- router
function route() {
  const h = location.hash.slice(1) || (state.queue.length ? "queue" : "extract");
  const view = h.startsWith("doc-") ? "doc" : ["extract", "queue", "metrics"].includes(h) ? h : "extract";
  for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== `view-${view}`;
  for (const a of document.querySelectorAll(".nav a")) {
    if (a.dataset.view === view || (view === "doc" && a.dataset.view === "queue" && state.fromQueue)) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (view === "queue") loadQueue();
  if (view === "metrics") loadMetrics();
  if (view === "doc") openDoc(h.slice(4));
  else stopPolling();
}

// ---------------------------------------------------------------- try a document
function renderApproachPicker() {
  const h = state.health;
  const box = $("#approach-picker");
  box.querySelectorAll("label").forEach((l) => l.remove());
  for (const [code, a] of Object.entries(APPROACHES)) {
    const ok = h.available_approaches.includes(code);
    const model = h.models?.[code];
    const lab = document.createElement("label");
    lab.title = ok ? `${a.note}${model ? ` Model: ${model}.` : ""}` : `Not available here: no key is set for ${model}.`;
    lab.innerHTML = `<input type="radio" name="approach" id="approach-${code}" value="${code}" ${ok ? "" : "disabled"}
      ${state.approach === code ? "checked" : ""}><span><b>${code}</b>${a.name}</span>`;
    box.append(lab);
  }
  box.onchange = (e) => { state.approach = e.target.value; renderApproachNote(); };
  renderApproachNote();
}
function renderApproachNote() {
  const h = state.health;
  const a = state.approach;
  const missing = Object.keys(APPROACHES).filter((c) => !h.available_approaches.includes(c));
  let note = APPROACHES[a].note;
  if (h.models?.[a] && a !== "A") note += ` Model: ${h.models[a]}.`;
  if (missing.length) note += ` ${missing.join(" and ")} ${missing.length > 1 ? "are" : "is"} off on this deployment because no model key is set.`;
  $("#approach-note").textContent = note;
}
async function loadSamples() {
  const box = $("#samples");
  let samples = [];
  try { samples = await api("GET", "/samples"); } catch (e) { box.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  if (!samples.length) { box.innerHTML = `<p class="empty">No samples are bundled with this deployment.</p>`; return; }
  const levels = ["clean", "light", "medium", "heavy"];
  box.innerHTML = samples.map((s) => {
    const [type, quality] = s.name.split("-");
    const q = levels.indexOf(quality) + 1;
    return `<button class="sample" data-name="${esc(s.name)}" title="${esc(s.label)}">
      <span class="thumb"><img src="/samples/${encodeURIComponent(s.name)}/image" alt="" loading="lazy"></span>
      <span class="meta"><span class="type">${esc(DOC_TYPES[type] || type)}</span>
      <span class="quality"><span aria-hidden="true">${[1, 2, 3, 4].map((i) => `<span class="qbar${i <= q ? " on" : ""}"></span>`).join("")}</span>
      ${esc(quality)} scan</span></span></button>`;
  }).join("");
  box.onclick = (e) => {
    const b = e.target.closest(".sample");
    if (b) submit(() => api("POST", `/samples/${encodeURIComponent(b.dataset.name)}?approach=${state.approach}`), b.title);
  };
}
function setupUpload() {
  const drop = $("#drop");
  const input = $("#file-input");
  const send = (file) => {
    if (!file) return;
    const max = state.health.max_upload_mb;
    if (file.size > max * 1024 * 1024) { toast(`This file is ${(file.size / 1048576).toFixed(1)} MB. The limit is ${max} MB.`, true); return; }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("approach", state.approach);
    submit(() => api("POST", "/documents", fd), file.name);
  };
  input.onchange = () => { send(input.files[0]); input.value = ""; };
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); send(e.dataTransfer.files[0]); };
}
async function submit(start, label) {
  const box = $("#progress");
  box.hidden = false;
  box.className = "progress";
  const t0 = Date.now();
  const paint = (status) => {
    box.innerHTML = `<div class="head">${esc(label)}<span>${((Date.now() - t0) / 1000).toFixed(0)} s</span></div>
      <ul class="stages"><li class="${status === "queued" ? "active" : ""}">Waiting for a worker</li>
      <li class="${status === "processing" ? "active" : ""}">Reading, extracting and validating with approach ${state.approach}</li></ul>`;
  };
  paint("queued");
  let job;
  try { job = await start(); } catch (e) { box.className = "progress failed"; box.textContent = e.message; return; }
  for (;;) {
    await new Promise((r) => setTimeout(r, 900));
    let d;
    try { d = await api("GET", `/documents/${job.job_id}`); } catch (e) { continue; }
    if (d.status === "done") { box.hidden = true; state.fromQueue = false; location.hash = `doc-${d.id}`; refreshQueueCount(); return; }
    if (d.status === "failed") { box.className = "progress failed"; box.textContent = `Processing failed: ${d.error || "unknown error"}`; return; }
    paint(d.status);
  }
}

// ---------------------------------------------------------------- queue
async function refreshQueueCount() {
  try { state.queue = (await api("GET", "/review/queue?limit=200")).sort((a, b) => a.score - b.score); } catch { return; }
  const c = $("#queue-count");
  c.textContent = state.queue.length;
  c.hidden = !state.queue.length;
}
function scoreBar(score, thr) {
  const pass = score >= thr;
  return `<span class="scorebar" title="score ${score.toFixed(2)}, threshold ${thr.toFixed(2)}"><span class="track">
    <span class="fill${pass ? " pass" : ""}" style="width:${Math.max(2, score * 100)}%"></span>
    <span class="thr" style="left:${thr * 100}%"></span></span>${score.toFixed(2)}</span>`;
}
async function loadQueue() {
  await refreshQueueCount();
  const body = $("#queue-body");
  if (!state.queue.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">Nothing to review. Documents scoring below the threshold appear here.</td></tr>`;
    return;
  }
  body.innerHTML = state.queue.map((q) => `<tr tabindex="0" data-id="${esc(q.id)}">
    <td class="file">${esc(q.filename)}</td><td>${esc(DOC_TYPES[q.doc_type] || q.doc_type)}</td>
    <td>${scoreBar(q.score, thresholdFor(q.approach))}</td><td class="reason">${esc(humanReason(q.route_reason, q.doc_type))}</td>
    <td class="age">${ago(q.created_at)}</td></tr>`).join("");
  const open = (tr) => { if (tr) { state.fromQueue = true; location.hash = `doc-${tr.dataset.id}`; } };
  body.onclick = (e) => open(e.target.closest("tr[data-id]"));
  body.onkeydown = (e) => { if (e.key === "Enter") open(e.target.closest("tr[data-id]")); };
}
// "low confidence on 'seller.npwp' (0.58)" -> "Low confidence on seller NPWP (0.58)"
function humanReason(reason, type) {
  const labels = {};
  for (const g of LAYOUT[type] || []) for (const [p, en] of g.items) labels[p] = `${p.includes(".") ? `${p.split(".")[0].replace("_", " ")} ` : ""}${en}`;
  labels.line_items = "line items";
  const s = String(reason || "").replace(/'([^']+)'/g, (_, p) => labels[p] || p);
  return s.charAt(0).toUpperCase() + s.slice(1);
}
const thresholdFor = (a) => state.health?.thresholds?.[a] ?? 0.9;

// ---------------------------------------------------------------- workspace
function stopPolling() { clearTimeout(state.polling); state.polling = null; }
async function openDoc(id) {
  stopPolling();
  const view = $("#view-doc");
  let d;
  try { d = await api("GET", `/documents/${id}`); } catch (e) {
    view.innerHTML = `<div class="view"><p class="empty">${esc(e.message)}. <a href="#queue">Back to the queue</a>.</p></div>`;
    return;
  }
  if (d.status === "queued" || d.status === "processing") {
    view.innerHTML = `<div class="view"><p class="empty">This document is still being processed.</p></div>`;
    state.polling = setTimeout(() => openDoc(id), 1000);
    return;
  }
  state.doc = d;
  renderDoc(d);
}

// After a correction, show what the reviewer stored instead of the model output.
function fieldRow(path, en, idLabel, kind, ex, critical, corrected) {
  const node = corrected ? { value: getPath(corrected, path) ?? null, confidence: 1 } : getPath(ex, path) || {};
  const value = node.value ?? null;
  const conf = value == null ? 0 : Number(node.confidence ?? 0);
  const shown = fmtValue(kind, value);
  const id = `f-${path.replace(/\./g, "-")}`;
  state.initial[path] = shown;
  const low = !corrected && (value == null || conf < LOW_CONF);
  const lvl = value == null ? 0 : confLevel(conf);
  const adorn = kind === "money" ? `<span class="adorn">Rp</span>` : "";
  const ph = value == null ? "not found" : "";
  const input = kind === "long"
    ? `<textarea id="${id}" data-path="${path}" rows="2" placeholder="${ph}">${esc(shown)}</textarea>`
    : `<input id="${id}" class="${["text", "long"].includes(kind) ? "" : "data"}" data-path="${path}" data-kind="${kind}" value="${esc(shown)}" placeholder="${ph}"
        ${kind === "money" || kind === "num" ? 'inputmode="decimal"' : ""} autocomplete="off" spellcheck="false">`;
  const hint = kind === "date" && value ? `<span class="hint">${longDate(value)}</span>` : "";
  const confText = corrected ? "value set by the reviewer" : value == null ? "not found" : `${low ? "low " : ""}confidence ${conf.toFixed(2)}`;
  const confCell = corrected ? `<div class="conf reviewed" title="${confText}"><span class="cv">reviewed</span></div>`
    : `<div class="conf c${lvl}" title="${confText}"><span class="meter" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
      <span class="cv" aria-hidden="true">${value == null ? "–" : conf.toFixed(2)}</span><span class="sr">${confText}</span></div>`;
  return `<div class="field${low ? " low" : ""}" data-field="${path}">
    <label for="${id}">${esc(en)}${idLabel ? `<small>${esc(idLabel)}</small>` : ""}${critical ? `<span class="tag">critical</span>` : ""}</label>
    <div class="fv">${adorn}${input}</div>
    ${confCell}
    ${hint}</div>`;
}

function itemsTable(items) {
  const rows = items.map((it, i) => itemRow(it, i)).join("");
  return `<section class="group" aria-labelledby="items-title"><h3 class="group-title" id="items-title">Line items · Rincian barang</h3>
    <div class="items-wrap"><table class="items"><thead><tr><th class="num">#</th>
    ${ITEM_COLS.map(([, l, k]) => `<th${k !== "text" ? ' class="num"' : ""}>${l}</th>`).join("")}<th><span class="sr">Remove</span></th></tr></thead>
    <tbody id="items-body">${rows}</tbody></table></div>
    <div class="items-foot"><button type="button" id="add-item">Add line</button>
    <span>Sum of line totals <b id="items-sum"></b></span></div></section>`;
}
function itemRow(it, i) {
  const cells = ITEM_COLS.map(([key, label, kind]) => {
    const node = it?.[key] || {};
    const v = node.value ?? null;
    const shown = fmtValue(kind, v);
    const path = `line_items.${i}.${key}`;
    if (it) state.initial[path] = shown;
    const low = it && (v == null ? key === "total" : node.confidence < LOW_CONF);
    return `<td class="${low ? "low" : ""}"><input class="${kind === "text" ? "desc" : "num"}" data-path="${path}" data-kind="${kind}"
      value="${esc(shown)}" aria-label="${label}, line ${i + 1}" autocomplete="off" spellcheck="false"
      ${kind !== "text" ? 'inputmode="decimal"' : ""} title="${it && v != null ? `confidence ${Number(node.confidence).toFixed(2)}` : ""}"></td>`;
  }).join("");
  return `<tr data-row="${i}"><td class="idx">${i + 1}</td>${cells}<td class="rowtools"><button type="button" class="danger" data-remove="${i}" aria-label="Remove line ${i + 1}">Remove</button></td></tr>`;
}

function renderDoc(d) {
  const view = $("#view-doc");
  state.initial = {};
  state.removed = 0;
  const ex = d.extraction || {};
  const type = d.doc_type;
  const critical = new Set(state.health?.critical_fields?.[type] || []);
  const thr = thresholdFor(d.approach);
  const auto = d.route === "auto_approved";
  const decided = d.review?.action;
  const corrected = d.review?.corrected_values || null;
  const items = corrected
    ? (corrected.line_items || []).map((it) => Object.fromEntries(Object.entries(it).map(([k, v]) => [k, typeof v === "boolean" ? v : { value: v, confidence: 1 }])))
    : ex.line_items || [];
  const groups = (LAYOUT[type] || []).map((g) => `<section class="group"><h3 class="group-title">${esc(g.title)}</h3>
      ${g.items.map(([p, en, idl, kind]) => fieldRow(p, en, idl, kind, ex, critical.has(p), corrected)).join("")}</section>`).join("");
  const issues = (d.issues || []).map((i) => `<li class="${i.severity}"><b>${esc(i.rule)}</b>${esc(i.message)}
      ${i.fields?.length ? ` <a data-goto="${esc(i.fields[0])}">Go to field</a>` : ""}</li>`).join("");
  const u = d.usage || {};
  const qi = state.queue.findIndex((q) => q.id === d.id);

  view.innerHTML = `
    <div class="doc-head">
      <button type="button" class="back" id="back">← ${state.fromQueue ? "Queue" : "Back"}</button>
      <span class="fname">${esc(d.filename)}</span>
      <span class="chip">${esc(DOC_TYPES[type] || type || "unknown")}</span>
      <span class="chip ${auto ? "ok" : "warn"}">${auto ? "Auto-approved" : "Needs review"}</span>
      ${d.repaired ? `<span class="chip">Repaired once</span>` : ""}
      ${ex.document_inconsistent ? `<span class="chip err">Printed totals inconsistent</span>` : ""}
      <span class="facts"><span>approach <b>${esc(d.approach)}</b></span>
        <span>score <b>${(d.score ?? 0).toFixed(2)}</b> / threshold <b>${thr.toFixed(2)}</b></span>
        <span>latency <b>${((d.latency_ms || 0) / 1000).toFixed(1)} s</b></span></span>
    </div>
    <div class="split">
      <div class="scan" id="scan">
        <div class="scan-tools"><button type="button" id="zoom" aria-pressed="false">Zoom in</button></div>
        ${decided ? `<span class="stamp${decided === "reject" ? " rejected" : ""}">${DECISIONS[decided]}</span>` : ""}
        <img src="/documents/${encodeURIComponent(d.id)}/preview" alt="Scan of ${esc(d.filename)}, ${esc(DOC_TYPES[type] || "document")}">
      </div>
      <div class="fields" id="fields">
        <div class="verdict">
          <p class="reason">${esc(humanReason(d.route_reason, type))}.${d.status === "failed" ? ` Processing failed: ${esc(d.error)}` : ""}</p>
          ${issues ? `<ul class="issues">${issues}</ul>` : ""}
        </div>
        ${groups}
        ${type ? itemsTable(items) : ""}
        <details class="tech"><summary>Technical details</summary><div class="body">
          <dl class="kv">
            <dt>Model</dt><dd>${esc(u.model || "none (local OCR + rules)")}</dd>
            <dt>Tokens</dt><dd>${u.input_tokens || 0} in · ${u.output_tokens || 0} out · ${u.llm_calls || 0} call(s)</dd>
            <dt>Cost</dt><dd>$${(u.cost_usd || 0).toFixed(5)} · Rp ${idNum.format(u.cost_idr || 0)}</dd>
            ${Object.entries(d.versions || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}
            ${(d.trace || []).map((s) => `<dt>${esc(s.name)}</dt><dd>${s.duration_ms} ms</dd>`).join("")}
          </dl>
          <pre class="raw">${esc(JSON.stringify(d.values, null, 2))}</pre>
        </div></details>
      </div>
    </div>
    <div class="actionbar">
      <label class="reviewer" for="reviewer">Reviewer <input id="reviewer" value="${esc(localGet("reviewer") || "demo")}" autocomplete="off"></label>
      ${qi >= 0 ? `<span class="navhint">${qi + 1} of ${state.queue.length} in queue · <kbd>j</kbd> <kbd>k</kbd> to move</span>` : ""}
      <span class="result" id="result">${decided ? `Decision recorded by ${esc(d.review.reviewer)}${d.review.changed_fields?.length ? `: ${d.review.changed_fields.length} field(s) corrected` : ""}.` : ""}</span>
      <span class="spacer"></span>
      <button type="button" class="danger" id="reject">Reject<span class="k">R</span></button>
      <button type="button" id="save" disabled>Save correction<span class="k">S</span></button>
      <button type="button" class="primary" id="approve">Approve<span class="k">A</span></button>
    </div>`;

  $("#back").onclick = () => { location.hash = state.fromQueue ? "queue" : "extract"; };
  $("#zoom").onclick = (e) => {
    const z = $("#scan").classList.toggle("zoomed");
    e.target.textContent = z ? "Fit width" : "Zoom in";
    e.target.setAttribute("aria-pressed", z);
  };
  $("#fields").addEventListener("input", onEdit);
  $("#fields").addEventListener("click", (e) => {
    const go = e.target.closest("[data-goto]");
    if (go) focusField(go.dataset.goto);
    const rm = e.target.closest("[data-remove]");
    if (rm) { rm.closest("tr").remove(); state.removed++; renumberItems(); updateDirty(); }
    if (e.target.id === "add-item") {
      const body = $("#items-body");
      body.insertAdjacentHTML("beforeend", itemRow(null, body.rows.length));
      body.rows[body.rows.length - 1].querySelector("input").focus();
      updateDirty();
    }
  });
  $("#reviewer").onchange = (e) => localSet("reviewer", e.target.value);
  $("#approve").onclick = () => decide("approve");
  $("#reject").onclick = () => decide("reject");
  $("#save").onclick = () => decide("correct");
  updateSum();
  if (!auto && !decided) {
    const weakest = (d.route_reason || "").match(/'([^']+)'/)?.[1];
    if (weakest && weakest !== "line_items") focusField(weakest, false, false);
  }
}

function renumberItems() {
  [...$("#items-body").rows].forEach((tr, i) => {
    tr.dataset.row = i;
    tr.cells[0].textContent = i + 1;
    tr.querySelectorAll("input").forEach((inp) => { inp.dataset.path = inp.dataset.path.replace(/line_items\.\d+/, `line_items.${i}`); });
  });
}
function focusField(path, smooth = true, focus = true) {
  const row = document.querySelector(`[data-field="${CSS.escape(path)}"]`);
  if (!row) return;
  const pane = $("#fields");
  if (getComputedStyle(pane).overflowY === "auto") {
    pane.scrollTo({ top: row.offsetTop - pane.clientHeight / 3, behavior: smooth ? "smooth" : "auto" });
  } else {
    row.scrollIntoView({ block: "center", behavior: smooth ? "smooth" : "auto" });
  }
  document.querySelectorAll(".field.target").forEach((r) => r.classList.remove("target"));
  row.classList.add("target");
  // Opening a document only points at the weakest field; focusing it would swallow the a/s/r shortcuts.
  if (focus) row.querySelector("input, textarea")?.focus({ preventScroll: true });
}
function onEdit(e) {
  const el = e.target;
  if (!el.dataset?.path) return;
  const changed = el.value.trim() !== (state.initial[el.dataset.path] ?? "");
  (el.closest(".field") || el.closest("td")).classList.toggle("edited", changed);
  updateDirty();
  if (el.closest("#items-body")) updateSum();
}
function editedCount() {
  const fields = document.querySelectorAll("#fields .edited").length;
  const added = [...document.querySelectorAll("#items-body tr")].filter((tr) => !(`line_items.${tr.dataset.row}.total` in state.initial)).length;
  return fields + state.removed + added;
}
function updateDirty() {
  const n = editedCount();
  const btn = $("#save");
  btn.disabled = n === 0;
  btn.firstChild.textContent = n ? `Save ${n} correction${n > 1 ? "s" : ""}` : "Save correction";
}
function parseIdNumber(s) {
  const t = String(s).replace(/[^\d,.-]/g, "");
  if (!t) return null;
  const n = Number(t.replace(/\./g, "").replace(",", "."));
  return isNaN(n) ? null : n;
}
function updateSum() {
  const totals = [...document.querySelectorAll('#items-body input[data-path$=".total"]')].map((i) => parseIdNumber(i.value) || 0);
  const el = $("#items-sum");
  if (el) el.textContent = idr(totals.reduce((a, b) => a + b, 0));
}
function collectValues() {
  const values = {};
  for (const el of document.querySelectorAll("#fields .field [data-path]")) setPath(values, el.dataset.path, el.value.trim() || null);
  values.line_items = [...document.querySelectorAll("#items-body tr")].map((tr) => {
    const row = { is_adjustment: false };
    tr.querySelectorAll("input").forEach((inp) => { row[inp.dataset.path.split(".").pop()] = inp.value.trim() || null; });
    const prev = state.doc.extraction?.line_items?.[Number(tr.dataset.row)];
    if (prev?.is_adjustment) row.is_adjustment = true;
    return row;
  });
  values.document_inconsistent = Boolean(state.doc.extraction?.document_inconsistent);
  return values;
}
async function decide(action) {
  const d = state.doc;
  if (!d) return;
  if (action === "correct" && $("#save").disabled) return;
  const body = { action, reviewer: $("#reviewer").value.trim() || "anonymous" };
  if (action === "correct") body.values = collectValues();
  for (const b of document.querySelectorAll(".actionbar button")) b.disabled = true;
  try {
    const r = await api("POST", `/review/${encodeURIComponent(d.id)}`, body);
    const msg = action === "correct" ? `Saved. Changed: ${r.changed_fields.join(", ") || "nothing after normalisation"}.`
      : action === "approve" ? "Approved." : "Rejected.";
    toast(msg);
    await refreshQueueCount();
    const next = state.fromQueue ? state.queue[0] : null;
    if (next) { location.hash = `doc-${next.id}`; return; }
    openDoc(d.id);
  } catch (e) {
    toast(e.message, true);
    for (const b of document.querySelectorAll(".actionbar button")) b.disabled = false;
    updateDirty();
  }
}

// ---------------------------------------------------------------- metrics
async function loadMetrics() {
  const box = $("#metrics");
  let m;
  try { m = await api("GET", "/metrics"); } catch (e) { box.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  const pct = (v) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
  const rv = m.reviews || {};
  const stat = (v, k) => `<div class="stat"><span class="v">${v}</span><span class="k">${k}</span></div>`;
  const off = m.offline_eval?.approaches || [];
  box.innerHTML = `
    <section class="metrics-sec"><h2>This deployment</h2>
      <p class="src">Live counts from documents processed by this server since it started. Demo storage resets on restart.</p>
      <div class="stats">
        ${stat(m.documents, "documents processed")}
        ${stat(pct(m.auto_approve_rate), "auto-approved")}
        ${stat(`${rv.approve || 0} / ${rv.correct || 0} / ${rv.reject || 0}`, "approved / corrected / rejected")}
        ${stat(m.latency_ms_p50 == null ? "–" : `${(m.latency_ms_p50 / 1000).toFixed(1)} s`, "latency p50")}
        ${stat(m.latency_ms_p95 == null ? "–" : `${(m.latency_ms_p95 / 1000).toFixed(1)} s`, "latency p95")}
        ${stat(`$${(m.cost_usd_total || 0).toFixed(4)}`, "API and compute cost")}
      </div></section>
    <section class="metrics-sec"><h2>Offline evaluation</h2>
      <p class="src">Synthetic ${esc(m.offline_eval?.split || "dev")} set, from <code>eval/reports/summary.json</code>. Field accuracy counts every labelled field; document accuracy needs every critical field right.</p>
      ${off.length ? `<div class="table-wrap"><table><thead><tr><th>Approach</th><th class="num">Docs</th><th class="num">Field accuracy</th>
        <th class="num">Document accuracy</th><th class="num">Line items F1</th><th class="num">Auto-approved at threshold</th>
        <th class="num">Cost / 1,000 docs</th><th class="num">Latency p50</th></tr></thead><tbody>
        ${off.map((a) => `<tr><td>${esc(a.approach)}</td><td class="num">${a.n}</td><td class="num">${pct(a.field_accuracy)}</td>
          <td class="num">${pct(a.doc_accuracy)}</td><td class="num">${a.items_f1.toFixed(3)}</td>
          <td class="num">${a.stp_at_threshold == null ? "none safe" : `${pct(a.stp_at_threshold)} at ${a.recommended_threshold}`}</td>
          <td class="num">$${a.cost_usd_per_1000.toFixed(2)}</td><td class="num">${a.latency_p50_s.toFixed(1)} s</td></tr>`).join("")}
        </tbody></table></div>` : `<p class="empty">No offline evaluation is bundled with this deployment.</p>`}
    </section>`;
}

// ---------------------------------------------------------------- keyboard
document.addEventListener("keydown", (e) => {
  const typing = e.target.closest?.("input, textarea, select");
  if (e.key === "Escape" && typing) { e.target.blur(); return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey || $("#view-doc").hidden || !state.doc) return;
  const k = e.key.toLowerCase();
  if (k === "a") decide("approve");
  else if (k === "r") decide("reject");
  else if (k === "s") decide("correct");
  else if (k === "/") {
    e.preventDefault();
    document.querySelector("#fields .field.target input, #fields .field.target textarea, #fields .field.low input, #fields .field.low textarea")?.focus();
  }
  else if (k === "j" || k === "k") {
    const i = state.queue.findIndex((q) => q.id === state.doc.id);
    const next = state.queue[i + (k === "j" ? 1 : -1)];
    if (next) { state.fromQueue = true; location.hash = `doc-${next.id}`; }
  }
});

function localGet(k) { try { return localStorage.getItem(`docex.${k}`); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(`docex.${k}`, v); } catch { /* storage unavailable */ } }

// ---------------------------------------------------------------- boot
(async function boot() {
  try { state.health = await api("GET", "/health"); } catch (e) {
    document.body.insertAdjacentHTML("beforeend", `<p class="empty">The API is not reachable: ${esc(e.message)}</p>`);
    return;
  }
  const h = state.health;
  state.approach = h.available_approaches.includes(h.default_approach) ? h.default_approach : h.available_approaches[0];
  $("#deploy-info").innerHTML = `Default approach <b>${esc(h.default_approach)}</b>`;
  $("#max-mb").textContent = h.max_upload_mb;
  renderApproachPicker();
  setupUpload();
  loadSamples();
  await refreshQueueCount();
  window.addEventListener("hashchange", route);
  route();
})();
