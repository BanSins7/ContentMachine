# Group B + Group C — certified fan-in integration (2026-10-29)

## Goal and actual execution topology

**QDP → two independently prepared/authorized streams → Group B (B1–B8) and Group C (C0–C7) → GroupBC certified join → future Unified Analytics acceptance.**

Group C is **not** run on B's classification text; it uses D1 metric snapshots, approved calculation configuration and frozen cohort evidence. B and C can execute concurrently. They are joined only after both own complete, source-current result. The joint event is **not** automatically UAE-qualified and never recomputes C7.

This integration lives only on branch \`integration/group-b-group-c-pipeline-20261029\`, based on \`main\` at \`e647060d69c00a5988773ca335564c47cd397103\`. Original feature branches and \`main\` were not written to.

## Imported source modules

- Full \`intelligence/group_b/\`: QDP and GA adapters, B1–B8 analysis, model budget, jobs/leases, review, source freshness, deterministic SQL005 analytics candidate reader, operator Studio and tests.
- Full \`intelligence/group_c/\`: QDP C intake, D1 reader/ports, frozen comparison cohorts, C0–C7 exact Decimal calculations, configurable approvals, job recovery, certification, Analytics bridge, WSGI Studio and tests.
- Original additive SQL migrations \`055_group_b_runtime.sql\` through \`059_group_b_observability.sql\`, and \`056_group_c_runtime.sql\` through \`064_group_c_performance_indexes.sql\`; all unchanged. They are distinct filename keys even where numeric prefixes overlap other migrations. \`intelligence.migrate\` applies filenames in lexical order with SHA-256 checksums.
- New \`065_group_bc_joint_handoff.sql\` adds scoped immutable composite outbox with lineage guard, same-pair idempotency and delivery ACK state.

Do **not** overwrite main's newer \`app.py\`, SP/RTB/Collector code, or migrations with versions from either module branch.

## Certified joint event

\`GroupBCPipeline.prepare(...)\` runs in one **SERIALIZABLE** Postgres transaction. Before committing it:

1. Reads *exact* B/C run IDs; both must be completed and their own inbox entries accepted. Joins by project, canonical D1 video, source, same QDP run UUID and exact source-policy version. **B and C QDP preparation digests can differ** and are pinned separately.
2. Calls the native B \`read_uae_handoff\` with an actual source-current \`CurrentLineage\`, an observed \`UAECompatibility\` and the trusted source/quality verifier. Requires a candidate whose canonical SHA256 matches the original B payload.
3. Calls the native C \`PostgreSQLAnalyticsBridge.load_persisted\` with real \`GroupCPorts\`. The bridge must certify source rights, approved config/cohort, QDP/D1 freshness and the stored Decimal C7. This is *not* an alternate C7 scorer.
4. Reconciles every identity and both policy versions against the chosen run pair. No B text or fabricated C score is copied into the joint event; only immutable versioned pointers and cryptographic digests.
5. Writes one \`group_bc_joint_receipts\` outbox record. A duplicate exact pair produces the same record, while a changed digest for that pair is a hard conflict. SQL guards reject misjoined direct inserts and updates to immutable fields.

\`GroupBCPipeline.deliver_one(...)\` locks one queued receipt \`FOR UPDATE SKIP LOCKED\`, **revalidates both peers**, and invokes a trusted downstream adapter that durably writes a consumer-side record **on the same database connection and transaction**. A matching ACK and independent \`verify_durable_ack\` readback are required before marking delivered. Missing rights, stale QDP/GA, changed B hash, C revision, model approval, source-policy conflict or unavailable downstream port leaves the receipt undelivered.

### Trusted application bootstrap example

\`\`\`python
from intelligence.group_bc import GroupBCPipeline
# conn: psycopg3 connection at top level (not already in a transaction)
# current_b: verified intelligence.group_b.integration.freshness.CurrentLineage
# compatibility: independently negotiated B UAECompatibility
# verifier: trusted B source/quality/freshness capability adapter
# ports: GroupCPorts connected to actual QDP/D1/registry/source/cohorts/config
pipeline = GroupBCPipeline()
receipt = pipeline.prepare(
    conn, project_id=project_id, b_run_id=b_run_id, c_run_id=c_run_id,
    b_current=current_b, b_compatibility=compatibility,
    b_verifier=verifier, c_ports=ports,
)
# A trusted scheduled dispatcher may subsequently call deliver_one with
# b_current_provider and a downstream transactional sink.
\`\`\`

**No default permissive verifier or fake source snapshot is installed.** Upstream producer listeners must independently call \`group_b.intake.receive_qdp_delivery\` and \`group_c.qdp_intake.QDPGroupCConsumer.receive\`; a QDP receipt must not be ACKed before its own DB transaction commits. Existing B and C workers are independently driven. Invoke \`prepare\` from the host when it has the exact two completed run IDs. This module intentionally does not start background tasks or buy model requests.

## HTTP and UI

The localhost \`app.py\` mounts Group B operator routes at \`/api/projects/{project_id}/group-b\` and serves \`/group-b.js\`, \`/group-b.css\`; writes fail closed unless a real privileged operator host has been installed.

Group C's \`GroupCHTTPAPI\` is an independent **WSGI** app requiring explicit production IAM+CSRF, allowed HTTPS hosts/origins and a database factory. It must **not** be mounted into \`app.py\`'s lightweight local header-only authorization. Static Group C Studio assets are copied, but are not evidence of a production-authenticated C endpoint. Do not expose operational Group C API until trusted IAM is configured.

The joint bridge is backend-only; no browser endpoint can supply \`CurrentLineage\`, \`GroupCPorts\`, a fake B approval or C7 value.

## Local verification (not GitHub Actions)

\`\`\`bash
python -m unittest -v tests.test_group_bc_pipeline
python -m pytest -q tests/test_group_b_contracts.py tests/test_group_c_foundation.py tests/test_group_bc_pipeline.py
python -m intelligence.migrate
\`\`\`

For full end-to-end validation on local PostgreSQL 17:
- Ensure schema migrations 003–005, B 055–059, C 056–064, and BC 065 apply without checksum changes (on a **fresh** local DB or already-migrated compatible DB).
- Deliver actual, consented QDP B and C preparations, finish the required GA/D1 work, B review/quality gates and C approved cohorts/config.
- Exercise both native worker chains; verify independent B/C completed records, then invoke \`prepare\` twice and assert same receipt ID/one outbox row.
- Inject mismatched project/video/source/QDP run/policy/preparation SHA; ensure no composite row.
- Revoke one current source-right or change a C snapshot/review, then attempt dispatch; ensure state remains queued and no durable ACK.
- Use a real transactional UAE acceptance adapter, verify consumer-side durable receipt, then dispatch twice; ensure one delivery and no duplicate.
- Apply PG17 schema and tests on owner's workstation; Github Actions completion has deliberately not been assumed.

## Integration blockers before merge into main

The branch contains assembled B and C source + joint bridge. **Do not label it a live deployment yet**: local PG17 full migrations, source-current QDP + GA + D1 proofs, configured C reviewer/cohorts, real B external QA, C trusted IAM, and downstream UAE durable sink are still environment/dependency acceptance gates. The **current main** now contains QDP and Group A; this branch merges that exact main without overwriting its QDP→GA integration. UAE still requires a trusted downstream acceptance adapter. The new join fails closed while any of them are missing. Live end-to-end green status and production readiness must be recorded from actual execution, not inferred from file presence.


## Bounded autonomous scheduling interface

\`GroupBCSupervisor(connection_factory).step(...)\` performs one bounded host-driven cycle. It discovers completed same-QDP B/C pairs without a previous joint receipt (up to \`max_candidates=32\`), skips explicitly blocked pairs, certifies the next pair using a **current B provenance provider evaluated within the serializable transaction**, and optionally dispatches it through a real durable downstream sink. If no sink is configured the receipt remains queued, never falsely delivered.

\`\`\`python
from intelligence.group_bc import GroupBCSupervisor

supervisor = GroupBCSupervisor(connection_factory)  # trusted psycopg3 factory
result = supervisor.step(
    project_id=project_id,
    b_current_provider=read_live_b_current_lineage,  # (conn, exact_pair)
    b_compatibility=observed_b_uae_compatibility,
    b_verifier=verified_b_source_quality_host,
    c_ports=real_c_ports,
    sink=transactional_uae_receiver,                # optional until UAE is ready
)
\`\`\`

Call this method after existing B/C worker completion or from an authorized scheduler. There are no self-triggering unbounded workers or provider calls in this bridge. \`GroupBCPipeline.deliver_one(receipt_id=...)\` also supports dispatch of a specific verified receipt so a stale earlier queued event cannot prevent a new good pair from being delivered. To recover old events, the host can call the original FIFO mode when dependencies are restored.



## Live QDP fan-out now wired to installed Group B and Group C

**Peer mismatch found and repaired:** the old \`intelligence.group_b.adapters.qdp.read_current_preparation\` expects a *pointer-as-persisted-event* and \`qdp-preparation/v1\`; actual integrated QDP persists \`qdp-event/v1\` and \`qdp-preparation/v2\`. Merely flipping its compatibility flag would fabricate an ACK. This branch instead adds \`intelligence.group_bc.qdp_b_v2.read_qdp_v2\`, which reads/locks the original hash-matched QDP outbox row, committed B-specific current preparation, D1 media/metadata, policy and source, then calls the **native Group B** \`receive_qdp_delivery\` with its original transaction, inbox, eight initial block states and immutable receipt. The source-policy/media validity is checked again within B intake, and QDP's dispatcher independently rechecks live SRE/media bytes before send and current QDP rights before accepting the ACK. The old B v1 reader remains unchanged for its own existing contracts.

\`intelligence.qualified_data.integrations.group_b.from_installed_group_b(connect,enabled=True)\` now supplies the actual versioned receiver; no self-authenticating browser ACKs. C uses the existing production \`from_installed_group_c(connect,enabled=True)\` → \`QDPGroupCConsumer.receive\` → real C inbox/run/score job, on an autocommit PostgreSQL dict-row connection. Both run from \`intelligence.qualified_data.group_bc_runtime.dispatch_groups_bc_once\` using QDP's native \`dispatch_once\` outbox claim, retry, deduplication and post-commit acknowledgment.

### Local opt-in settings

Keep all opt-ins off until the actual local PG17 database, approved QDP/GA media, SRE source rights and C config/cohorts are present.

\`\`\`dotenv
CM_DB_ENABLED=1
CM_DB_DSN=postgresql://YOUR_LOCAL_USER:YOUR_PASSWORD@127.0.0.1:54329/contentmachine
CM_QDP_GROUP_B_ENABLED=1
CM_QDP_GROUP_C_ENABLED=1
CM_QDP_GROUP_BC_AUTO_DISPATCH=0
\`\`\`

Manual **readiness**: \`GET /api/projects/<project_id>/qualified-data/group-bc/readiness\`. Manual **bounded delivery**: \`POST /api/projects/<project_id>/qualified-data/deliver-groups-bc\` with exact JSON \`{}\`; both use the existing loopback Host/Origin/CSRF protections. The endpoint accepts no caller-supplied event ID, source ID, positive approval or score. It leases at most one authentic QDP Group B and one Group C event for that project, independently. When \`CM_QDP_GROUP_BC_AUTO_DISPATCH=1\` the existing local QDP worker optionally executes one B/C dispatch cycle after its QDP/GA work (no paid B model work and no C7 scoring are started by QDP).

**B intake acceptance is not completed B1–B8 analysis; C intake acceptance is not a completed C7 score.** Actual B and C workers require their own authorized execution, real A and D1 dependencies, analysis/model consent, approved cohorts/reviews and completion. Once both have completed, the \`GroupBCSupervisor.step\` join runs with current trusted B/C adapters and may deliver to an explicitly connected transactional downstream sink. The composite is never an authoritative UAE-qualified result until that downstream actually consumes and certifies it.

### Additional local pre-merge tests

\`\`\`bash
python -m unittest -v tests.test_qdp_group_bc_v2 tests.test_group_bc_pipeline tests.test_group_bc_supervisor
python -m unittest -v tests.test_qdp_group_a_main_wiring tests.test_qdp_downstream_compatibility
python -m intelligence.migrate
\`\`\`

**Release caveats:** current source revocations are fail-closed through QDP's current view, B's source-freshness gate and C's retention verification. A separate durable B/C **revocation acknowledgment** protocol must still be completed if claiming complete compensating revocation delivery in the QDP outbox (GA already has this, B/C do not yet). Live PostgreSQL 17, local media bytes/ASR/OCR, model approvals, metrics/cohorts and UAE sink E2E are workstation/environment acceptance tests, not verified by this source-branch commit.
