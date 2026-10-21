# docex review desk: UI design

This document is the visual and interaction spec for the web UI that ships with docs-extractor.
The UI is a static app (`web/`) served by the FastAPI process, so the demo runs as one container on one port.
The code in `web/` follows this file. Where they differ, this file is the target and the difference is listed under
"Changes from the current UI".

Design read: a working tool for finance and tax admins who check extractions, in a paperwork-and-ledger visual
language (a rubber stamp for decisions, an adding-machine tape for arithmetic), kept quiet so the scan is the loudest
thing on screen.

Dial: ENERGY 1 / RHYTHM 2 / MOTION 1

Direction was chosen by the product owner on 2026-10-21 ("choose the design best fit"). The Dribbble shot the owner
first pointed to could not be opened from the build environment, so nothing here is copied from it.

## Who uses it, and for what

**Primary user: a finance or tax admin who checks extractions.** They work through a queue of documents the
pipeline was not sure about. For each one they compare the scan with the extracted fields, fix what is wrong, and
approve or reject. They do this dozens of times a day, so speed and keyboard use matter more than decoration.

**Secondary user: a hiring manager or engineer evaluating the portfolio.** They open the demo link once, try a
sample document, and look for evidence that the system is measured. They need the first screen to work with no setup
and no upload of their own data.

The product has three jobs, in this order:

1. Show one document and its fields side by side, and make a correction take seconds.
2. Let a visitor run a synthetic sample (or upload a file) and see the result.
3. Show how the system performs, from live counts and from the offline evaluation.

## Information architecture

| View | Menu label | Route | Purpose |
|---|---|---|---|
| Try a document | Coba dokumen | `#extract` | Sample gallery, upload, method picker. Opens the workspace when a result is ready. |
| Review queue | Antrean cek | `#queue` | Documents routed to review, lowest score first. The default view when the queue is not empty. |
| Workspace | none | `#doc-<id>` | Scan on the left, fields on the right, actions at the bottom. Shared by the try and review flows. |
| Results and numbers | Hasil & angka | `#metrics` | Download of approved data, live counts, which fields get corrected most, and the offline comparison. |

The top bar holds the product name, the three menu items, the queue count, and the default method on this
deployment. There is no sidebar, because the workspace needs the full width.

## Visual language

### Identity

Two motifs carry the identity and nothing else is decorative.

1. **The stamp.** A decision (DISETUJUI, DIKOREKSI, DITOLAK) is stamped on the corner of the scan in stamp-ink
   colour, the way a paper document is stamped at an Indonesian finance desk. It is the only large gesture in the
   product and the only place where uppercase is used.
2. **The tape.** Arithmetic is shown the way an adding-machine tape prints it: right-aligned figures in a
   tabular face, a hairline above the result. The checks on a document use it (see "Calculation check").

Everything else is plain: flat panels, one accent, no shadows except on the sticky action bar and toast.

### Colour tokens

The palette is a cool grey with a slight blue bias and one accent, the stamp ink. Semantic colours mark state only.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F3F4F7` | `#121419` | Page background |
| `--surface` | `#FFFFFF` | `#1A1D24` | Panels, inputs |
| `--surface-2` | `#E9EBF0` | `#232731` | Scan backdrop, table header, hover |
| `--line` | `#D5D9E1` | `#323744` | Dividers between panels and rows (decoration, not a control edge) |
| `--line-strong` | `#80889A` | `#6B7385` | Edge of every input, button and drop zone |
| `--fg` | `#171A21` | `#E6E8EE` | Primary text |
| `--fg-muted` | `#5B6273` | `#9AA1B2` | Labels, secondary text |
| `--ink` | `#3B3FA8` | `#9EA3F5` | Stamp ink: primary action, focus ring, decisions, links, the focused source box |
| `--ink-tint` | `#ECEDFB` | `#262A4A` | Edited field, hover, selected nav item |
| `--on-ink` | `#FFFFFF` | `#121419` | Text on `--ink` |
| `--ok` | `#1E7A4C` | `#5CC48E` | Auto-approved, high confidence, checked field |
| `--warn` | `#A15C00` | `#E7A64A` | Low confidence, warnings, source box of a weak field |
| `--warn-tint` | `#FFF4E2` | `#2E2416` | Low-confidence row background |
| `--err` | `#B42318` | `#F07A6E` | Validation errors, reject, a non-zero difference on the tape |
| `--err-tint` | `#FDECEA` | `#33201E` | Error row background |

