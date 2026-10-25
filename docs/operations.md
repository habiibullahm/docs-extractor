# Running and operating docex

Setup, API, CI, cost control, the public-demo limits and deployment. For what docex is and how to use it, see the [README](../README.md).

## Run locally

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
cp .env.example .env    # optional: GEMINI_API_KEY + DOCEX_ENABLE_LLM=true for B/C (free tier, see Cost control)
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
  field / document accuracy drops more than 2 points below `eval/baselines/`; no paid model API is
  called in CI; Docker build + smoke test; optional CORD eval.
* `eval-full.yml` — manual: rebuild the dataset and evaluate the approaches picked in the run (default A). Picking
  B or C turns on `DOCEX_ENABLE_LLM` for that run only and uses the `GEMINI_API_KEY` secret (see Cost control).
* `deploy.yml` — pushes the app to a Hugging Face Docker Space when `HF_TOKEN` is set.

### Cost control

Approaches B and C call a hosted model API. They are **off by default**: an API key alone does nothing.
`docex` only calls Gemini, OpenAI or Anthropic when `DOCEX_ENABLE_LLM=true` is also set. CI never sets it,
`eval-full` sets it only when B or C is picked by hand in that run, and the demo server only has it when it is
in the server's `.env`. The B/C numbers in [technical.md](technical.md#results) come from one `eval-full` run on 115 dev documents.

The default provider is the **Gemini API free tier** (`GEMINI_API_KEY`). It costs nothing only under one
condition: the key comes from a Google AI Studio project that has **no billing account attached**. Without
billing, a request over the free limit is rejected with HTTP 429 and nothing is charged; the provider retries
a 429 a few times, then marks the document as failed. Once billing is turned on for that project, the same
calls are charged at paid-tier prices. Other points about the free tier:

* Google may use free-tier prompts and responses (here: the document images and OCR text) to improve its
  products, and human reviewers may read them. Send synthetic documents only, never real faktur pajak with
  personal NPWP/NIK.
* The limits are per project, differ per model and change over time; check them in AI Studio. `docex` spaces
  requests 6 s apart (`gemini.min_interval_s`) to stay under about 10 requests per minute.
* Approach C makes 2 to 3 requests per document (classify, extract, sometimes repair), so a full dev eval
  (115 documents) takes roughly 230 to 345 requests and may run into the daily limit. Start with `--limit 10`.

For OpenAI or Anthropic (paid), set a monthly spend limit in the provider's billing settings before turning
`DOCEX_ENABLE_LLM` on.

### Running it as a public demo

Anyone can try the demo for free, so the API is built for visitors it does not know. The audience is people looking
at a portfolio, not finance teams: there is no login, and the UI asks visitors not to upload real documents.

* **Private per visitor.** A random cookie (`docex_sid`, HttpOnly, SameSite=Lax) marks the browser; every document
  carries that id and the list, review queue, metrics and export show only the visitor's own. Someone else's
  document returns the same 404 as a missing one.
* **Deleted automatically.** A background task removes the file, the page image and all rows older than
  `retention_minutes`, at startup and every 15 minutes. A job still running is never removed under the worker.
* **Caps** (in `configs/pipeline.yaml` under `limits`, overridden with `DOCEX_LIMITS__<KEY>`):

| Limit | Default | Protects | Error |
|---|---|---|---|
| `retention_minutes` | 120 | privacy, disk | documents disappear |
| `session_max_docs` | 20 per visitor in the window | one visitor filling the disk | 429 `session_limit` |
| `storage_max_mb` | 500 | the VPS disk | 503 `storage_full` |
| `llm_daily_docs` | 20 B/C documents per UTC day | the free Gemini quota | 429 `llm_daily_cap`, B and C show as paused |
| `rate_limit` | `30/minute;30/hour` per IP | a script that sends no cookie (the session cap cannot see it) | 429 |

Approach A is never capped by the daily limit. The daily B/C count lives in its own `llm_usage` table, so the cleaner
deleting old documents does not reset it. The cap check and the insert run under one lock, so concurrent requests
cannot overshoot a cap. The 10 MB size cap and the magic-byte type check stay. `storage_max_mb` counts uploaded
files, not the cached page images, so real disk use is a little higher.

* **Behind a proxy.** Set `DOCEX_TRUST_PROXY=true` when a front proxy sits in front of the API, so the rate limit
  uses the first `X-Forwarded-For` address and the cookie gets `Secure` from `X-Forwarded-Proto`. Leave it off for a
  direct deployment: otherwise a visitor could spoof the header. The flag trusts whoever can reach the port, so
  firewall the origin to the proxy, or a script that hits the server's address directly can pick its own address.
