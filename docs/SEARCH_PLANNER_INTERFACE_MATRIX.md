# Search Planner SP-00 — Interface and Compatibility Matrix
Date 2026-10-26 · **Frozen analysis only**. Existing schemas are authoritative; target v2 structures below are proposals, not current DTOs.

## 1. Publication C → SP1 / SP2

| Source | Current status | Expected exact contract | Compatibility |
| --- | --- | --- | --- |
| Deep C9 V1 approved | Present in main | `tb_taxonomy_publications.id` + matching `tb_deep_publications`, `tb_runs/tb_deep_runs` approved, exactly Human Reviews 1 & 2, `tb_fact_review_submissions.approved_facts`, version + 11-field tree | **SUPPORTED** by `load_approved_publication` and `build_hypothesis_contexts` |
| B9 snapshot | Present in main | `knowledge_approved_snapshots`, `knowledge-snapshot/v1`, `snapshot_sha256`, `context_id`; facts `fact_id` is SHA (not a C/V1 UUID) | **UPSTREAM AVAILABLE**, but SP V1 loader does not consume it directly |
| B9→flat C | Present in main | `knowledge-c-handoff/v1`, `knowledge_c_generation_runs` (migration 053) | **NOT an approved Deep C9 V2 SP input** |
| Deep C9 V2 | PR #31 initial contract only | 14 fields: V1 11 + `source_observation_ids`, `search_signals`, `validation_focus`; A original evidence is in `video-intelligence/v1` dictionary, not a flat `observations[]` array | **NOT SUPPORTED** until C9 schema+publication+reviews real; never guess version from optional field presence |
| SP hypothesis context V1 | Present in main | `hypothesis-context/v1` ancestry and 11 exact node fields | **SUPPORTED**, preserve byte compatibility |
| SP hypothesis context V2 | Target | frozen C9 V2 IDs, A snapshot and B9 snapshot SHA, verified per-hypothesis video evidence + signals/focus | **WAIT ON C9 DEEP V2** |

## 2. SP3–SP8 verified published interfaces

| Producer → consumer | Version | Identifier fields and semantic rules | DB |
| --- | --- | --- | --- |
| SP2 → SP3 | `hypothesis-context/v1` | project + C9 publication + taxonomy SHA + ancestry, policy | `sqg_contexts` |
| SP4 → SP5 | stage D versioned schema/pins | one completion per batch; model-proposed facets/sketches ≠ actual video; server generates candidate IDs and assigned ancestry | `sqg_batches/attempts/candidates` |
| SP5 → SP6 | `query_candidates/v1` | all hypotheses must have coverage; candidate id UUID; `sqg_outbox` | `sqg_candidates, sqg_hypothesis_coverage, sqg_outbox` |
| SP6/SP7 → SP8 | `validated_queries/v1` | `validation_run_id`, `generation_id`, `project_id`, `embedding_model_key/mode`, `config_sha256`, `source_bundle_sha256`, `status`, `queries`, `coverage`, `decisions` | `qv_runs, qv_decisions, qv_coverage, qv_outbox` |
| SP8 outbox → next consumer | `validated_queries_ready/v1` | QV event pointer, pending/delivered state, only `semantic_local` publishable for production; ACK guarded in `ack_validated_outbox` | `qv_outbox` |

## 3. Actual SP8 ↔ Collector PR #20 incompatibility

| Field / behavior | SP8 `validated_queries/v1` | Collector `validated_query_set/v1` | Target SP handoff resolution |
| --- | --- | --- | --- |
| schema_version | `validated_queries/v1` | `validated_query_set/v1` | **Not directly compatible**; explicit negotiated adapter version |
| qv provenance | `validation_run_id`, source SHA, model/policy pins | no first-class `qv_run_id`/qv outbox ID in DTO | SP inbox retains full QV linkage + source hashes |
| publication | SP8 bundle omits `taxonomy_publication_id/version/sha` | all mandatory | read from trusted `sqg_generations`/C9 DB, never client |
| query ID | `candidate_id` UUID | `query_id` identifier | **equal UUID, unchanged** |
| query mode | `query_mode` | `mode` | exact value, no coercion from hashtag to keyword |
| human validation | row in `qv_decisions` with `decision='selected'` | string `validation_state='accepted'` | DB verified selected → projection accepted; client cannot assert |
| query attributes | `branch_id`, `source_fact_ids`, `angle`, `research_stance`, relevance/MMR and rank | 7 minimal query fields | preserve full provenance in SP-owned immutable record; projection is derivative |
| top-level selection | 0–480 selected queries in SP QV schema; 0 is valid | 1–50 queries per query set | stable chunks ≤50 plus terminal durable `no_work` |
| limit case | 117 accepted | per-set cap 50 | **50+50+17**, no lost UUIDs, chunk_index/count total |
| query text | SP QV max 120 chars | Collector intake validates ≤160; planner DTK search_posts ≤120 | **compatible for SP production candidates**, but still capability check |
| evidence tests | `test_fixture` can exist only under explicit test opt-in; production QV APIs reject it | simple `validation_state` claim insufficient | refuse test fixture and forged input before any executable job |
| project | project ID in SP QV/outbox with scoped SQL | same project in Collector query set | exact project/published C9 check via DB and FK |
| physical tasks | per-hypothesis candidates | Planner deduplicates by `(mode, NFKC+casefold(text), language, operation)` | retain all candidate/hypothesis links if one physical task |
| source readiness | no actual source registry | `operationally_verified=False` for all 4 draft descriptors | no tool is assumed technically ready; empty or incompatible port is fail-closed |
| commit/authorization | SP generation consent | Collector `commit_run` creates HELD plan, release separately | **two unrelated authorizations**; SP does not release provider tasks |
| receipt semantics | `qv_outbox.delivered_at` managed by QV ACK | needs persisted trusted `collector_query_sets` and later Collector job | SP inbox ACK only after atomic durable SP-ingest; separate Collector delivery + ACK |

