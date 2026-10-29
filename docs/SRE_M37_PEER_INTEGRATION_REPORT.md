# SRE M37 — cross-branch contract audit / integration handoff

**2026-10-28. Source evidence inspected against real GitHub branch commits. Source CODE COMMITTED, exact-head Linux/Windows/PG17 test PASS not observed; real integrated Collector→SRE→QDP E2E BLOCKED.** Only SRE branch changed. No peer/main source files modified or cherry-picked. No LLM/provider/paying call.

## Exact peer source identities inspected

Repository: `BanSins7/ContentMachine`.

| Owner | Exact branch commit | Source path | Observed Git blob |
|---|---|---|---|
| Collector | `feature/video-collector-v1` @ `8e17f32aeb2501ec4dc0f6aca892e360201a6e8d` | `intelligence/collector/sre_delivery.py` | `8ffe77bfd70a179d5e466c7771118eaf2a757a74` |
| Collector | same | `intelligence/collector/ports.py` | `03016bf36e5e52349f0a971562cb50eb06ae206b` |
| QDP | `feature/qualified-data-pipeline-v1-20261023` @ `a0bb24e1c2d6ebd7be0a69c57a1978b66747cbfa` | `intelligence/qualified_data/sre_inbox.py` | `97bbdee780cb4a602d7ec0a432e901970daca320` |
| QDP | same | `intelligence/qualified_data/intake.py` | `2b551ed8ab16ebdce8973e09ebb57815c32b32c8` |
| QDP | same | `intelligence/qualified_data/repository.py` | `4d6bbe4b3183f8fea157a28b1f62aa779ac3142a` |

Observed via read-only GitHub repository file fetch with **exact commit refs**, not guessed from the current default branch. The SRE-owned `peer_contract_audit.py` additionally validates Git blob SHA-1 against *exact* local peer mirror files when such independent checkouts exist. If absent or mismatched it reports `blocked_missing_or_changed_source`, NOT `verified`. A Git blob SHA-1 is Git object identity, **not** transport authentication, deployment attestation, data-policy approval or quality PASS.

## FIX:03/04 — Collector ⇢ SRE discovery intake

**Observed partial compatibility, runtime deployment still BLOCKED.** Collector `sre_delivery.py` emits `sre.discovery_ready/v1`; SRE `ports/inbound.py` and M35 `collector_inbox.py` already validate precisely this syntax with matching canonical SHA-256. Collector keeps the selected QV candidate IDs as **UUID strings** and actual D1 `discovery_id`, `source_request_id`, `source_policy_version_id` as BIGINT integers. SRE never translates `collector_query_ids` to D1 `search_queries.id` BIGINT, does not derive category approvals from candidate text, and independently joins real D1/S3 project, source/request/policy/provenance rows.

**Specific mismatch:** Collector `dispatch_one` requires an injected private `DurableSREInboxPort` implementing `capabilities() → {schema_version,authenticated,durable_inbox,ready}` and `deliver(sre_event) → {schema_version:'sre.inbox_ack/v1',event_id,project_id,event_sha256,persisted:true,durable_receipt_id}`. M35 `receive_collector_delivery` originally returns `sre-collector-inbox/v1` internal state, NOT this ACK. The Collector dispatcher is default-disabled, so no real Stage10 delivery is implied.

**SRE-only repair committed:** `intelligence/relevance/collector_inbox_adapter.py` introduces `CollectorDurableInboxAdapter`: private opt-in adapter, default `enabled=False`, PostgreSQL schema readiness handshake, independently supplied trusted DB connection factory, actor authorizer and project→approved-C9 publication resolver; invokes M35 DB-authoritative durable inbox; translates only true committed `state='acked'` results into the Collector exact immutable event UUID/SHA receipt. Quarantined/foreign/mismatched events NEVER return a `persisted:true` ACK. This is **an installable SRE-side adapter**, not an already installed/delivered Collector E2E. Collector's feature flag remains untouched. No cross-branch code changes required **for the syntax/port handshake**; host installation and real PG17 receipt acceptance still needed.

**Exact practical server-side setup (once schema and security have been verified):**

```python
# SRE-owned adapter class imported ONLY in trusted private host deployment.
# connect = operator-managed connection factory returning autocommit=True PG.
# publication_resolver(project_id) MUST use trusted backend-approved C9,
# not any value from the Collector message.
# auth = independently authenticated project/action authorizer.
from intelligence.relevance.collector_inbox_adapter import CollectorDurableInboxAdapter

adapter = CollectorDurableInboxAdapter(
    connect,
    publication_resolver,
    actor_id="collector:trusted-host",
    actor_authorizer=auth,
    enabled=False, # leave false until combined-schema PG17 / auth QA PASS
)
# Inject this object into Collector dispatch_one(..., adapter=adapter) in
# the authenticated server host. Activation needs explicit operator decision.
```

Never expose `adapter.deliver` directly on untrusted HTTP. Only after actual durable receipt rechecks is it appropriate for host/operator to set SRE adapter enabled and Collector's `CM_COLLECTOR_SRE_DELIVERY_ENABLED=1`. Independent D1 selected backfill continues to operate without Collector.

## FIX:16 — SRE M36 ⇢ QDP (two independently blocking mismatches)

