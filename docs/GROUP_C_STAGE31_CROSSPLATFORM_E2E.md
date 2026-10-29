# GC-MOD-31 — PostgreSQL17 QDP → Group C → Analytics cross-platform E2E

**Code status:** implementation of an end-to-end *synthetic-port* PostgreSQL17 fixture, scoped concurrency/races, exact immutable SHA lineage, original signed-test C7 and same-transaction Analytics ACK, plus optional cross-platform evidence gate **CODE IMPLEMENTED**. **Exact HEAD test execution/CI and genuine Windows+Linux PG17, production QDP and UAE/Analytics handshakes NOT VERIFIED.** This stage's "CI of every important SHA confirmed" DoD is **BLOCKED_QA**; no claim of green CI, production merge readiness or real peer E2E.

## Files

- \`tests/test_group_c_crossplatform_e2e.py\` — 2 pure contract-disclaimer tests plus conditional disposable PG17 round-trip tests with *real PostgreSQL SQL commits*. It brings together existing \`QDPGroupCConsumer\`, verified D1 data, \`PostgreSQLGroupCWorker\`, existing GC11/12 signed **test-only** human/config approval fixture, GC20 original certified C7, GC25 \`PostgreSQLAnalyticsBridge.deliver_one\`, and a same-connection durable synthetic Analytics sink. **Both QDP schema producer and Analytics sink are test doubles**, not external installations.
- \`intelligence/group_c/e2e_gate.py\` — optional local CLI that gates on independent PG17/database-name/test-only permissions, executes the exact test module and saves a *redacted* machine-readable JSON result with actual local Git SHA, OS, PG17 major, test run/skip/failure counts and 3 immutable \`production_*_verified:false\` fields. Requires at least 12 actually executed tests and no skips. If no Git SHA available or any test skipped/failed/errors it returns nonzero and **never emits a PASS claim**.
- \`tests/test_group_c_e2e_gate.py\` — four pure controls for honest reporting: skipped/failed/too-few tests, missing SHA, no secret/score fields, no real production port claim.
- \`.github/workflows/group-c-module.yml\` — existing Linux PG17 service now executes new full synthetic-port E2E on **fresh** and **upgrade** disposable databases; existing Linux+Windows pure-contract job executes GC31 report checks. **This is not Windows PG17 E2E**. The owner must run the Windows local gate on Windows with a *real PG17 test database*, and collect a SHA-bound separate JSON report; do not call Windows ready because it ran pure tests.
- No original \`003\` / \`004\` / \`056..064\` migration altered, no live QDP / UAE source modified, no main merge.

## Genuine data flow inside disposable PostgreSQL17 (test doubles at boundaries)

1. Seed canonical source and exact D1 \`project_registry\`/video/source-policy/metadata/current+previous \`metric_snapshots\`, creator/prior frozen cohorts and a signed **test-only** human review with activated calculation config.
2. Create a synthetic producer \`qdp_outbox\` and a \`qdp_group_current_preparations\` view only after independent \`GC_TEST_DISPOSABLE_DB=YES\` AND \`GC31_ALLOW_SYNTHETIC_QDP=YES\`, database name prefix \`contentmachine_gc_\` or \`gc_e2e_\`, PG version 17, and verifying none of the QDP/Analytics test relations already exist. Never overwrite real schemas. The producer has exact \`qdp-event/v1\` and \`qdp-preparation/v2\` hashes, exact source/policy, metric-definition/version, current+previous snapshot IDs, metadata and config.
3. \`QDPGroupCConsumer.receive\` re-reads QDP *and* canonical source policy/raw D1 inside real PG transaction and ACKs only an accepted durable inbox/run/job. Repeated producer event returns exact original run/job IDs; changed SHA records conflict and cannot be ACKed.
4. Only the job created by **that exact producer QDP event** is claimed; real worker advisory global lock, \`SKIP LOCKED\`, lease fencing and running budgets prevent double claim. Config activation and reviewer IAM are **synthetic signed test doubles**, not live production IAM.
5. Original \`PostgreSQLGroupCWorker.execute_score\` commits one certified \`calculated_metrics\` row and one pinned Analytics outbox payload in the same PostgreSQL commit. No custom E2E C7 scorer or numeric replacement.
6. \`PostgreSQLAnalyticsBridge.deliver_one\` rechecks current source rights and saved score; test sink writes the *same original C7 and original metric ID* to a durable local receipt in the SAME transaction and returns an exact ACK/ID/hash. A wrong ACK causes full rollback of both sink and outbox; another delivery on already-delivered outbox returns none. Source revocation before delivery forbids numeric exposure to sink and marks event blocked.
7. Exact SELECT of joined \`group_c_inbox → group_c_runs → group_c_jobs → calculated_metrics → group_c_outbox → gc25_disposable_analytics_receipts\` asserts the original project/source/run/event/video/job/score IDs and equal original \`breakout_score\`, as well as pinned outbox \`canonical_hash(payload)\`.
8. Adversarial routes: 3 concurrent independent QDP deliveries produce only one accepted unique run/job, 6 independent workers receive at most one lease for that run, replay doesn't create extra outbox, foreign-project notice denied, late retracted/newer raw metric denies an older pinned score, unavailable Analytics preserves the pending outbox, altered QDP preparation/version fails without partial ACK, source-policy revocation blocks QDP or Analytics at its own boundary.
9. Original migrations \`003\`, \`004\`, \`056\`, \`063\`, \`064\` must match current \`schema_migrations\` exact SHA-256, and \`migrate.apply_migrations\` must return no new migrations on repeat. Full fresh/upgrade lifecycle migration checks remain in existing CI.

## Run on local Windows (PowerShell) — *real PostgreSQL17 test DB required*

Use an independent empty PG17 database, e.g. \`contentmachine_gc_e2e_windows\`, and local checkout of the exact Group C commit:

\`\`\`powershell
$env:GC_TEST_DISPOSABLE_DB = 'YES'
$env:GC31_ALLOW_SYNTHETIC_QDP = 'YES'
$env:CM_TEST_PG_DSN = 'postgresql://<test-user>:<test-password>@127.0.0.1:5432/contentmachine_gc_e2e_windows'
python -m intelligence.group_c.e2e_gate --report .\gc31-windows-evidence.json
\`\`\`

## Run on local Linux/macOS (bash)

\`\`\`bash
export GC_TEST_DISPOSABLE_DB=YES
export GC31_ALLOW_SYNTHETIC_QDP=YES
export CM_TEST_PG_DSN='postgresql://<test-user>:<test-password>@127.0.0.1:5432/contentmachine_gc_e2e_linux'
python -m intelligence.group_c.e2e_gate --report ./gc31-linux-evidence.json
\`\`\`

Requirements: Python 3.11+, \`psycopg[binary]>=3.2,<4\`, PostgreSQL **17**, genuine disposable DB with no existing \`qdp_outbox\`, \`qdp_group_current_preparations\`, or \`gc25_disposable_analytics_receipts\`. The gate uses actual \`git rev-parse HEAD\`; commit must equal the HEAD being reviewed. Credentials are read from environment but not embedded in report. A clean test database and real source-of-truth IAM/retention ports are an operator responsibility: naming conventions alone do not prove safety. The fixture creates and drops only its test-owned QDP/Analytics relations; its other test synthetic rows may remain until disposable test DB is discarded.

\`tests/test_group_c_e2e_gate.py\` is pure and can be run without PostgreSQL; passing that **never** means the full E2E ran.

## Confirmed interbranch blocker — **UAE Group C adapter is stale**

As inspected on **28 October 2026** in **peer branch \`feature/unified-analytics-full-module\`** (Draft PR **#38**, no changes by GC31), \`intelligence/unified_analytics/group_c_adapter.py\` still states GC057 only has \`frozen_unreviewed\` and lacks signed effective approval. In \`GroupCPersistedScoreReader.read_persisted_metric()\` it **unconditionally** returns \`ReadState.MISSING\` with \`c_effective_approval_unavailable\` after inspection. That is now **outdated** against GC-MOD-11/12/20: Group C later has signed human review \`058_group_c_human_reviews.sql\`, global activated config \`059_group_c_config_activation.sql\`, atomic C7 receipts \`060_group_c_calculation_receipts.sql\`, and live source/peer rights \`062_group_c_retention_revocation.sql\`. Thus a successful source-backed saved C7 would NOT become qualified in that UAE reader until a peer-owned code change.

**Required change in UAE branch, not made by Group C:** UAE owner should extend its Group C reader to verify the actual 058 signed decision/authorization, 059 current globally activated config, immutable 060 original C7 receipt, source/metadata/current+prior frozen member rights/TTL from 062 and original QDP/Group C project/video/source/version IDs under one serializable or repeatable-read authorized transaction. It must fail closed if any verified approval/currentness missing and must never reconstruct, clamp or normalize the C7 score. Then create independent UAE integration tests using *real Group C* migrated PG17 fixtures and the actual new UAE \`080..090\` schema. Do not weaken approval checks or treat 057 \`frozen_unreviewed\` itself as approval. This peer implementation is out of Group C branch scope and **needs owner intervention before true unified E2E**.

Additional production blockers: actual QDP deployment with current preparation view/real outbox, human IAM signature service, real Analytics receiver implementing same-DB durable ACK, genuine source permissions, Windows PG17 host and fresh+upgrade verification on actual HEAD. There is no claim the old PR #23 or UAE PR #38 has merged or passed production E2E.

## Acceptance truth

| Gate | Current implementation | Verified executed? |
|---|---|---|
| Pure Windows/Linux contract/report | Authored + CI configured | No |
| Disposable Linux PG17 fresh E2E | Authored + workflow step configured | No |
| Disposable Linux PG17 upgraded E2E | Authored + workflow step configured | No |
| Windows real PG17 E2E | Reproducible local CLI prepared | No |
| Real QDP provider outbox and source permissions | Mock-boundary contract only | **Blocked external** |
| Real UAE/Analytics reads/ACK, review parity | Known UAE adapter unconditional missing | **Blocked other branch** |
| Per-SHA proof/cross-platform compare | Redacted SHA-bound report available on local execution | **No reports produced** |

**Next GC-MOD-32 — Final release gate:** migration collision audit with other modules, branch-only diff, documented flags, QDP/UAE handshakes, rollback/release blockers and Draft PR, **no merge**.
