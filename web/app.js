// docex review desk. Behaviour is specified in design.md.
"use strict";

const LOW_CONF = 0.8;
const APPROACHES = {
  A: { name: "OCR + aturan", note: "Dibaca di server sendiri dengan OCR Tesseract dan aturan teks. Gratis, tidak butuh kunci API." },
  B: { name: "OCR + AI", note: "Teks hasil OCR dikirim ke model AI, lalu AI mengisi kolom-kolomnya." },
  C: { name: "AI baca gambar", note: "Gambar dokumen langsung dibaca oleh model AI yang bisa melihat gambar." },
};
const DOC_TYPES = { faktur_pajak: "Faktur pajak", invoice: "Invoice", receipt: "Struk" };
const DECISIONS = { approve: "DISETUJUI", correct: "DIKOREKSI", reject: "DITOLAK" };
const DECIDED = { approve: "Disetujui", correct: "Dikoreksi", reject: "Ditolak" };
const QUALITY = { clean: "bersih", light: "sedikit kotor", medium: "cukup kotor", heavy: "sangat kotor" };

// Field layout per document type, in the order the paper document uses (design.md, "The workspace").
// Each item: [path, label, short note, kind]. Labels are plain Indonesian; the note keeps the official term.
const PARTY_NAMES = { seller: "penjual", buyer: "pembeli", vendor: "vendor", customer: "pelanggan" };
const party = (key, title) => ({
  title, items: [
    [`${key}.name`, "Nama", "", "text"],
    [`${key}.npwp`, "NPWP", "Nomor pokok wajib pajak", "npwp"],
    [`${key}.nik`, "NIK", "Nomor KTP", "nik"],
    [`${key}.address`, "Alamat", "", "long"],
  ],
});
const LAYOUT = {
  faktur_pajak: [
    { title: "Dokumen", items: [
      ["tax_invoice_number", "Nomor seri faktur", "", "id"],
      ["date", "Tanggal", "", "date"],
    ] },
    party("seller", "Penjual"),
    party("buyer", "Pembeli"),
    { title: "Jumlah uang", items: [
      ["total", "Harga jual", "Harga jual / penggantian", "money"],
      ["discount", "Potongan harga", "", "money"],
      ["dpp", "Dasar pajak", "DPP", "money"],
      ["ppn", "Pajak pertambahan nilai", "PPN", "money"],
      ["ppnbm", "Pajak barang mewah", "PPnBM", "money"],
    ] },
  ],
  invoice: [
    { title: "Dokumen", items: [
      ["invoice_number", "Nomor invoice", "", "text"],
      ["date", "Tanggal invoice", "", "date"],
      ["due_date", "Jatuh tempo", "", "date"],
      ["currency", "Mata uang", "", "text"],
    ] },
    party("vendor", "Vendor (penagih)"),
    party("customer", "Pelanggan (ditagih)"),
    { title: "Jumlah uang", items: [
      ["subtotal", "Subtotal", "", "money"],
      ["discount", "Diskon", "", "money"],
      ["tax", "Pajak", "PPN", "money"],
      ["total", "Total", "", "money"],
    ] },
    { title: "Pembayaran", items: [
      ["bank_account.bank_name", "Nama bank", "", "text"],
      ["bank_account.account_number", "Nomor rekening", "", "id"],
      ["bank_account.account_name", "Atas nama", "", "text"],
    ] },
  ],
  receipt: [
    { title: "Toko", items: [
      ["store_name", "Nama toko", "", "text"],
      ["store_address", "Alamat", "", "long"],
      ["date", "Tanggal", "", "date"],
      ["time", "Jam", "", "text"],
      ["payment_method", "Cara bayar", "", "text"],
    ] },
    { title: "Jumlah uang", items: [
      ["subtotal", "Subtotal", "", "money"],
      ["discount", "Diskon", "", "money"],
      ["tax", "Pajak", "PPN / PB1", "money"],
      ["service_charge", "Biaya layanan", "Servis", "money"],
      ["total", "Total", "", "money"],
    ] },
  ],
};
const ITEM_COLS = [
  ["description", "Nama barang", "text"],
  ["quantity", "Jumlah", "num"],
  ["unit_price", "Harga satuan", "money"],
  ["discount", "Diskon", "money"],
  ["total", "Total", "money"],
];
// Validation rules explained in plain words, with the numbers from this document where the check used them.
// The server's exact message stays in the tooltip. Each rule gets (field label, values, issue, doc type).
const RULES = {
  required_fields: (f) => `${f || "Kolom penting"} tidak ditemukan di dokumen. Isi dari scan kalau ada.`,
  line_items_sum: (f, v, i, t) => {
    const items = v.line_items || [];
    if (items.some((it) => it.total == null)) return "Ada baris barang yang belum punya total. Isi totalnya dari scan.";
    const target = i.fields?.[1];
    const sum = items.reduce((a, it) => a + Number(it.total || 0), 0);
    if (!target || v[target] == null) return "Jumlah semua baris barang tidak sama dengan subtotal atau total di dokumen.";
    return `Jumlah rincian barang ${money(sum, v)}, tapi ${lowerFirst(fieldLabel(target, t))} di dokumen ${money(v[target], v)}.`;
  },
  npwp_format: (f, v, i) => `${f || "NPWP"} terbaca ${digits(v, i)} digit, padahal NPWP harus 15 atau 16 digit.`,
  nik_format: (f, v, i) => `${f || "NIK"} terbaca ${digits(v, i)} digit, padahal NIK harus 16 digit.`,
  faktur_serial_format: (f, v, i) => `Nomor seri faktur terbaca ${digits(v, i)} digit, padahal seharusnya 16 atau 17 digit.`,
  date_plausible: (f, v, i) => {
    const m = String(i.message || "");
    const when = longDate(v[i.fields?.[0]]) || v[i.fields?.[0]];
    if (m.includes("in the future")) return `${f || "Tanggal"} (${when}) ada di masa depan. Cocokkan dengan scan.`;
    if (m.includes("older than")) return `${f || "Tanggal"} (${when}) terlalu lama. Cocokkan dengan scan.`;
    return `${f || "Tanggal"} tidak terbaca sebagai tanggal yang valid. Tulis dengan format TTTT-BB-HH.`;
  },
  due_after_issue: (f, v) => `Jatuh tempo (${longDate(v.due_date) || v.due_date}) lebih awal dari tanggal invoice (${longDate(v.date) || v.date}).`,
  totals_arithmetic: (f, v, i, t) => {
    const n = (k) => Number(v[k] || 0);
    if (t === "faktur_pajak" && v.total != null && v.dpp != null)
      return `Dasar pajak (DPP) ${money(v.dpp, v)} tidak cocok dengan harga jual dikurangi potongan (${money(n("total") - n("discount"), v)}), baik penuh maupun 11/12.`;
    if (t === "invoice" && v.subtotal != null && v.total != null)
      return `Subtotal dikurangi diskon ditambah pajak hasilnya ${money(n("subtotal") - n("discount") + n("tax"), v)}, tapi total di dokumen ${money(v.total, v)}.`;
    if (t === "receipt" && v.subtotal != null && v.total != null)
      return `Subtotal dikurangi diskon ditambah pajak dan biaya layanan hasilnya ${money(n("subtotal") - n("discount") + n("tax") + n("service_charge"), v)}, tapi total di dokumen ${money(v.total, v)}.`;
    return "Hitungan tidak cocok: subtotal, diskon, pajak, dan total tidak saling sesuai.";
  },
  ppn_rate: (f, v, i, t) => {
    if (t === "faktur_pajak" && v.ppn != null && v.dpp != null)
      return `PPN ${money(v.ppn, v)} tidak sama dengan DPP ${money(v.dpp, v)} dikali tarif PPN yang berlaku pada tanggal faktur.`;
    if (v.tax != null) return `Pajak ${money(v.tax, v)} tidak cocok dengan tarif PPN mana pun. Cocokkan dengan scan.`;
    return "Nilai PPN tidak sesuai dengan tarif PPN yang berlaku.";
  },
};
// Rupiah unless the invoice says another currency; two decimals only when the amount has cents.
function money(x, v) {
  const n = Number(x);
  const s = Number.isInteger(n) ? idNum.format(n) : num2(n);
  const cur = v?.currency && v.currency !== "IDR" ? v.currency : "Rp";
  return `${cur} ${s}`;
}
const digits = (v, i) => String(getPath(v, i.fields?.[0] || "") ?? "").replace(/\D/g, "").length;

