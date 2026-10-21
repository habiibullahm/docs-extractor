# Public Showcase Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let strangers try the docex demo for free without seeing each other's uploads, with automatic deletion and caps that protect the disk and the free Gemini quota.

**Architecture:** A middleware gives each browser an anonymous `docex_sid` cookie. Documents get an `owner_id`, every read route filters by it, a background task purges documents past the retention time, and three DB-backed caps guard uploads. Limits live in `configs/pipeline.yaml` under `limits`.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite/Postgres), slowapi, pytest + TestClient, plain HTML/JS in `web/`.

**Spec:** `docs/superpowers/specs/2026-10-21-public-showcase-design.md`

## Global Constraints

* No paid API and no real Gemini call anywhere: tests use approach A and env flags only.
* Commits and PRs: no `Co-Authored-By` or `Claude-Session` trailers, no "Generated with Claude Code" footer, no assignee or reviewer. The PR body keeps only the platform's two-line "Requested by" block at the top. Merge with a merge commit, not squash.
* UI copy is plain Indonesian; code, comments and README stay English.
* New limits are config keys in `configs/pipeline.yaml` `limits:`; env override is `DOCEX_LIMITS__<KEY>` (the override only works for keys that already exist in the YAML).
* Defaults: `retention_minutes: 120`, `session_max_docs: 20`, `storage_max_mb: 500`, `llm_daily_docs: 20`. Cleaner runs at startup and every 900 s; a `queued`/`processing` document younger than 30 minutes is never purged.
* Cookie: `docex_sid`, 32 lowercase hex chars, `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` = retention seconds, `Secure` only for https. `DOCEX_TRUST_PROXY` (default off) makes the client IP and https detection follow `X-Forwarded-For` (first entry) and `X-Forwarded-Proto`.
* Error codes are the `detail` string: 429 `session_limit`, 503 `storage_full`, 429 `llm_daily_cap`. A document owned by someone else is a 404 identical to a missing one.
* All timestamps are UTC.
* Any UI change needs before/after screenshots in the PR description and the thread reply.

## Review Focus

* A client with no cookie jar (plain `curl`) uploads and then polls: it gets a fresh session each call and a 404. Expected: the upload response carries `Set-Cookie`, so a client that keeps it works; README documents `curl -c/-b`. (Task 1, Task 7)
* The cleaner meets a document whose file is already gone, or a job still running. Expected: no exception, the row is still removed, a young running job is kept. (Task 4)
* Garbage in `Cookie` (1000 characters, non-hex, uppercase) and in `X-Forwarded-For` (empty, `unknown`, junk). Expected: cookie replaced; with trust on, a junk first entry falls back to the socket address; with trust off the header is ignored. (Task 1)
* The daily B/C cap reached while A keeps working, and the count restarting at 00:00 UTC. Expected: A uploads succeed, B/C refused with `llm_daily_cap`, yesterday's B/C documents not counted. (Task 5)
* Rows from before the change (`owner_id` NULL) after the migration. Expected: invisible to every visitor, deleted by the cleaner once past retention, the column added without data loss. (Task 2, Task 4)

---

## File Structure

* Create `src/docex/api/session.py`: cookie middleware, `client_ip`, `is_https`, `trust_proxy`.
* Create `src/docex/db/retention.py`: `purge_expired`, `purge_loop`.
* Modify `src/docex/db/models.py`: `Document.owner_id`, migration in `init_db`, owner filter in `review_queue`.
* Modify `src/docex/api/app.py`: wire middleware and loop, owner scoping, caps, `/health` fields.
* Modify `src/docex/api/export.py`: owner filter in `approved`.
* Modify `configs/pipeline.yaml`, `.env.example`, `web/index.html`, `web/app.js`, `README.md`.
* Create `tests/test_public_mode.py` with a `make_client(**env)` fixture (same reload pattern as `tests/test_api.py::client`, plus extra env set before the reload, and a helper `png()` copied from `test_api.py`).

---

### Task 1: Session identity and proxy-aware client address

**Files:**
- Create: `src/docex/api/session.py`, `tests/test_public_mode.py`
- Modify: `src/docex/api/app.py` (register middleware, limiter key), `configs/pipeline.yaml`

**Interfaces:**
- Produces: `SID_COOKIE = "docex_sid"`; `valid_sid(value: Optional[str]) -> bool`; `trust_proxy() -> bool`; `client_ip(request: Request) -> str`; `is_https(request: Request) -> bool`; `async def session_middleware(request, call_next)`; `request.state.sid: str` on every request; `current_sid(request: Request) -> str`.
- Produces (config): the four new `limits` keys with the defaults above, so later tasks can read them.

