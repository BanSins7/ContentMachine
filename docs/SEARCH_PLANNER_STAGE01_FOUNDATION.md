# SP-01 — Standalone Search Planner Service Foundation

Date: 2026-10-26. Branch: feature/search-planner-full-integration.
Scope: a read-only public service facade; NOT paid execution, Collector integration, workers, migration, UI or Deep C9 V2.

## Changed runtime files

- intelligence/search_planner/__init__.py — retains existing V1 exports, adds facade and DTO exports.
- intelligence/search_planner/service.py — project-scoped facade over existing approved C9 loader, context builder, generation status, and validated QV reader.
- intelligence/search_planner/api_contracts.py — frozen, snapshot-backed DTOs with canonical payload SHA and defensive identity checks.
- intelligence/search_planner/configuration.py — immutable bounded preview options, no pay/Collector flags.
- intelligence/search_planner/ports.py — typed protocols for reader/embedding/transport/PostgreSQL and future Collector adapters; zero concrete Collector imports.
- tests/test_search_planner_service_stage01.py — offline contract, identity, fail-closed, import and mutation isolation QA.

No changes to main, old SQG/QV schemas, Collector, app.py or PostgreSQL migration files.

## New callable facade

~~~python
from intelligence.search_planner import SearchPlannerService, SearchPlannerOptions
service = SearchPlannerService(options=SearchPlannerOptions(max_preview_hypotheses=48))
state = service.status().to_dict()  # collector.state == unavailable
preview = service.preview_contexts(project_id, taxonomy_publication_id, policy).to_dict()
generation = service.get_generation(project_id, generation_id).to_dict()
validated = service.get_validated_queries(project_id, qv_run_id).to_dict()
~~~

The legacy functions load_approved_publication and build_hypothesis_contexts retain their original imports and behavior.

### Typed response versioning

- search-planner-service/v1: an informational readiness snapshot; no DB/provider activity and no promise of a ready Collector.
- search-planner-context-preview/v1: checked approved source project/publication/version, full original V1 contexts, deterministic context hash; zero provider requests or spending permission.
- search-planner-generation-status/v1: trusted project-scoped generation state/counters; no transitions.
- search-planner-validated-view/v1: verified completed semantic_local QV bundle, identity and source pins, original query UUIDs, SHA; test_fixture is rejected and no external search dispatch is performed.

DTO dataclasses are frozen; JSON payloads are privately serialized and each to_dict() returns independent data. They are views, NOT receipts, bearer tokens or authorization artifacts. Reader ports are internal dependency injection, not HTTP user-supplied code. Existing SQG/QV database readers remain authoritative for project scope and immutable publication status.

### Ports

PublicationReader, HypothesisContextBuilder, GenerationStatusReader, ValidatedBundleReader, PostgreSQLConnectionFactory, EmbeddingEncoder and GenerationTransport describe existing dependency expectations. CollectorCapabilitiesPort and CollectorHandoffPort describe optional **future** versioned connection points; neither runs now. No single source registry is duplicated. The status must remain unavailable until later actual negotiated integration.

### Fail-closed constraints

Invalid policy or UUID stops before any read. The facade independently rejects a foreign approved publication, mismatched context publication/hash, duplicate hypotheses, wrong generation ID/status, test-only or forged QV id, duplicate selected UUIDs and unknown configuration keys. It preserves V1 C9 and does not infer C9 V2 from optional B9/A fields. All GET-like methods are non-paid and do not invoke workers or download weights.

## QA

~~~bash
python -m unittest discover -s tests -p 'test_search_planner_service_stage01.py' -v
python -m unittest discover -s tests -p 'test_search_planner_foundation.py' -v
python -m unittest discover -s tests -v
~~~

CI evidence is recorded separately, including exact SHA. This stage creates no new SQL and does not require external API keys. SP-02 adds approved C9 V2 dispatch only after its upstream real schema/publication is available.