const state = { health: null, approach: null, queue: [], doc: null, initial: {}, polling: null };
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------------------------------------------------------------- formatting
const idNum = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 });
const idr = (v) => (v == null ? "–" : `Rp ${idNum.format(v)}`);
const num2 = (v) => Number(v).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
  return d && !isNaN(d) ? d.toLocaleDateString("id-ID", { day: "numeric", month: "short", year: "numeric" }) : "";
}
function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "baru saja";
  if (s < 3600) return `${Math.round(s / 60)} menit lalu`;
  if (s < 86400) return `${Math.round(s / 3600)} jam lalu`;
  return `${Math.round(s / 86400)} hari lalu`;
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
// Raw provider errors can carry a whole HTML page (a proxy or CDN error). Show one plain sentence instead.
function failureText(err) {
  const e = String(err || "");
  const code = Number((e.match(/error (\d{3})/) || [])[1]);
  if (/location is not supported/i.test(e)) return "Google menolak lokasi server ini (atau lokasi proxy-nya) untuk sementara. Coba lagi nanti atau pakai metode A.";
  if (/KEY is not set/i.test(e)) return "Kunci API untuk metode ini belum dipasang di server. Coba metode A.";
  if (/calls are disabled/i.test(e)) return "Metode B dan C sedang dimatikan di server ini. Coba metode A.";
  if (code === 429 || /quota/i.test(e)) return "Kuota gratis Gemini sedang habis. Tunggu beberapa menit, lalu coba lagi.";
  if (code === 401 || code === 403) return "Kunci Gemini ditolak. Periksa kunci di server.";
  if (code) return `Layanan Gemini tidak bisa dihubungi (HTTP ${code}). Ini masalah di server atau proxy, bukan di scan-mu.`;
  if (/connection error|timed? ?out/i.test(e)) return "Layanan Gemini tidak bisa dihubungi. Coba lagi sebentar lagi.";
  return "Dokumen ini gagal dibaca. Coba scan yang lebih jelas atau metode lain.";
}
function errorText(status, data) {
  const detail = typeof data?.detail === "string" ? data.detail : data?.error;
  if (status === 413) return `File terlalu besar. Batasnya ${state.health?.max_upload_mb ?? 10} MB. Coba scan yang lebih kecil atau satu halaman saja.`;
  if (status === 415) return "Jenis file ini tidak didukung. Gunakan PDF, PNG, JPG, atau WEBP.";
  if (status === 429) return "Terlalu banyak permintaan dari alamat ini. Tunggu satu menit, lalu coba lagi.";
  if (status === 400 && detail?.includes("paused")) return "Metode B dan C sedang dijeda karena kuota gratis Gemini habis atau Google menolak lokasi server. Pakai metode A dulu.";
  if (status === 400 && detail?.includes("not available")) return "Metode ini belum aktif di server ini karena kunci API belum dipasang. Coba metode A.";
  if (status === 404) return "Data tidak ditemukan. Mungkin sudah terhapus saat server dimulai ulang.";
  if (status === 422) return "Ada nilai koreksi yang formatnya tidak bisa dibaca. Periksa angka dan tanggalnya, lalu simpan lagi.";
  if (status >= 500) return `Server sedang bermasalah (HTTP ${status}). Coba lagi sebentar lagi, atau pakai metode lain.`;
  return `Permintaan gagal (HTTP ${status})${detail ? `: ${detail}` : ""}. Coba lagi.`;
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

// ---------------------------------------------------------------- sidebar and menu button
// Wide screens: the sidebar sits beside the page and the button collapses it (remembered). Narrow screens: it is a
// drawer over the page, closed by default, closed again by Esc, the dim area or choosing a page.
function initShell() {
  const shell = $("#shell"), btn = $("#menu"), side = $("#sidebar"), scrim = $("#scrim"), app = $("#app");
  const narrow = matchMedia("(max-width: 900px)");
  const links = () => [...side.querySelectorAll("a")];
  function set(open) {
    shell.dataset.nav = open ? "open" : "closed";
    btn.setAttribute("aria-expanded", String(open));
    side.inert = !open;
    scrim.hidden = !(open && narrow.matches);
    app.inert = open && narrow.matches;  // behind the drawer nothing can be tabbed to
    if (!narrow.matches) localSet("nav", open ? "open" : "closed");
  }
  const isOpen = () => shell.dataset.nav === "open";
  const reset = () => set(!narrow.matches && localGet("nav") !== "closed");
  reset();
  narrow.addEventListener("change", reset);
  btn.onclick = () => {
    set(!isOpen());
    if (isOpen() && narrow.matches) (side.querySelector('a[aria-current="page"]') || links()[0]).focus();
  };
  scrim.onclick = () => { set(false); btn.focus(); };
  side.addEventListener("click", (e) => {
    if (e.target.closest("a") && narrow.matches) { set(false); app.focus({ preventScroll: true }); }
  });
  document.addEventListener("keydown", (e) => {
    if (!narrow.matches || !isOpen()) return;
    if (e.key === "Escape") { set(false); btn.focus(); return; }
    if (e.key !== "Tab") return;
    const ring = [btn, ...links()];
    const at = ring.indexOf(document.activeElement);
    if (e.shiftKey && at <= 0) { e.preventDefault(); ring.at(-1).focus(); }
    else if (!e.shiftKey && at === ring.length - 1) { e.preventDefault(); btn.focus(); }
  });
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
  if (view === "extract" && state.health) refreshHealth();
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
    lab.title = ok ? `${a.note}${model ? ` Model: ${model}.` : ""}`
      : h.paused_approaches?.[code] ? `Dijeda sementara: kuota gratis Gemini habis atau lokasi server ditolak.`
      : `Belum aktif di sini: kunci API untuk ${model} belum dipasang.`;
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
  const paused = Object.keys(h.paused_approaches || {});
  const missing = Object.keys(APPROACHES).filter((c) => !h.available_approaches.includes(c) && !paused.includes(c));
  let note = APPROACHES[a].note;
  if (h.models?.[a] && a !== "A") note += ` Model: ${h.models[a]}.`;
  if (paused.length) {
    const mins = Math.max(1, Math.ceil(Math.max(...Object.values(h.paused_approaches)) / 60));
    note += ` Metode ${paused.join(" dan ")} dijeda karena kuota gratis Gemini habis atau Google menolak lokasi server. Muncul lagi sekitar ${mins} menit lagi.`;
  }
  if (missing.length) note += ` Metode ${missing.join(" dan ")} belum aktif di server ini karena kunci API belum dipasang.`;
  $("#approach-note").textContent = note;
}
// B and C pause themselves when Gemini's free quota runs out; re-read /health so the picker follows.
let healthTimer;
async function refreshHealth() {
  try { state.health = await api("GET", "/health"); } catch { return; }
  const h = state.health;
  if (!h.available_approaches.includes(state.approach)) {
    state.approach = h.available_approaches.includes(h.default_approach) ? h.default_approach : h.available_approaches[0];
  }
  renderApproachPicker();
  clearTimeout(healthTimer);
  const wait = Math.max(0, ...Object.values(h.paused_approaches || {}));
  if (wait) healthTimer = setTimeout(refreshHealth, (wait + 1) * 1000);
}
async function loadSamples() {
  const box = $("#samples");
  let samples = [];
  try { samples = await api("GET", "/samples"); } catch (e) { box.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  if (!samples.length) { box.innerHTML = `<p class="empty">Server ini tidak menyertakan contoh dokumen.</p>`; return; }
  const levels = ["clean", "light", "medium", "heavy"];
  box.innerHTML = samples.map((s) => {
    const [type, quality] = s.name.split("-");
    const q = levels.indexOf(quality) + 1;
    const title = `${DOC_TYPES[type] || type}, scan ${QUALITY[quality] || quality}`;
    return `<button class="sample" data-name="${esc(s.name)}" title="${esc(title)}">
      <span class="thumb"><img src="/samples/${encodeURIComponent(s.name)}/image" alt="" loading="lazy"></span>
      <span class="meta"><span class="type">${esc(DOC_TYPES[type] || type)}</span>
      <span class="quality"><span aria-hidden="true">${[1, 2, 3, 4].map((i) => `<span class="qbar${i <= q ? " on" : ""}"></span>`).join("")}</span>
      ${esc(QUALITY[quality] || quality)}</span></span></button>`;
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
    if (file.size > max * 1024 * 1024) { toast(`Ukuran file ini ${(file.size / 1048576).toLocaleString("id-ID", { maximumFractionDigits: 1 })} MB. Batasnya ${max} MB.`, true); return; }
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
    box.innerHTML = `<div class="head">${esc(label)}<span>${((Date.now() - t0) / 1000).toFixed(0)} detik</span></div>
      <ul class="stages"><li class="${status === "queued" ? "active" : ""}">Menunggu giliran diproses</li>
      <li class="${status === "processing" ? "active" : ""}">Membaca dan memeriksa dokumen dengan metode ${state.approach} (${APPROACHES[state.approach].name})</li></ul>
      <p class="stage-note">Hasilnya terbuka otomatis setelah selesai.</p>`;
  };
  paint("queued");
  let job;
  try { job = await start(); } catch (e) { box.className = "progress failed"; box.textContent = e.message; refreshHealth(); return; }
  for (;;) {
    await new Promise((r) => setTimeout(r, 900));
    let d;
    try { d = await api("GET", `/documents/${job.job_id}`); } catch (e) { continue; }
    if (d.status === "done") { box.hidden = true; state.fromQueue = false; location.hash = `doc-${d.id}`; refreshQueueCount(); return; }
    if (d.status === "failed") { box.className = "progress failed"; box.textContent = failureText(d.error); refreshHealth(); return; }
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
  return `<span class="scorebar" title="skor ${num2(score)}, batas ${num2(thr)}"><span class="track">
    <span class="fill${pass ? " pass" : ""}" style="width:${Math.max(2, score * 100)}%"></span>
    <span class="thr" style="left:${Math.min(thr, 1) * 100}%"></span></span>${num2(score)}</span>`;
}
// "Tanggal 0,62, NPWP penjual 0,58, dan 2 lainnya": up to three weak critical fields, weakest first.
function weakText(q) {
  const w = q.weak_fields || [];
  if (!w.length) return "";
  const name = (f) => `${upperFirst(fieldLabel(f.path, q.doc_type))} ${f.confidence == null ? "belum terbaca" : num2(f.confidence)}`;
  const shown = w.slice(0, 3).map(name);
  const rest = w.length - shown.length;
  if (rest > 0) return `${shown.join(", ")}, dan ${rest} lainnya`;
  if (shown.length === 3) return `${shown.slice(0, 2).join(", ")}, dan ${shown[2]}`;
  return shown.join(" dan ");
}
async function loadQueue() {
  await refreshQueueCount();
  const body = $("#queue-body");
  if (!state.queue.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">Belum ada dokumen yang perlu dicek. Dokumen dengan skor di bawah batas akan muncul di sini.
      <a href="#extract">Coba contoh dokumen</a> untuk melihatnya.</td></tr>`;
    return;
  }
  body.innerHTML = state.queue.map((q) => `<tr tabindex="0" data-id="${esc(q.id)}">
    <td class="file">${esc(q.filename)}</td><td>${esc(DOC_TYPES[q.doc_type] || q.doc_type)}</td>
    <td>${scoreBar(q.score, thresholdFor(q.approach))}</td><td class="reason">${esc(weakText(q) || humanReason(q.route_reason, q.doc_type))}</td>
    <td class="age">${ago(q.created_at)}</td></tr>`).join("");
  const open = (tr) => { if (tr) { state.fromQueue = true; location.hash = `doc-${tr.dataset.id}`; } };
  body.onclick = (e) => open(e.target.closest("tr[data-id]"));
  body.onkeydown = (e) => { if (e.key === "Enter") open(e.target.closest("tr[data-id]")); };
}
// Field path -> plain label, e.g. "seller.npwp" -> "NPWP penjual".
function fieldLabel(path, type) {
  if (path === "line_items") return "rincian barang";
  for (const g of LAYOUT[type] || []) {
    for (const [p, label] of g.items) {
      if (p !== path) continue;
      const owner = PARTY_NAMES[p.split(".")[0]];
      return owner ? `${label} ${owner}` : label;
    }
  }
  return path;
}
// Server route reasons are English and machine-shaped; say them in plain Indonesian.
// "low confidence on 'seller.npwp' (0.58)" -> "Kurang yakin dengan NPWP penjual (0,58)"
function humanReason(reason, type) {
  const s = String(reason || "");
  let m;
  if ((m = s.match(/^low confidence on '([^']+)' \(([\d.]+)\)/))) return `${upperFirst(fieldLabel(m[1], type))} terbaca dengan tingkat yakin rendah (${num2(m[2])})`;
  if ((m = s.match(/^(\d+) validation error/))) return `${m[1]} pemeriksaan otomatis tidak lolos`;
  if ((m = s.match(/^score ([\d.]+) >= threshold ([\d.]+)/))) return `Skor ${num2(m[1])} sudah mencapai batas ${num2(m[2])}, jadi disetujui otomatis`;
  if (s.includes("internally inconsistent")) return "Angka-angka di dokumen ini tidak saling cocok, jadi perlu dicek manusia";
  return s.charAt(0).toUpperCase() + s.slice(1);
}
// "Nama vendor" -> "nama vendor", but keep abbreviations such as "NPWP penjual".
const lowerFirst = (t) => (/^[A-Z]{2}/.test(t) ? t : t.charAt(0).toLowerCase() + t.slice(1));
const upperFirst = (t) => t.charAt(0).toUpperCase() + t.slice(1);
function issueText(issue, type, values) {
  const f = issue.fields?.[0] ? fieldLabel(issue.fields[0], type) : "";
  const say = RULES[issue.rule];
  return say ? say(f ? upperFirst(f) : "", values || {}, issue, type) : issue.message;
}
// Short names for the calculation check, where "Pajak pertambahan nilai seharusnya" would be a mouthful.
const TAPE_LABEL = { dpp: "Dasar pajak (DPP)", ppn: "PPN", tax: "Pajak" };
const tapeLabel = (field, type) => TAPE_LABEL[field] || fieldLabel(field, type);
const pctText = (r) => `${(r * 100).toLocaleString("id-ID", { maximumFractionDigits: 2 })}%`;
function termLabel(t, type) {
  if (t.field) return tapeLabel(t.field, type);
  if (t.label === "rate") return `tarif PPN ${pctText(t.value)}`;
  if (t.label === "dpp_factor") return Math.abs(t.value - 11 / 12) < 1e-4 ? "faktor DPP 11/12" : t.value === 1 ? "tanpa faktor 11/12" : `faktor DPP ${num2(t.value)}`;
  return "";
}
// The check printed like an adding-machine tape. All numbers come from the server's `expected`; nothing is recomputed here.
function tapeHtml(issue, type, values) {
  const e = issue.expected;
  if (!e || !Array.isArray(e.terms)) return "";
  const row = (cls, label, value) => `<tr class="${cls}"><th scope="row">${esc(label)}</th><td>${value == null ? "" : esc(money(value, values))}</td></tr>`;
  const terms = e.terms.filter((t) => t.field ? !(t.op && !t.value) : true).map((t) => {
    const op = t.op === "x" ? "x " : t.op === "-" ? "– " : t.op === "+" ? "+ " : "";
    const isFactor = t.label === "rate" || t.label === "dpp_factor";
    return row("term", `${op}${termLabel(t, type)}`, isFactor ? null : t.value);
  });
  const label = tapeLabel(e.field, type);
  const sum = issue.rule === "line_items_sum";
  const off = e.difference > e.tolerance;
  return `<table class="tape"><caption class="sr">Rincian hitungan</caption><tbody>${terms.join("")}
    ${row("result", sum ? "Jumlah rincian barang" : `${label} seharusnya`, e.expected)}
    ${row("", `${label} di dokumen`, e.actual)}
    ${row(`diff${off ? " off" : ""}`, "Selisih", e.difference)}</tbody></table>`;
}
const thresholdFor = (a) => state.health?.thresholds?.[a] ?? 0.9;

// ---------------------------------------------------------------- workspace
function stopPolling() { clearTimeout(state.polling); state.polling = null; }
async function openDoc(id) {
  stopPolling();
  const view = $("#view-doc");
  let d;
  try { d = await api("GET", `/documents/${id}`); } catch (e) {
    view.innerHTML = `<div class="view"><p class="empty">${esc(e.message)} <a href="#queue">Kembali ke antrean</a>.</p></div>`;
    return;
  }
  if (d.status === "queued" || d.status === "processing") {
    view.innerHTML = `<div class="view"><p class="empty">Dokumen ini masih dibaca. Halaman ini terbuka sendiri setelah selesai.</p></div>`;
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
  const ph = value == null ? "tidak ditemukan" : "";
  const input = kind === "long"
    ? `<textarea id="${id}" data-path="${path}" rows="2" placeholder="${ph}">${esc(shown)}</textarea>`
    : `<input id="${id}" class="${["text", "long"].includes(kind) ? "" : "data"}" data-path="${path}" data-kind="${kind}" value="${esc(shown)}" placeholder="${ph}"
        ${kind === "money" || kind === "num" ? 'inputmode="decimal"' : ""} autocomplete="off" spellcheck="false">`;
  const hint = kind === "date" && value ? `<span class="hint">${longDate(value)}</span>` : "";
  const confText = corrected ? "nilai diisi oleh pemeriksa" : value == null ? "tidak ditemukan" : `${low ? "kurang yakin, " : ""}tingkat yakin ${num2(conf)}`;
  const confCell = corrected ? `<div class="conf reviewed" title="${confText}"><span class="cv">dicek</span></div>`
    : `<div class="conf c${lvl}" title="${confText}"><span class="meter" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
      <span class="cv" aria-hidden="true">${value == null ? "–" : num2(conf)}</span><span class="sr" data-orig="${confText}">${confText}</span>
      <span class="st st-checked">sudah dicek</span><span class="st st-edited">diubah</span></div>`;
  return `<div class="field${low ? " low" : ""}" data-field="${path}">
    <label for="${id}">${esc(en)}${idLabel ? `<small>${esc(idLabel)}</small>` : ""}${critical ? `<span class="tag">penting</span>` : ""}</label>
    <div class="fv">${adorn}${input}</div>
    ${confCell}
    ${hint}</div>`;
}

function itemsTable(items) {
  const rows = items.map((it, i) => itemRow(it, i)).join("");
  return `<section class="group" aria-labelledby="items-title"><h2 class="group-title" id="items-title">Rincian barang</h2>
    <div class="items-wrap"><table class="items"><thead><tr><th class="num">#</th>
    ${ITEM_COLS.map(([, l, k]) => `<th${k !== "text" ? ' class="num"' : ""}>${l}</th>`).join("")}<th><span class="sr">Hapus</span></th></tr></thead>
    <tbody id="items-body">${rows}</tbody></table></div>
    <div class="items-foot"><button type="button" id="add-item">Tambah baris</button>
    <span>Jumlah semua baris <b id="items-sum"></b></span></div></section>`;
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
      value="${esc(shown)}" aria-label="${label}, baris ${i + 1}" autocomplete="off" spellcheck="false"
      ${kind !== "text" ? 'inputmode="decimal"' : ""} title="${it && v != null ? `tingkat yakin ${num2(node.confidence)}` : ""}"></td>`;
  }).join("");
  return `<tr data-row="${i}"${it ? ` data-src="${i}"` : ""}><td class="idx">${i + 1}</td>${cells}<td class="rowtools"><button type="button" class="danger" data-remove="${i}" aria-label="Hapus baris ${i + 1}">Hapus</button></td></tr>`;
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
  const hasBoxes = Object.keys(d.sources || {}).length > 0;
  const items = corrected
    ? (corrected.line_items || []).map((it) => Object.fromEntries(Object.entries(it).map(([k, v]) => [k, typeof v === "boolean" ? v : { value: v, confidence: 1 }])))
    : ex.line_items || [];
  const groups = (LAYOUT[type] || []).map((g) => `<section class="group"><h2 class="group-title">${esc(g.title)}</h2>
      ${g.items.map(([p, en, idl, kind]) => fieldRow(p, en, idl, kind, ex, critical.has(p), corrected)).join("")}</section>`).join("");
  const issues = (d.issues || []).map((i) => `<li class="${i.severity}" title="${esc(i.message)}">${esc(issueText(i, type, d.values))}
      ${i.fields?.length ? ` <a data-goto="${esc(i.fields[0])}">Lihat kolomnya</a>` : ""}${tapeHtml(i, type, d.values)}</li>`).join("");
  const u = d.usage || {};
  const qi = state.queue.findIndex((q) => q.id === d.id);

  view.innerHTML = `
    <h1 class="sr-only">Cek dokumen ${esc(d.filename)}</h1>
    <div class="doc-head">
      <button type="button" class="back" id="back">${state.fromQueue ? "Kembali ke antrean" : "Kembali ke Coba dokumen"}</button>
      <span class="fname">${esc(d.filename)}</span>
      <span class="chip">${esc(DOC_TYPES[type] || type || "jenis tidak dikenal")}</span>
      <span class="chip ${auto ? "ok" : "warn"}">${auto ? "Disetujui otomatis" : "Perlu dicek"}</span>
      ${d.repaired ? `<span class="chip" title="Data sempat salah, lalu AI diminta memperbaikinya satu kali">Diperbaiki AI</span>` : ""}
      ${ex.document_inconsistent ? `<span class="chip err">Angka di dokumen tidak cocok</span>` : ""}
      <span class="facts"><span>metode <b>${esc(d.approach)}</b></span>
        <span>skor <b>${num2(d.score ?? 0)}</b> / batas <b>${num2(thr)}</b></span>
        <span>waktu proses <b>${((d.latency_ms || 0) / 1000).toLocaleString("id-ID", { maximumFractionDigits: 1 })} detik</b></span></span>
    </div>
    <div class="split">
      <div class="scan" id="scan">
        <div class="scan-tools"><span class="src-status" id="src-status" role="status"></span>
          ${hasBoxes && !corrected ? `<button type="button" id="weak-toggle" aria-pressed="false">Tandai kolom lemah</button>` : ""}
          <button type="button" id="zoom" aria-pressed="false">Perbesar</button></div>
        ${decided ? `<span class="stamp${decided === "reject" ? " rejected" : ""}">${DECISIONS[decided]}</span>` : ""}
        <div class="page" id="page">
          <img src="/documents/${encodeURIComponent(d.id)}/preview" alt="Scan ${esc(d.filename)}, ${esc(DOC_TYPES[type] || "dokumen")}">
          <svg class="boxes" id="boxes" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true"></svg>
          <span class="src-label" id="src-label" hidden></span>
        </div>
      </div>
      <div class="fields" id="fields">
        <div class="verdict">
          <p class="reason">${esc(humanReason(d.route_reason, type))}.${d.status === "failed" ? ` ${esc(failureText(d.error))}` : ""}
            ${!auto && !decided ? "Cocokkan kolom yang ditandai dengan scan. <kbd>Enter</kbd> di sebuah kolom pindah ke kolom berikutnya yang perlu dicek. Lalu pilih Setujui, Simpan koreksi, atau Tolak." : ""}</p>
          ${issues ? `<ul class="issues">${issues}</ul>` : ""}
        </div>
        ${groups}
        ${type ? itemsTable(items) : ""}
        <details class="tech"><summary>Detail teknis</summary><div class="body">
          <dl class="kv">
            <dt>Model</dt><dd>${esc(u.model || "tidak ada (OCR lokal + aturan)")}</dd>
            <dt>Token</dt><dd>${u.input_tokens || 0} masuk, ${u.output_tokens || 0} keluar, ${u.llm_calls || 0} panggilan</dd>
            <dt>Biaya</dt><dd>$${(u.cost_usd || 0).toFixed(5)} (Rp ${idNum.format(u.cost_idr || 0)})</dd>
            ${Object.entries(d.versions || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}
            ${(d.trace || []).map((s) => `<dt>${esc(s.name)}</dt><dd>${s.duration_ms} ms</dd>`).join("")}
          </dl>
          <pre class="raw">${esc(JSON.stringify(d.values, null, 2))}</pre>
        </div></details>
      </div>
    </div>
    <div class="actionbar">
      <label class="reviewer" for="reviewer">Pemeriksa <input id="reviewer" value="${esc(localGet("reviewer") || "demo")}" autocomplete="off"></label>
      ${qi >= 0 ? `<span class="navhint">${qi + 1} dari ${state.queue.length} di antrean. <kbd>j</kbd> berikutnya, <kbd>k</kbd> sebelumnya</span>` : ""}
      <span class="sweep" id="sweep" role="status"></span>
      <span class="result" id="result">${decided ? `${DECIDED[decided]} oleh ${esc(d.review.reviewer)}${d.review.changed_fields?.length ? `, ${d.review.changed_fields.length} kolom diubah` : ""}.` : ""}</span>
      <span class="spacer"></span>
      <button type="button" class="danger" id="reject" title="Pintasan: R">Tolak<span class="k">R</span></button>
      <button type="button" id="save" disabled title="Pintasan: S">Simpan koreksi<span class="k">S</span></button>
      <button type="button" class="primary" id="approve" title="Pintasan: A">Setujui<span class="k">A</span></button>
    </div>`;

  $("#back").onclick = () => { location.hash = state.fromQueue ? "queue" : "extract"; };
  $("#zoom").onclick = (e) => {
    const z = $("#scan").classList.toggle("zoomed");
    e.target.textContent = z ? "Kembali ke ukuran pas" : "Perbesar";
    e.target.setAttribute("aria-pressed", z);
  };
  $("#fields").addEventListener("input", onEdit);
  wireSources(corrected ? null : d.sources);
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
  // Fields that need a look: weak, not found, or named by a validation issue. Enter sweeps through them.
  state.checked = new Set();
  const flagged = new Set((d.issues || []).flatMap((i) => i.fields || []));
  if (!decided) {
    for (const row of view.querySelectorAll(".field")) {
      if (row.classList.contains("low") || flagged.has(row.dataset.field)) row.dataset.needs = "1";
    }
  }
  updateSweep();
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
  setActiveSource(path, true);
  // Opening a document only points at the weakest field; focusing it would swallow the a/s/r shortcuts.
  if (focus) row.querySelector("input, textarea")?.focus({ preventScroll: true });
}
function onEdit(e) {
  const el = e.target;
  if (!el.dataset?.path) return;
  const changed = el.value.trim() !== (state.initial[el.dataset.path] ?? "");
  const holder = el.closest(".field") || el.closest("td");
  holder.classList.toggle("edited", changed);
  if (holder.classList.contains("field")) { paintRowState(holder); updateSweep(); }
  updateDirty();
  if (el.closest("#items-body")) updateSum();
}

// ---------------------------------------------------------------- source boxes on the scan
// d.sources maps a field path to a box on the page OCR read, as fractions of that page. A field gets a box only when
// the server was certain which printed value it came from, and loses it once the reviewer edits it.
const src = { map: null, focus: null, hover: null, weak: false };
const sourceOf = (path) => (path && src.map?.[path]) || null;
function rowOfSource(path) {
  const m = String(path).match(/^line_items\.(\d+)$/);
  return m ? document.querySelector(`#items-body tr[data-src="${m[1]}"]`) : document.querySelector(`#fields .field[data-field="${CSS.escape(path)}"]`);
}
function sourceEdited(path) {
  const row = rowOfSource(path);
  if (!row) return true;  // a removed item row
  return row.matches("tr") ? !!row.querySelector("td.edited") : row.classList.contains("edited");
}
function sourcePathOf(el) {
  const field = el.closest?.("#fields .field");
  if (field) return field.dataset.field;
  const tr = el.closest?.("#items-body tr");
  return tr?.dataset.src != null ? `line_items.${tr.dataset.src}` : null;
}
function sourceName(path) {
  const m = String(path).match(/^line_items\.(\d+)$/);
  return m ? `Baris ${Number(rowOfSource(path)?.dataset.row ?? m[1]) + 1}` : upperFirst(fieldLabel(path, state.doc?.doc_type));
}
function paintSources() {
  const svg = $("#boxes");
  if (!svg) return;
  const active = src.hover || src.focus;
  const draw = (path, weak) => {
    const [x, y, w, h] = sourceOf(path).box;
    const px = 0.004, py = 0.002;
    const x0 = Math.max(0, x - px), y0 = Math.max(0, y - py);
    return `<rect class="src-box${weak ? " weak" : ""}" data-src="${path}" x="${x0}" y="${y0}" width="${Math.min(1 - x0, w + 2 * px)}" height="${Math.min(1 - y0, h + 2 * py)}"/>`;
  };
  const show = (p) => sourceOf(p) && !sourceEdited(p);
  const weakPaths = src.weak ? [...document.querySelectorAll("#fields .field.low")].map((r) => r.dataset.field).filter((p) => p !== active && show(p)) : [];
  svg.innerHTML = weakPaths.map((p) => draw(p, true)).join("") + (show(active) ? draw(active, false) : "");
  const label = $("#src-label");
  label.hidden = !show(active);
  if (!label.hidden) {
    const [x, y, , h] = sourceOf(active).box;
    label.textContent = sourceName(active);
    label.style.left = `${x * 100}%`;
    label.style.top = `${(y < 0.03 ? y + h : y) * 100}%`;
    label.classList.toggle("below", y < 0.03);
  }
  // Say why there is no box, so a missing box is not mistaken for a bug.
  const status = $("#src-status");
  const row = active && rowOfSource(active);
  const hasValue = row && row.matches?.(".field") && row.querySelector("input, textarea")?.value.trim() && !row.classList.contains("edited");
  status.textContent = hasValue && !sourceOf(active) ? `${sourceName(active)}: lokasi di scan tidak ditemukan.` : "";
  status.title = status.textContent;
}
function setActiveSource(path, scroll) {
  src.focus = path;
  paintSources();
  if (scroll && sourceOf(path) && !sourceEdited(path)) scrollScanTo(sourceOf(path).box);
}
function scrollScanTo([x, y, w, h]) {
  const scan = $("#scan"), page = $("#page");
  if (!scan || !page || getComputedStyle(scan).overflowY === "visible") return;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const top = page.offsetTop + (y + h / 2) * page.offsetHeight - scan.clientHeight / 2;
  const left = page.offsetLeft + (x + w / 2) * page.offsetWidth - scan.clientWidth / 2;
  scan.scrollTo({ top: Math.max(0, top), left: Math.max(0, left), behavior: reduce ? "auto" : "smooth" });
}
function wireSources(map) {
  src.map = map && Object.keys(map).length ? map : null;
  src.focus = src.hover = null;
  src.weak = false;
  const fields = $("#fields");
  fields.addEventListener("focusin", (e) => {
    const p = sourcePathOf(e.target);
    if (p) setActiveSource(p, true); else { src.focus = null; paintSources(); }
  });
  fields.addEventListener("focusout", (e) => { if (!fields.contains(e.relatedTarget)) { src.focus = null; paintSources(); } });
  fields.addEventListener("mouseover", (e) => { const p = sourcePathOf(e.target); if (p !== src.hover) { src.hover = p; paintSources(); } });
  fields.addEventListener("mouseleave", () => { src.hover = null; paintSources(); });
  fields.addEventListener("input", () => paintSources());
  $("#boxes").addEventListener("click", (e) => {
    const p = e.target.closest?.("[data-src]")?.dataset.src;
    if (!p) return;
    const row = rowOfSource(p);
    (row?.querySelector("input, textarea"))?.focus();
  });
  const toggle = $("#weak-toggle");
  if (toggle) toggle.onclick = () => {
    src.weak = !src.weak;
    toggle.setAttribute("aria-pressed", src.weak);
    paintSources();
  };
}

// ---------------------------------------------------------------- sweep through the fields that need a look
const needsRows = () => [...document.querySelectorAll("#fields .field[data-needs]")];
const isResolved = (row) => row.classList.contains("checked") || row.classList.contains("edited");
// The visible words replace the confidence for screen readers too, so they never hear a stale score.
function paintRowState(row) {
  const sr = row.querySelector(".conf .sr");
  if (sr) sr.textContent = isResolved(row) ? "" : sr.dataset.orig;
}
function updateSweep() {
  const box = $("#sweep");
  if (!box) return;
  const rows = needsRows();
  const done = rows.filter(isResolved).length;
  box.textContent = rows.length ? `${done} dari ${rows.length} kolom sudah dicek` : "";
}
function sweep(row, back) {
  const rows = needsRows();
  const after = (r) => row.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING;
  if (!back && row.dataset.needs) {
    row.classList.add("checked");
    state.checked.add(row.dataset.field);
    paintRowState(row);
  }
  let target;
  if (back) target = rows.filter((r) => r !== row && !after(r)).at(-1);
  else target = rows.find((r) => r !== row && after(r) && !isResolved(r)) || rows.find((r) => r !== row && !isResolved(r));
  updateSweep();
  if (target) { focusField(target.dataset.field); return; }
  if (back) return;
  // Nothing left to check: leave the field so a, s and r work, and say what is next.
  document.activeElement?.blur();
  const box = $("#sweep");
  if (box) box.textContent = "Semua kolom yang ditandai sudah dicek. Tekan A untuk menyetujui atau S untuk menyimpan koreksi.";
}
// Up and Down in the line items table move within the same column.
function moveInItems(input, down) {
  const td = input.closest("td");
  const tr = td.parentElement;
  const next = down ? tr.nextElementSibling : tr.previousElementSibling;
  const target = next?.cells[td.cellIndex]?.querySelector("input");
  if (!target) return false;
  target.focus();
  target.select();
  return true;
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
  btn.firstChild.textContent = n ? `Simpan ${n} koreksi` : "Simpan koreksi";
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
  const body = { action, reviewer: $("#reviewer").value.trim() || "anonim" };
  if (action === "correct") body.values = collectValues();
  for (const b of document.querySelectorAll(".actionbar button")) b.disabled = true;
  try {
    const r = await api("POST", `/review/${encodeURIComponent(d.id)}`, body);
    const changed = r.changed_fields.map((p) => fieldLabel(p.split(".").slice(0, p.startsWith("line_items") ? 1 : 2).join("."), d.doc_type));
    let msg = action === "correct"
      ? (changed.length ? `Koreksi tersimpan. Yang berubah: ${[...new Set(changed.map(lowerFirst))].join(", ")}.` : "Tersimpan, tapi tidak ada nilai yang berubah setelah formatnya dirapikan.")
      : `${DECIDED[action]}.`;
    await refreshQueueCount();
    const next = state.fromQueue ? state.queue[0] : null;
    if (next) msg += " Membuka dokumen berikutnya di antrean.";
    toast(msg);
    if (next) { location.hash = `doc-${next.id}`; return; }
    openDoc(d.id);
  } catch (e) {
    toast(e.message, true);
    for (const b of document.querySelectorAll(".actionbar button")) b.disabled = false;
    updateDirty();
  }
}

// ---------------------------------------------------------------- metrics
// Download of approved documents: GET /export/approved (xlsx has a sheet for documents and one for line items).
function exportSection(ex) {
  if (!ex) return "";
  const by = ex.by_decision || {};
  const parts = [["auto_approved", "disetujui otomatis"], ["approved", "disetujui pemeriksa"], ["corrected", "dikoreksi pemeriksa"]]
    .filter(([k]) => by[k]).map(([k, label]) => `${by[k]} ${label}`);
  const body = ex.documents
    ? `<p class="src">${ex.documents} dokumen siap diunduh (${parts.join(", ")}), dengan ${ex.line_items} baris barang.
        Dokumen yang ditolak atau masih di antrean cek tidak ikut. Untuk dokumen yang dikoreksi, yang diunduh adalah nilai hasil koreksi.</p>
      <div class="export-actions">
        <a class="btn primary" href="/export/approved?format=xlsx" download>Unduh Excel</a>
        <a class="btn" href="/export/approved?format=csv" download>CSV dokumen</a>
        <a class="btn" href="/export/approved?format=csv&part=items" download>CSV rincian barang</a>
      </div>
      <p class="src">File Excel berisi dua sheet: "Dokumen" (satu baris per dokumen) dan "Rincian barang" (satu baris per barang). Keduanya bisa dicocokkan lewat kolom <code>document_id</code>.</p>`
    : `<p class="src">Belum ada dokumen yang disetujui. Setujui atau koreksi dokumen di Antrean cek, nanti datanya bisa diunduh di sini.</p>`;
  return `<section class="metrics-sec"><h2>Unduh data yang sudah disetujui</h2>${body}</section>`;
}
// One label per path across document types; "total" is "Harga jual" on a faktur but "Total" everywhere else.
const SHARED_LABEL = { date: "Tanggal", total: "Total", discount: "Diskon", tax: "Pajak" };
function anyFieldLabel(path) {
  if (SHARED_LABEL[path]) return SHARED_LABEL[path];
  const type = Object.keys(LAYOUT).find((t) => fieldLabel(path, t) !== path);
  return upperFirst(type ? fieldLabel(path, type) : fieldLabel(path, ""));
}
function correctionsSection(c) {
  const rows = c?.fields || [];
  const intro = `<p class="src">Dihitung dari dokumen yang dikoreksi pemeriksa di Antrean cek. Kolom di urutan atas paling sering salah dibaca.</p>`;
  if (!rows.length) return `<section class="metrics-sec"><h2>Kolom yang paling sering dikoreksi</h2>${intro}
    <p class="empty">Belum ada koreksi. Di Antrean cek, ubah sebuah kolom lalu pilih Simpan koreksi, nanti kolom itu muncul di sini.</p></section>`;
  const pct = (v) => `${(v * 100).toLocaleString("id-ID", { maximumFractionDigits: 0 })}%`;
  return `<section class="metrics-sec"><h2>Kolom yang paling sering dikoreksi</h2>${intro}
    <div class="table-wrap narrow" tabindex="0" role="region" aria-label="Kolom yang paling sering dikoreksi"><table><thead><tr><th>Kolom</th><th class="num">Dikoreksi</th><th class="num">Dari ${c.documents} dokumen yang dikoreksi</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${esc(anyFieldLabel(r.path))}</td><td class="num">${r.count} kali</td><td class="num">${pct(r.count / c.documents)}</td></tr>`).join("")}
    </tbody></table></div></section>`;
}
async function loadMetrics() {
  const box = $("#metrics");
  let m;
  let ex = null;
  try { [m, ex] = await Promise.all([api("GET", "/metrics"), api("GET", "/export/approved/count").catch(() => null)]); } catch (e) { box.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  const pct = (v) => (v == null ? "–" : `${(v * 100).toLocaleString("id-ID", { maximumFractionDigits: 1 })}%`);
  const rv = m.reviews || {};
  const stat = (v, k) => `<div class="stat"><span class="v">${v}</span><span class="k">${k}</span></div>`;
  const off = m.offline_eval?.approaches || [];
  box.innerHTML = `${exportSection(ex)}
    <section class="metrics-sec"><h2>Server ini</h2>
      <p class="src">Angka langsung dari dokumen yang diproses server ini sejak dinyalakan. Data demo bisa hilang saat server dimulai ulang.</p>
      <div class="stats">
        ${stat(m.documents, "dokumen diproses")}
        ${stat(pct(m.auto_approve_rate), "disetujui otomatis")}
        ${stat(`${rv.approve || 0} / ${rv.correct || 0} / ${rv.reject || 0}`, "disetujui / dikoreksi / ditolak")}
        ${stat(m.latency_ms_p50 == null ? "–" : `${(m.latency_ms_p50 / 1000).toLocaleString("id-ID", { maximumFractionDigits: 1 })} detik`, "waktu proses biasa (p50)")}
        ${stat(m.latency_ms_p95 == null ? "–" : `${(m.latency_ms_p95 / 1000).toLocaleString("id-ID", { maximumFractionDigits: 1 })} detik`, "waktu proses terlama (p95)")}
        ${stat(`$${(m.cost_usd_total || 0).toFixed(4)}`, "biaya API dan server")}
      </div></section>
    ${correctionsSection(m.corrections_by_field)}
    <section class="metrics-sec"><h2>Hasil uji dengan data contoh</h2>
      <p class="src">Diuji pada data sintetis set ${esc(m.offline_eval?.split || "dev")}, dari <code>eval/reports/summary.json</code>. Akurasi kolom menghitung setiap kolom. Akurasi dokumen hanya menghitung dokumen yang semua kolom pentingnya benar.</p>
      ${off.length ? `<div class="table-wrap" tabindex="0" role="region" aria-label="Hasil uji per metode"><table><thead><tr><th>Metode</th><th class="num">Dokumen</th><th class="num">Akurasi kolom</th>
        <th class="num">Akurasi dokumen</th><th class="num">Ketepatan rincian barang (F1)</th><th class="num">Disetujui otomatis</th>
        <th class="num">Biaya per 1.000 dokumen</th><th class="num">Waktu proses biasa</th></tr></thead><tbody>
        ${off.map((a) => `<tr><td>${esc(a.approach)}</td><td class="num">${a.n}</td><td class="num">${pct(a.field_accuracy)}</td>
          <td class="num">${pct(a.doc_accuracy)}</td><td class="num">${num2(a.items_f1)}</td>
          <td class="num">${a.stp_at_threshold == null ? "belum ada yang aman" : `${pct(a.stp_at_threshold)} pada batas ${num2(a.recommended_threshold)}`}</td>
          <td class="num">$${a.cost_usd_per_1000.toFixed(2)}</td><td class="num">${a.latency_p50_s.toLocaleString("id-ID", { maximumFractionDigits: 1 })} detik</td></tr>`).join("")}
        </tbody></table></div>` : `<p class="empty">Hasil uji tidak disertakan di server ini.</p>`}
    </section>`;
}

// ---------------------------------------------------------------- keyboard
document.addEventListener("keydown", (e) => {
  const typing = e.target.closest?.("input, textarea, select");
  if (e.key === "Escape" && typing) { e.target.blur(); return; }
  if (typing && !e.isComposing && !$("#view-doc").hidden && state.doc) {
    const row = e.target.closest("#fields .field");
    if (e.key === "Enter" && row && e.target.matches("input, textarea")) {
      // In an address box a plain Enter still starts a new line; Ctrl+Enter sweeps like Enter does elsewhere.
      if (e.target.tagName === "TEXTAREA" && !(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      sweep(row, e.shiftKey);
      return;
    }
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && e.target.matches("#items-body input") && !(e.ctrlKey || e.metaKey || e.altKey || e.shiftKey)) {
      if (moveInItems(e.target, e.key === "ArrowDown")) e.preventDefault();
      return;
    }
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey || $("#view-doc").hidden || !state.doc) return;
  const k = e.key.toLowerCase();
  if (k === "a") decide("approve");
  else if (k === "r") decide("reject");
  else if (k === "s") decide("correct");
  else if (k === "/") {
    e.preventDefault();
    const row = document.querySelector("#fields .field.target") || needsRows().find((r) => !isResolved(r));
    row?.querySelector("input, textarea")?.focus();
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
  initShell();
  try { state.health = await api("GET", "/health"); } catch (e) {
    document.body.insertAdjacentHTML("beforeend", `<p class="empty">Server tidak bisa dihubungi (${esc(e.message)}). Muat ulang halaman ini sebentar lagi.</p>`);
    return;
  }
  const h = state.health;
  state.approach = h.available_approaches.includes(h.default_approach) ? h.default_approach : h.available_approaches[0];
  $("#deploy-info").innerHTML = `Metode bawaan <b>${esc(h.default_approach)}</b>`;
  $("#max-mb").textContent = h.max_upload_mb;
  renderApproachPicker();
  setupUpload();
  loadSamples();
  await refreshQueueCount();
  window.addEventListener("hashchange", route);
  route();
})();
