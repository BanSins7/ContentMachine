# SRE M23 — Immutable Post-Batch Opus 5.5 Decimal USD Quote

**2026-10-27** · Branch: \`feature/sre-production-v1-20261023\` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Implementation:** source, additive PostgreSQL17 DDL, negative tests and CI wiring committed. **Acceptance:** GitHub Actions not confirmed SUCCESS. **No paid model call, no consent or budget reservation.** All pricing fixtures are simulated. The user's real OpenRouter account pricing, credits, fees, true Opus token measurements and model execution were NOT verified.

## M23 precise meaning and conservative limitation

M23 builds a **final immutable *conditional upper-bound USD quote*** only **after** an exact SHA-pinned M20 request/batch manifest and M22 independently operator-SHA-approved fresh exact Opus 5.5 endpoint snapshot. It cannot truthfully report exact billed input tokens: M20's UTF-8 bytes are NOT tokens. Thus it does **not** estimate a likely invoice or use a byte-to-token multiplier. Instead it reserves the **entire maximum input-token capacity per request of the selected pinned endpoint**, minus output cap and 1024 context headroom. This overestimates typical usage, intentionally prioritizing no surprise spend over low apparent prices.

For each request:

\`\`\`text
max_input_tokens  = endpoint.context_window_tokens
                    - request.max_output_tokens - 1024
max_output_tokens = request.max_output_tokens

conservative_input_rate_per_token =
  prompt + input_cache_read + input_cache_write + input_cache_write_1h

request_upper_usd = ceil_9_decimal_places(
    max_input_tokens * conservative_input_rate_per_token
  + max_output_tokens * completion_rate
)

total_reserve_usd = sum(ceil_9_decimal_places(each_request_upper))
\`\`\`

All rates are pinned **decimal strings** from the exact selected M22 catalog endpoint. \`Decimal\` math (context precision 60) and **ROUND_CEILING** at 9 fractional USD digits never knowingly round an upper reserve down. All cache rates are treated as possibly additive, not assumed alternative discounts. **No free cache discount, no best-price provider switching, no float or scientific notation allowed.**

The output gives explicit \`min_total_usd="0.000000000"\`, \`max_total_usd\`, \`reserve_upper_bound_usd\`, a typed operator \`operator_maximum_usd\`, per-request upper bounds and per-request request SHA. If even the rounded-up reserve exceeds the trusted operator cap, reject with \`quote_budget_exceeded\`. **Never auto-shrink the workload or substitute a cheaper Opus endpoint.** Zero is the technical minimum since M23 performs no paid work and canceled runs cost nothing; it is not a guarantee of a zero-cost executed request.

**Conditional upper bound, not an invoice:** this reservation only covers the five named *token* pricing fields under the current specific endpoint. Account-level credit purchase fees, taxes, enterprise/BYOK rules, requests with paid tools/web/image/media or unrecognized fees are **NOT** priced. The output therefore requires an account-price/extra-fee check. A future dispatch MUST reject unknown billable dimensions rather than blindly treating \`max_total_usd\` as an all-in guarantee. See [OpenRouter billing/pricing explanation](https://openrouter.ai/pricing). Actual execution must independently meter/limit input/output tokens, enforce the pinned endpoint and no fallback, and reject if the real request would violate these ceilings. Without these prerequisites the quote is **not spendable**.

## Pure API and immutable storage

**New \`intelligence/relevance/opus_final_quote.py\`**:
- \`build_final_opus_quote(m20_manifest,catalog_snapshot,operator_expected_sha256,operator_endpoint_tag,operator_model_revision,operator_maximum_usd,quote_ttl_seconds=180,now=...)\`: pure no-network generator. It runs M22's exact **\`preflight_exact_opus_batch\`**, which checks M20's C9 all-leaf/request SHA, batch ordinals, model, SHA, cap and selected endpoint. It cannot be called on incomplete, altered or unapproved-price batch without a typed block. Stable \`sre-opus-final-quote/v1\` JSON includes batch SHA, catalog SHA, full Decimal price schedule, per-request SHA, project/publication/video/content version SHA, source M19 SHA, issue and expiry UTC, model revision, endpoint tag, all C9 keys, conservative token ceilings, min/max USD and \`quote_sha256\`.
- The quote expiration is \`min(issue + requested_TTL<=300sec, trusted_catalog_capture + 900sec)\`, **rounded down to whole seconds** so fractional-second remaining validity is never inflated. Expired or future-forged timestamps fail. Quote replay with the same inputs/time produces the same SHA. Price/model/manifest/amount drift produces a different SHA.
- \`verify_final_opus_quote(quote,m20_manifest,catalog_snapshot,...)\`: checks exact exhaustive quote shape and self-SHA, expiration using trusted UTC time, rebuilds the entire quote with the original issue time and trusted current operator USD cap/catalog SHA, compares **every** field, and checks catalog expiry **at verification time**. This is transport integrity, **not quote authority** if the quote came from an HTTP caller; always read the original trusted PostgreSQL row.
- \`issue_live_opus_quote(conn,project,run,item,...)\`: inside one PostgreSQL transaction, locks the project workspace SHARE, validates exact M22 model/catalog, **rebuilds LIVE M20 through project-owned M12 run/item, approved C9 H1/H2 and current D1/S3 rights**, computes the pure quote, inserts an immutable unique row using \`ON CONFLICT DO NOTHING\`, and re-reads the exact stored SHA and JSON. The host must authenticate owner/operator independently, and this route does not grant consent or paid worker lease.
- \`read_live_opus_quote(conn,project,run,item,quote_sha,...)\`: selects only that project/run/item's **DB-issued** quote with \`FOR SHARE\` and checks live DB current C9/D1 rights, exact repacked M20/price/model manifest, original SHA and not-expired deadline before returning any positive validation. Output explicitly \`dispatch_allowed=false\`, \`consent_granted=false\`, \`paid_calls=0\`.

**Additive migration \`intelligence/migrations/058_sre_live_opus_quotes.sql\`**:
- New \`sre_live_opus_quotes\` with composite FK \`(run_id,project_id)\` and \`(item_id,run_id,project_id)\` to actual SRE LIVE rows, unique \`(project_id,quote_sha256)\`, JSONB object plus checked SHA/model status, \`NUMERIC(24,9)\` min/max USD, and expiry <=300seconds; PUBLIC grants revoked.
- \`BEFORE UPDATE\` trigger makes issued quotes **immutable**. **DELETE is deliberately permitted** to meet privacy/retention/source revocation and project removal requirements (with \`ON DELETE CASCADE\` for parent cleanup). A deleted quote cannot be validated and provides no future authorization; M24 must reference only an existing, still-valid exact quote.
- The SQL intentionally does not mutate \`sre_live_attempt_fences\`, consent tables, classification results, budget ledgers or worker states. Previously issued quoted money is NOT a reservation; M25 must allocate and atomically lock the budget later.
- Existing \`016/017/054–057\` SQL not rewritten. \`migration_guard.py\` requires 058 and next free prefix becomes 059; clean installs, main-only SRE upgrades, checksums and idempotent reruns remain tested.

## QA

\`tests/test_sre_opus_final_quote.py\`: pure Decimal upper reserve, exact full C9 SHA and catalog price/revision replay, operator cap too small (fail closed), float/negative/exponent money refusal, expiry/future/stale behavior, altered per-request/batch/source/model SHA, no fake token count or payment permission.

\`tests/test_sre_opus_final_quote_schema.py\`: PG DDL structure, exact project/run/item FK, \`NUMERIC(24,9)\`, immutable updates, retention-compatible deletion, no PUBLIC access and next migration 059.

\`tests/test_sre_offline_pg.py::test_m23_pg_immutable_quote_owned_run_item_sha_revoke_and_privacy\`: **real disposable PostgreSQL17** and two-human-approved C9, D1 source/content version. Plans under reviewed external grant, revokes external-send grant before **local** quotation, stores and idempotently re-reads the same quote; verifies immutable UPDATE rejection, foreign tenant FK and lookup rejection, valid exact SHA, no paid attempts, no results, full rights revocation deny and deletion cleanup. **All catalog prices here are offline fixtures**, not live Opus tokens or inference.

\`.github/workflows/sre-contracts.yml\` updated M02–M23 Linux+Windows pure test, and PostgreSQL17 integration. Local commands (not claimed run):

\`\`\`sh
python -m unittest discover -s tests -p test_sre_opus_final_quote.py -v
python -m unittest discover -s tests -p test_sre_opus_final_quote_schema.py -v
CM_TEST_PG_DSN='<DISPOSABLE_PG17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
\`\`\`

**No QA PASS from merely committed tests or GitHub Actions \`queued\`.** The private GitHub repository cannot be cloned from this container (GitHub DNS unavailable). Keep test validation unchecked until exact branch HEAD reports actual success. Separate real pretrained M16–M18 ONNX semantics and cache acceptance remain BLOCKED without independently pinned model weights.

## M24–M25 required before any charge

M23 issues an **immutable upper-bound money quote**, **not permission to pay**. M24 must record explicit authenticated user/project-actor **SRE-only consent**, exactly tied to this existing quote SHA, USD spending cap and expiry, with revocation. M25 must atomically reserve the accepted amount and protect all concurrent runs against overspending. Later worker/send phases must enforce exact provider token/context limits, current account pricing/fees, supported structured output, endpoint routing with \`allow_fallbacks=false\`, current D1 \`before_external_send\` rights and M14/M15 fencing/UNKNOWN fail-stop. **M23 itself makes zero OpenRouter POSTs and zero financial ledger writes.**
