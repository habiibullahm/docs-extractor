# Public showcase mode: design

Date: 2026-10-21. Status: draft for Nash's review. No code has been written.

## Intent

Nash wants docex to be free for anyone to try, as an AI project showcase in a portfolio. The visitors are people
who look at a portfolio: recruiters, other developers, curious users. They try the demo with the bundled samples
or with fake documents. This is not a service for real finance or tax work.

Success looks like this:

* A visitor can open the demo and see a result in under a minute, with no sign-up.
* One visitor never sees another visitor's uploads.
* Uploaded files do not stay on the server for longer than the stated retention time.
* One visitor, or a script, cannot fill the disk or use up the shared free Gemini quota.
* The project costs nothing to run (no paid API, see the project's cost rule).
* The README explains what was thought through, so the repo itself shows engineering judgment.

Out of scope: login, accounts, payments, per-user API keys, a legal privacy policy for real tax data,
setting up the Vercel domain (later, see Open questions).

## What exists today

* `POST /documents` already checks size (`limits.max_upload_mb`, 10), file type by magic bytes
  (`limits.allowed_types`) and a per-IP rate limit (`limits.rate_limit`, 30/minute, slowapi).
* `/health` reports `max_upload_mb` and `paused_approaches`.
* Approach A is the default; B and C are behind `DOCEX_ENABLE_LLM` and pause for 15 minutes after a Gemini 429.
* The UI says uploaded files are visible to everyone and asks visitors not to upload real documents.

Gaps this design closes:

1. Uploaded files and database rows are never deleted.
2. `GET /documents`, `/review/queue`, `/metrics`, `/export/approved` and the per-document routes are global,
   so every visitor sees every upload.
3. No cap on total stored bytes, documents per visitor, or daily B/C use.

## Approach

Anonymous per-visitor sessions with automatic deletion. A visitor gets a random cookie, owns the documents they
create, and sees only those. A background cleaner deletes everything older than the retention time.

Rejected alternatives: a shared pool with auto-delete only (visitors still see each other's files), and no
server-side storage at all (the review queue and export would need a rewrite for a demo).

## Design

### 1. Session identity

* Middleware reads cookie `docex_sid`. If it is missing or malformed (not 32 hex chars), it issues a new random
  `uuid4().hex` and sets the cookie: `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` = retention time.
  `Secure` is set only when the request arrived over https (`request.url.scheme` or `X-Forwarded-Proto`), because
  the demo is plain HTTP today.
* The id is stored as-is in `documents.owner_id`. It is not a credential for anything except seeing one's own
  short-lived demo uploads, so no signing or hashing is needed. Guessing another id is infeasible (128 bits).
* The session is exposed to routes as `request.state.sid`.

### 2. Ownership and scoping

* New column `documents.owner_id` (String(32), indexed, nullable).
* `_create(...)` takes the owner id and stores it.
* Every route that reads documents filters by `owner_id == sid`: `GET /documents`, `GET /documents/{id}`,
  `GET /documents/{id}/preview`, `GET /review/queue`, `POST /review/{id}`, `GET /metrics` (counts, routes,
  latency, corrections), `GET /export/approved` and its `/count`. A document owned by someone else returns 404,
  the same as a missing one, so ids cannot be probed.
* `GET /metrics` keeps `offline_eval` global (it comes from `eval/reports/summary.json`, not from uploads).
* `/samples`, `/samples/{name}/image` stay public. `POST /samples/{name}` creates a document owned by the caller.
* Rows with `owner_id` NULL (created before this change) are visible to nobody and are deleted by the cleaner once they pass the retention time, like any other row.
* Migration: `Base.metadata.create_all` does not add columns to an existing table, and the VPS keeps its SQLite file
  in volume `docex-data`. `init_db` therefore checks `PRAGMA table_info(documents)` (or the SQLAlchemy inspector for
  Postgres) and runs `ALTER TABLE documents ADD COLUMN owner_id VARCHAR(32)` plus the index when it is missing.

### 3. Retention

* Config `limits.retention_minutes` (default 120), env override `DOCEX_LIMITS__RETENTION_MINUTES` (the existing `DOCEX_<SECTION>__<KEY>` convention; it only works for keys that exist in `configs/pipeline.yaml`).
* `purge_expired()` deletes documents with `created_at` older than the cutoff: the stored file, the cached
  page-read image (`read_page_path`), then reviews, extractions and the document row. It skips documents whose
  status is `queued` or `processing` and younger than 30 minutes, so an active job is never pulled out from under
  the worker. Anything older than that and still unfinished is treated as stuck and deleted.
* It runs once at startup and then every 15 minutes from a background asyncio task started in the existing
  `startup` hook. A failure is logged and never stops the loop.
* `/health` reports `retention_minutes`. The UI shows it in the banner.
* The compose setup (API and worker share the `uploads` volume) works unchanged: only the API process purges, and
  the worker only reads files of live jobs.

### 4. Abuse limits

All three are in `configs/pipeline.yaml` under `limits`, overridable by env, and return a clear HTTP error that
the UI shows in plain Indonesian.

| Limit | Default | Error |
|---|---|---|
| Documents per session within the retention window | 20 | 429 `session_limit` |
| Total size of stored documents (`sum(documents.size_bytes)`) | 500 MB | 503 `storage_full` |
| B/C documents per day, across all visitors | 20 (about 60 free-tier requests) | 429 `llm_daily_cap`, and `/health` lists B and C in `paused_approaches` |

The existing per-IP rate limit and 10 MB cap stay. Counts come from the database, so they survive restarts.
`llm_daily_cap` is counted from `documents.created_at` with approach B or C since 00:00 UTC.
Approach A remains the default and is never capped by the daily limit.

### Behind a proxy

If the demo is later reached through a front proxy such as Vercel, every request reaches the VPS from the
proxy's address. Two existing behaviors would break:

* The rate limit uses `get_remote_address`, so all visitors would share one bucket. The limiter key becomes the
  first address in `X-Forwarded-For`, but only when `DOCEX_TRUST_PROXY=true` (default false), so a direct visitor
  cannot spoof their address.
* `Secure` on the cookie follows `X-Forwarded-Proto` under the same flag.

The session cookie is unaffected: it is set by the backend and passed through on the same origin.

### 5. Honest copy

The privacy line under the upload box becomes: files are private to this browser, are deleted automatically after
N hours, and real documents with NPWP or NIK must still not be uploaded. N comes from `/health`. Copy stays in
plain Indonesian, matching the existing UI.

### 6. Portfolio material

* README gets a section "Running it as a public demo": who the visitors are, what each limit protects against,
  what is deliberately not handled (no login, plain HTTP until a domain exists), and how to change the numbers.
* The "Live demo" line in the README states the retention time.

## Testing (test-first)

Each behavior below gets a failing test in `tests/test_api.py` or a new `tests/test_public_mode.py` before the code:

* A new visitor gets a `docex_sid` cookie; a malformed cookie is replaced.
* Visitor B cannot read, preview, review or export visitor A's document (404), and does not see it in the list,
  queue or metrics counts.
* A pre-migration database without `owner_id` gets the column added and keeps working.
* `purge_expired` removes file, cached page image and rows for old documents, keeps young ones, keeps a young
  `processing` document, and removes a stuck one.
* The per-session cap, the storage cap and the daily B/C cap each return their error and stop at the right count.
* `Secure` is set on the cookie only for https requests.
* With `DOCEX_TRUST_PROXY=true`, the rate limit and `Secure` follow `X-Forwarded-For` / `X-Forwarded-Proto`;
  with it off, those headers are ignored.

CI stays free: tests use approach A and mocked providers.

## Open questions

* Domain: Nash chose a Vercel domain for the live demo, to be set up later (not part of this spec's code).
  Vercel cannot run this backend (Tesseract, a long-running FastAPI process, files on disk), so the backend stays on
  the VPS and Vercel would only front it, for example with a rewrite from the `*.vercel.app` domain to the VPS. That gives
  HTTPS on the visible URL. Two things in this spec are written with that in mind, see "Behind a proxy". Whether
  Vercel's rewrite accepts a 10 MB upload and a 60+ second request must be checked when that task starts.
* Retention of 2 hours and the 20 / 500 MB / 20 numbers are starting values. They are config, so changing them
  needs no code change.