Both themes follow the system setting. Measured contrast (WCAG 2.x ratio):

| Pair | Light | Dark |
|---|---|---|
| `--fg` on `--surface` | 17.4 | 13.8 |
| `--fg-muted` on `--surface` | 6.1 | 6.5 |
| `--fg-muted` on `--warn-tint` | 5.6 | 5.9 |
| `--ink` on `--surface` | 8.5 | 7.2 |
| `--ink` on `--ink-tint` | 7.3 | 6.0 |
| `--on-ink` on `--ink` | 8.5 | 7.9 |
| `--ok` on `--surface` | 5.3 | 7.8 |
| `--warn` on `--warn-tint` | 4.8 | 7.2 |
| `--err` on `--err-tint` | 5.8 | 5.7 |
| `--line-strong` on `--surface` (needs 3.0) | 3.6 | 3.6 |

### Type

One family, set in two cuts, plus a mono face for values.

| Role | Face | Use |
|---|---|---|
| Interface | IBM Plex Sans 400 / 500 / 600 | Everything that is a word: labels, copy, buttons, view titles |
| Stamp | IBM Plex Sans Condensed 700 | The decision stamp only |
| Data | IBM Plex Mono 400 / 500 | Field values, NPWP, amounts, scores, file names, the tape |

Why this family: IBM Plex comes from forms and ledgers, its sans and mono cuts share proportions so a value
sits comfortably beside its label, and one family means two font requests instead of three. The earlier pairing of
Bricolage Grotesque and Instrument Sans is dropped because the display face was only used for three headings.

Scale: 12 / 13 / 14 / 16 / 20 / 28 px. Body is 14px because this is a working tool, not a page to read.
View titles are 28px at weight 600. Field values are 14px mono with `tabular-nums` so amounts line up. Line length
for prose is capped at 65ch.

Labels are sentence case at weight 600, 13px, `--fg-muted`. There are no tracked-out uppercase labels. Mono is used
only for values and identifiers, never for labels.

Fonts load from Google Fonts with system fallbacks, so the UI still works offline.

### Shape and spacing

- 4px spacing unit. Panels use 16px or 24px padding.
- Radius: 6px on inputs and buttons, 8px on grouped panels, 0 on the two split regions of the workspace (they are
  regions, not cards). Cards exist only in the sample gallery.
- Shadows only on the sticky action bar and the toast.
- Focus: a 2px `--ink` outline with a 2px offset on every interactive element.
- Motion: 120ms colour and background transitions. The stamp fades in over 160ms. A source box appears in 120ms.
  Scan scrolling to a box is a smooth scroll. Everything is instant under `prefers-reduced-motion`.

## The workspace

This is the screen that matters most. Everything else leads to it.

```
+--------------------------------------------------------------------------------+
| < Kembali ke antrean  faktur-0002.jpg  Faktur pajak  Perlu dicek     metode A    |
+-------------------------------------+------------------------------------------+
|                          [Perbesar] | Tanggal terbaca dengan tingkat yakin     |
|                                     | rendah (0,62).                           |
|   scan                              | Perhitungan PPN tidak cocok              |
|   +---------------------+           |   DPP x tarif ...        1.164.020       |
|   |  NPWP  80.841...    | <- box    |   PPN di dokumen         1.164.000       |
|   +---------------------+           |   Selisih                       20       |
|                                     |------------------------------------------|
|                                     | Dokumen   Nomor seri   [0200012413...]   |
|                                     |           Tanggal      [2024-05-03]  0,77|
|                                     | Penjual   NPWP         [80.841...]   0,58|
|                                     | Rincian barang  (tabel)                  |
+-------------------------------------+------------------------------------------+
| Pemeriksa [demo]   2 dari 5 kolom sudah dicek    Tolak R   Simpan koreksi S  Setujui A |
+--------------------------------------------------------------------------------+
```

- **Split view.** Scan left, fields right, each scrolling on its own. Under 900px the scan stacks above the fields
  and is capped at 60% of the viewport height.
- **Fields are grouped the way the paper document is.** For a faktur pajak: document, seller, buyer, amounts, line
  items. Invoices and receipts follow their own schema order. Labels are plain Indonesian, with the official term in
  smaller text where one exists ("Dasar pajak", "DPP").
