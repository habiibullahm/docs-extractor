# docex — Indonesian financial document extraction

Turn **faktur pajak, invoices and receipts (struk)** — PDFs, scans or phone photos — into validated,
structured JSON. Documents the system is not sure about go to a **human review queue** instead of being
silently accepted.

> All data in this repository is synthetic or public (CORD). No real NPWP/NIK is used anywhere.

**Live demo:** http://43.157.227.176:7860 (approaches A, B and C, synthetic samples, UI in Indonesian) · **Video:** _TODO_

---

## The business problem

Finance teams in Indonesia still key in thousands of faktur pajak, supplier invoices and expense receipts by
hand: copying NPWP numbers, DPP/PPN amounts and line items into the ERP or e-Faktur/Coretax. It is slow, and
the errors that slip through (a wrong NPWP, a PPN amount that does not match DPP × rate) cause failed
tax-credit claims and reconciliation work later.

Who it is for: AP/AR teams, tax staff (PPN input credit), expense management and bookkeeping services.

What matters is not "accuracy" in the abstract but two numbers:

* **Straight-through rate (STP)** — the share of documents that need no human touch *and* are correct.
* **Error rate among auto-approved documents** — must be very low, because nobody looks at those again.

Everything in this repo (the scoring, the threshold choice, the eval harness) is built around that trade-off.

## Architecture

```mermaid
flowchart LR
    U[Upload<br/>PDF / JPG / PNG] --> API[FastAPI<br/>POST /documents]
    API -->|job id| Q[(Queue<br/>Redis+RQ or in-process)]
    Q --> W[Worker]
    subgraph Pipeline
      direction LR
      P[preprocess<br/>PDF→image, orientation,<br/>deskew, resize] --> C[classify<br/>faktur / invoice / receipt]
      C --> X{extract}
      X -->|A| XA[Tesseract OCR<br/>+ regex/layout rules]
      X -->|B| XB[OCR text<br/>+ LLM structured output]
      X -->|C| XC[Vision LLM<br/>structured output]
      XA & XB & XC --> N[normalize<br/>Rp 1.250.000,00 → 1250000.0<br/>12 Januari 2025 → 2025-01-12]
      N --> V[validate<br/>line sum, totals, PPN rate,<br/>NPWP/NIK, dates]
      V -->|errors, B/C only| R[repair pass ×1<br/>prompts/repair_v1.md]
      R --> N
      V --> S[score<br/>min critical-field conf<br/>× validation penalties]
    end
    W --> P
    S -->|score ≥ threshold| AA[auto-approved]
    S -->|below / inconsistent| RV[review queue]
    AA & RV --> DB[(Postgres / SQLite<br/>results + model & prompt versions)]
    RV --> UI[review desk web UI<br/>scan left, fields right]
    UI -->|POST /review/id| DB
    DB -->|export_corrections| DS[(new eval / training data)]
```

| Stage | Code |
|---|---|
| Schemas (Pydantic v2, every field = value + confidence) | `src/docex/schemas/` |
| Preprocess | `src/docex/preprocess/image.py` |
| OCR providers (Tesseract, RapidOCR) | `src/docex/providers/ocr.py` |
| LLM/VLM providers (OpenAI default, Anthropic, any OpenAI-compatible local server, mock) | `src/docex/providers/llm.py` |
| Classify | `src/docex/classify/classifier.py` |
| Extract A / B / C + repair | `src/docex/extract/` |
| Normalize | `src/docex/normalize/` |
| Validate (rules in `configs/validation.yaml`, tax rules in `configs/tax_rules.yaml`) | `src/docex/validate/rules.py` |
| Score & route | `src/docex/scoring/score.py` |
| API / worker / DB | `src/docex/api/`, `src/docex/worker/`, `src/docex/db/` |
| Review UI (spec in `design.md`) | `web/` |
| Synthetic data | `synth/` |
| Evaluation | `eval/` |

## Review UI

