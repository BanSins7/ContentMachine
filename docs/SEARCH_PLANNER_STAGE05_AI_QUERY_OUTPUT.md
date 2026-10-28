# ContentMachine — SP-05: pinned AI Query + immutable Output Manager handoff

**Date:** 2026-10-26. **Work branch:** `feature/search-planner-full-integration`.  
**Status:** code and offline tests committed, full SP-05 signoff NOT YET CONFIRMED (GitHub Actions on exact HEAD and disposable PostgreSQL release gate pending). No real OpenRouter calls or paid usage in this stage.

## Target versus inherited implementation

The module already had real AI Query code (not reimplemented):
- `ai_query/prompts.py`: one pinned completion per batch, full HypothesisContext JSON, ThinkQE-inspired facets, MuGI-inspired hypothetical sketches, Jina-inspired lexical expansion; deterministic prompt/schema/strategy version hashes and strict OpenRouter JSON schema. These are proposals, NOT real search results or faithful reproductions of the three original research systems.
- `ai_query/transport.py`: OpenRouter one-shot transport; refuses provider fallback; unknown billing stays ambiguous; no hidden POST retry.
- `ai_query/quality.py`: lexical and structural method-trace audit, no fictitious retrieval metrics.
- `generation_engine/output_manager.py`: complete per-hypothesis coverage and selected query storage, durable outbox, atomic monetary settlement.

**SP-05 changes fix gaps in those boundaries**, rather than fork AI Query or create a competing registry.

## Implemented changes

| Path | New contract |
| --- | --- |
| `intelligence/search_planner/generation_engine/output_manager.py` | Hash-validate **every stored HypothesisContext** when settling a batch, including duplicate context IDs. Pin and audit Stage D ThinkQE/MuGI/Jina raw trace inside the same settlement transaction, not solely in the outer live worker. Refuse candidate/coverage mismatches on completed bundle read. |
| `intelligence/search_planner/ai_query/provenance.py` | Pure `verify_candidates_bundle`: require completed `query_candidates/v1`, valid server UUID pins, source SHA/model/prompt, exact selected candidate-to-hypothesis counts, unique candidate UUIDs, valid fact ID lists, honest `no_viable_queries` records. Never silently truncate or repair. `candidate_manifest_sha256` hashes the actual verified bundle. |
| `intelligence/search_planner/service.py`, `ports.py` | `CandidateBundleReader` dependency and `get_generation_candidates(project, generation)` read-only projection; verifies trusted reader's project and generation, returns `search-planner-candidate-view/v1` with digest and `paid_calls_in_this_read=0`. Existing downstream `query_candidates/v1` unchanged. |
| `intelligence/search_planner/api_contracts.py` | Advertises candidate read in public non-paid operations. |
| `tests/test_search_planner_stage05_provenance.py` | Offline tests: pinned prompt/trace cannot silently bypass atomic settlement, duplicate/corrupt SHA rejection, zero-candidate terminal outcome, broken coverage and fake project/generation rejection, SQL-to-candidate projection. |
| `.github/workflows/sp-stage01-fast.yml` | Focused SP-01..SP-05 QA now includes new regressions. |

## Critical security/data invariants

1. Generate at most one LLM completion per persisted batch, always after owner-confirmed funds and exact catalog/request quote. Historical OpenRouter code already enforces this; no automatic paid POST replay.
2. Do not accept model-provided fact IDs, project UUIDs, popularity metrics or real video claims as authoritative.
3. The immutable SQG context source SHA must be checked again at output-settlement time. If the context differs from its recorded digest, fail the transaction without publishing any candidates/outbox.
4. For pinned Stage D strategy, a reply must pass raw schema **and** the method-trace audit *inside* SQL settlement, even if a caller skips the outer live-worker quality check. The fake Stage C contract is not retroactively tightened by a new Stage D version.
5. Each approved hypothesis receives one coverage outcome. A successful generation can have zero selected queries only when each hypothesis explicitly has a no-viable reason; fake video matches must not be generated.
6. A completed candidate bundle must contain all selected queries under the correct hypothesis, exact per-hypothesis counts and unique UUIDs. Corrupted/read-faulty data fails closed before downstream semantic QV.
7. The SHA-256 digest represents the actual canonical `query_candidates/v1` body returned from trusted PostgreSQL, not just a provider assertion. Existing V1 format and original candidate UUIDs are maintained for QV.
8. Output Manager inserts charge/candidates/coverage/completion/outbox as one SQL transaction. A billed-invalid model response must still be settled as a known failure or held uncertain; do not refund or retry automatically.

## Public preview usage

```python
from intelligence.search_planner import SearchPlannerService

service = SearchPlannerService()
preview = service.get_generation_candidates(project_id, completed_generation_id)
# preview["schema_version"] == "search-planner-candidate-view/v1"
# preview["bundle"] is the unchanged, DB-verified query_candidates/v1
# preview["bundle_sha256"] hashes that canonical bundle
# preview["paid_calls_in_this_read"] == 0
```

This is a read-only projection, not a QV acceptance, Collector ACK, or source task. Test fixtures do not gain any executable status.

## Verification and unfinished gates

```bash
python -m unittest discover -s tests -p 'test_search_planner_stage05_provenance.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage_d.py' -v
python -m unittest discover -s tests -p 'test_search_planner_engine_stage_c.py' -v
python -m unittest discover -s tests -v
```

The GitHub Actions focused, Linux/PostgreSQL17 and Windows workflows must PASS on a single exact final SP branch SHA before claiming SP-05 complete. Full fresh+upgrade PostgreSQL and paid-maybe mock settlement / QV downstream E2E remain a SP-16 gate. No real external video recall or performance metric is claimed.

**Other dependencies:** Deep C9 V2 awaits actual publication from independent C Corrector PR; SP-05 remains operational on existing C9 V1, and neither main nor Collector branch is modified.
