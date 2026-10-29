# SRE M24 — Dedicated Owner-Authorized Opus Spending Consent & One-Way Revoke

**2026-10-27 · Branch:** \`feature/sre-production-v1-20261023\` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Status:** source + migration 059 + negative unit and real PG17 tests committed. **CI QA not confirmed.** M24 **never** makes a provider POST, creates a payment intent, reserves USD in a ledger, advances a LIVE run, or authorizes worker dispatch.

## Requirement

M24 is a **new dedicated SRE namespace** for exactly one human-approved M23 immutable quote. No consent or permission from C/Query Planner/Taxonomy Builder, source policy, earlier SRE run planning or any generic checkbox can authorize paid SRE execution. A project id is not a login credential. The user must explicitly confirm the **exact** quote SHA, upper USD amount, and \`sre_live_opus_paid_execution\` stage. A changed/missing/expired/foreign/revoked quote does not confer consent.

An M23 quote is only a conservative **token-only conditional upper bound**; exact real Opus token counts, provider fees/taxes/account routing, actual D1 \`before_external_send\` rights, USD budget reserves and provider call safeguards remain **open mandatory gates**. The dedicated consent itself gives **no permission to spend or launch**.

## Trusted host-only API

New \`intelligence/relevance/opus_consent.py\`:
- \`confirmation_for_quote(quote_sha256, usd)\` generates the exact display/echo data object \`{"action":"authorize_sre_spending","stage":"sre_live_opus_paid_execution","quote_sha256":"...","max_usd":"NN.NNNNNNNNN"}\`. **Producing this deterministic template is not user approval** and cannot stand in for an authenticated click. The host must display the quote details and capture a fresh explicit event, bound to its authenticated owner/project identity and a CSRF-protected session.
- \`grant_live_sre_opus_consent(conn, project, run_id, item_id, quote_sha256, snapshot, *, actor_id, actor_authorizer, explicit_confirmation, operator_expected_sha256, operator_endpoint_tag, operator_model_revision, operator_maximum_usd, consent_ttl_seconds=180,now=...) \`: a **trusted internal service, not a public HTTP handler**. It requires an independent, host-owned \`actor_authorizer(project_id,actor_id,"grant_sre_opus_consent") is True\`. Missing, failing or truthy-nonbool authorization is **denied** before SQL. The trusted host supplies the actor ID from its session, **never from an untrusted request field**, and the confirmation object from an authenticated user action. It locks the project and exact LIVE run (state \`pending_consent\`, no cancel), revalidates the **database-stored** M23 quote by rebuilding full approved H1/H2 C9+M12/D1, current source rights, model/catalog pricing and batch SHA, compares the exact quoted USD cap and confirmation. A consent with an unknown/foreign/expired quote cannot be inserted.
- \`verify_live_sre_opus_consent\`: read-only validation of **existing** PostgreSQL consent, original amount, stage, confirmation digest, expiry, one-way revoked state and current trusted M23 LIVE project/source/catalog checks. The future M25 ledger and later fenced sender must independently recheck this just before spending. This function does NOT authenticate a worker or grant dispatch.
- \`revoke_live_sre_opus_consent\`: independently authorized internal \`actor_authorizer(...,"revoke_sre_opus_consent")\`; one-way transition writes immutable \`revoked_at/revoked_by/revoke_reason\`. Idempotent reads of already-revoked consent return \`revoked\`, with no attempt to resurrect/extend the original grant. Revocation is allowed even if source rights, queue state or quotation have since become unusable (provided the consent record and its FK quote remain); the revoked consent must never be accepted by \`verify\`.

All three paths compare any test/operator-provided UTC \`now\` against PostgreSQL \`clock_timestamp()\`; stale caller timestamps do **not** extend real validity. The database also rejects expired grants and impossible future times. The trusted host must use its real UTC clock, never a client-submitted timestamp.

**Integration boundary not complete in M24:** existing \`intelligence/relevance/http_api.py\` currently rejects all \`POST /api/sre/v1/*\`. We **do not** quietly add an unauthenticated endpoint to obtain consent. Authentication, owner session, interactive affirmation and CSRF controls must be integrated/tested in later application wiring; pure Python callback unit fixtures **do not prove** that a real user confirmed. A malicious internal caller could fabricate an \`actor_authorizer\` callback, so only trusted service code with host-owned authorization inputs may invoke these ports. Public API callers must not receive direct access to this method or unrestricted SQL credentials. SQL \`REVOKE ALL ... FROM PUBLIC\` is defense in depth, not a substitute for application auth.

## PostgreSQL17 schema

Additive forward-only \`intelligence/migrations/059_sre_live_opus_consents.sql\`:
- \`sre_live_opus_consents\` references the **exact** existing M23 quote through composite \`(project_id,run_id,item_id,quote_sha256)\` FK. A supporting unique index on the quote table is installed in 059. \`UNIQUE(project_id,quote_sha256)\` prevents concurrent duplicate grants for one quote. A grant may be replayed idempotently only by the same actor/quote/amount/confirmation. Different actor/consent cap/stage fails; a revoked grant can never be regranted via \`ON CONFLICT\`. A new attempt must request a **new quote** (and a new confirmation).
- Immutable original \`consent_id\`, project, run/item, quote SHA, actor, fixed stage, \`max_usd NUMERIC(24,9)\`, \`granted_at\`, \`expires_at\`, \`confirmation_sha256\`. On INSERT a trigger \`FOR SHARE\` checks the parent quote, **exact equal USD bound** and \`expires_at <= quote.expires_at\`. Consent TTL <=300 seconds (defaults to 180), cannot outlive the stored quote or catalog. Run state checks are in the trusted service and must later be repeated at final dispatch.
- A BEFORE UPDATE trigger forbids modifying ANY original grant field, including amount or actor. The **only update** is a single irreversible transition from null revocation fields to \`revoked_at,revoked_by,revoke_reason\` (reason in \`user_revoked|operator_revoked|security_revoked\`). It cannot later be undone or repeated. Normal revocation is a state transition, not a record deletion.
- Physical DELETE is permitted for privacy/retention and cascades from quote deletion, ensuring a removed quote cannot retain usable consent. Never delete to fake a new grant while preserving the original quoted permission; future spend validation must require the current parent quote FK and persisted grant. \`REVOKE ALL ON sre_live_opus_consents FROM PUBLIC\`; no C/Query consent tables imported or referenced.
- No ledger/budget table exists in M24, no attempt, classification result, worker lease, run state transition or provider request is created. M25 must atomically reserve against exactly this consent's verified USD cap; M24 responses always include \`budget_reserved=false\`, \`dispatch_allowed=false\` and \`paid_calls=0\`.

## QA coverage

- \`tests/test_sre_opus_consent.py\`: strict exact quote/amount/stage confirmation, wrong amount by 1 nano USD denied, no default/generic/query consent, actor authorization denied even before DB access, callback exceptions/false/truthy-nonbool rejected, invalid actor and float/scientific money rejected, no provider import.
- \`tests/test_sre_opus_consent_schema.py\`: real SQL schema asserts quote FK, composite owner isolation, \`NUMERIC(24,9)\`, amount equality, <=quote expiry, immutable grant, one-way revoke and PUBLIC grants revoked.
- \`tests/test_sre_offline_pg.py::test_m24_pg_explicit_per_quote_owner_consent_revoke_once\`: disposable PostgreSQL17 actual two-human-approved RTB C9 + D1 content, M12 LIVE and M23 stored quote. Confirms zero consent from planning/quote; explicit owner approval, exact amount and idempotent replay; wrong amount, wrong actor, foreign project, expired grant, direct SQL mutation and unrevocation all denied; authenticated revoke and repeat revoke; no paid attempt/result rows; privacy quote deletion cascades consent deletion.
- Updated migration guard requires 059 and next prefix 060, main-only migration bootstraps and PG17 table checks; \`.github/workflows/sre-contracts.yml\` Linux+Windows pure unit and PostgreSQL17 integration.

Commands (not claimed executed):

\`\`\`bash
python -m unittest discover -s tests -p test_sre_opus_consent.py -v
python -m unittest discover -s tests -p test_sre_opus_consent_schema.py -v
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES17_DSN>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
\`\`\`

**QA reality:** do not mark a test suite PASS from existence of its source; exact GitHub CI result must be \`conclusion=success\`. The previous branch SHA's four runs all ended \`cancelled\`, NOT successful. Genuine M16–M18 prepaid/operator-pinned ONNX cold/hot evaluation is separately BLOCKED.

**Next M25** — \`Decimal\` atomic per-project budget ledger/reservations with concurrency/row locking, once user explicitly requests the next stage. Until that work plus paid model pre-send gates, a correctly stored explicit consent is **still non-spendable** and cannot trigger OpenRouter.
