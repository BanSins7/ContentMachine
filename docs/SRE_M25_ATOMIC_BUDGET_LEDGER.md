# SRE M25 — PostgreSQL17 Decimal project budget and atomic pre-dispatch reserve

**Branch:** `feature/sre-production-v1-20261023` · **PR:** [#22](https://github.com/BanSins7/ContentMachine/pull/22) · **2026-10-27**

**Status:** code, forward-only migration 060, Linux/Windows pure and real PostgreSQL17 test source committed. **CI PASS NOT CONFIRMED.** M25 creates no payment, receipt, provider transport, paid attempt, billed token, semantic verdict or QDP event.

## Trusted owner flow

The budget is **absent by default**. `configure_live_sre_budget(conn, project, actor_id, actor_authorizer, project_cap_usd)` requires a host-authenticated owner permission `configure_sre_budget`, a **plain positive Decimal string** with up to nine fractional USD digits, current PostgreSQL clock and an accessible SRE workspace. The first configured cap and owner are immutable in M25. Replays with the same cap/actor are idempotent; attempts to raise/lower/recharge the cap fail closed. **No API endpoint exposes this service.** An untrusted browser's `actor_id` or project header must not be treated as host authentication. No C/Query Planner consents or budgets are imported.

`reserve_live_sre_opus_budget(..., actor_id, actor_authorizer, catalog_snapshot, operator_expected_sha256, operator_endpoint_tag, operator_model_revision, operator_maximum_usd)` requires `reserve_sre_opus_budget` and:
1. exclusive workspace row lock to serialize with M15 cancellation and dispatch witness writes;
2. fresh exact M24 active, original owner-granted consent, existing M23 immutable quote, current approved H1/H2 C9, M12 LIVE project/item, D1 version/rights and trusted pinned M22 catalog;
3. **full** M23 `reserve_upper_bound_usd` for one consent/item, with no partial amount, negative amount, float, dynamic downsizing or provider/model fallback;
4. an independently configured project cap; atomic cap test in PostgreSQL **AFTER INSERT** ledger trigger and `NUMERIC(24,9)` account row update. Unlike a BEFORE INSERT charge, a duplicate `ON CONFLICT DO NOTHING` never charges twice;
5. one current reservation per consent, and at most one ACTIVE reservation per LIVE run/item; any previous potentially paid `dispatch_intent/unknown/completed/failed_known` witness denies new reservation.

Return metadata explicitly has `budget_reserved=True`, `dispatch_allowed=False`, `paid_calls=0`. An active hold is **not** a transaction receipt or proof that the OpenRouter account can send. No automatic dispatch is enabled; M26 durable paid-send fence, M27 exact transport and M28 actual settlement are separate future work.

`release_pre_dispatch_sre_budget` requires a separately authenticated `release_sre_opus_budget` permission. Only a clearly pre-dispatch hold can transition to `released`, once; subsequent replays are idempotent. It does not treat an expired/revoked consent as proof of a free remote failure. Any existing possible provider-send intent retains its reservation until audited later reconciliation. Release timing is always PostgreSQL `clock_timestamp()`, not a client clock. A released original consent cannot be reserved a second time, even if its old quote still exists.

## Migration 060 and financial isolation

`intelligence/migrations/060_sre_live_budget_ledger.sql` adds `sre_live_budget_accounts`, `sre_live_budget_reservations` and `sre_live_budget_events`; tenant composite FK to exact M24 consent and its quote and actual C9/D1 LIVE run, project cap and `reserved + spent <= cap` constraints, fixed cap/owner, one-way reservation release, unique active item, event UPDATE immutability and no PUBLIC grants. Counters are updated in **one SQL transaction** on insert/release, and failed transactions roll back all accounts/events/reservations. `spent_usd` exists and is pinned at **zero for M25**: there is **no settlement port**. Future M28 cannot merely toggle a reservation to 'completed'; it must introduce verified receipts and a controlled settlement migration. Historical 016/017 and 054–059 are untouched.

Finance rows cascade on privacy deletion where safely pre-dispatch; if an attempt witness indicates possible billing, retention deletion is deliberately blocked until a later audited reconciliation. Minimal hash+amount event journal supports audit; DELETE is permitted for explicit authorized project retention, but journal UPDATE is forbidden. PostgreSQL table ownership and unrestricted SQL credentials must remain restricted to trusted processes; `REVOKE FROM PUBLIC` is defense in depth, not application authentication or proof against a compromised database owner.

## QA and remaining gates

- `tests/test_sre_opus_budget.py`: exact Decimal validation (no floats/NaN/scientific values), negative owner authorization before SQL, no transport import.
- `tests/test_sre_opus_budget_schema.py`: forward-only 060 and SQL atomic cap, composite consent identity, release-after-intent denial, PUBLIC restrictions, next 061.
- `tests/test_sre_offline_pg.py::test_m25_pg_actual_c9_d1_consent_budget_reserve_release`: actual disposable PG17 approved two-human C9, D1 source, M12 LIVE, real M23 quotes, explicit two M24 consents, one-owner cap, replay, denied oversubscription and mutation, one-way revocation+pre-send release, no paid attempt/semantic writes.
- `tests/test_sre_migration_guard.py`, `test_sre_migration_guard_pg.py`: exact new required SQL checksum, fresh and main-only upgrades, 060 required, next 061.
- `.github/workflows/sre-contracts.yml`: Linux/Windows pure and disposable PG17 integration. **Tests are authored, not assumed successful:** inspect exact Actions SHA and conclusion before changing M25 checkbox to DONE. No local clone was available from the execution environment due to GitHub DNS failure.

**M25 does not close earlier blocked gates:** genuine vetted/pretrained M16–M18 ONNX acceptance, exact Opus tokenizer/billing, live OpenRouter account routing/extra fee/tax, interactive host CSRF/identity, authenticated paid POST, receipts, full LIVE completion and external QDP E2E are NOT verified. **Next M26** only after request, with dispatch intent committed before transport and zero blind retry. No merge to `main`.