The API serves a small review desk at `/` (plain HTML, CSS and JavaScript in `web/`, specified in
[`design.md`](design.md)). Documents that score below the threshold land in the review queue. The workspace
puts the scan next to the extracted fields, marks low-confidence and critical fields, and records the
reviewer's decision. Corrections are stored with the model and prompt versions that produced the original
output. Keyboard: `a` approve, `s` save correction, `r` reject, `j`/`k` next/previous, `/` edit the flagged field.
The metrics page also exports every approved document (auto-approved, approved or corrected) as an Excel
workbook or CSV files, with a sheet for documents and one for line items (`GET /export/approved`).
The interface text is in plain Indonesian for the finance admins who use it; the code and docs are in English.

![Review workspace: scan on the left, extracted faktur pajak fields with confidence on the right](docs/screenshots/workspace.png)

| Review queue | Try a document |
|---|---|
| ![Review queue sorted by score](docs/screenshots/queue.png) | ![Sample gallery and upload](docs/screenshots/try.png) |

## Results

Every number below is produced by `python -m eval.run` + `python -m eval.summarize` (nothing is typed by hand).
Full reports: [`eval/reports/`](eval/reports/) — each run has `report.md` (per field, per document type, per
image quality, threshold table), `threshold_curve.png` and `errors.md` (20 worst documents with images and causes).

### Dev split (used for threshold selection)

<!-- RESULTS:dev:START -->
Split `dev` · dataset `45bd8612abcc784c` · generated by `python -m eval.summarize` from `eval/reports/*/metrics.json`

| approach | n | field acc | doc acc (critical) | items F1 | hallucination | straight-through | auto-approve error | rec. threshold (dev curve) | cost / 1k docs | latency p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|
| [A · OCR + regex](eval/reports/A_dev_ci/report.md) | 115 | 90.5% | 71.3% | 0.856 | 1.1% | 2.6% | 0.0% | 0.77 | $0.03 (Rp548) | 2.5s / 4.3s |
| [B · OCR + LLM](eval/reports/B_dev_ci/report.md) | 115 | 95.4% | 79.1% | 0.978 | 1.1% | 0% (none safe) | – | None | $2.45 (Rp40,485) | 7.7s / 18.2s |
| [C · Vision LLM](eval/reports/C_dev_ci/report.md) | 115 | 98.2% | 94.8% | 0.968 | 0.8% | 0% (none safe) | – | None | $3.69 (Rp60,816) | 8.3s / 12.9s |

**Document accuracy (all critical fields correct) by image quality**

| approach | clean | light | medium | heavy |
|---|---|---|---|---|
| A · OCR + regex | 95.5% | 77.4% | 64.5% | 54.8% |
| B · OCR + LLM | 86.4% | 93.5% | 64.5% | 74.2% |
| C · Vision LLM | 95.5% | 93.5% | 100.0% | 90.3% |

**Field accuracy / document accuracy by document type**

| approach | faktur_pajak | invoice | receipt |
|---|---|---|---|
| A · OCR + regex | 95.0% / 97.1% | 84.7% / 33.3% | 94.7% / 85.4% |
| B · OCR + LLM | 93.8% / 80.0% | 94.6% / 59.0% | 98.7% / 97.6% |
| C · Vision LLM | 99.5% / 94.3% | 96.9% / 94.9% | 98.7% / 95.1% |
<!-- RESULTS:dev:END -->

### Locked test split (final numbers; nothing was tuned on it)

<!-- RESULTS:test:START -->
Split `test` · dataset `ca095be1085d07f7` · generated by `python -m eval.summarize` from `eval/reports/*/metrics.json`

| approach | n | field acc | doc acc (critical) | items F1 | hallucination | straight-through | auto-approve error | threshold (fixed on dev) | cost / 1k docs | latency p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|
| [A · OCR + regex](eval/reports/A_test/report.md) | 119 | 89.9% | 73.9% | 0.845 | 2.3% | 4.2% | 0.0% | 0.77 | $0.04 (Rp697) | 3.1s / 5.0s |

**Document accuracy (all critical fields correct) by image quality**

| approach | clean | light | medium | heavy |
|---|---|---|---|---|
| A · OCR + regex | 100.0% | 86.7% | 75.8% | 45.7% |

