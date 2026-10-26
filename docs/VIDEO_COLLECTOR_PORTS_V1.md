# Video Collector — versioned ports and public contracts V1 (VC-MOD-01)

Branch: feature/video-collector-v1. These are implemented DTO/Protocol interfaces and thin adapters. Trusted publisher, transactional D1 ingest and SRE delivery are later stages; none is falsely claimed complete.

## Port owners

| VC port | Existing authoritative system | Implemented adapter | Stage that completes the write |
| --- | --- | --- | --- |
| SearchPlannerQVPort | main search_planner/query_validation/service.py | collector/adapters/main_qv.py (read only) | VC-MOD-03/04 |
| SourceProviderPort | intelligence/connectors/dtk.py | collector/adapters/dtk_provider.py (same transport) | VC-MOD-07 |
| CanonicalDiscoveryPort | D1 videos, discoveries, source_video_metadata | typed D1VideoRefV1 + DiscoveryCommittedV1 | VC-MOD-09/10 |
| SREHandoffPort | independent SRE subscriber | typed SREReadyV1, no classifier import | VC-MOD-11 |

No second SP query generator, task planner, D1 database, DTK HTTP transport, SRE classifier or QDP qualification logic is allowed in VC.

## Exact QV V1 upstream source

Main pending_validated_outbox(project_id, include_test=False, limit=50) returns outbox_id, validation_run_id, event_type and payload_pointer. Event type is validated_queries_ready/v1. Pointer includes validation_run_id, generation_id, schema_version=validated_queries/v1, embedding_mode and selected_count. The actual get_validated_bundle(project_id, validation_run_id, include_test=False) returns schema_version, validation_run_id, generation_id, project_id, embedding_model_key, embedding_mode, config_sha256, source_bundle_sha256, status, queries, coverage and decisions.

Current production mode is semantic_local with status=completed, selected_count in 0..480. No test_fixture event is eligible for production. Every candidate ID must remain an actual canonical UUID; source hypothesis/direction IDs, modes and languages are validated. Selected candidate IDs must exactly match selected decisions and coverage. Structural DTO parsing is not a database attestation.

Implemented DTOs:
- QvReadyV1.parse(project_id, pending_outbox_event) validates the actual source event and its matching pointer. No network/DB side effects.
- QvBundleShapeV1.parse(notice, bundle) checks version, source ancestry, hashes, selected UUIDs, decision states and per-hypothesis coverage. No permissions claimed.
- Collector internal validated_query_set/v1 retains existing 1..50 query format; VC-MOD-03/04 performs the trustworthy DB-backed conversion and shard publishing.
- A genuine zero-selected event produces a no_queries manifest later, never a fake job.
- Only a future DB transaction can safely ACK qv_outbox after durable publishing.

## Provider boundary

DTKSourceProvider exposes status(), submit(action,params), task(task_id) over the one installed intelligence.connectors.dtk module. Its only automated submit operation is search_posts with a keyword. There is no implicit hashtag fallback and no right to call submit before explicit held plan -> release -> durable submit intent -> quota checks. Mere construction or import does nothing on the network.

## Discovery and SRE identity

QV candidate_id is UUID; D1 videos.id, discoveries.id, source_requests.id and source_video_metadata.id are positive signed BIGINTs. NEVER cast UUID into D1's legacy query_id BIGINT FK or invent a query ID. UUID attribution remains in collector_task_queries and collector_video_hypotheses (many-to-many).

DiscoveryCommittedV1 describes an actually committed provider page checkpoint with source_request_id, real D1 references and a content hash. A provider task in state done does not imply such a receipt exists.

SREReadyV1 contains project ID, real canonical D1 video/discovery/metadata references, collector run/request, approved publication/version/hash, UUID query lineage, metadata_only evidence scope and not_evaluated relevance state. It has a deterministic content-derived delivery_key. SRE delivery may be disabled or SRE absent without losing the transactionally written future outbox event. Search provenance is never evidence of semantic relevance.

No future Search Planner v2 / Corrector v2 / SRE v2 format is silently accepted; each requires its own explicit versioned adapter and negative contract tests.

## Errors

Boundary code inventory in collector/ports.py reserves invalid_port_payload, event_integrity_mismatch, identity_type_mismatch and source_not_verified in addition to existing CollectorError schema/identity/project codes. Unknown schema versions and test embeddings fail closed. Unsupported provider operations must not invoke network calls.

## Independent flags

All flags default to 0, evaluated only when runtime_flags() is explicitly invoked:
- CM_COLLECTOR_QV_CONSUMER_ENABLED — future QV consumer dispatch.
- CM_COLLECTOR_LIVE_ENABLED — existing dedicated DTK worker eligibility.
- CM_COLLECTOR_SRE_DELIVERY_ENABLED — future SRE outbox delivery, NOT outbox creation.

Setting a consumer flag is never a substitute for backend owner authorization, rights/configuration, separate release or quota; no process starts on import.

## Public Python usage

~~~python
from intelligence.collector.ports import (
    QvReadyV1, QvBundleShapeV1, D1VideoRefV1, DiscoveryCommittedV1,
    SREReadyV1, SearchPlannerQVPort, SourceProviderPort,
    CanonicalDiscoveryPort, SREHandoffPort, runtime_flags,
)
from intelligence.collector.adapters.main_qv import MainQVAdapter
from intelligence.collector.adapters.dtk_provider import DTKSourceProvider
~~~

## Stage QA

~~~sh
python -m unittest discover -s tests -p 'test_video_collector_ports_v1.py' -v
python -m unittest discover -s tests -p 'test_video_collector_adapters_v1.py' -v
python -m unittest discover -s tests -v
python -m compileall -q intelligence/collector
~~~

Tests must demonstrate wrong project, generation, version, UUID/BIGINT confusion and fixture rejection, plus no import-time network access. This stage does NOT include database writes, provider searches, D1 persistence or live SRE handoff. Those are VC-MOD-02..11.
