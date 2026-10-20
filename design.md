# docex review desk: UI design

This document defines the web UI that ships with docs-extractor. It replaces the earlier Streamlit page.
The UI is a single static app (`web/`) served by the FastAPI process, so the demo runs as one container on one port.

## Who uses it, and for what

**Primary user: a finance or tax admin who checks extractions.** They work through a queue of documents
the pipeline was not sure about. For each one they compare the scan with the extracted fields, fix what is
wrong, and approve or reject. They do this dozens of times a day, so speed and keyboard use matter more
than decoration.

**Secondary user: a hiring manager or engineer evaluating the portfolio.** They open the demo link once,
try a sample document, and look for evidence that the system is measured. They need the first screen to
work with no setup and no upload of their own data.

The page has three jobs, in this order:

1. Show one document and its extracted fields side by side, and make a correction take seconds.
2. Let a visitor run a synthetic sample (or upload a file) and see the result.
3. Show how the system performs, from live counts and from the offline evaluation.

## Information architecture

| View | Route | Purpose |
|---|---|---|
| Try a document | `#extract` | Sample gallery, upload, approach picker. Opens the workspace when a result is ready. |
| Review queue | `#queue` | Documents routed to review, lowest score first. The default view when the queue is not empty. |
| Workspace | `#doc-<id>` | Scan on the left, fields on the right, actions at the bottom. Shared by the try and review flows. |
| Metrics | `#metrics` | Live counts from `/metrics` and the offline A/B/C comparison from `summary.json`. |

A top bar holds the product name, the three main views, the queue count, and the default approach on
this deployment. There is no sidebar: the workspace needs the full width.

## The workspace

This is the screen that matters most. Everything else leads to it.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ← Queue   faktur-0002.jpg   FAKTUR PAJAK   approach A   score 0.64 / 0.77│
├───────────────────────────────┬──────────────────────────────────────────┤
│                               │ NEEDS REVIEW  low confidence on 'date'   │
│                               │ ! ppn_rate: PPN 11% expected ...         │
│      scan (zoom, fit width)   │──────────────────────────────────────────│
│                               │ Document     Nomor faktur  [0100...] 0.87│
│                               │              Tanggal       [2023-..] 0.76│
│                               │ Penjual      Nama / NPWP / Alamat        │
│                               │ Pembeli      ...                         │
│                               │ Jumlah       DPP / PPN / Total           │
│                               │ Line items   editable table              │
├───────────────────────────────┴──────────────────────────────────────────┤
│ reviewer [demo]        Reject (R)     Save correction (S)   Approve (A)  │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Split view.** The scan sits on the left and the fields on the right, each scrolling on its own. At
  widths under 900px the scan stacks above the fields and is capped at 60% of the viewport height.
- **Fields are grouped the way the paper document is.** For a faktur pajak the groups are: document (nomor
  faktur, tanggal), penjual (seller), pembeli (buyer), amounts (DPP, PPN, PPnBM, total), and line items.
  Invoices and receipts follow their own schema order. Labels show the English field name with the
  Indonesian term in smaller text where one exists, for example "Tax base · DPP".
- **Confidence is a property of each field, not a separate column of numbers.** Each field row has a
  four-step meter (0–0.5, 0.5–0.8, 0.8–0.95, 0.95+) and the exact value on hover. Fields below 0.8 get an
  amber left edge and the row background tint. Empty fields show "not found" in muted italic, not a blank
  input, so a reviewer can tell missing from empty.
- **Critical fields are marked.** The fields that decide auto-approval (from `configs/validation.yaml`)
  carry a small "critical" tag. The weakest critical field, the one named in the route reason, is
  scrolled into view and outlined when the workspace opens. It is not focused, so the `a`, `s` and `r`
  shortcuts work straight away; `/` moves into it.
- **Validation issues sit at the top of the field column**, each linked to the field it concerns where
  the rule names one. Errors are red, warnings amber.
- **Values are shown in Indonesian formats** and accepted back in the same format: `Rp 12.500.000`,
  NPWP as `01.234.567.8-901.000`, dates as `21 Feb 2023` in display and ISO in the input. The server
  re-normalizes every correction, so the reviewer can type `12.500.000` or `12500000`.
- **Changed fields are tracked on the client.** An edited field gets a blue "edited" mark, and the save
  button shows the count ("Save 3 corrections"). After saving, the server's `changed_fields` list is shown.
- **The decision is a stamp.** Once a document is approved, corrected, or rejected, a stamp (DISETUJUI,
  DIKOREKSI, DITOLAK) appears on the top corner of the scan in stamp-ink colour, the way a paper document is stamped
  at an Indonesian finance desk. This is the one decorative element in the product.
- **Corrected documents show the stored values.** After a correction, each field shows the value the
  reviewer saved and a "reviewed" label in place of the model's confidence.
- **Technical detail is one click away and closed by default**: route reason, usage (tokens, cost in USD
  and IDR, latency), prompt and model versions, trace spans, and the raw JSON.

### Keyboard

| Key | Action |
|---|---|
| `j` / `k` | Next / previous document in the queue |
| `a` | Approve |
| `s` | Save correction |
| `r` | Reject |
| `/` | Focus the outlined field (or the first low-confidence one) |
| `Esc` | Leave the field and return focus to the page |

Shortcuts do nothing while a text input has focus, except `Esc`.

## Try a document

- The approach picker is a three-way segmented control: **A · OCR + rules**, **B · OCR + LLM**,
  **C · Vision LLM**. An approach the deployment has no key for is disabled with the reason in its tooltip.
- Samples are shown as a grid of small cards, each with a thumbnail, document type, and image quality
  (clean, light, medium, heavy). Quality is shown because it predicts the outcome: the evaluation shows
  heavy scans pass far less often.
