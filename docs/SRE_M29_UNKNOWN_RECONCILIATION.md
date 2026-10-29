# SRE M29 — Verified UNKNOWN reconciliation, no duplicate paid dispatch

**Branch:** `feature/sre-production-v1-20261023` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22) · **2026-10-28**. Source, forward-only SQL 063, regression/PG17 tests **committed**; exact-HEAD CI **NOT ACCEPTED**. **No paid HTTP POST has been enabled.**

## Accounting truth after a timeout or crash

OpenRouter supports **`GET https://openrouter.ai/api/v1/generation?id=...`** with a bearer API key for **a generation ID already known to the host**: https://openrouter.ai/docs/api/api-reference/generations/get-generation. This API does **not** establish that no generation was charged when the ID was lost on timeout before a response.

M29 therefore deliberately distinguishes:

1. **UNKNOWN, no provider generation ID.** Record `unknown_no_generation_id`, keep M25 reservation untouched and M26 potential-paid attempt immutable. No retry, replacement generation, fake refund, `no_match`, worker requeue or state promotion. Later human audit may find an ID via provider's authoritative records. Repeating the same audit returns the same event; no second provider call.
2. **Known ID, but provider GET fails/is stale/404/429/unknown fee or actual cost over original reserved ceiling.** Record `generation_lookup_unverified` and keep full hold. Even a 404 is NOT sufficient evidence that an earlier POST was never billed; without authenticated evidence there is no safe refund or retry.
3. **Known ID and exact authenticated M28 generation receipt exists.** Record `generation_accounted`, bound by SQL to original M26 intent, original M25 reservation, exact M28 charged tokens/cost and matching ID. Replaying the action only reads prior receipt+audit and cannot trigger a second GET or debit. Any prior UNKNOWN audit remains in history; it does not rewrite/erase the M15 dispatch witness. The full run still cannot retry or dispatch again.
4. **Generation ID known, original LLM completion body irretrievably lost.** Added M28 capability to settle by authentic GET with `provider_response=None`. This produces `evidence_status=source_unavailable`, a dedicated non-answer hash witness, confirmed inference USD and NO fabricated semantic answer. This is the only viable positive reconciliation without completion bytes; it still does not claim semantic completion. No user-supplied metadata can substitute for the trusted GET.

### New host-only service

`intelligence/relevance/opus_reconciliation.py`:

- `reconcile_live_sre_opus_outcome(conn, project, run_id, item_id, intent_id, *, actor_id, actor_authorizer, generation_id=None, api_key=None, provider_response=None, catalog_snapshot=None, operator_expected_sha256=None)` requires trusted authenticated `reconcile_sre_opus_outcome` permission; for a verified GET, the host must separately grant M28's `settle_sre_opus_generation` permission. Requires a fresh idle autocommit psycopg connection; verifies owned M26/quote/hold under exclusive SRE workspace lock **before** any privileged GET. Separate M28 HTTPS GET happens outside SQL transaction. All remaining SQL changes are in atomic PostgreSQL transactions. No caller-supplied `GET` opener is accepted, no `POST` code imported/invoked, no automatic retries or model fallbacks.
- `inspect_live_sre_opus_reconciliation(... actor_authorizer)` requires trusted `read_sre_opus_reconciliation` permission and returns original quote's exact `request_count`, accounted request ordinal(s), missing ordinals and UNKNOWN count **without source text**, keys, secrets, or authorization to send. Reports `all_hypotheses_complete=False` for any incomplete original C9/M20 batch. A previously settled request is NOT equivalent to its category having been classified.

### PostgreSQL 063: irreversible evidence

`intelligence/migrations/063_sre_live_opus_reconciliation.sql` adds append-only `sre_live_opus_reconciliation_events` with composite foreign keys to original M26 intent and M28 receipt (if any); project actor audit; exact generation/receipt matching, unique final confirmed event and idempotent one-event-per-kind+generation; optional immutable multiple UNKNOWN observations. The `BEFORE INSERT` trigger checks the M15 potential-paid witness and M25 state, enforces `reserved` + no confirmed receipt for unresolved outcomes, and enforces exact **settled**, original `generation_authenticated`, original M26 SHA/receipt/reservation for `generation_accounted`. `UPDATE` and `DELETE` fail; `PUBLIC` revoked. No old migrations 016/017 or 054–062 edited. Required 063 preflight, next prefix 064.

M29 also fixed a pre-existing M25 budget-result bug: `release_pre_dispatch_sre_budget` now **rejects** a previously settled reserve instead of returning the inaccurate `released_pre_dispatch` status.

## Zero-cost QA (authored; PASS not claimed)

- `tests/test_sre_opus_reconciliation.py`: missing ID idempotently freezes and never calls M28, known ID routes through exactly one trusted M28 service invocation, repeated confirmed receipt returns same audit without GET, timeout/404/cost/extra fee uncertainty retains reserve, forged foreign authorization rejected, inspect lists all missing request ordinals and never grants next POST.
- `tests/test_sre_opus_reconciliation_schema.py`: PG forward-only 063, composite immutable M26/M28 audit, confirmed requires authenticated M28 matching receipt, unknown requires reserved budget, next schema prefix 064.
- `tests/test_sre_offline_pg.py::test_m26_pg_committed_single_request_intent_crash_no_retry`: disposable PG17 using genuinely approved C9+D1, M23 quote/M24 consent/M25 reserved/M26 committed M27 zero-cost fake, then cancellation UNKNOWN -> M29 no-generation audit, immutable wrong receipt denied -> independent **mocked generation GET** allows M28 to account original USD even when completion body is missing -> M29 exact confirmed audit idempotently, balance settled once and all remaining request ordinals stay BLOCKED.
- `.github/workflows/sre-contracts.yml`: Linux/Windows SRE tests and PG17 integrated. **Queued != passing**; run with a disposable PostgreSQL database for real acceptance. This QA deliberately uses ONLY synthetic generation metadata and makes no paid calls.

### Important scope limit, not papered over

M26 `UNIQUE(project_id,run_id)`, M15 no-auto-retry claim fence and M25 one-reserve-per-consent protect a LIVE run but currently restrict it to **one** intent. M29 exposes accurate missing ordinals and reconciles paid UNKNOWN outcomes but **does not remove this barrier or grant permission for second paid M20 request**. Doing that safely requires new audited per-request reservation/receipt accounting, one-shot execution permits, valid *fresh* owner consent and exact token/account fees before M14/M26 can be relaxed. Until then the full one-to-many C9 LIVE classification is **BLOCKED**, and M30 cannot claim full coverage.

**Next**: M30 all-leaf coverage and partial/blocked state machine, without pretending a partial response or a settled inference charge is a finished classification; follow-up dedicated request-sequencing/verified pre-send gates remain open prerequisites. No unapproved paid POST. M16–M18 ONNX pretrained acceptance remains BLOCKED; PR #22 draft, `main` unchanged.
