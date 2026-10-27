# SRE M28 — Authenticated generation receipts, exact Decimal USD and atomic reserve settlement

**Branch:** `feature/sre-production-v1-20261023` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22) · 2026-10-27. **Implementation committed, QA outcome unverified. No paid POST or model output generation performed by M28.**

## Independent provider financial witness

OpenRouter has a separate authenticated read-only [GET /api/v1/generation](https://openrouter.ai/docs/api/api-reference/generations/get-generation) for a particular completed `generation_id`, whose metadata includes `total_cost`, provider identity, tokens and routing fields. The M27 chat-completion response can supply a generation ID/output, **but neither a client-provided response JSON nor a locally computed estimate verifies actual provider billing**. M28 does not claim that the model reply is semantically true.

`intelligence/relevance/opus_receipt.py` provides:

- `validate_openrouter_generation`: pure, untrusted-shape verification of exact `anthropic/claude-opus-5.5`, provider `Anthropic`, `is_byok=false`, `cancelled=false`, strict nonnegative integer native token counts within M23 request's original input/output upper bounds, **explicit zero** unknown auxiliary-fee sources, no fallback. Accepts JSON provider number **decoded as `Decimal`**, never a `float`, rounds fractional USD **UP** to nine decimal places, refuses actual USD above the specific per-request quote or the originally reserved cap. A bare parsed dictionary is not authenticated: result `provider_origin_authenticated=false`.
- `_read_generation_https`: independently authenticated read-only HTTPS GET with trusted host-held OpenRouter credential, exact fixed URL and escaped generation ID, verified OS CA, no redirects or proxies, max 10-second timeout, 128-KiB response cap, JSON `parse_float=Decimal`, and no retry. On failure **the M25 reserve remains held**, M26 is still a possible paid attempt, no result or free outcome is manufactured. Only injected fake opener in internal offline tests. The public settlement method has no opener override.
- `settle_live_sre_opus_generation`: host-authenticated permission `settle_sre_opus_generation`, idle autocommit PostgreSQL connection, checks M26 tenant ownership **before the privileged provider GET**, then releases network phase and starts a separate exclusively locked SRE workspace transaction; verifies original M23 per-request quote SHA, exact M26 intended request, M25 reserve, separately fetched generation ID/model/account/token/cost and response ID/model; inserts one immutable M28 financial receipt, whose PostgreSQL trigger finalizes M25 reserve to **spent = actual provider generation cost** and **released = original hold minus charge** in the **same transaction**.
- Malformed model content can still cost USD. M28 never changes `malformed_model_output` into `no_match` or frees the original reservation without a matching authenticated provider generation record. M21 may verify one reply's technical evidence if current genuine C9/D1 and operator-approved catalog are available. If not, status is `source_unavailable` or `invalid_untrusted_output`, and financial settlement can still record the paid amount. **No semantic decision or LIVE classification row is written** until all C9 hypotheses, M29 reconciliation and M30 coverage have been resolved.

## SQL migration 062

`intelligence/migrations/062_sre_live_opus_receipts.sql` is forward-only; **older 016/017/054–061 SQL is not edited**.

- New `sre_live_opus_receipts`: unique project+generation, project+M26 intent and project+M25 reservation, exact composite FK to the original M26 request, immutable hashes for provider response, GET metadata and evidence, trusted GET financial witness, token counts, fixed model/provider, held and charged `NUMERIC(24,9)`; no raw D1 text, secret or prompt stored. SQL UPDATE/DELETE prohibited.
- Add terminal `settled` state to M25 reservation and immutable `settlement_receipt_id`/time. Replace only forward-compatible M25 functions in migration 062: forbid any direct reserved→settled transition without the one existing M28 receipt; preserve original pre-dispatch release, quote/consent identity gates, direct spent mutation protections and project cap. One `INSERT receipt` requires genuine M15 dispatch-intent/UNKNOWN witness and triggers reservation finalization and account counter update atomically, with a unique append-only `settled` event.
- **Zero-cost generations** may legitimately yield `charged=0`; their settled audit event must also be zero rather than forging `spent`. Any cost above the quote/hold remains **BLOCKED/UNKNOWN**, not silently truncated. Failing to authenticate GET results in no database mutation.

## Assumptions and limitations

The trusted host must ensure `provider_response` comes from its M27 transport and not a user-controlled HTTP body. SQL cannot itself authenticate an external HTTPS server; privileged PostgreSQL table ownership and the host-authenticated receipt service are part of the trust boundary. No public route for settlement or generation-object insertion is introduced. BYOK, unquoted search/audio/media fees, unknown provider identity, cancelled generations, floats, partial provenance, and model/catalog drift fail closed; all-in account tax/credit fees are **not** represented as verified. M28 accounts **reported inference cost** only. Provider receipts do not prove independent semantic ground truth.

M26 currently only supports one committed request per run; consequently, M28 cannot close all 1–48 taxonomy leaves or complete any LIVE run. M29 must add verified unknown-outcome reconciliation, single-use transport gating and safe multi-request progress; M30 must enforce complete coverage. Genuine M16–M18 pretrained ONNX acceptance remains BLOCKED.

## QA authored but not marked PASS

- `tests/test_sre_opus_receipt.py`: Decimal ceiling, zero cost, float/NaN/negative, BYOK/extra fees/over cap/tokens, JSON provider generation read over injected fake HTTPS opener, redirect/status/content length checks, unauthorized actor blocks provider GET.
- `tests/test_sre_opus_receipt_schema.py`: composite uniqueness/FK, PostgreSQL reservation/account state transition and append-only receipts, zero spent audit and migration prefix 063.
- `tests/test_sre_offline_pg.py::test_m26_pg_committed_single_request_intent_crash_no_retry`: after genuine RTB-approved C9+D1, M23/M24/M25/M26, cancellation→UNKNOWN and M27 fake response, an **offline mocked independent generation GET** authorizes exactly one M28 settlement; verifies zero reserved, exact spent/released, replay blocked, direct receipt deletion/finance tampering denied and no classification output. The stub is NOT a genuine provider authentication test.
- Linux/Windows unit and PostgreSQL17 migration/integration tests are wired into `.github/workflows/sre-contracts.yml`; `CI QA [ ]` until exact-head successful execution. Queued jobs do **not** establish correctness.

**Next:** M29 verified provider-status reconciliation, durable single-use send guard and multi-request handling, without enabling paid POST prematurely or modifying main/other modules.