- Upload accepts PDF, PNG, JPG, and WEBP up to 10 MB, by drag-and-drop or file picker. A line under the
  drop zone says not to upload real documents with personal NPWP or NIK.
- While a document is processing, a status card shows whether it is waiting for a worker or being
  processed, with the elapsed time. The workspace opens when the result arrives, and its technical
  details list the real duration of every pipeline stage.

## Review queue

A dense table, one row per document: file name, type, score bar against the threshold, the route reason,
and age. Rows are sorted by score, lowest first. Clicking a row or pressing Enter opens the workspace.
Empty state: "Nothing to review. Documents scoring below the threshold appear here."

## Metrics

Two sections, clearly labelled with where the numbers come from:

1. **This deployment** (live, from `/metrics`): documents processed, auto-approve rate, review outcomes,
   latency p50/p95, total API cost.
2. **Offline evaluation** (synthetic dev set, from `eval/reports/summary.json`): a table with one row per
   approach covering field accuracy, document accuracy, line-item F1, straight-through rate at the chosen
   threshold, cost per 1,000 documents, and p50 latency. Below it, a link to the full report in the repo.

No chart is drawn unless it has real data behind it. With one approach evaluated, a table is enough.

## Visual language

The subject is paperwork: carbon-copy tax forms, rubber stamps, ledger columns. The UI borrows only two
things from that world. One is the stamp-ink blue-violet used for decisions and the primary action. The
other is tabular figures for every amount. The rest stays quiet so the scan is the most prominent thing
on screen.

### Colour tokens

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F3F4F7` | `#121419` | Page background (cool grey with a slight blue bias) |
| `--surface` | `#FFFFFF` | `#1A1D24` | Panels, inputs |
| `--surface-2` | `#E9EBF0` | `#232731` | Scan backdrop, table header, hover |
| `--line` | `#D5D9E1` | `#323744` | Borders and dividers |
| `--fg` | `#171A21` | `#E6E8EE` | Primary text |
| `--fg-muted` | `#5B6273` | `#9AA1B2` | Labels, secondary text |
| `--ink` | `#3B3FA8` | `#9EA3F5` | Stamp ink: primary action, focus ring, decisions, links |
| `--ok` | `#1E7A4C` | `#5CC48E` | Auto-approved, high confidence |
| `--warn` | `#A15C00` | `#E7A64A` | Low confidence, warnings |
| `--warn-tint` | `#FFF4E2` | `#2E2416` | Low-confidence row background |
| `--err` | `#B42318` | `#F07A6E` | Validation errors, reject |

Semantic colours (ok, warn, err) are only used for state, never for decoration. Both themes follow the
system setting.

### Type

| Role | Face | Use |
|---|---|---|
| Display | Bricolage Grotesque 600 | Product name, view titles, the stamp |
| Body | Instrument Sans 400/500/600 | Labels, copy, buttons |
| Data | IBM Plex Mono 400/500 | Field values, NPWP, amounts, scores, IDs |

Scale: 12 / 13 / 14 / 16 / 20 / 28 px. Body text is 14px because this is a working tool, not a page to
read. Field values use 14px mono with `tabular-nums` so amounts line up. Uppercase labels (document type,
group names) are 11–12px with 0.06em letter spacing.

### Shape and spacing

- 4px spacing unit; panels use 16px or 24px padding.
- Radius: 6px on inputs and buttons, 0 on the split panels (they are regions, not cards). Cards are used
  only in the sample gallery.
- No shadows except on the sticky action bar and on popovers.
- Focus: a 2px `--ink` outline with a 2px offset on every interactive element.
- Motion: 120ms colour and background transitions only. The stamp fades in over 160ms. Everything is
  instant under `prefers-reduced-motion`.

## Copy rules

- The interface speaks plain Indonesian, the way a finance admin talks: "Perlu dicek", "Disetujui otomatis",
  "tidak ditemukan", "Dasar pajak · DPP". Official terms stay as the small note under a field.
- Buttons say exactly what happens: "Setujui", "Simpan 3 koreksi", "Tolak".
- Errors say what went wrong and what to do: "Ukuran file ini 14 MB. Batasnya 10 MB." "Metode ini belum
  aktif di server ini karena kunci API belum dipasang. Coba metode A."
- Reviewers read field labels, never schema paths: "NPWP penjual", not `seller.npwp`. Server reasons and
  validation messages stay English and path-based (the repair prompt sends them to the model); the UI
  rewrites them and keeps the original in the tooltip.
- A failed check names the numbers it compared, in Indonesian format: "Subtotal dikurangi diskon ditambah
  pajak hasilnya Rp 2.424.780, tapi total di dokumen Rp 1.733.900."
- A document that needs review says what to do next. Status lines name who decided ("Dikoreksi oleh demo,
  2 kolom diubah."), and after a decision in the queue the toast says the next document is opening.
- No emoji in the interface.

## Accessibility

- Colour is never the only signal: low-confidence fields also get a text label read by screen readers
  ("low confidence 0.62"), and route state is written out next to its colour.
- Contrast is at least 4.5:1 for text in both themes.
- Every control is reachable by keyboard and every input has a visible label.
- The scan image has alt text naming the file and document type.

## Implementation notes

- Plain HTML, CSS, and JavaScript in `web/` (no build step, no framework). FastAPI serves `web/index.html`
  at `/` and the assets under `/web`.
- The API needs two small additions for the UI: `GET /samples/{name}/image` for gallery thumbnails, and
  `available_approaches` plus the scoring thresholds and critical fields in `GET /health`.
- Fonts load from Google Fonts with system fallbacks, so the UI still works offline.
- The single-container image runs uvicorn on `$PORT` (7860 on Hugging Face Spaces). Streamlit is removed.