**Field accuracy / document accuracy by document type**

| approach | faktur_pajak | invoice | receipt |
|---|---|---|---|
| A · OCR + regex | 93.4% / 88.1% | 83.6% / 45.9% | 94.9% / 85.0% |
<!-- RESULTS:test:END -->

### Status of the comparison

All three approaches were evaluated on the same runner and the same 115 dev documents (`eval-full` workflow,
`gpt-4.1-mini` for B and C). The locked test split has only been run for A so far; C's final test numbers are
the next paid run. The dev dataset hash differs from the older local A runs because the synthetic images are
rendered with the runner's fonts, so compare rows within one table only.

The headline: **C reads documents best (94.8% of documents with every critical field right, against 71.3% for
A), but its confidence scores do not yet separate its few mistakes from its many correct answers.** At the
target of at most 1% wrong documents among auto-approved ones, no threshold qualifies for B or C, so both
currently route every document to review (`scoring.thresholds.B/C = 1.0`). For C, a threshold of 0.95 would
auto-approve 54% of documents with 3.2% of them wrong. The silent errors are single misread digits or swapped
date parts (`2026-03-22` read as `2022-03-26`, a 16-digit faktur number read with 17 digits), which the
model reports with high confidence. See [`C_dev_ci/errors.md`](eval/reports/C_dev_ci/errors.md).

## Technical decisions

**1. Three approaches behind one interface, compared on the same data.**
`Pipeline(approach)` swaps only the extract step; preprocessing, normalization, validation, scoring and the
eval are shared, so differences in the table come from the extractor alone. Providers are chosen in
`configs/pipeline.yaml` (`llm.provider`, `vlm.provider`, `ocr.provider`), including a local open-source VLM via
any OpenAI-compatible server (Ollama/vLLM).

**2. The model never "fixes" the numbers.** The extraction prompt ([`prompts/extract_v1.md`](prompts/extract_v1.md))
forbids computing missing values; the repair prompt ([`prompts/repair_v1.md`](prompts/repair_v1.md)) is called once
with the exact validation errors and must keep printed values, setting `document_inconsistent: true` instead.
Documents flagged inconsistent always go to review. About 4% of synthetic documents are generated *deliberately*
inconsistent (printed total ≠ items, PPN ≠ DPP × rate) and the eval counts auto-approving any of them as an
error, so a model that quietly "repairs" totals is penalised rather than rewarded. This is the most dangerous
failure mode of LLM extraction in finance: errors that pass validation because the model made them pass.

**3. Score = weakest critical field, not the average.** A document is only as trustworthy as its least
certain critical field (e.g. NPWP or PPN), so the score is `min(critical-field confidence, classifier confidence)`
× penalties (`× 0.25` if any validation error, `× 0.85` per warning). The auto-approve threshold is then picked
per approach from the dev-set curve: the highest straight-through rate with ≤ 1% errors among auto-approved
documents.

**4. Tax rules are configuration, not code.** NPWP lengths (15-digit legacy, 16-digit since 2024), faktur
serial formats (legacy 16-digit e-Faktur, 17-digit Coretax), and PPN schemes (11%; from 2025 12% with
DPP Nilai Lain 11/12 for non-luxury goods, and 12% on full price for luxury goods) live in
[`configs/tax_rules.yaml`](configs/tax_rules.yaml). Every rule I could not verify against a DJP primary source is
marked `TODO(verify)`.

**5. Evaluation is strict and reproducible.**
Fields are compared after normalization (numbers with ±1 IDR / 0.1% tolerance, IDs digit-exact, text by fuzzy
ratio ≥ 90). Line items are matched one-to-one and scored with precision/recall/F1. A document only counts as
correct for STP when all critical fields *and* all line items are right. The test split is locked
(`data/test_lock.json`); `eval/run.py` refuses `--split test` without `--final`, and all tuning (OCR page
segmentation mode, heuristics) was done on train/dev only.

