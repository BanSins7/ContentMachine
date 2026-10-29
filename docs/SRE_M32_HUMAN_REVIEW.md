# SRE M32 — Immutable Human Review, Corrections and Single-Head Supersession

**Scope:** SRE-only `feature/sre-production-v1-20261023` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22) · 2026-10-28. Code/DDL/tests authored. Latest exact-HEAD CI QA **not verified**. **No paid provider POST, fake human approvals or writes to main/other module branches.**

## Objective

Give a trusted human reviewer a reproducible way to confirm or correct one **existing source-verified** M30 model category judgment, retaining the original model decision, original M26 request hash, M28 paid generation receipt and immutable M21 evidence SHA. Human review does not fabricate missing inference, solve stale D1 rights, resolve a possibly paid UNKNOWN request, or change a C9 pair from `not_evaluated` to `no_match`. The review cannot be created without M30 validated evidence and a currently reviewable, noncancelled LIVE run.

## PostgreSQL 066

`intelligence/migrations/066_sre_live_human_reviews.sql` is a **new forward-only migration**:

- `sre_live_human_reviews` is an append-only table. Composite FK pins each review to the **exact original M30 source-grounded result**, `project_id`, `run_id`, item, category UUID, original result ID, provider receipt UUID, original request SHA and verified chunk SHA. No text prompt, raw D1 source, provider secret, model body or free-form reviewer note is stored.
- `quote_sha256` and `catalog_sha256` are copied from the original M26 intent and checked by the DB trigger and human confirmation UI, preventing approval against a silently changed financial/model version. `original_model_decision` is a permanently retained immutable literal copied from M30. `human_decision` is a separate value in `match`/`no_match`/`uncertain`; the M13 expected pair, M30 original model judgment and M25–M29 finance remain untouched.
- Revision 1 is either `confirm` (same decision as original, reason `verified_model_decision`) or `correct` (different decision, structured reason). Revision 2+ must explicitly `supersede` an existing head, change the effective human decision, cite the **exact prior review ID**, and give a structured correction reason. `UNIQUE(project_id,run_id,item_id,category_key,revision)` and `UNIQUE(project_id,prior_review_id)` prevent duplicate revision or branching from the same predecessor; composite self-FK ties prior ID to project and SQL trigger verifies original source/identity/head/revision. SQL `BEFORE UPDATE OR DELETE` rejects history mutation, PUBLIC privileges revoked, and workspace lock coordinates with M15 cancellation.
- SQL additionally rejects reviewing a cancelled/UNKNOWN run, a mismatched model judgment, or any M28 receipt not already authenticated with M21 evidence.

**Threat model:** PostgreSQL owner remains privileged enough to bypass triggers, and an application database role which is authorized to write could physically assert `human_verified=TRUE`. Human identity is therefore checked **at the trusted host/service boundary**: never expose `record_live_sre_human_review` to unauthenticated clients or automated model workers. Audit trail means DB append-only under the permitted runtime role, not tamper-proof against DBA.

## Host-only service

`intelligence/relevance/live_human_review.py` provides:

- `human_review_confirmation(project,run_id,item_id,category_key,base_result_id,original_model_decision,human_decision,reason_code,expected_prior_review_id=None,*,quote_sha256,catalog_sha256)`: exact payload that a human UI must explicitly echo from a CSRF-protected, host-authorized confirmation; this factory **alone** is NOT consent, actor authorization or human verification.
- `record_live_sre_human_review(conn,...,reviewer_actor,actor_authorizer,human_decision,reason_code,explicit_confirmation,expected_prior_review_id=None)`: requires dedicated host permission `human_review_sre_live_pair`, idle autocommit DB session, exclusive project workspace lock, original M30/M28 evidence and **fresh D1 `before_publish` right/provenance** via the existing M30 gate; exact confirmation from reviewer; no free-text reasons or privacy-sensitive payloads. Original model and current head are read in the same transaction. Attempt to replace prior review without its exact ID returns `stale_review`; no silent last-write-wins.
- `read_effective_live_sre_decision(...)`: host permission `read_sre_human_review`, REPEATABLE READ READ ONLY transaction, fresh D1 rights; returns both original model decision and the latest human decision, explicit `effective_source`, `human_reviewed` flag, revision and base M30 receipt. If no human review, effective judgment is identified as **unreviewed original model**, not claimed human truth.
- `read_sre_human_review_history(...)`: full ordered immutable chain with original model pin and all reviewer IDs, structured reasons and timestamps. Verifies sequential revision/prior links and source/receipt identity before rendering. Never changes model or finance.
- `read_live_sre_human_review_queue(...,limit=50)`: bounded max-100 page of **verified existing M30 results only**, original/current human decision and status `awaiting_review`/`reviewed`. Does not pretend unclassified C9 pairs are human-reviewed; checks LIVE run/owner and fresh D1 rights even for the truncation sentinel. This is one bounded first page, not a fully paginated bulk-review API.

No automated provider request is added or re-enabled. Human decisions are exposed **only through the explicit effective-decision read service**, not silently written into original model result/analytics. Existing consumers must explicitly integrate this new contract before asserting effective human-corrected labels.

## QA authored (NOT PASS)

- `tests/test_sre_live_human_review.py`: initial explicit human confirmation, model `match`→human `no_match`→supersede `uncertain`, immutable source remains `match`, stale/replayed/same-decision corrections denied, authorization spoof/revoked D1/cancelled/foreign original result rejected, history fork denied, bounded queue unchanged model and no paid POST. Uses **offline fake PostgreSQL**, not an actual human or provider.
- `tests/test_sre_live_human_review_schema.py`: additive 066, immutable composite source FK, single prior/head, reviewed actor/reason and cancellation gate, SQL updates forbidden, next migration prefix 067.
- `tests/test_sre_offline_pg.py::test_m26_pg_committed_single_request_intent_crash_no_retry`: extends authentic approved C9/D1 fixture where an M28 charged inference **has lost completion** and hence M30 has **no** source-grounded result. M32 correctly refuses to invent human judgment/forged direct SQL review and queue reports zero reviewable leaves. This **negative PG test does not prove a successful production positive human review**.
- `.github/workflows/sre-contracts.yml`: Linux/Windows pure tests and PG17 migration/runtime wired; queued ≠ PASS. True human role/UI approval, positive real M30+M32 PostgreSQL integration and signed authentication need environment QA.

## Project status and blockers

M32 model result and correction/lineage storage implementation committed, but the complete production acceptance remains **[ ]** while latest CI, authenticated human UI and positive real provider/M30 review E2E remain unverified. Ordinary LIVE multi-C9 paid completion remains blocked by the intentionally preserved one-intent-per-run M26 fence and M25 one-reservation consent; M16–M18 actual pretrained ONNX QA remains blocked. Financial invoices never become semantic `no_match` or human truth.

**Next M33:** reproducible human-gold benchmark, calibration and precision/recall/F1 with explicit denominators; use only authorized genuine human-reviewed records, no fabricated accuracy numbers. Keep draft PR #22 and main untouched.