**BLOCKER QDP-01 — schema/version/granularity.** SRE M36 emits `sre.classifications_ready/v1` **per LIVE video**, with exact C9 sorted decisions and source SHA; invalidation is `sre.results_invalidated/v1`. QDP FIX-05 `sre_inbox.py` **strictly** accepts only `sre-analytics-event/v1` with `event_type='sre.classification_run_completed'` **per whole run** and exactly eight fields: `schema_version,event_id,event_type,project_id,run_id,publication_id,taxonomy_version_id,occurred_at`. QDP rejects M36 per-video ready envelope as unsupported version or fields. It has no direct `sre.results_invalidated/v1` inbox. *No direct delivery attempt made*: an invalid envelope must not be converted into a false positive ACK.

**BLOCKER QDP-02 — authoritative database source divergence.** QDP `intelligence/qualified_data/intake.py` `read_live_sre_run/get_live_result_rows` queries **`sre_classification_runs`**, **`sre_classification_results`**, and **`sre_human_labels`** and expects the original live run in `sre_classification_runs.id` with status `completed` and approved C9 lineage. New SRE production M11/M30/M32 stores a real LIVE run in **`sre_live_runs`**, per-item **`sre_live_items`**, authenticated per-pair **`sre_live_verified_pair_results`**, immutable **`sre_live_human_reviews`**, receipts **`sre_live_opus_receipts`**, and suppresses revoked source via M34 tombstones. Merely emitting a fabricated run-completed summary would not make QDP's authoritative reread find these rows. Conversely, writing fake rows into legacy tables to satisfy QDP would violate source provenance, audit and LIVE-only rules. **Do not mirror/forge evidence.**

**Requested changes for QDP owner, NOT performed:**

1. Create a **new versioned QDP live-result/invalidation receiver** or explicitly version the existing FIX-05 contract for `sre.classifications_ready/v1` + `sre.results_invalidated/v1` with durable UUID+SHA idempotency, project authentication, quarantine and ACK **after real commit**. Preserve old `sre-analytics-event/v1` behavior for existing callers.
2. Replace/extend the **authoritative read-only QDP SRE snapshot adapter** for the separate M11 LIVE namespace (the new run ID is `sre_live_runs.run_id`, not the legacy `sre_classification_runs.id`); exact item × category cardinality and current project-approved publication/C9 leaf keys, every M30 receipt `generation_authenticated=true` plus `validated_untrusted_output`, newest M32 human decision and revision, current D1/S3 rights/content hash, M34 tombstone absence, no M26 UNKNOWN charge, all items + batches terminal completed. Never infer LIVE from a legacy status or an envelope hash.
3. On SRE `results_invalidated/v1`, atomically record original ready-event UUID/SHA tombstone at QDP and suppress current qualified result/readiness **even if invalidation precedes ready**. Resolve QDP runs that reference the SRE run/video, call QDP-owned invalidation/compensation workflow; do not blindly equate SRE run UUID with QDP run UUID. Downstream Group A/B/C acknowledgement remains a separate QDP/Group responsibility.
4. Add QDP-owned combined-schema disposable PG17 positive and adversarial E2E checks for human correction, content-rights loss, late duplicate, ID hash collision, UNKNOWN provider, incorrect project/publication, offline_fixture fake, partially completed C9 matrix and crash between commit/ACK. No paid POST in deterministic tests.
5. Publish QDP exact commit and applied migration checksum inventory, then SRE M37 reruns `peer_contract_audit.inspect_peer_mirror` against checked-out exact peer refs. Until then `ecosystem_connected=blocked`, not `ready`.

## QA instructions and evidence boundaries

SRE tests authored: `tests/test_sre_collector_inbox_adapter.py` (private port, safe capability handshake, real SRE M35 response projection, replay/ACK shape, quarantine and source-bound resolver) and `tests/test_sre_peer_contract_audit.py` (actual SHA-pinned peer identities, missing/mutated checkout rejection, Collector UUID vs D1 BIGINT, incompatible QDP run-level event rejection, integrity checksum). Linux/Windows SRE workflow updated. These tests **do not import** QDP or Collector packages at SRE import/startup and **do not execute** a cross-branch PostgreSQL integration with a live provider.

Manual exact local mirror layout for SHA verification:

```text
peer-root/
  collector/intelligence/collector/sre_delivery.py
  collector/intelligence/collector/ports.py
  qdp/intelligence/qualified_data/sre_inbox.py
  qdp/intelligence/qualified_data/intake.py
  qdp/intelligence/qualified_data/repository.py
```

The files must be copied unchanged from the **above pinned commits**, not an arbitrary current branch checkout, else report `sha_mismatch`. Then call:

```python
from intelligence.relevance.peer_contract_audit import inspect_peer_mirror
report = inspect_peer_mirror("/absolute/path/to/peer-root")
assert report["ecosystem_connected"] == "blocked"  # until QDP owner repair
```

**Other active blockers not closed by M37:** malformed existing `062_sre_live_opus_receipts.sql` prevents fresh PG17 chain (see [forensics](SRE_M36_062_MIGRATION_BLOCKER_FORENSICS.md)); M34 secure erasure after D1/backups/third-party retention, M16–18 genuine ONNX model files and M25/26 full paid multi-C9 genuine provider execution not yet accepted. PR #22 stays Draft. Code committed ≠ CI PASS ≠ real integrated E2E.