**6. Baseline A tuning, documented.** Three preprocessing choices carry A to 91.8% field accuracy
on a train sample: removing table borders before OCR, flattening uneven illumination / inverting white-on-dark
headers, and Tesseract `--psm 6`. `eval/experiments/ocr_settings.sh` reruns the comparison on 60 train
documents: psm 6 91.8% field / 75.0% doc accuracy, psm 4 90.3% / 68.3%, psm 3 82.9% / 45.0%, psm 11
64.5% / 16.7% (reports in `eval/reports/exp_*`). RapidOCR (PaddleOCR models on ONNX) is supported as an
alternative OCR engine but scored much lower with the same heuristics (54.8% / 10.0%) and was ~5× slower on CPU,
because the regex rules were written against Tesseract's line layout.

**7. C is the main path, A is the free fallback.** On the dev set C has the highest document accuracy
(94.8% against 79.1% for B and 71.3% for A) and degrades least on dirty scans (90.3% on heavy noise against
54.8% for A). B is a weaker middle ground: it inherits OCR mistakes, and on faktur pajak it is worse than A
(80.0% against 97.1% document accuracy), because the regex rules for faktur fields are already precise and the
LLM sometimes rewrites what OCR read. A stays as the fallback because it is free, runs offline, and is still the
most reliable extractor for clean faktur pajak. The open problem is calibration: C needs confidence that
predicts its errors (for example cross-checking each value against the local OCR text) before it can
auto-approve anything at a 1% error target.

## Cost analysis (per 1,000 documents)

Costs are computed per document from token usage × the price table in `configs/pipeline.yaml`, plus CPU time
at an assumed $0.05 per vCPU-hour; IDR uses the rate in the same file (`usd_to_idr`, TODO verify).

* **A (local OCR + rules):** about **$0.05 (≈Rp 900) per 1,000 documents** of compute, no API cost —
  but most documents still need a human.
* **B (OCR + `gpt-4.1-mini`):** **$2.45 (≈Rp 40,500) per 1,000 documents**, p50 latency 7.7s.
* **C (vision `gpt-4.1-mini`):** **$3.69 (≈Rp 60,800) per 1,000 documents**, p50 latency 8.3s.

Prices come from the table in `configs/pipeline.yaml` (TODO verify against current OpenAI pricing). Other
models are one flag away (`python -m eval.run --approach C --model gpt-4.1`), and Claude
(`provider: anthropic`) is one config change away; measure before choosing.

The useful number for a business case is *cost per correctly processed document including review time*:
if a reviewer costs Rp 1,000 per document, raising STP from 5% to 70% saves far more than any API bill here.

## Limitations and failure examples

* **Synthetic data.** Templates are realistic but not exhaustive; real faktur pajak exports, crumpled thermal
  receipts and handwritten notes will be harder. CORD (real Indonesian receipts) is supported by
  `python -m docex.ingest.cord` but is downloaded from Hugging Face, which was not reachable from the build
  environment; the CI `cord-eval` job runs it.
* **Approach A failure modes** (from [`errors.md`](eval/reports/A_dev/errors.md)):
  heavily degraded photos where OCR confidence drops below ~0.75 (vendor names read as "ray", "Pijet");
  invoices misclassified as receipts when the header is unreadable; Roman-numeral invoice numbers misread
  (`INV/2026/V11I/539`); multi-line addresses truncated to the first line.
* **Single page.** Up to three pages are processed by stacking them; long multi-page invoices are not handled.
* **Fine-tuning (phase 7)** was skipped: it needs a GPU and the B/C results first, to know whether a small
  model (Donut / a small VLM with LoRA) has a gap worth closing.

## Running it

```bash
# local dev
python -m venv .venv && . .venv/bin/activate
pip install -e ".[api,eval,synth,dev]" && playwright install chromium
sudo apt-get install tesseract-ocr tesseract-ocr-ind     # or brew install tesseract tesseract-lang

python -m synth.build --per-type 200      # 600 synthetic documents + ground truth + manifest/split
pytest -q
python -m eval.run --approach A --split dev
uvicorn docex.api.app:app --port 8000     # review UI: http://localhost:8000 , API docs: /docs

# everything (API + UI, RQ worker, Postgres, Redis)
cp .env.example .env    # optional: OPENAI_API_KEY for approaches B/C
docker compose up --build   # review UI http://localhost:8000, API docs http://localhost:8000/docs
```

