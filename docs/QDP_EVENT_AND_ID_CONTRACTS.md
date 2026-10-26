# QDP contracts: identifiers, events, status and versioning (FIX-00)

**Authority:** Existing code/SQL in QDP PR #24 and SRE/Group A branches, audited 2026-10-26. This is a QDP-owned integration proposal. SRE future event/manifest fields require handshake with SRE owner before declaring LIVE compatibility.

## IDs — never coerce one domain into another

| ID | Type | Owner / invariant |
|---|---|---|
| project_id | 32 lowercase hex TEXT | local project registry, exact project joins |
| C hypothesis.id / source_node_id | STRING e.g. H1 | approved C hierarchy; NOT category UUID |
| category_key | UUID | SRE taxonomy category from approved project version |
| publication_id | UUID where upstream exposes it | approved RTB C9 publication |
| taxonomy_version_id | BIGINT | `sre_taxonomy_versions.id`; same project |
| sre_run_id | UUID | `sre_classification_runs.id`; completed LIVE only |
| content_version_id | BIGINT | `sre_content_versions.id`; exact project/video/source |
| sre result_id / human_label_id | BIGINT | SRE evidence and human revision |
| video_id | BIGINT | D1 `videos.id` canonical publication |
| discovery_id, source_request_id | BIGINT when present | Discovery D1; do not invent absent joins |
| SQG candidate / validated query id | UUID or documented TEXT | not the D1 `search_queries.id` BIGINT |
| metadata_id, media_asset_id, metric_snapshot_id | BIGINT | D1 original observations/assets |
| source_id | TEXT | D1 sources (distinct from platform) |
| source_policy_version_id | BIGINT | source permission/version snapshot |
| qdp_run_id, SRE event_id, QDP outbox event_id | UUID | durable run/event keys |
| QDP worker job_id | BIGINT | shared jobs, namespaced `qdp.prepare_video` |
| snapshot_sha256 / input_sha256 | lowercase 64-hex | deterministic canonical JSON digest |

## Current observed QDP interfaces: KEEP

HTTP: `GET /api/projects/{pid}/qualified-data/preview?sre_run_id=...`; `POST .../runs`; `GET .../runs` and `.../runs/{uuid}`; `POST .../runs/{uuid}/cancel`. QDP uses `QDPError(code, message, status)`, `options`, `qualify_sre_rows` and `work_once`. These must remain compatible.

Worker type: `qdp.prepare_video`. Existing `qdp_preparations` has one row per (run, video, A/B/C), states ready/blocked. A ready means *input prepared*, not FFmpeg completed.

## Proposed versioned QDP events

Events below are TARGET contracts, NOT rows already emitted by PR #24. FIX-05 and FIX-12 implement durable inbox/outbox and dedicated receipt tables:
- `sre.classification_run_completed` upstream notification (exact producer schema to be verified with SRE), accepted only after QDP authoritative DB re-read.
- `qdp.qualified_set_created/v1`: selection summary, project/run pin, immutable digest.
- `qdp.preparation_ready/v1`: reference to exact current group input; no data copy or paid dispatch by default.
- `qdp.preparation_blocked/v1`: group and blocking reason; no execution job.
- `qdp.preparation_invalidated/v1`: source/human/config revocation; current readiness closed.
- `qdp.run_completed/v1`: terminal counters separated from downstream execution.

The QDP-owned `qdp-handoff/v1` pointer has canonical event UUID, project ID, QDP run UUID, canonical video_id, A/B/C group, preparation input SHA256 and schema_version. See `QDPHandoffPointer`. This is a reference only: **DB current eligibility + exact input hash must be independently revalidated by consumer**. Event payload hash, producer/consumer names and event schema metadata are stored in the future durable outbox/receipts and must be validated by dispatcher.

## Reliability and replay

(1) SRE commits results+event → (2) QDP inbox verifies event ID/hash and DB live run → (3) QDP explicit run and Worker transactionally commit qualified snapshot and preparations+outbox → (4) consumer persists own inbox+job atomically → (5) ACK to QDP only after durable downstream commit. Replays with same ID/hash are idempotent; same ID/different hash is quarantined. A/B/C each need independent delivery state. Delivery ACK is not analysis completion.

SRE event arrival does not authorize automatic paid processing. QDP GET/preview cannot mutate or dispatch. Worker must not call OpenRouter/DTK or fabricate missing source data.

## Provenance and currentness

Immutable C publication and taxonomy → SRE live run + complete content×leaf manifest → SRE decision/evidence/content hash and human review revision → D1 exact discovery/video/metadata/metrics/media source IDs → QDP selection/enrichment/preparation hashes → consumer receipt/job IDs → actual independently completed outputs.

Do not infer source identity from query text, infer unknown published_at from discovery time, infer zero metrics from null, or derive SRE relevance from the video title.

## Status vocabulary

`SRE match` ≠ `QDP qualified` ≠ `QDP A/B/C ready` ≠ `Group completed` ≠ `Analytics qualified`. Independent run statuses are queued/running/completed/partial/failed/cancelled; current data may become invalidated after historical completion. Define exact transition and invalidation semantics in FIX-11/15.

## Contract change rule

Pin exact schema names, owners and versions to tested SHA at integration. Unknown peer fields remain unresolved; use capability = unavailable/incompatible, never auto-generate placeholder publication, content, media or score IDs. Introduce v2 only when compatibility is reviewed; preserve historic v1 rows.