- [ ] **Step 1: Write failing tests** in `tests/test_public_mode.py`:
  `test_new_visitor_gets_cookie` (GET `/health` sets `docex_sid`, 32 hex, `httponly`, `samesite=lax`, no `secure`);
  `test_valid_cookie_is_kept_and_bad_ones_replaced` (a valid cookie sends no `Set-Cookie`; values `"x"*1000`, `"ABC..."` uppercase, non-hex are replaced);
  `test_secure_only_on_https` (request with `base_url="https://testserver"` gets `Secure`);
  `test_forwarded_headers_ignored_unless_trusted` (default: `X-Forwarded-Proto: https` gives no `Secure`; with `DOCEX_TRUST_PROXY=true` it does);
  `test_rate_limit_per_forwarded_ip` (`DOCEX_LIMITS__RATE_LIMIT=2/minute`, trust on: three uploads from `X-Forwarded-For: 1.1.1.1` end in 429, then one from `2.2.2.2` is 202; with trust off, the second address shares the first bucket);
  `test_junk_forwarded_for_falls_back_to_socket_address` (trust on, header `unknown` and empty both count as the socket address).
- [ ] **Step 2: Run** `pytest tests/test_public_mode.py -q`. Expected: FAIL (module/cookie missing).
- [ ] **Step 3: Implement** `session.py` per the interfaces; `client_ip` takes the first comma-separated entry, validates it with `ipaddress.ip_address`, otherwise uses `request.client.host`. In `app.py`: `app.middleware("http")(session_middleware)` and `Limiter(key_func=client_ip, ...)`. Add the four keys to `limits:` in `configs/pipeline.yaml`.
- [ ] **Step 4: Run** `pytest tests/test_public_mode.py tests/test_api.py -q`. Expected: PASS (existing tests pass because `TestClient` keeps cookies).
- [ ] **Step 5: Commit** `git commit -m "Give each visitor an anonymous session cookie and a proxy-aware client address"`.

### Task 2: owner_id column and migration

**Files:**
- Modify: `src/docex/db/models.py`
- Test: `tests/test_public_mode.py`

**Interfaces:**
- Produces: `Document.owner_id: Mapped[Optional[str]]` (String(32), indexed, nullable); `init_db` adds the column and index to an existing `documents` table when missing.

- [ ] **Step 1: Write failing tests:** `test_owner_id_is_stored` (create a `Document(owner_id="a"*32, ...)` through `session()`, read it back); `test_migration_adds_owner_id_to_old_database` (build a SQLite file with the old `documents` schema via raw SQL and one row, call `init_db(url)`, assert the column exists via `sqlalchemy.inspect`, the old row survives with `owner_id IS NULL`, and calling `init_db` again is a no-op).
- [ ] **Step 2: Run** `pytest tests/test_public_mode.py -k "owner_id or migration" -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the column, and `_ensure_owner_column(engine)` called from `init_db` after `create_all`: inspect columns; if `owner_id` is absent, `ALTER TABLE documents ADD COLUMN owner_id VARCHAR(32)` then `CREATE INDEX IF NOT EXISTS ix_documents_owner_id ON documents (owner_id)`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add documents.owner_id with an in-place migration"`.

### Task 3: Scope every read route to the visitor

**Files:**
- Modify: `src/docex/api/app.py`, `src/docex/api/export.py`, `src/docex/db/models.py`
- Test: `tests/test_public_mode.py`

**Interfaces:**
- Consumes: `current_sid(request)`, `Document.owner_id`.
- Produces: `review_queue(s, limit=50, owner_id: Optional[str]=None)` and `approved(s, doc_type=None, owner_id: Optional[str]=None)` (None = no filter, kept for scripts); `_create(data, filename, mime, approach, doc_type, owner_id: str)`; `_owned(s, doc_id, sid) -> Document` raising `HTTPException(404, "not found")`.

