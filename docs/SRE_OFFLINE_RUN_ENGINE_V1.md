# SRE S4 — Durable Offline Run Engine and Fake Classifier

**Scope:** S4 only. Branch \`feature/sre-production-v1-20261023\`. No network, model API, credits, quote/consent, or automatic Collector run. User-requested development only, no merge to main.

## Security and ownership

- S2 \`read_authorized_taxonomy\` checks an exact project-scoped published deep RTB tree, both human review gates, all 11 node fields, SRE projection and UUID5 links. S4 **never** accepts client-supplied category lists or an arbitrary SRE taxonomy ID. It snapshots all hypothesis contexts and their canonical digest.
- S3 \`read_content\` checks a **real** previously project-attached D1 content_version_id, fresh reviewed source permissions, current metadata observation, expiry, content hash and project scope before planning and AGAIN at result settlement. Revoked videos produce an explicit \`blocked_rights\` terminal item, never \`no_match\`.
- \`create_offline_run(conn,project,publication,content_version_ids,pipeline_id,idempotency_key)\` is an INTERNAL method. Future S9 HTTP must authenticate the local project owner before access. It cannot be safely exposed directly to untrusted remote clients.
- The one-stage pipeline is checked against stored \`sre_pipeline_configs\`. S4 rejects cascade and candidate-dropping routes; they belong to later S6/S7. A fixed fake model replaces the stage's provider, NEVER the real model registry.
- S4 uses \`execution_mode='offline_fixture'\` plus a companion \`sre_offline_runs\` table and \`stage_trace.synthetic=true\`. All future Analytics consumers must use \`sre_live_classification_results\` (filters execution_mode=live and current D1 rights), not raw results. Legacy runs default to \`legacy\`, never silently assumed live.

## Database migration 017 (additive)

- \`intelligence/migrations/017_sre_offline_run_engine.sql\`, on top of 016. Previous migrations unchanged.
- Adds \`execution_mode\` to \`sre_classification_runs\` (\`legacy|offline_fixture|live\`) and allows \`partial\` status. Existing SRE rows retain legacy mode.
- \`sre_offline_runs\`: UUID project-scoped FK to original run, RTB publication UUID, taxonomy/pipeline/request SHA, unique \`(project_id,idempotency_key)\`, status/cancel flag, timestamps; immutable lineage guard.
- \`sre_offline_items\`: per-run/project/video + pinned content_version_id/content_sha256/approved hypothesis contexts with SHA, bounded attempts, lease seconds, fencing token, lease_generation, state, error_code; composite tenant FKs; immutable input guard.
- \`sre_live_classification_results\`: view exposing only completed/partial genuinely LIVE-mode results still permitted by S3 readable content view. Empty for fake runs. It is a protective source for the future S10 downstream pipeline, not a real delivery implementation.
- Existing \`sre_classification_results\` stores one row per hypothesis; \`sre_stage_executions\` stores one fake stage (cost=0, provider=offline_fixture). Existing unique keys and taxonomy approval triggers remain active.

## Public/internal Python APIs

- \`intelligence.relevance.offline_runs.create_offline_run(...)\`: pins approved RTB + D1 + stored single-stage config, sorts video items, validates S1 run plan, stores exact canonical request hash, fake resolved model, full context SHA and unique items in one transaction. Replays identical requests without duplicate rows; mismatched requests using same key -> idempotency conflict. No SRE paid authorization needed because mode explicitly zero-cost synthetic.
- \`offline_runs.get_offline_run(...)\`: project-scoped status and item lineage.
- \`offline_runs.get_offline_results(...)\`: project-scoped fake results with **fresh per-content D1 rights gate**, omitting revoked quotes and counting omissions.
- \`offline_runs.cancel_offline_run(...)\`: marks queued/running items cancelled, invalidates their fencing tokens, reconciles parent run; old worker finish is rejected.
- \`offline_worker.claim_item(conn,lease_seconds=60)\`: project/run/item-locking \`SELECT ... FOR UPDATE OF r,i SKIP LOCKED\`; serialized within a run, concurrent across runs; allocates UUID token and monotonically increasing generation, enforces maximum attempts.
- \`offline_worker.heartbeat(conn,claim,lease_seconds=60)\`: only the current unexpired lease holder may renew; stale/closed/cancelled claim cannot renew.
- \`offline_worker.finish_item(conn,claim)\`: verifies fresh fencing token/lease, immutable SHA of stored/claim contexts and content, fresh S3 rights, deterministic fake output plus evidence grounding; persists ALL hypothesis verdicts and stage journal atomically, then closes item/reconciles parent run. Zero partial writes on rollback; no external request while SQL locks held.
- \`offline_worker.fail_item(...)\`: errors are technical and bounded; safe retry only because this is a pure zero-cost fixture; no fake negative verdicts.
- \`offline_worker.reap_exhausted(...)\`: records terminal failure for expired max-attempt leases and finalizes run; no infinite retry.
- \`offline_worker.work_once(conn,...)\`: one synchronous, directly invoked work unit, no automatic background daemon. A future S9/Windows lifecycle owner may call it repeatedly.
- \`offline_fake.evaluate_fake(content,contexts)\`: deterministic and *visibly synthetic*: \`match\` only when the approved hypothesis name appears literally in allowed metadata with an exact quote; \`no_match\` when the full exclusion appears literally; otherwise \`uncertain\`. Never represents real semantic reasoning or a quality estimate. Runs through \`validate_model_output\` evidence grounding and exact candidate-set checks.

## Crash/retry/cancel protocol

1. Plan from approved RTB and authorized D1, unique immutable \`(project,key)\` and request hash. Reject duplicate video IDs and expired/denied D1 content.
2. Claim item transaction commits token + generation + lease before any simulated work.
3. Processing checks token and expiry and commits all results + stage execution + terminal item + parent run atomically. For S4 this is safe because it performs NO external I/O. **Do not reuse this transaction-holding algorithm for paid providers.** S5/S6 must implement durable dispatch intents outside long-held SQL transactions and maintain reserved budget through unknown outcomes.
4. If a worker dies after claiming, an unexpired holder may heartbeat; when lease expires, another worker may reclaim with higher generation if attempts remain. The old token may never settle. Expired max attempts are terminal \`lease_exhausted\`.
5. If source rights are revoked before settlement, the item becomes \`blocked_rights\` with zero results, not semantic \`uncertain\`.
6. Cancelling a run atomically marks remaining queued/running items cancelled; finished items remain immutable. Only a not-yet-cancelled, current claim may commit.
7. With all items finished: all completed -> \`completed\`; mixture completed and blocked/failed -> \`partial\`; all blocked/failed -> \`failed\`; cancellation -> \`cancelled\`. Existing storage API now blocks writing to closed \`partial\` run as well.
8. No paid retry, no credit reservation, no provider token usage, no background service and no automatic S3 project attach.

## QA

- \`tests/test_sre_offline_unit.py\`: literal quote grounding, synthetic match/no_match/uncertain, context immutability and invalid/duplicate category rejection.
- \`tests/test_sre_offline_pg.py\`: genuine PostgreSQL17 full RTB first/second human reviews (fake extraction/expansion sender), SRE categories, two D1 content snapshots, duplicate-run replay and mismatch conflict, six persisted per-hypothesis outcomes, synthetic-only analytics view, zero USD, no duplicates after replay, foreign-project rejection, immutable snapshots, current-rights output filtering, block after claim, partial status, refusal of legacy writes to partial runs, cancelled claimed item, crash rollback, heartbeat, stale-generation fencing, concurrent claims using separate DB connections, exhausted lease and terminal run failure.
- \`.github/workflows/sre-contracts.yml\`: offline S1–S4 on Linux and Windows plus separate PG17 S2–S4 integration. Full repository Python/Windows CI runs independently.

Run \`python -m unittest discover -s tests -p test_sre_offline_unit.py -v\` or use an intentionally disposable PostgreSQL17 \`CM_TEST_PG_DSN\` for \`python -m unittest discover -s tests -p test_sre_offline_pg.py -v\`. Never run these fixture tests against the real user's database.

## Remaining work

- S5 financial quote/consent/reservation and paid dispatch intent before any real providers.
- S6 live provider connectors and strict typed model output, prompts and receipts.
- S7 embeddings/cascades; S8 calibration and human gold; S9 authenticated UI/API; S10 Analytics outbox; S11 rights purge and security/chaos release gate.
- Because old S3 and global analytical tables can still contain historic results, S4's read gate is not a comprehensive physical retention/deletion service. S11 must implement downstream deletions and audit tombstones. No production model accuracy claim is made.
