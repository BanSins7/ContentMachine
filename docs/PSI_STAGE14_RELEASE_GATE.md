# Project Source Intelligence — Stage 14 QA / Reliability / Release Gate

**Status is evidence-driven, not a declaration of production approval.** A green technical gate means its documented offline fixtures, PostgreSQL17 integrity checks and existing CI passed for that **exact commit**. It does **not** mean TikTok/Instagram/YouTube live downloads, production OCR/ASR accuracy, web content truth, OpenRouter cost or human permissions were verified. PR #29 remains draft; `main` must not change without explicit user/maintainer review.

## 1. Reproduction / developer runbook

Prerequisites: Python 3.12, Node 22, PostgreSQL **17**, project dependencies in `intelligence/requirements.txt`. The existing local ContentMachine frontend serves sources under `/api/projects/<id>` and its nine-step wizard is unchanged.

In a **disposable** local PostgreSQL17 database with sample-only data:

```bash
python -m pip install -r intelligence/requirements.txt
export CM_DB_DSN='postgresql://local_user:local_password@127.0.0.1:5432/contentmachine_ci'
export CM_TEST_PG_DSN="$CM_DB_DSN"
python -m intelligence.migrate
python -m unittest discover -s tests -v
node tests/test_context_stage13_ui.cjs
node tests/test_source_activity_stage13_ui.cjs
node tests/test_taxonomy_run_ui.cjs
python -m scripts.psi_release_gate --strict-ci
```

On Windows PowerShell, use `$env:CM_DB_DSN = "..." `, `$env:CM_TEST_PG_DSN = $env:CM_DB_DSN` and the same Python/Node commands. Do not use a production database for these tests: integration fixtures insert deliberate placeholder projects and jobs. The app's Windows D1 smoke is `python tests/windows_d1_smoke.py` with the CI-specific ephemeral DB environment shown in `.github/workflows/windows-d1-smoke.yml`; do not invoke against a user database.

The release gate exits with **nonzero status** if any migration is missing or modified, the old SQL baseline has been changed, PostgreSQL17 is absent, a table or immutable trigger/FK is missing, or essential files are absent. `--skip-git` and omitting `--strict-ci` are **diagnostic-only** modes which cannot report an approved gate. The technical gate never merges or grants human consent. No external model, platform worker or URL download is executed by the release-gate command.

## 2. Stage14 QA traceability matrix

| Requirement | Executable evidence | Honest boundary |
|---|---|---|
| 14.1 Complete contracts/regression | `python -m unittest discover -s tests -v`, current project/source/RTB/Discovery/SRE/SearchPlanner suites | Not proof of every real provider failure mode |
| 14.2 Fresh and old-schema migrations; isolation | `test_psi_stage14_migrations.py`, `scripts/psi_release_gate.py`, existing Stage01–13 PostgreSQL17 tests | Temporary PostgreSQL schema only; production restoration requires operator backup |
| 14.3 Fixture A PDF+DOCX → Evidence → Context → RTB/SRE | `test_psi_stage14_documents_db.py` creates real format bytes, extracts them offline, checks locators & exact quotes, approves Evidence/Context, runs two mocked LLM stages and human reviews | OCR of genuinely scanned pages and non-mock LLM completion not tested |
| 14.4 Fixture B TikTok+Reel+Short → transcript/Vision/fusion | `test_psi_stage14_platforms.py` checks three real identity/asset adapters with local verified mock MP4, SRT normalizer, synthetic sampled-frame Vision manifest & fused Video Intelligence JSON; Stage09 SQL fixture verifies persisted pins | **No real downloader, ASR, OCR or multimodal API execution**; mock Vision is deliberately labeled |
| 14.5 Fixture C website → extraction → Evidence/Context | `test_psi_stage14_web_db.py` checks one budget-consented mock OpenRouter web result, exact page-quote origin, Evidence human review and context | Page text is model-reported, not an independently captured HTTP response |
| 14.6 Security/integrity | URL parser tests, file corruption/size/ZIP-bomb parser tests, cross-project DB isolation, media archive path and SHA checks, prompt-injection/data boundary, same-origin GET+CSRF tests | Provider-side DNS rebinding/redirect handling cannot be independently tested through OpenRouter's web_fetch |
| 14.7 Reliability | Existing retry/idempotency/lease/timeout/provider-uncertain/budget, cancellation, source replacement & stale approval tests; added migration replay and project-source version tests | No 24-hour soak, forced process termination under real live providers, or live rights revocation |
| 14.8 Accuracy | `test_psi_stage14_platforms.py` asserts monotonic time codes, exact SRT/OCR text, citation existence, single-frame observation boundaries and no inferred claims promoted as facts | No independently labeled real-world OCR WER/CER or ASR WER benchmarks; quality gate for production models remains open |
| 14.9 Existing regressions | Projects, Downloaders, RTB Flat/Deep, D1, Search Planner, legacy UI and additive schema all run by existing workflows | Existing continuous CI is not an external production acceptance test |
| 14.10 OS | GitHub Actions Ubuntu/PostgreSQL17 and Windows2025/PostgreSQL17 + real localhost D1 smoke | macOS best effort — not a hard gate or advertised as tested |
| 14.11 Runtime bounds | Existing finite document/zip/upload/token budgets, per-job timeouts, frame caps, lease/idempotency, MP4 4MiB chunking and cost reservations; contracts under unit/SQL tests | No representative high-concurrency load or production storage/CPU soak run |
| 14.12 Documentation | This runbook + Stage01–13 component contracts/docs, install/environment note, limitations/consent flow | Model & platform legal/policy compliance remain the operator's concern; backend rights-gating intentionally not reintroduced |
| 14.13 Changelog/release notes | Draft PR #29 with compare to `main`, completed CI run links and this matrix | Full live environment validation is separately permissioned |
| 14.14 Manual approval | PR stays Draft and `main` untouched | Human reviewer must explicitly choose whether/when to merge |