- [ ] **Step 1: Write failing tests** using two clients on one app (`TestClient` instances sharing `app`, separate cookie jars):
  `test_other_visitor_gets_404_everywhere` (A uploads; B gets 404 on `GET /documents/{id}`, `/preview`, `POST /review/{id}`; B's `/documents` and `/review/queue` are empty; A still sees it);
  `test_metrics_and_export_are_per_visitor` (A has one corrected document; B's `/metrics["documents"] == 0`, `/export/approved/count["documents"] == 0`, A's is 1; `offline_eval` is present for both);
  `test_samples_are_public_but_runs_are_owned` (B can list samples; a sample run by A is invisible to B);
  `test_cookieless_client_cannot_read_but_can_keep_cookie` (a fresh client uploads, the response has `Set-Cookie`; reusing that cookie reads the document, a second fresh client gets 404);
  `test_null_owner_rows_are_invisible` (insert a document with `owner_id=None`, no visitor sees it in the list or by id).
- [ ] **Step 2: Run** `pytest tests/test_public_mode.py -k "visitor or owned or null_owner or cookieless" -q`. Expected: FAIL.
- [ ] **Step 3: Implement:** thread `owner_id` through `_create`, `upload`, `run_sample`; filter `list_docs`, `get_doc`, `preview`, `get_queue`, `submit_review`, `metrics` (every query, including the `Review` and latest-`Extraction` queries, joins `Document` and filters), `export_count`, `export_approved`.
- [ ] **Step 4: Run** `pytest -q`. Expected: PASS, including the unchanged `tests/test_api.py`.
- [ ] **Step 5: Commit** `git commit -m "Show each visitor only their own documents"`.

### Task 4: Retention cleaner

**Files:**
- Create: `src/docex/db/retention.py`
- Modify: `src/docex/api/app.py` (startup/shutdown, `/health.retention_minutes`)
- Test: `tests/test_public_mode.py`

**Interfaces:**
- Consumes: `read_page_path(storage_path)` from `docex.worker.tasks`.
- Produces: `purge_expired(now: Optional[datetime] = None, retention_minutes: Optional[int] = None, young_job_minutes: int = 30) -> int` (count deleted; `retention_minutes` defaults to `pipeline_config()["limits"]["retention_minutes"]`); `async def purge_loop(interval_s: int = 900) -> None`; `/health` gains `retention_minutes: int`.

- [ ] **Step 1: Write failing tests** (documents created directly with `session()` and fake `created_at`, real temp files):
  `test_purge_removes_files_and_rows_past_retention` (file, `.read.jpg`, reviews, extractions and document all gone; a 10-minute-old document stays);
  `test_purge_keeps_young_running_job_and_removes_stuck_one` (`processing`, 5 minutes old, past a 1-minute retention: kept; `processing`, 45 minutes old: removed);
  `test_purge_survives_missing_file` (the stored path does not exist: row still removed, no exception);
  `test_purge_removes_null_owner_rows_after_retention`;
  `test_deleted_document_is_404` (via the API after a purge);
  `test_health_reports_retention` (`DOCEX_LIMITS__RETENTION_MINUTES=90` gives 90).
- [ ] **Step 2: Run** `pytest tests/test_public_mode.py -k "purge or retention" -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `purge_expired` (delete children before the document; unlink with `missing_ok=True`) and `purge_loop` (`await asyncio.to_thread(purge_expired)` in a `try/except Exception` that logs and continues, then `asyncio.sleep`). In `app.py`: start the loop with `asyncio.create_task` in a startup handler, cancel it in a shutdown handler.
- [ ] **Step 4: Run** `pytest -q`. Expected: PASS (the loop's first purge runs at startup inside every `TestClient`, so confirm no test document disappears).
- [ ] **Step 5: Commit** `git commit -m "Delete uploads and results after the retention time"`.

### Task 5: Abuse caps

**Files:**
- Modify: `src/docex/api/app.py`
- Test: `tests/test_public_mode.py`

**Interfaces:**
- Produces: `limits() -> dict` (reads `pipeline_config()["limits"]` per call so env overrides work without a reload); `_check_caps(sid: str, approach: str) -> None` called at the top of `_create`; `llm_docs_today() -> int` (documents with approach B or C created since 00:00 UTC); `/health` gains `llm_daily: {"limit": int, "used": int}` and adds B and C to `paused_approaches` (seconds until 00:00 UTC) and removes them from `available_approaches` when `used >= limit`.

- [ ] **Step 1: Write failing tests:**
  `test_session_cap` (`DOCEX_LIMITS__SESSION_MAX_DOCS=2`: third upload is 429 `session_limit`; another visitor still uploads; documents older than the retention window do not count);
  `test_storage_cap` (`DOCEX_LIMITS__STORAGE_MAX_MB=0.001`: 503 `storage_full`);
  `test_llm_daily_cap_blocks_b_c_but_not_a` (`DOCEX_ENABLE_LLM=true`, `GEMINI_API_KEY=test`, `DOCEX_LIMITS__LLM_DAILY_DOCS=1`; insert one approach-C document created now; C upload is 429 `llm_daily_cap`; an A upload is 202; `/health` lists B and C in `paused_approaches` and not in `available_approaches`);
  `test_llm_daily_cap_ignores_yesterday` (the same document dated yesterday 23:59 UTC does not count).
- [ ] **Step 2: Run** `pytest tests/test_public_mode.py -k "cap" -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the helpers from the DB (`count`, `sum(size_bytes)`); order of checks in `_create`: session cap, storage cap, daily cap (B/C only).
- [ ] **Step 4: Run** `pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Cap documents per visitor, stored bytes and daily B/C use"`.

### Task 6: Honest copy and error messages in the UI

**Files:**
- Modify: `web/index.html` (give `<p class="privacy">` `id="privacy"`), `web/app.js` (`errorText`, boot, pause note), `docs/evidence/public-showcase/`
- Test: `tests/test_public_mode.py`

**Interfaces:**
- Consumes: `/health.retention_minutes`, error codes from Task 5.
- Produces: banner text set at boot; `errorText` maps `session_limit`, `storage_full`, `llm_daily_cap` (checked before the generic 429/503 branches).

- [ ] **Step 1: Write failing test** `test_ui_mentions_retention_and_new_errors`: `GET /` contains `id="privacy"`; `/web/app.js` contains `session_limit`, `storage_full`, `llm_daily_cap` and `retention_minutes`.
- [ ] **Step 2: Run** `pytest tests/test_public_mode.py -k ui -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Banner: "File yang kamu unggah hanya terlihat di browser ini dan dihapus otomatis setelah {2 jam | 90 menit}. Tetap jangan unggah dokumen asli yang memuat NPWP atau NIK." (hours when minutes divide by 60). Errors: `session_limit` "Batas 20 dokumen per sesi sudah tercapai. Dokumenmu terhapus otomatis nanti, lalu kamu bisa mencoba lagi."; `storage_full` "Server demo sedang penuh. Coba lagi beberapa jam lagi."; `llm_daily_cap` "Batas harian metode B dan C untuk demo ini sudah habis. Pakai metode A, atau coba lagi besok." Change the existing pause note so it reads "kuota gratis (Gemini atau batas harian demo) habis". Use the limit from `/health`, not the literal 20.
- [ ] **Step 4: Run** `pytest -q`. Expected: PASS.
- [ ] **Step 5: Evidence.** Serve `origin/main`'s `web/` on a second port against the same data dir (`DOCEX_WEB_DIR`) and this branch on another; with Playwright and `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` capture the "Coba dokumen" page at 1280 and 390 px, and the error message for `session_limit` (set `DOCEX_LIMITS__SESSION_MAX_DOCS=1`, upload twice). Save `before-*.png` / `after-*.png` under `docs/evidence/public-showcase/`.
- [ ] **Step 6: Commit** `git commit -m "Tell visitors about auto-delete and the new limits"`.

### Task 7: Docs, config example, final verification, PR

**Files:**
- Modify: `README.md`, `.env.example`
- Test: full suite

- [ ] **Step 1: README.** Add "Running it as a public demo": who the visitors are, a table of the four limits with their `DOCEX_LIMITS__*` names, what each protects against (disk, free Gemini quota, privacy), what is deliberately not handled (no login, plain HTTP until the Vercel domain exists), `DOCEX_TRUST_PROXY` for running behind a proxy, and the curl form `curl -c jar -b jar -F file=@x.pdf …/documents`. Change the "Live demo" line to state the retention time.
- [ ] **Step 2: `.env.example`** gets commented `DOCEX_TRUST_PROXY=false` and the four `DOCEX_LIMITS__*` overrides.
- [ ] **Step 3: Verify** (superpowers:verification-before-completion): run `pytest -q` and show the summary line; start the app locally, open it with Playwright, upload a sample, confirm the cookie and the banner; confirm `git status` is clean.
- [ ] **Step 4: Push and open the PR** from `claude/project-thread-ypki60`. Body: the platform two-line block first, then what changed, the table of limits, and the before/after screenshots linked as `blob/<sha>/docs/evidence/public-showcase/<file>?raw=true`. No assignee, no reviewer, no footer.
- [ ] **Step 5: Do not deploy.** Deploying to the VPS waits for Nash's "deploy" (build thread owns it); the Vercel domain is a separate task.
