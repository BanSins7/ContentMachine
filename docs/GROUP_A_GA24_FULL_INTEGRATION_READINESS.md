# GA-MOD-24 — Full integration preparation and peer dependency handshake

**Status:** Group A implementation package prepared in `feature/group-a-full-module`; **NOT ready for production/merge** until the external fixes and real QA gates listed below are closed. No code or commits on QDP, SRE, Group B, UAE, Group C, PIE, or `main` were changed. No CI/DB/Windows/model tests were run during GA24 by user request.

## Exact observed peer code (read-only inspection, 2026-10-28)

| Peer | Read-only branch and pinned SHA | Public boundary actually observed | Group A integration disposition |
|---|---|---|---|
| QDP | `feature/qualified-data-pipeline-v1-20261023` @ `a0bb24e1c2d6ebd7be0a69c57a1978b66747cbfa` | `intelligence/qualified_data/integrations/group_a.py`: `from_installed_group_a(connect,media_roots,enabled)`, QDP → A `GroupAConsumerAdapter.receive`, QDP `qdp-event/v1` envelope translation into `qdp.preparation_ready/v1`, exact `group-a-receipt/v1` ACK. `revocation_delivery.dispatch_invalidation_once("group_a",receiver,...)` accepts `receiver.revoke(pointer,payload_sha,notice)` | Ready **only as opt-in producer/transport contract**. GA24 implements matching durable revocation receiver and origin checks; live combined DB not tested |
| SRE / D1 | `feature/sre-production-v1-20261023` @ `bf5e9b81faa1b8d62319f3a2a2060240a607e875` | `intelligence/project_sources/contracts.py` source-version, `project-media-asset/v1`, source pin, `sre_project_videos`, `sources`, `source_policy_versions`, `media_assets` | Read pinned original D1 rows and bytes; do not duplicate media/catalog or modify SRE |
| Group B | `feature/group-b-full-module` @ `0ecc6cc02d7823622ff1b331f16a00073935a283` | `intelligence/group_b/adapters/group_a.py::read_exact_a`; `intelligence/group_b/lineage.py::bind_exact_group_a(connect,b_run_id,ga_result_id,expected_sha256)`; strict `group-a-to-b/v1`, native A003/A004 and QDP/source/media pins | GA24 `GroupBExactBindTransport` uses **host-injected real B bind and re-read proof**, never imports/modifies B. Requires QDP Group B draft run and host-owned exact identity/ACK check |
| Unified Analytics | `feature/unified-analytics-full-module` @ `ae84cdb46512a250dcae4600e606ce91865194fe` | `intelligence/unified_analytics/group_a_adapter.py::GroupAVerifiedFactsReader` reads `ga_local_readable_results`, native A003/A004, `ga_steps` and qualified live QDP pinned source. Wire `group-a-to-b/v1` | Already defined read-only, no GA writes to UAE. It requires **all relevant GA steps completed and verified**; GA19 results without actual GA20 step completion intentionally remain invisible |
| Group C | `feature/group-c-full-module` @ `f1157c28328fd90f96d91d4f1bbb8e0c74d601cd` | Separate qualified C runtime and metrics, not a Group A writer | No direct GA code dependency. Shared QDP/migration integration needs separate throwaway checkout |
| PIE | `feature/patterns-insights-full-module` @ `53cd63bdd13c8261620568026b0afa759b445611` | `intelligence/patterns_insights/ports.py`: UAE qualified reader and source-policy reader | Indirect via UAE, **not a Group A→PIE direct export** |

This is a version/shape inspection, **not** a passed executable ABI or SQL migration test. The authoritative peer SHAs must be pinned again when they change.

## Changes already prepared entirely inside Group A