* **Scripts need the cookie.** A client without a cookie jar is a new visitor on every call, so it cannot read its
  own result: `curl -c jar -b jar -F file=@scan.pdf http://HOST/documents`, then `curl -b jar http://HOST/documents/ID`.

Not handled on purpose: accounts, per-user keys and a privacy policy for real tax data. The demo is plain HTTP until
it has a domain (see Public URL on Vercel).

### Deploy

The demo runs as a single container (the API also serves the review UI, `deploy/start.sh`) on a VPS with
synthetic samples. On the server, clone the repository and run:

```bash
cd ~/docs-extractor && git pull && bash deploy/vps.sh   # PORT=8080 to change the port
```

`deploy/vps.sh` builds the image, restarts the `docex` container with a `docex-data` volume, waits for
`/health` and prints the URL. It uses `sudo docker` when the user is not in the `docker` group. The demo runs
approach A by default. B and C need both `GEMINI_API_KEY` and `DOCEX_ENABLE_LLM=true` in `~/docs-extractor/.env`
on the server (the script passes the file with `--env-file` and never prints it; see Cost control). Every
B/C document a visitor runs uses the free-tier quota, and the API is rate-limited per IP. Clone with git on the server rather than copying files from Windows:
`.gitattributes` keeps shell scripts on LF line endings.

If Gemini answers `User location is not supported for the API use`, Google rejects the VPS's network (the check
uses the IP the call leaves from). Set `GEMINI_BASE_URL` in the same `.env` to a reverse proxy that forwards
`/v1beta/*`; `deploy/gemini-proxy/` has a Cloudflare Worker (the live demo uses one) and an nginx example. The
Worker holds no key. Open its address first: it must show `gemini-proxy ok`.

When Gemini answers 429 (free quota used up) or the location error, the API pauses B and C for
`DOCEX_LLM_COOLDOWN_S` seconds (default 900). During the pause `/health` lists them under `paused_approaches`
instead of `available_approaches`, uploads with B or C get a 400 that says "paused", and the review desk greys
them out and selects A. They come back by themselves when the pause ends. The pause lives in the server process,
so a restart clears it.

`deploy.yml` can also push the same container to a Hugging Face Docker Space when the `HF_TOKEN` secret is set.

### Public URL on Vercel

Vercel only fronts the demo (HTTPS and a domain). Tesseract and the FastAPI app keep running in the VPS container,
because Vercel cannot run them. `deploy/vercel/vercel.json` has one rewrite that sends every path to the origin:

```
visitor  ->  https://<project>.vercel.app  ->  rewrite  ->  http://43.157.227.176:7860  (docex container)
```

1. In Vercel, import this repository and set **Root Directory** to `deploy/vercel`. No build command, no framework.
2. Deploy once, then run `bash deploy/vercel/check.sh https://<project>.vercel.app` from the repository root. It checks
   `/health`, an approach-A upload with the cookie round trip, that a visitor without the cookie gets 404, and a
   9.5 MB upload. Vercel does not document whether a rewrite accepts an `http://IP:port` origin or what body size it
   passes, so run this before sharing the link.
3. On the VPS add `DOCEX_TRUST_PROXY=true` to `.env` and run `bash deploy/vps.sh`, so the rate limit sees visitors'
   addresses and the cookie is `Secure`. Do this only after step 2 passes: with the flag on and no proxy in front,
   anyone can spoof their address.
4. Add the domain in Vercel (Settings, Domains) and put the https link in the README.

Limits to know: Vercel waits up to 120 seconds for the origin to start answering (older docs say 30). Vercel's
addresses are not fixed, so the origin cannot be firewalled to them; a script that calls `http://43.157.227.176:7860`
directly can send its own `X-Forwarded-For`. The per-session cap, the storage cap and the daily B/C cap do not depend
on that header, so the worst case is a bypassed per-IP rate limit. The hop from Vercel to the VPS is plain HTTP; the
demo only handles synthetic documents. If step 2 fails, point the domain's A record at the VPS and run Caddy there
instead.

## Repository layout

## Repository layout

```
configs/      pipeline, validation rules, tax rules (TODO-verify items)
data/         manifest + test lock only (datasets are rebuilt, not committed)
prompts/      versioned prompts (extract_v1, repair_v1, classify_v1)
src/docex/    ingest, preprocess, classify, extract, normalize, validate, scoring,
              providers, api, worker, db, schemas, observability, pipeline
synth/        synthetic generator + 7 HTML templates + degradations
eval/         harness, metrics, reports, CI subset and baselines
docs/         technical notes, operations guide, README screenshots
web/          review UI (plain HTML/CSS/JS, served by the API; design in design.md)
tests/        unit, pipeline (mock LLM), API, dataset tests
```
