# Group A → Group B: durable integration on ContentMachine

Integration branch: integration/group-a-group-b-pipeline-20261029.
Base: integration/group-b-group-c-pipeline-20261029, already containing main QDP + Group A and both Group B/C modules. No changes to main or original feature branches.

## Exact execution pipeline

1. QDP independently creates current, SHA-pinned A and B preparations for the same approved SRE/D1 publication.
2. QDP→Group A writes the authentic GA inbox; actual GA technical execution must finish A003/A004 and certify immutable ga_results. QDP→Group B independently commits B inbox and a real draft B run.
3. GA writer enqueues group_a.result_ready/v1 only for a completed GA result; partial and unsupported media remain blocked.
4. GA20 outbox claims a fenced lease and invokes intelligence.group_ab.bridge.GroupABTransport outside its database transaction.
5. The bridge selects exactly one B run with the same project, video, source, source-policy version, and QDP run. It refuses an ambiguous candidate or a missing QDP B receipt.
6. Native Group B bind_exact_group_a commits exact A003/A004 analysis ID, original GA result SHA256, manifest SHA256 and policy in the B draft transaction.
7. The bridge independently re-reads the original GA snapshot, current QDP A and B preparations, real media evidence, B stored pin and source policy. The GA20 outbox is acknowledged only after a matching durable B receipt proof.
8. B1–B8 use B's own independent readiness, budget, provider approval, worker, and quality gates. The handoff ACK does not mean B paid blocks have run.

## Corrected incompatible contract

QDP qdp-preparation/v2 has separate A and B group inputs. Since each group identifier enters the canonical payload hash, the two preparation digests are not interchangeable. Group B's old join incorrectly required GA qdp_input_sha256 equal B inbox preparation_sha256. The integration replaces this with two independent current QDP view joins: each SHA is matched against its own group and both point to the same real QDP run, D1 video, source, and source-policy version. GA snapshot validation uses the A preparation SHA. No invented equality, synthetic B draft, or migration rewrite.

## Code changes

- intelligence/group_b/adapters/group_a.py: independent QDP A/B digest checks and read-only exact completed B replay.
- intelligence/group_a/delivery.py: optional project-scoped GA outbox claims; preserves existing fenced lease/attempts and idempotent GA acknowledgment.
- intelligence/group_ab/bridge.py: GroupABTransport, GroupABPipeline, exact B selector, native bind, authentic post-commit readback and recomputed receipt SHA.
- intelligence/group_ab/runtime.py: database/feature readiness, manual one-event dispatch and opt-in auto-dispatch.
- intelligence/qualified_data/http_api.py: local host-protected GET .../group-a-b/readiness and POST .../deliver-group-a-b with empty JSON.
- intelligence/qualified_data/worker.py: optional GA→B one-event cycle after existing QDP worker operations, no paid model start.
- tests/test_group_ab_pipeline.py: independent A/B digests, one exact B selector, missing/ambiguous run rejection, source-policy revocation, duplicate receipt/replay checks and tenant-scoped dispatch.

## Local environment switches (explicit opt-in only)

CM_DB_ENABLED=1
CM_DB_DSN=postgresql://YOUR_LOCAL_PG17/your_database
CM_QDP_GROUP_A_ENABLED=1
CM_GROUP_A_ENABLED=1
CM_GROUP_A_QDP_HANDOFF_ENABLED=1
CM_QDP_GROUP_B_ENABLED=1
CM_GROUP_A_B_ENABLED=1
CM_GROUP_A_B_AUTO_DISPATCH=0

The QDP→GA handler still requires CM_QDP_GROUP_A_MEDIA_ROOTS pointing to real approved local media roots. Set CM_QDP_GROUP_BC_AUTO_DISPATCH=1 only when separately intending to allow QDP's A/B/C intake fan-out. For automatic GA→B transfer, explicitly set CM_GROUP_A_B_AUTO_DISPATCH=1 after configuring media, PG17 and source rights. GA extraction and paid B requests remain separately authorized operations.

Manual readiness: GET /api/projects/{project_id}/qualified-data/group-a-b/readiness.
Manual bounded step: POST /api/projects/{project_id}/qualified-data/deliver-group-a-b with JSON {} and existing local CSRF/Host/Origin checks.

## Pre-main local acceptance

Run python -m unittest -v tests.test_group_ab_pipeline; and existing Group A handoff/GA20, Group B exact pin and QDP v2 regressions.
Run python -m intelligence.migrate on disposable PostgreSQL17 database after backing up original database. Verify both fresh installation and upgrade with immutable schema_migrations SHA entries. All GA/QDP/B and BC migrations remain untouched.
On real local media, execute SRE→QDP A+B readiness, QDP→GA and QDP→B committed intake, GA DAG, actual A003/A004 evidence production and frozen GA result/outbox. Dispatch one GA→B event. Assert native B FK, manifest, SHA, policy and post-commit GA ack exactly once. Replay after lost acknowledgment, revoke source or swap SHA/policy, and verify no false ACK or duplicate binding.

Not claimed: PG17 live E2E passed, actual technical ASR/OCR executed, B paid model runs executed or fully verified downstream UAE. These require the operator's real environment. Authenticated B receipt means exact A result pinned in B, not that B1–B8 are completed.
