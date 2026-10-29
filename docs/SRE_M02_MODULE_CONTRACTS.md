# SRE M02 — Standalone Module Manifest & Versioned Ports

Status: **M02 implementation + QA running** (do not mark DONE until observed CI on exact code SHA). Branch: `feature/sre-production-v1-20261023`. Previous milestone M01: merge commit `09a8c7542abafa7c856e3555581a70ea52b87796`; its combined validation tracked separately.

## What M02 actually implements

An import-only, standalone SRE module boundary that requires **no Collector, QDP, Analytics or provider connector package**. No new migration, SQL table, HTTP handler, jobs, web scraping, paid calls, environment-variable access or external API request.

| File | Contract / behavior | Trust boundary |
|---|---|---|
| `intelligence/relevance/module.py` | `sre-module/v1`, `get_module_manifest()`, `validate_feature_flags()`; default OFF | Descriptive manifest is NOT runtime or DB readiness |
| `intelligence/relevance/ports/taxonomy.py` | `sre-taxonomy-ref/v1`, exact UUID5(source RTB run UUID, source node ID), approved hypothesis leaves | `load_verified_taxonomy_reference` delegates to S2 real PostgreSQL human-reviewed C9/RTB loader; pure reference validation does NOT approve a publication |
| `intelligence/relevance/ports/discovery.py` | `sre-discovery-ref/v1`, per-project video BIGINT, source/policy, D1 discovery BIGINT, content revision BIGINT and SHA256, nullable legacy D1 source request/query BIGINT | `read_verified_discovery` delegates to S3 live rights-gated PostgreSQL content reader; pure reference is NOT an authority |
| `intelligence/relevance/ports/inbound.py` | optional `sre.discovery_ready/v1` from future Collector producer: persisted D1 discovery/video/source-policy refs, separate sorted candidate UUIDs, event hash | Validates format + digest ONLY; no sender authentication, D1 ownership proof, inbox insert or implicit auto-attach. Those arrive in M35 |
| `intelligence/relevance/ports/outbound.py` | optional `sre.classifications_ready/v1`, only execution_mode=live, run_status=completed, exact per-leaf match/no_match/uncertain coverage and source content/hash and event hash | Validates transport shape ONLY; no delivery/DB provenance or live-only authorization by itself. M36 must read rights, full coverage, evidence and effective human reviews before publishing |

## Product decisions frozen and testable

- SRE's real-time classification model ID is **exactly** `anthropic/claude-opus-5.5`, only once the model catalog/preflight exists. `allow_fallback=false`, `allow_fast_llm=false`. Local embeddings `embedding_role=rank_all` with `allow_candidate_drop=false`. This metadata is NOT an operational model connector or evidence that the model is available.
- Feature flags: `enabled`, `offline_worker`, `live_classification`, `collector_event_ingest`, `qdp_event_delivery`, all default to explicit `false`. Only `enabled=true,offline_worker=true` is structurally permissible; runtime M03 still must verify DB and source permissions before actually starting. Attempting to turn on unimplemented flags raises `feature_unavailable`.
- `requires_external_feature_branches=[]`. Required existing main services: approved deep RTB C9 and canonical D1. The module can be imported with neither optional Collector/QDP module installed.
- Project ID is existing canonical **32 lower hex**, D1 video/discovery and legacy source/query IDs are integer BIGINT (not bool/string/UUID); RTB publication/RTB run/classifier run/category/event IDs are canonical lowercase UUID. The RTB category key is exactly UUID5 of the run UUID + source node ID. Do NOT silently cast candidate-query UUID to D1 search_queries BIGINT FK.
- Inbound Collector `collector_query_ids` is its own sorted UUID array. It is never a semantic relevance judgment, and the event cannot grant model rights.
- Outbound `expected_hypothesis_keys` must exactly equal disjoint sorted `match/no_match/uncertain` sets. No `not_evaluated` may be silently labelled `no_match` or exported as a complete result. `offline_fixture`, `legacy` and `partial` statuses are forbidden.
- Input/output `event_sha256` covers the exact JSON payload excluding itself. It detects accidental/unauthorized post-generation payload alteration but **is not a signature or sender authentication**. It does not prove source review, evidence correctness or user authorization: later M35/M36 require DB verification and access control, repeated at delivery.
- No privilege or budget check in a pure formatter; M03/M08/M24–M26/M36 own real permission/consent/ledger.

## API examples (no network, no DB for pure validations)

```python
from intelligence.relevance.module import get_module_manifest, validate_feature_flags
from intelligence.relevance.ports.taxonomy import load_verified_taxonomy_reference
from intelligence.relevance.ports.discovery import read_verified_discovery
from intelligence.relevance.ports.inbound import validate_collector_delivery
from intelligence.relevance.ports.outbound import validate_live_result_event

# Always safe without external modules or provider config:
assert get_module_manifest()["deployment_readiness"] == "disabled"
assert get_module_manifest()["live_model_policy"]["allow_fallback"] is False

# Only for an already configured, authenticated local service:
# ref = load_verified_taxonomy_reference(project_id, approved_publication_uuid)
# d1 = read_verified_discovery(conn, project_id, persisted_content_version_id)
# Pure event validators do NOT acknowledge or publish messages.
```

## QA

- `tests/test_sre_module_ports.py`: manifest default/flags/immutability; independent fresh-process import without psycopg/Collector/QDP/provider import; real S1-approved RTB shape and strict UUID5; D1 legacy BIGINT negative casts; signed-*looking* event digest/field schema checks; incomplete/duplicate/forged category sets; reject offline_fixture/legacy/partial; zero client-provided permission grants.
- Extended existing **PG17** `tests/test_sre_taxonomy_adapter_pg.py`: real two-human-reviewed RTB → verified M02 taxonomy port.
- Extended existing **PG17** `tests/test_sre_intake_pg.py`: real S3 project-scoped D1 → M02 verified content ref; foreign project denied.
- `.github/workflows/sre-contracts.yml`: Linux + Windows pure tests; PG17 S2–S4 includes M02 port integration checks.
- No new tests may call OpenRouter or any paid provider.

## Follow-up boundaries

M03 actual safe configuration and health; M04 minimal app mount; M05 migration compatibility; M06/M07 full adapters with real producer lineage; M35 authenticated Collector inbox and M36 rights-gated QDP outbox. Never announce Collector/QDP as integrated from these contracts alone.

**DoD:** code and negative tests committed only to existing SRE PR #22; observed focused + full Linux/Windows/PG CI green on exact code SHA; tracker marks M02 DONE only then. `main` unchanged and no merge performed.