1. `intelligence/migrations/059_group_a_qdp_revocation_bridge.sql` adds an immutable QDP invalidation receipt inbox keyed by original QDP event UUID + exact original payload SHA/provenance + generation, without modifying QDP tables. Prior GA 054–058 migration file contents/checksums are **unchanged**. SRE/Group B/Group C may use the same numeric prefixes 054–065 but filenames and SQL namespaces differ; **numeric prefix alone does not guarantee safe combined ordering**. Whole-repo sorted migration sequence must be applied to an empty disposable database AND checked against an existing schema.
2. `intelligence/group_a/qdp_revocation.py::PostgresQDPRevocationReceiver.revoke` is an **exact observed QDP FIX-15 receiver**. Validates original `qdp-event/v1`, source SHA, original event hash, project/QDP/video/generation, then re-reads the real immutable `qdp_outbox` event/consumer lease in the same PostgreSQL instance, atomically invalidates matching GA runs, releases incomplete work, fences GA→B outbox and commits `ga_qdp_revocation_receipts`. An already invalidated event replays idempotently; no false ACK before commit. If A never received the ready event, still retain the revocation so a late old preparation cannot be treated as fresh. Requires explicit host registration; **no QDP import/patch**.
3. `intelligence/group_a/group_b_exact_transport.py::GroupBExactBindTransport` is a **host-injected** transport for existing `Group B bind_exact_group_a`; it neither imports B nor writes B tables. The owning host must select an authenticated pre-existing draft B run, bind an exact completed A result ID+SHA, independently **re-read** matching B commit pins and verify the receipt. On network-reply loss it checks the same B bound result and returns stable `consumer_receipt_sha256` without rebinding or billing. It intentionally refuses unsupported `image_post`, missing or conflicting IDs and mismatched source/QDP/media SHA.
4. `intelligence/group_a/integration_readiness.py`: a read-only `inspect_merged_schema(conn)` inventory for QDP, SRE/D1, Group A tables, B adapter prerequisites and UAE's `ga_local_readable_results`. Reports `schema_present_not_authenticated` (never marks a peer live), exact required version names, unverified external gates, `release_ready=False`. No main/peer imports or mutations.
5. `GroupAService` and module exports include these three explicit host features. `capabilities.py` describes GA24 revocation and B transport as opt-in; neither auto-starts. Pure negative/shape tests are committed to `tests/test_group_a_revocation_bridge.py` and `tests/test_group_a_group_b_exact_transport.py`; GA23 PostgreSQL 17 migration matrix includes GA059. Their actual execution remains **NOT RUN** by user request.

## Exact host installation sequence (after all necessary branches are deliberately combined)

All examples below are integration **recipes**; no peer module or live host has been auto-patched in this Group A branch.

1. Create a disposable *combined* worktree from the then-current `main` with reviewed selected QDP, SRE, Group A, Group B, UAE and applicable Group C/PIE commits. Do not merge these feature branches into Group A or use a production DB to validate dependencies. Record every exact SHA. Detect duplicate filenames, SQL object changes and conflicts before migrations.
2. Back up the target environment **before** any forward-only migrations: PostgreSQL data+schema+migration ledger, D1 media and immutable A003/A004/GA snapshots, QDP approval/event/outbox ledgers and B references. Document restore point, owner and read-only recovery plan. No automatic DOWN script is safe for immutable GA facts.
3. Apply **all foundational D1/SRE/QDP** schema dependencies first, then run the full combined `intelligence.migrate.apply_migrations` on a **disposable PostgreSQL 17 database**. Verify new GA 054/055/056/057/058/059 checksums and QDP/B/UAE migration dependencies. The filename sorter is lexical; e.g. `054_group_a_...` before `054_sre_...`, `055_group_a_...` before `055_group_b_...`; do **not** assume a shared number means the same migration.
4. Explicitly register the existing QDP → A preparation-ready adapter, with the current shared database and authorized local media roots:

```python
from intelligence.qualified_data.integrations.group_a import from_installed_group_a
from intelligence.qualified_data.outbox_delivery import dispatch_once
receiver = from_installed_group_a(
    connect,media_roots=[TRUSTED_EXISTING_MEDIA_ROOT],enabled=True
)
dispatch_once("group_a",receiver=receiver,connect=connect)
```

An ACK is proof of **committed GA inbox + initial step only**, not of completed analysis or the start of a model.

5. Register the new GA FIX-15 revocation bridge **independently** from the ready-event dispatcher:

```python
from intelligence.group_a import PostgresQDPRevocationReceiver
from intelligence.qualified_data.revocation_delivery import dispatch_invalidation_once
receiver = PostgresQDPRevocationReceiver(connect)
dispatch_invalidation_once("group_a",receiver=receiver,connect=connect)
```

The trusted host must process both ready and invalidation events, and retry failed deliveries with QDP's leases. The original QDP event hash and GA durable receipt are not a replacement for current project authentication.

6. Install an explicit GA20 worker registry for **real** GA06–18 local stage executors, source byte/permission verifier, current QDP and independent A003/A004 evidence authorizer; use bounded subprocesses, CPU/GPU admission, safe stop/restart. **Merely installing a DAG and publishing GA19 snapshots does not make all GA20 `ga_steps` completed or satisfy UAE.** Never mark sparse OCR or missing ASR as complete.
7. For a completed A result, the host must resolve the preexisting **QDP-owned Group B draft run**, inject actual `bind_exact_group_a` (from the peer B branch), its committed pinned B row reader and independent trust callback. Example:

```python
from intelligence.group_a import GroupBExactBindTransport
from intelligence.group_b.lineage import bind_exact_group_a
transport = GroupBExactBindTransport(
    select_b_run=host_lookup_exact_authenticated_QDP_B_draft,
    bind_exact_group_a=lambda **pins: bind_exact_group_a(connect,**pins),
    read_b_committed_pin=host_read_real_B_exact_committed_pin,
    verify_pin_authenticity=host_attest_real_B_commit_and_current_source,
)
# Group A owns durable GA20 outbox and independently verifies B receipt:
service.deliver_group_b_event_once(
    conn,worker_id="ga-b-transport-01",
    verify_current=host_recheck_live_QDP_and_source,
    transport=transport,
    verify_receipt=host_independent_validate_B_receipt,
)
```

The example requires **real host implementations** for three named functions; `lambda _:True` is unacceptable in production. A QDP event for B and the existing B draft must be ready before calling this. B independently rechecks exact native evidence and rights, then separately handles its own B1–B8/CTA/taxonomy. Group A never tries to write B7 semantic CTA back into A003.

8. Install UAE's existing `GroupAVerifiedFactsReader` with its `GroupAVerifiedPins` and **current qualified QDP reader** on the same connection, not from user-supplied IDs; it reads completed GA A003/A004 (including every durable step) and maintains its own source/QDP visibility. Never write UAE tables directly from GA.
9. UI host: existing `app.py` instantiates `ThreadingHTTPServer((HOST,PORT),Handler)` with no GA mount. To expose the optional GA21 Studio, a **host application integration change is required at deployment**: use `mount_group_a_handler(app.Handler,facade)` (see [GA21 guide](GROUP_A_HTTP_STUDIO_GUIDE.md)) as the HTTP handler; do **not** edit main or app.py in this Group A branch. Require owner/project authentication, Host/Origin and source authorization for every request.
10. Only after real CI, full 17 migration, Windows/Linux media, source revocation, genuine QDP→GA/B and exact B receipt, UAE read and real licensed human-gold tests PASS at the exact release HEAD may the host enable the feature flag. Keep default auto worker/legacy A1 bootstrap disabled until then.

## Required changes outside Group A — **for your approval, NOT performed**