- **Confidence belongs to each field.** Each row has a four-step meter (0 to 0.5, 0.5 to 0.8, 0.8 to 0.95, 0.95 and up)
  and the number beside it. Fields below 0.8 get an amber left edge and a tinted row. Empty fields show "tidak
  ditemukan" in muted italic, so a reviewer can tell missing from empty.
- **Critical fields carry a small "penting" tag** in `--ink`, sentence case. The weakest critical field, the one named
  in the route reason, is scrolled into view and outlined when the workspace opens. It is not focused, so the `a`, `s`
  and `r` shortcuts work straight away.
- **Values use Indonesian formats** and are accepted back in the same format: `Rp 12.500.000`, NPWP as
  `01.234.567.8-901.000`, dates shown as `3 Mei 2024` under the ISO input. The server re-normalizes every correction.
- **Three field states beyond "weak".** A field is one of: untouched, checked (the reviewer moved past it with Enter),
  edited (the value differs from the model's). They are mutually visible:

  | State | Row | Right-hand cell |
  |---|---|---|
  | Untouched, confident | no mark | meter and score |
  | Untouched, weak | amber left edge, tint | meter and score in `--warn` |
  | Checked | amber edge and tint removed, thin `--ok` edge | text "sudah dicek" |
  | Edited | `--ink` edge and `--ink-tint` | text "diubah", meter hidden because the value is no longer the model's |
  | After saving a correction | no mark | text "dicek" |

  Checked is a mark on screen only. It is not sent to the server and is lost on reload.
- **The decision is a stamp.** After approve, correct or reject, the stamp appears on the top corner of the scan.
- **Technical detail is one click away and closed by default**: model, tokens, cost, versions, the duration of every
  pipeline stage, and the raw JSON.

### Source box

When a field is focused or hovered, a box marks the place in the scan where the value was read, and the scan scrolls
to centre it (hover only draws the box, it does not scroll). The weakest field is boxed when the document opens, and
"Lihat kolomnya" on a validation issue boxes its field.

- The box is an SVG layer over the scan image (`viewBox` 0 to 1, so the server's fractional positions are used as they
  are). It scales with zoom and with the stacked mobile layout.
- Colour: `--ink` stroke 2px with a 12% `--ink` fill for the focused field. `--warn` stroke and fill for the other
  weak fields when "Tandai kolom lemah" is on.
- A small label with the field name sits on the top edge of the focused box (below it when the box is at the top of
  the page), so colour is never the only signal. A line item row is labelled "Baris 2".
- Clicking a box focuses its field.
- The box is as tight as the OCR allows: the words of the value when the engine reports word positions (Tesseract
  does), the whole printed row otherwise.
- A field without a box says "Nama vendor: lokasi di scan tidak ditemukan." in the status chip at the left of the scan
  tools, only while that field is focused or hovered and has a value. It is not under the input, so nothing shifts.
  A box is only drawn when the match is certain: the server matches the value to one OCR row (using the printed label,
  such as "Subtotal" or "PPN", when two rows carry the same amount). No box is better than a wrong box.
- An edited field loses its box, because its value is no longer what was read from the document. Restoring the
  original value brings it back. Line items keep the position of the row they came from, so removing a row does not
  move the other boxes, and an added row has none.
- Boxes exist when the extraction ran OCR, which is the case for methods A and B. A document with no stored positions
  (older data, or method C) has no "Tandai kolom lemah" button and shows the same status line.
- The scan shown is the page OCR actually read (deskewed, and rotated when the orientation fallback ran), saved next
  to the upload, not the original file, so box coordinates match what is drawn. Documents processed before this
  change show the original page and no boxes.
- The scan tools hold the status chip, "Tandai kolom lemah" (a toggle, `aria-pressed`) and "Perbesar".

### Calculation check

A failed arithmetic or tax check is shown as a tape under the issue sentence, not as a sentence alone.
The figures below are an illustration of the layout (they come from a badly scanned demo faktur).

```
 Dasar pajak (DPP)         Rp 2.898.300.000
 x tarif PPN 11%
 ---------------------------------------------
 PPN seharusnya              Rp 318.813.000
 PPN di dokumen                Rp 3.188.130
 Selisih                     Rp 315.624.870
```

- Right-aligned `IBM Plex Mono` with `tabular-nums`. A hairline `--line-strong` sits above the result.
- The difference is `--err` when it is above the rule's tolerance and `--fg-muted` otherwise.
- Lines are the same for every rule that has expected values: PPN rate, faktur DPP, invoice and receipt totals, and
  the sum of line items. The rows come from the server's expected values, so the screen never recomputes tax rules.
- The line-items check has no terms: "Jumlah rincian barang", the document's figure, and the difference.
- Without expected values (older documents), the current plain sentence is shown instead.
- The server's exact English message stays in the tooltip of the issue.
- Errors are red-edged, warnings amber-edged. Each issue links to the field it concerns ("Lihat kolomnya").

### Keyboard

| Key | Where | Action |
|---|---|---|
| `j` / `k` | Outside inputs | Next / previous document in the queue |
| `a` / `s` / `r` | Outside inputs | Approve / save correction / reject |
| `/` | Outside inputs | Focus the outlined field, or the first one that needs checking |
| `Enter` | In a field | Mark it checked and move to the next field that needs checking |
| `Shift+Enter` | In a field | Move to the previous field that needs checking |
| `Ctrl+Enter` | In an address box | Same as `Enter` (plain `Enter` makes a new line). `Ctrl+Shift+Enter` goes back |
| Up / Down | In the line items table | Same column, previous / next row |
| `Esc` | In a field | Leave the field and return focus to the page |

"Needs checking" means: confidence below 0.8, not found, or named by a validation issue, in on-screen order.
The action bar says how far the reviewer is ("2 dari 5 kolom sudah dicek"). Approve is never blocked by unchecked
fields, so approving stays one keypress. Shortcuts do nothing while a text input has focus, except the ones above.

## Try a document

- The method picker is a three-way segmented control: **A OCR + aturan**, **B OCR + AI**, **C AI baca gambar**.
  A method the deployment cannot run is disabled and says why in its tooltip and in the note under the control. On
  the demo deployment only A is on.
- Samples are a grid of small cards with a thumbnail, document type, and scan quality shown as four bars plus a word
  (bersih, sedikit kotor, cukup kotor, sangat kotor). Quality is shown because it predicts the outcome.
- Upload accepts PDF, PNG, JPG and WEBP up to 10 MB by drag-and-drop or file picker. A line under the drop zone says
  not to upload real documents with a personal NPWP or NIK.
- While a document processes, a status card says whether it is waiting or being read, with the elapsed time. The
  workspace opens when the result arrives.

## Review queue

A dense table, one row per document: file, type, score bar against the threshold, why it needs checking, and age.
Rows are sorted by score, lowest first. Clicking a row or pressing `Enter` opens the workspace.

The "why" column names the weak fields, up to three, then a count: "Tanggal 0,62, NPWP penjual 0,58, dan 2 lainnya".
A weak field is a critical field, or the line items, with confidence below 0,80, weakest first; a field the reader did
not find reads "belum terbaca". Two fields join with "dan" ("Nama vendor 0,02 dan Rincian barang 0,75").
Server reasons stay machine-shaped; the browser words them. A document with no weak fields shows the validation count
instead ("2 pemeriksaan otomatis tidak lolos").

## Results and numbers

Four sections, each saying where its numbers come from:

1. **Download of approved data.** Counts and links for Excel and CSV (existing).
2. **This server** (live, from `/metrics`): documents processed, auto-approve rate, review outcomes, latency
   p50 and p95, total cost.
3. **Fields corrected most often** (from `changed_fields` of saved reviews): a table of the ten fields that
   reviewers changed most, with the number of corrections and the share of corrected documents. It tells the owner
   where the extractor is weakest. With no corrections yet, it says so and says what creates the data.
4. **Offline evaluation** (synthetic dev set, from `eval/reports/summary.json`): one row per method with field
   accuracy, document accuracy, line-item F1, auto-approve rate at the chosen threshold, cost per 1,000 documents and
   median latency.

No chart is drawn unless real data with a shape backs it. Tables are enough for all four.

## States

Every view has a loading, empty and error state. Copy is plain Indonesian, names what happened, and says what to do.

| View | Loading | Empty | Error |
|---|---|---|---|
| Samples | Grid of grey cards | "Server ini tidak menyertakan contoh dokumen." | The API message, with a retry hint |
| Upload | Status card with elapsed time | none | "File terlalu besar. Batasnya 10 MB. Coba scan yang lebih kecil atau satu halaman saja." |
| Queue | Table header with "Memuat" row | "Belum ada dokumen yang perlu dicek. ... Coba contoh dokumen untuk melihatnya." | Row with the API message |
| Workspace | "Dokumen ini masih dibaca. Halaman ini terbuka sendiri setelah selesai." | none | "Data tidak ditemukan. Mungkin sudah terhapus saat server dimulai ulang." with a link back |
| Source box | none | "lokasi di scan tidak ditemukan" under the field | Same line, never an error toast |
| Calculation check | none | Falls back to the plain sentence | none |
| Results and numbers | "Memuat" | "Belum ada dokumen yang disetujui. ..." and "Belum ada koreksi. ..." | The API message |

## Copy rules

- Plain Indonesian for everything a user reads. Code, README and this file stay English. Keep the official tax term
  in small text beside the plain label ("Dasar pajak" with "DPP").
- Name things the way a finance admin would: "Perlu dicek", "Disetujui otomatis", "tidak ditemukan".
- Buttons say exactly what happens: "Setujui", "Simpan 3 koreksi", "Tolak", "Unduh Excel". A toast repeats the
  button's word ("Disetujui.").
- Errors say what went wrong and what to do next. They do not apologise.
- No emoji, no arrows appended to buttons, no middle-dot meta strings, no em dashes in any user-facing text.

## Accessibility

- Colour is never the only signal. A weak field also carries the text "kurang yakin, tingkat yakin 0,62" for screen
  readers, a checked field says "sudah dicek", and a source box carries a text label.
- Text contrast is at least 4.5:1 in both themes, and the edge of every control is at least 3:1
  (`--line-strong`). The table above lists the measured values.
- Every control works from the keyboard, every input has a visible label, and the focus ring is always visible.
- The scan image has alt text naming the file and the document type. The source box layer is hidden from assistive
  technology because the same information is in the field's own text.
- At 390px width nothing scrolls horizontally except the line items table, which scrolls inside its own container.
  Touch targets are at least 40px high in the action bar and the queue.

## Changes from the current UI

| Change | Reason | Planned in |
|---|---|---|
| Enter and Shift+Enter move between fields that need checking, arrows in line items, "checked" and "edited" states, progress in the action bar | Audit: reviewers cannot sweep the flagged fields without a mouse | PR 1 |
| Tape-style calculation check with expected values from the server | Audit: the PPN message names no expected figure and no difference | PR 2 |
| "Why" column in the queue lists weak fields | Audit: only one reason is visible before opening a document | PR 2 |
| "Fields corrected most often" table | Audit: `changed_fields` is stored and never used | PR 2 |
| Source box on the scan, "Tandai kolom lemah", OCR-read page served as the scan | Audit: a field does not point to where it came from | PR 3 |
| One type family (IBM Plex Sans, Condensed for the stamp, Mono for values) in place of three faces | Fewer font requests, forms-and-ledger character, no pairing that reads as a default | PR 1 |
| Sentence-case labels in place of tracked uppercase labels in headers, chips and tags | Reads as a template tell | PR 1 |
| `--line-strong` on every control edge | The current `--line` edge is 1.4:1 on white, below the 3:1 needed for a control boundary | PR 1 |

Not changing: layout, colour tokens other than the one added, the stamp, the Indonesian copy, and the tabular
figures. Not building now: drawing a box on the scan to fill a missing field, a command palette, or a template
dashboard.

## Implementation notes

- Plain HTML, CSS and JavaScript in `web/` (no build step, no framework). FastAPI serves `web/index.html` at `/` and
  the assets under `/web`.
- The API additions this spec needs: `expected` on each validation issue, `weak_fields` on `GET /review/queue`,
  `corrections_by_field` on `GET /metrics`, and `sources` on the document result together with a preview that serves
  the OCR-read page. They are listed in the refactor plan and land with the PRs above.
- The single-container image runs uvicorn on `$PORT` (7860 on Hugging Face Spaces).
- Verification for every UI PR: tests and CI green, before and after screenshots (light, dark, 390px) in the PR
  description, a click-through of each new control, and the anti-slop Delivery Gate report.