The source plan's statement about license/rights revocation is recorded as an **unverified operational dependency**: earlier user requirements intentionally removed rights-based download processing gates. This release does not re-add those gates or claim to enforce rights revocation. Technical protections (project scope, source identity, SHA pins, SSRF/data integrity, exact user cost consent) remain separate and enforced.

## 3. Technical release invariants

1. `schema_migrations` SHA256 must match every versioned SQL file; all 030–041 PSI migrations must be present exactly once and must not rewrite 001–016 legacy versions. Baseline upgrade fixture uses an **isolated fresh PostgreSQL schema**, first applying old migrations, inserts a user profile, then applies PSI migrations and verifies the old project/profile remain intact. Re-applying must be an idempotent no-op; altered historical SQL must be rejected.
2. Project A must never read/write Project B's sources, uploaded assets, pinned outputs, approvals, consent or history. Composite project + source/version references and immutable snapshot triggers are confirmed by SQL gate and tests.
3. A registered URL, queued task, incomplete download, no-speech asset or unsampled frame must not be represented as `completed` analysis or product feature. Only **stored immutable Evidence reviewed by a human** can become a sourced RTB candidate; quote and model fact ID / exact quote checked against persisted pins.
4. A new Context cannot inherit earlier source version success, failed, or incomplete statuses. Source history is visible, but scope/time of job result are explicit. A context change prevents new quote/consent use; historical already-paid runs replay immutable pins.
5. No model call occurs on preview, upload, status list, context approval, cost quote or mere UI navigation. Paid extraction/vision/RTB requires separate, explicit user-confirmed budget and a manual stage dispatch. Unknown provider billing must not retry blindly.
6. Websites sent to Sonnet 4.6 via OpenRouter `web_fetch` are **model-reported_unverified** even if quoted exactly in a cached response. No independent provider DNS resolution/redirect trace or raw HTTP byte capture is available through that route. Do not claim they exist.
7. Verified MP4 is served only from a job-bound local archive with matching project/source/version/output/media-asset SHA, exact path and file size; browser GETs are read-only, byte range bounded and public JSON hides local archive paths.
8. CI must report exact test count, skip count, SHA, PostgreSQL major and OS. A green CI is not equivalent to a real-world ASR/OCR quality or paid provider certification.

## 4. Release/recovery decisions

- On `missing_api_key`, budget insufficient, stale source hash, malformed model output, unavailable media, no-speech, unknown provider charge, or partial Context: surface the **specific error code** to the user, retain previous immutable snapshots and prevent silent retries/payments. Do not synthesize missing words, scenes, price/likes, or website text.
- On worker restart: use existing durable queue/lease/idempotency logic; do not remove orphan assets or rerun charged tasks until the state is known. Stale work is visibly `failed`/`partial`/`unknown`, never falsely published.
- Before production DB upgrade, make an operator-managed database backup and test restore on a disposable environment. Check current `schema_migrations` and compatibility with old UI; run `python -m intelligence.migrate` in a maintenance window only after review.
- Rollback is **application code rollback plus forward/compensating migration**, not an untested automatic `DROP TABLE` of immutable source/quote history. Never delete existing approved Context or spend consent data to "fix" a bad deployment.
- A failed technical gate blocks merge until the underlying issue is repaired and all exact-SHA Linux/Windows CI are green. Paid live smoke (OpenRouter, actual TikTok/Instagram/YouTube or GPU/OCR/ASR) is a **separate user-authorized activity**; not silently triggered by release QA.

## 5. Source plan reconciliation / unresolved limitations

- Stage14 B presently uses local synthetic MP4 and Vision observations, and separate real PG fusion tests, not downloader→ASR→FFmpeg→Vision with real media on three platforms. This is a **mock contract E2E**, not production media quality evidence.
- Stage14 A uses genuine in-memory PDF/DOCX byte formats and real parsers, but cannot certify results for arbitrary encrypted/scanned PDFs, unsafe Word macros, damaged ZIPs, or giant uploads beyond the declared caps. Existing parser contracts fail closed instead of synthesizing missing content.
- Stage14 C routes target URL to Sonnet; operator-side DNS rebinding, destination redirects, and actual site page fidelity are not observable via OpenRouter's single remote web_fetch call. Local code enforces HTTPS/public hostname syntactic constraints and provider response provenance labels.
- Real ASR WER / OCR CER and full-video Vision scene accuracy need separately consented, licensed, labeled media benchmarks; `no_speech` is never inferred as `0 spoken words` if source is merely unavailable.
- User consent to **cost**, context inclusion and human review are explicit and must not be conflated. Source permissions/legal checks are not a backend gate per prior project decision.

**Release state after CI:** `READY FOR MANUAL REVIEW` only if all technical checks pass. Do not merge this PR into `main` automatically.
