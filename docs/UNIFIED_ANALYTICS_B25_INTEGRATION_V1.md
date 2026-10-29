# Post-UA-MOD-32 — UAE-owned Group B25 candidate negotiation (no producer approval)

**Branch:** `feature/unified-analytics-full-module` · **Source peer:** `feature/group-b-full-module` at independently inspected head `ba660a8dd4b42711d73b1f0028aa6a7f4d5f5fec`. No changes to Group B, Group C, SRE, PIE, `main` or legacy PR #23.

## Delivered in this integration slice

- `intelligence/unified_analytics/group_b25_bridge.py`: pure, side-effect-free compatibility inspection of Group B25's actual `UAECompatibility` and `HandoffDecision` fields. Explicit exact wire names `group-b-uae-handoff/v1`, `uae-group-b-reader/v1`, `uae-group-b-exact/v1`. Uses UAE `GroupBExactPins` to bind the candidate to an already structurally validated **project, B run ID and B analysis ID**. No peer import, HTTP, DB, media download or models.
- Strict compatibility digest `capability_sha256` recomputed over the seven exact B25 fields using the same canonical SHA256 JSON serialization as the inspected Group B implementation. All advertised capabilities are bools, required B25/staleness/review/QDP flags must be true, and carousel requires explicit `understands_carousel_na=true` (video may omit this optional feature).
- Exact Group B25 decision shape and state (`candidate` only), empty tuple `reasons`, `uae_qualified is False`, exact `project_id`, `run_id`, `analysis_id`; strictly canonical payload SHA256 when a payload exists. Supports either a strict dict or the actual peer's frozen dataclass shape without importing peer classes. Unknown keys, wrong UUID lineage, bad digest, noncanonical JSON, false capability and forged `uae_qualified=True` are rejected.
- **Current B25 pure `validate_snapshot` returns `HandoffDecision(state=CANDIDATE,payload=None,payload_sha256=None,uae_qualified=False)`** even when it satisfies purely structural inputs. This produces `b25_candidate_payload_not_emitted` (blocked) rather than invented evidence. Even an independently *digest-consistent* hydrated payload yields `candidate_unapproved`, with `has_independent_producer_proof=False` and `approved_for_uae_qualification=False`. SHA integrity alone does not authenticate QDP, media, human review, source rights or Group A/B producer identity.
- Existing UAE `GroupBExactReader` continues to require source-backed B005/GB runtime SQL, exact Group A parent, current QDP membership and all B1–B8 facts; no bypass of that reader, of UA13/UA85 or of the UA19 live rights gate. The bridge does **not** emit `EvidenceRead`, `EvidenceRef`, `qualified` or any outbox event and is not automatically wired into the production path until actual B25 source authority is available. The bridge only closes the *shape/version inspection* subtask.
- `tests/test_unified_analytics_group_b25_bridge.py`: **26 authored** synthetic video/carousel, peer-dataclass, strict digest/identity/feature, fail-closed and no-qualification tests. Added to the no-DB local `qa_runner.py`; **not executed in this GitHub-only session**.
- `intelligence/unified_analytics/release_preflight.py`: fixed a real audit coverage gap: `git diff --name-only --diff-filter=ACMR` originally **excluded deletes** and could therefore miss removal of a peer-owned file. It now includes `D`, and rejects relative parent path traversal and backslashes in changed filenames. Two additional preflight tests written (now **19** in that file); run via the existing QA runner. It remains read-only and always returns NO-GO.

## Explicit integration blockers (owner matrix)

| Concern | Current guarantee | Missing for positive qualified |
|---|---|---|
| B25 pure candidate | Valid format, capability hash, exact project/B run/analysis match | DB-produced immutable B25 hydrated payload, authenticated originating peer/producer, verified source-policy/QDP/A media, B review/correction state and current permissions. Existing B25 decision has no source/policy/media proof in its top-level fields. |
| B legacy UAE adapter | Source-backed B005 exact B1–B8 facts, current QDP/A pins | A reviewed mapping between new GB055–059 B25 runtime SQL and the existing older B005 reader. UAE-owned reader changes require actual merged peer schema and disposable PG17 verification. |
| C adapter | Persisted C facts can be inspected but not approved | Independently authenticated **activated** Group C human-review receipt and current cohort rights; UAE C remains hardcoded `approved=False`. Owner Group C may need a new production proof endpoint. |
| SRE adapter | Earlier not_evaluated/blocked data fail closed | SRE actual live pair settlement and exact 054–068 reviewed/rights schema, fresh source-backed proof. |
| Release | UA085 no forged qualified/complete, stable Draft PR | Positive effective QDP/SRE/A/B/C integration, consumer live readside and local PostgreSQL17/Windows/Linux/Node QA, 10k/50k measured load, explicit reviewer signoff. |

**No upstream branch code should be modified inside UAE.** If Group B's pure B25 `HandoffDecision` never supplies trusted source-backed hydrated payload/attestation, the Group B branch owner must implement and document an independently verifiable producer interface. If it already exists elsewhere, the UAE integrator should inspect that specific SQL/receipt contract and add a separately gated source-backed adapter rather than guess from the pure candidate.

## Local regression commands

```bash
python -m compileall -q intelligence/unified_analytics tests
python -m unittest discover -s tests -p 'test_unified_analytics_group_b25_bridge.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_release_preflight.py' -v
python -m intelligence.unified_analytics.qa_runner
python -m intelligence.unified_analytics.release_preflight
```

`release_preflight` deliberately exits nonzero and prints `ready_for_merge:false`. The two new test modules remain **authored, not executed** until local regression completes. PR38 must stay Draft and unmerged.

**Next integration slice:** inspect authenticated source-backed Group B25 producer persistence/receipt and Group C activated human review before changing read adapter SQL. Do not transform the existing B25 candidate to a completed UAE qualification based solely on these shape checks.