API:

| Method | Path | |
|---|---|---|
| POST | `/documents` | multipart `file` (+ optional `approach`, `doc_type`) → `{job_id}` · 10 MB limit, type sniffing, rate limit |
| GET | `/documents/{id}` | status, extraction with confidences, validation issues, score, route, versions, cost, trace |
| GET | `/review/queue` | documents routed to review |
| POST | `/review/{id}` | `{"action": "approve" \| "correct" \| "reject", "values": {...}}` |
| GET | `/metrics` | live counts, auto-approve rate, latency, cost, reviews + latest offline eval |
| GET | `/export/approved` | approved documents (auto-approved, approved or corrected; corrected values win) as `format=xlsx` (sheets `Dokumen` and `Rincian barang`) or `format=csv` (`part=documents` or `part=items`), optional `doc_type`; `/export/approved/count` for the totals |
| GET | `/` | review UI: try a sample or upload, review queue, workspace, metrics |

Human corrections are stored with the model and prompt version that produced the original extraction, and
`python -m docex.db.export_corrections` turns them into new labeled documents for evaluation and training.

### Observability and security

* One structured JSON log line per document: approach, model, prompt version (`extract_v1@<hash>`), tokens,
  cost, latency, validation results, route.
* Per-stage trace (preprocess, ocr, classify, extract, normalize, validate, repair, score) with durations,
  returned by the API and shown in the UI.
* NPWP/NIK are masked in logs and traces (`01***********34`).
* Prompts are versioned files in `prompts/`; the version id includes a content hash.
* Upload size limit, file-type sniffing by magic bytes, per-IP rate limit.

### CI

* `ci.yml` — unit tests; **eval regression** on a frozen 36-document subset (`eval/ci_data/`), failing if
  field / document accuracy drops more than 2 points below `eval/baselines/`; approach C is included
  automatically when the `OPENAI_API_KEY` secret exists; Docker build + smoke test; optional CORD eval.
* `eval-full.yml` — manual: rebuild the dataset and evaluate A, B and C on the same runner.
* `deploy.yml` — pushes the app to a Hugging Face Docker Space when `HF_TOKEN` is set.

### Deploy

The demo runs as a single container (the API also serves the review UI, `deploy/start.sh`) on a VPS with
synthetic samples. On the server, clone the repository and run:

```bash
cd ~/docs-extractor && git pull && bash deploy/vps.sh   # PORT=8080 to change the port
```

`deploy/vps.sh` builds the image, restarts the `docex` container with a `docex-data` volume, waits for
`/health` and prints the URL. It uses `sudo docker` when the user is not in the `docker` group. Without a key
only approach A runs, at no API cost. To enable B and C, put `OPENAI_API_KEY=...` in `~/docs-extractor/.env`
on the server (the script passes it with `--env-file` and never prints it). The public demo has B and C on, so
each B/C run spends API credits (about $0.002 to $0.004 per document); the API is rate-limited per IP. Clone with git on the server rather than copying files from Windows:
`.gitattributes` keeps shell scripts on LF line endings.

`deploy.yml` can also push the same container to a Hugging Face Docker Space when the `HF_TOKEN` secret is set.

## Repository layout

```
configs/      pipeline, validation rules, tax rules (TODO-verify items)
data/         manifest + test lock only (datasets are rebuilt, not committed)
prompts/      versioned prompts (extract_v1, repair_v1, classify_v1)
src/docex/    ingest, preprocess, classify, extract, normalize, validate, scoring,
              providers, api, worker, db, schemas, observability, pipeline
synth/        synthetic generator + 7 HTML templates + degradations
eval/         harness, metrics, reports, CI subset and baselines
web/          review UI (plain HTML/CSS/JS, served by the API; design in design.md)
tests/        unit, pipeline (mock LLM), API, dataset tests
```