| Severity | Other branch/file | Change required | Why Group A cannot do it alone |
|---|---|---|---|
| **P1 for full 5-kind QDP delivery** | QDP `feature/qualified-data-pipeline-v1-20261023` → `intelligence/qualified_data/integrations/group_a.py::QDPGroupAReader.read_preparation` and matching QDP `preparation.py` / `media_readiness.py` / `run_manifest.py` | Extend authoritative QDP-ready preparation and media proof beyond the current **`r["media_kind"]!="video"` → deny** path. Permit exact *ordered verified media* for `image_post`, `photo_carousel` and `mixed_carousel`, preserving source/QDP-run/policy/media SHA and event version semantics. Must specify how one canonical D1 video identity represents a carousel and how revocation works for any constituent asset. No fabricated group A complete text. | GA cannot manufacture a legitimately signed QDP event for image assets when QDP currently rejects them. Current QDP → Group A ready transport is video-only. **Requires QDP branch owner change if all five content types must enter through QDP.** |
| **P1 for automatic Studio on existing web app** | Deployment host / `app.py::main` on the eventual integration branch | Explicitly select `mount_group_a_handler(Handler,facade)` instead of bare `Handler`, providing project session verification and local connection/authority ports, behind feature flag | GA branch may provide adapter but cannot opt-in a different module's app server automatically without touching `app.py`. **This is a host-integration change, not a QDP/SRE/B change.** |
| **P1 for genuine Group B outbox send/ACK** | Integration host that starts GA20 delivery and B QDP draft scheduler; optionally B's public integration API if host cannot supply immutable committed pin read | Supply stable B draft resolver, real `bind_exact_group_a` call and durable independently authenticated B commit proof/receipt. GA provides compatible transport; **no Group B edit is inherently mandatory** if host injects the public B binding+read model. | A locally fabricated ACK cannot prove B accepted a result or prevent double model spending. |
| **P1 for UAE visibility** | Integration host GA worker/orchestrator registry, not UAE reader | Execute/persist authentic GA06–18 evidence and finish GA20 dependency DAG before publishing UAE-visible completed results; satisfy `GroupAVerifiedFactsReader` requirement for **all GA steps completed**. | Bare GA19 snapshot/outbox without completed stage evidence will be rejected by UAE; modifying UAE to skip the check would weaken integrity. |
| **P1 for true merge approval** | Combined integration environment (not peer branch code) | Test actual QDP/SRE/D1/B/UAE/GA with exact SHA and PG17/Windows/Linux, plus QA of user rights/media and real RU/EN human gold | Contracts and fake consumers are not cross-module E2E PASS. |

For **video-only** integration, no peer source-code change is currently proven necessary: the observed QDP ready adapter, B exact pin reader and UAE Group A adapter already expose the required shapes, provided the host wires them and full tests succeed. For the complete **five-content-kind** target, the QDP media-kind restriction is a concrete external blocker requiring approval. Separately, Group B deliberately rejects `image_post`; changing its B1–B8 product scope is a **product decision**, not a safe GA-only patch.

## Release / rollback / abort

**Preflight:** compare exact `main...feature/group-a-full-module` at final HEAD, assert every changed path is GA-owned (plus an explicitly reviewed README documentation addition), and assert `main` untouched. Verify migration filename uniqueness and checksum, QDP public versions, SRE source/media permissions, B draft identity, UAE adapter and real caller access. Update CI results **after running**, never transcribe queued/cancelled as PASS.

**Canary:** use one rights-cleared sample project, own verified media roots, no auto paid model, one local video, at most one opted-in worker and one B draft; QDP preparation → A inbox ACK → real GA06–18 stages → frozen GA19 A003/A004 → B exact binding → UAE read. Check known absence/NULL semantics and revoke after read. Then enable the Studio UI and inspect project isolation; only expand with explicit user approval and observed healthy metrics.

**Abort/rollback:** immediately disable GA QDP dispatch, GA20 workers, GA20 B outbox sender and optional GA21 HTTP mount. Cancel all GA-owned active leases using authorized APIs, stop new submissions, **preserve immutable A003/A004/GA/QDP/B history**. Restore PostgreSQL/media backup for a database-level rollback; don't delete GA054–059 forward-only migrations or rewrite `schema_migrations`, A003/A004 or Group B rows by hand. Any post-rollback D1/QDP/B ACK must be reconciled via the owning module, not by a phantom GA reply.

**Merge gate at this stage:** **NOT READY**: all GA00–GA24 implementation work can be marked prepared, but QA and the full five-kind QDP path are unresolved. No merge, auto-merge, real provider calls or production database actions permitted by this document.

**Previous PR:** [#26](https://github.com/BanSins7/ContentMachine/pull/26) is closed/unmerged and superseded, no action against its branch. [Draft #35](https://github.com/BanSins7/ContentMachine/pull/35) is the current Group A review vehicle; it must stay draft until external blockers + real E2E are resolved.