Collector PR #20 target files reviewed read-only: `intelligence/collector/{contracts,query_intake,registry,planner,repository,policy_gate,http_api}.py`; historical draft migration `016_collector_planning.sql`, `017_collector_worker.sql`. It does not contain a production publisher of selected `qv_decisions` into Collector query sets. **Do not copy its modules into SP branch.**

## 4. Capability port / plan-neutral boundary (SP-09 and SP-10)

**SP-owned consumer port** (target, *not current API*):
- `SourceCapabilitySnapshot`: source ID, registry/adapter version, operations per mode, locale/market constraints, per-op input parameters, max length, readiness, price/quota/rate validity, catalog SHA, explicit unavailable/unsupported reasons.
- `match(query, catalog, policy) -> eligible/rejected`: no network or unknown-to-ready inference.
- `PlanPreview`: QV ID, pinned policy/catalog/limits hashes, ordered neutral tasks, every query link, unsupported/blocked reasons, optional upper-bound costs, `plan_hash`, `held` status.
- `CollectorHandoffPort`: optional `probe_capabilities`, `accept_verified_plan` (durable receiver receipt), `get_delivery_receipt`; cannot import concrete Collector at module import time.
- Catalog absent => snapshot `unavailable`, 0 executable tasks, informative preview; never preselect a fictitious source.

Collector's own registry is authoritative only once its actual version/capabilities are attached. `dtk_tiktok` currently **declares** keyword `search_posts`, but `operationally_verified=False`; hashtag search is not declared. Apify/YouTube/Instagram adapters do not advertise executable operations in this Collector draft.

## 5. Frozen internal handoff invariants (target, NOT a v1 DTO claim)
1. Snapshot entire `validated_queries/v1` authoritative bundle and verify every selected `qv_decision`, source candidate and exact generation/C9 project lineage.
2. Compute deterministic chunk order `(hypothesis_id, rank_in_hypothesis, candidate_id)` with ≤50 records; persist source `qv_run_id`, `qv_outbox_id`, `projection_sha256`, `chunk_index`, `chunk_count`, `total_count`.
3. `qv_outbox` ACK only **after** SP inbox insertion is durable and atomic in the same database transaction. A separate Collector status transitions to `awaiting_collector` / `delivered_to_collector` with signed/pinned receiver receipt; never conflate QV ACK with Collector ACK.
4. Every source candidate and linked hypothesis can be replayed, including `source_fact_ids` and versioned future observations. Chunk sorting must not drop any selected query.
5. If selected=0, persist `no_work`; no empty legacy Collector QuerySet which disallows zero.
6. Any forged accepted JSON, cross-project ID, wrong publication/SHA, schema mismatch, missing consent or nonsemantic test result => fail-closed; no provider POST and no fabricated receipt.
7. External uncertain paid submit is reconciled by Collector, not restarted by SP.

## 6. Required follow-on tests
`SP-01`: importing SP/app with `intelligence.collector` absent.
`SP-02`: approved C9 V1 exact byte-stable + C9 V2 (only real fixed release), unapproved/B9 SHA foreign rejection.
`SP-06–08`: production semantic model pinned, fixture blocked; all candidates/coverage saved and QV idempotent.
`SP-09–10`: unknown/unsupported/price/locale rejects, stable plan hash; one physical task retains multiple hypotheses.
`SP-11–12`: 117 → 50+50+17; 0=no_work; concurrent replay once, crash before/after commit, qv ACK atomic; mock adapter receipt vs missing Collector.
`SP-13–16`: full HTTP/UI/PG/Linux/Windows tests; GET never charges.
