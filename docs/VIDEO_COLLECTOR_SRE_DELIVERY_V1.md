# VC-MOD-11 — single durable Collector→SRE outbox, lease/retry/ACK

**Branch:** `feature/video-collector-v1` · **Code:** IMPLEMENTED, **QA:** pending exact-HEAD GitHub Linux/Windows/PG17. **No live SRE inbox is installed and no delivery was attempted.** Do not mark delivery integration PASS until SRE M35 implements and exposes an authenticated durable inbox.

## Existing schema ownership / no duplication

Reuses one and only one already existing `collector_sre_outbox` table in migration **057**, populated **in the same D1 transaction** by VC-MOD-09 `ingest_page` per persisted canonical video. Applied migration SQL 057 remains byte-for-byte unchanged.

Durable outbox columns already support `project_id`, identity `id UUID`, `delivery_key`, `event_payload`, `pending|leased|delivered|blocked`, `attempts`, `leased_by`, `lease_generation`, `lease_until`, `next_delivery_at`, `delivered_at`, and machine-safe `last_error_code`. The schema prevents changing immutable identity/payload and requires leased rows to have owner+lease and delivered rows to have `delivered_at`.

No duplicate SRE/Discovery queue, no schema 058 name collision, no new D1 video table, no changes to main or the separate SRE feature branch.

## Real SRE contract matched — but NOT falsely called installed

The current SRE PR #22 (`feature/sre-production-v1-20261023`) already declares a **pure** optional contract in `intelligence/relevance/ports/inbound.py`, version `sre.discovery_ready/v1`, with `validate_collector_delivery`. This is the intended receiver shape:

```text
schema_version: sre.discovery_ready/v1
event_id: canonical UUID (Collector outbox primary key)
producer: video_collector
project_id: exact 32 lowercase hex
video_id: real D1 BIGINT
discovery_id: real D1 BIGINT
source_id: dtk_tiktok
source_policy_version_id: real D1 BIGINT
source_request_id: real D1 BIGINT or NULL
collector_query_ids: unique ascending sorted UUIDs from canonical
                     collector_page_video_queries
d1_persisted_at: real collector_ingest_pages.persisted_at (UTC)
occurred_at: real collector_sre_outbox.created_at (UTC)
event_sha256: canonical SHA256 of the exact other fields
```

**No inference:** this is only a candidate for future SRE analysis. The original Collector event `collector.sre_ready/v1` remains immutable and sets `evidence_scope=metadata_only`, `relevance_state=not_evaluated`. The local delivery envelope also carries QV publication ID, taxonomy version/SHA, SQG generation ID, exact hypothesis/query IDs and D1 page SHA **separately** from the strict SRE event, since SRE's current input schema forbids extra fields. No UUID→BIGINT coercion, fabricated SRE content version, taxonomy match, semantic match/no-match or LLM authorization.

SRE's **M35** authenticated durable Collector inbox is planned but has not been merged/implemented. The current SRE inbound validator merely checks shape and hash; **it does not authenticate a sender, attach D1 to a project, persist an inbox event or license semantic processing**. Therefore this stage does not claim end-to-end VC→SRE delivery.

## Implementation: project-only, opt-in, fail-closed

`intelligence/collector/sre_delivery.py` contains a local pure `DurableSREInboxPort`, no peer-module or SDK imports, and six operations:

1. `dispatch_one(project_id,worker_id,adapter=None,env=None)` — **disabled by default** unless `CM_COLLECTOR_SRE_DELIVERY_ENABLED=1`; no DB lease if disabled. An explicitly installed server-side adapter must advertise exactly `schema_version='sre.discovery_ready/v1'`, `authenticated=true`, `durable_inbox=true`, `ready=true`. Missing or not-ready SRE leaves all rows pending.
2. `claim_one(..., lease_seconds=90)` — read one due event in *one project*, `FOR UPDATE SKIP LOCKED`; increment `attempts` and monotonic `lease_generation`, set `leased_by/lease_until`. Never touches another project.
3. `_strict_event` — reads exact committed D1 page, video/discovery/metadata identity, source review/policy status and immutable approved `collector_query_sets`; verifies JSON SHA and each `collector_page_video_queries`→`collector_task_queries` link, query/hypothesis IDs, project/request and D1 timestamp order; rejects mismatches, expired rights or a non-UUID candidate. The SRE event is constructed from DB, not accepted from browser JSON.
4. `ack_delivery` — after the **trusted injected adapter** returns a receipt for exactly the event UUID, project and `event_sha256`, `persisted=True` and a durable receipt ID, perform fenced `leased→delivered`, `delivered_at=now()`, clear lease. ACK is project and lease generation scoped. This is a Python internal adapter boundary, **not a public HTTP endpoint**; a forged JSON digest or mock adapter does not establish production SRE persistence.
5. `fail_delivery` — sanitize error into a finite allowlist, persist bounded exponential delay (5 seconds doubling, max 3600), return `pending` or `blocked` once 8 attempts have failed. Malformed permanent D1/QV provenance is blocked before making external calls.
6. `recover_expired(project_id)` — project-only recovery `leased→pending` when lease has expired, or `blocked` at retry exhaustion. Never repeats delivered events. If real SRE committed an event but the local PostgreSQL ACK failed, retries use the **same event_id** and SRE must dedupe that immutable ID in its durable inbox.

## Security and operational boundaries

- No implicit background daemon, source rights override, OpenRouter/DTK post, SRE task run, payment authorization or classification. Enabling an environmental flag alone is insufficient to connect to SRE.
- SRE adapter is **only server-owned**, authenticated and configured by an explicit deployment integration. Its `capabilities()` declaration is a handshake, not cryptographic proof; **the adapter implementation must genuinely authenticate SRE and verify its durable ACK**. Without real M35, none is installed.
- Event SHA checks integrity but is not a signature/owner consent. Project scope and D1 FK/policy are rechecked at the moment of preparing delivery; receiver must independently authenticate and revalidate source rights/retention.
- This stage produces a candidate handoff, not SRE relevance. QDP must consume **only actual completed, independently qualified SRE output**, never Collector outbox or keyword hit count.
- `collector_sre_outbox` already stores enough durable state to track acknowledged delivery, but it does not retain the SRE receipt ID in a dedicated SQL column. A future additive audit migration may add this when the final receiver ACK contract is implemented; do not edit migration 057.
- `collector.sre_ready/v1` holds raw Collector origin, while `sre.discovery_ready/v1` is the separate strict current receiver projection. Do not conflate their version numbers.

## QA and next steps

- New `tests/test_video_collector_sre_delivery_v1.py`: 14 offline scenarios: disabled and missing SRE, tenant-scoped leasing, exact M02 event/D1/QV source, simulated durable ACK, SRE timeout, false ACK, wrong project/generation/expired lease, repeated/forged ACK, retry limit and backoff, expired lease recovery, poisoned D1/rights/query links, ACK-lost replay identity, unready adapter.
- CI step added for focused module tests. Real PG17 `SKIP LOCKED` interleavings, crash recovery, retention revocation and SQL CHECK constraints still require disposable database tests. The mocked SRE ACK is **not** a real receiver.
- Real VC↔SRE integration is blocked on separate SRE M35 authenticated inbox, explicit port registration and deploy-time delivery enablement. **Module remains mergeable on its own and defaults OFF**.

**NEXT:** VC-MOD-12 owner UI/APIs for genuine persisted D1 runs and QV→video→SRE-ready trace. No false promise of classified/approved videos.
