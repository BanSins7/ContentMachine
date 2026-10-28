# ContentMachine — SP-11 verified QV handoff projection / chunking

**Date:** 2026-10-26. **Branch:** `feature/search-planner-full-integration`.  
**Code status:** IMPLEMENTED. **Test status:** offline regressions committed, focused Linux/Windows/PostgreSQL Actions on exact SHA not verified (GitHub runner queue).  
**Scope:** SP11-A *read-only projection only*, not SP11-B durable inbox or Collector release.

## 1. Source of authority, no accepted-browser JSON

`SearchPlannerService.preview_verified_handoff(project_id, validation_run_id, chunk_size=50)` delegates to `VerifiedHandoffProjector`, which **only** accepts canonical project/QV IDs and uses the trusted server `load_verified_planning_input` DB loader. Its QV evidence comes from:
- completed, scoped **production** `qv_runs` (`embedding_mode='semantic_local'`);
- immutable `qv_decisions`, `qv_coverage`, exact original `sqg_candidates` and linked candidate↔hypothesis↔source fact rows (`query_validation/linkage.py`);
- real `qv_outbox` pointer, `sqg_outbox` source acknowledgment, immutable completed SQG generation;
- exact project C9 `taxonomy_publication_id`, version, `taxonomy_sha256`, approved published Deep V1 tree and both Human Reviews.

These validations are already in the SP10 trusted reader and SP08/SP07 readers, and SP11 rechecks the whole projection against that DB-derived source. The `query_id` of the external projection is **exactly** the original immutable UUID `candidate_id`, no random replacement and no resorting of UUID identifiers. `validation_state='accepted'` is derived exclusively from QV selected decisions; it is never accepted from caller-supplied JSON.

No arbitrary browser JSON, fake `accepted`, test-only embedding result, different tenant or unsupported Collector representation can become a handoff. The low-level pure projector is internal, **not** an HTTP endpoint or ingestion authorization.

## 2. Versioned JSON shapes

Source QV: `validated_queries/v1`, untouched.  
Projected top-level: `search-planner-handoff-projection/v1`.  
Projected chunk: `search-planner-handoff-chunk/v1`.  
Per-chunk Collector-compatible body: `validated_query_set/v1`, exactly matching the seven query fields of Collector PR #20 `intelligence/collector/query_intake.py` and its query-set top-level keys. We do **not** import that branch.

`docs/SEARCH_PLANNER_HANDOFF_V1.schema.json` is the versioned draft 2020-12 schema. It describes field limits, fixed nonexecution flags, identifiers and empty/nonempty states. **The Python runtime verifier is authoritative for hash/count/DB identity constraints**, which JSON Schema alone cannot prove.

Each envelope carries:
- project/QV ID, genuine QV outbox ID, SQG generation, C9 publication/version/SHA, generation input SHA;
- QV config/source-bundle SHA, embedding model key, full canonical `validated_bundle_sha256`, SP07 `linked_provenance_sha256`;
- original complete per-hypothesis QV coverage, its SHA and source decision count;
- stable ordered `selected_query_ids`, `selected_query_count`, `chunk_count` and `chunk_capacity`;
- every chunk's index/count, exact IDs, `collector_query_set_sha256`, complete `source_provenance`, `chunk_sha256`;
- total `projection_sha256` of the canonical full envelope.

`source_provenance` is not flattened away for Collector: it retains the full original selected SQG/QV row (branch ID, candidate rank, text/mode/language/angle/stance/rationale/scores, fact IDs), the selected QV decision, every original linked candidate/hypothesis/fact association, and the actual QV decisions for alias candidates. The Collector V1 body intentionally contains only minimal query fields; SP-owned provenance must remain durable in SP12.

## 3. Exact chunking and all-or-nothing integrity

Sort **every selected source candidate** by `(hypothesis_id, rank_in_hypothesis, candidate_id)`. For maximum chunk size 50:
- 0 selected → **0** chunks, `status='no_work'`, full original QV coverage and a real reason. No illegal empty Collector `validated_query_set/v1` fabricated.
- 1 → 1, 50 → 50, 51 → 50+1, **117 → 50+50+17**, 480 → 9×50+30.
- Other explicit sizes between 1 and 50 are versioned in the envelope and must produce exact contiguous chunks of that capacity (except the last). No silent truncate, skipping, deduping accepted queries or dropping the final remainder.
- The original candidate UUID is unique across all chunks and the ordered flattened chunks exactly equal the envelope's `selected_query_ids`.
- Duplicate aliases from other hypotheses stay in the separate SP provenance, even when a single query represents many hypotheses. Cross-query physical dedup in SP10 is **not** a reason to remove QV-selected query IDs from SP11 chunks.

`verify_handoff_projection` independently checks chunk bounds, same-source identity, exact QuerySet fields, canonical UUIDs, coverage/selected/source counts, all SHA digests, selected QV decision and alias evidence, and no external authorization. With `trusted_source`, it recomputes the entire envelope from the actual QV reader and requires exact equality. **A client-supplied SHA alone is not a trusted signature or QV proof.**

## 4. Security and operational boundaries

**Always false**: `external_dispatch_authorized`, `qv_outbox_acknowledged_by_this_projection`.  
**Always zero**: `provider_calls_in_this_projection`.  
**Always null**: `collector_receipt`.

Nonempty output status `awaiting_sp_inbox` is a read-only projection state: **not yet persisted**. No `qv_outbox` ACK, no acceptance by Collector, no video results, no new cost, no OpenRouter POST, no Collector REST, no background worker. SP10 immutable owner-approved held plans are separate records and SP13 will wire authenticated HTTP ACL. Real SP12 must persist this immutable envelope, chunks and full lineage in one PostgreSQL transaction, verify DB proof before QV ACK, and track a **separate** actual Collector receipt if an adapter later exists. Without Collector, the SP12 inbox may safely stay `awaiting_collector` rather than fabricating acceptance.

Collector PR #20 draft `validated_query_set/v1` allows 1–50 rows and requires `query_id, hypothesis_id, direction_id, mode, text, language, validation_state`. Its structural parse does **not** prove human/QV acceptance. SP11 uses its field contract while independent of installed Collector code. A QV row which cannot be projected *losslessly* to Collector's text/locale/node grammar is rejected instead of silently rewritten.

## 5. Files and tests

- `intelligence/search_planner/handoff/contracts.py` — frozen IDs/limits/field sets and strict compatible node and locale patterns.
- `intelligence/search_planner/handoff/projection.py` — DB-trusted only client, source lineage, ordered chunks, coverage and full manifest.
- `intelligence/search_planner/handoff/verification.py` — hash/schema/exact selected+coverage+alias checks and optional full trusted DB-source equality.
- `intelligence/search_planner/handoff/__init__.py` and `service.py` — safe public exports and read-only facade.
- `docs/SEARCH_PLANNER_HANDOFF_V1.schema.json` — frozen schema.
- `tests/test_search_planner_stage11_handoff.py` — 117→50+50+17; 0=no-work; 1/49/50/51/480; changed order; forged accepted JSON, aliased QV links, model fixture rejection, malformed mode/locale and IDs, SHA tampering, incomplete chunks, facade read-only and schema metadata.
- `.github/workflows/sp-stage01-fast.yml` — focused SP-01–SP-11 tests.

Run when a real runner exists:

```bash
python -m compileall -q intelligence/search_planner/handoff tests/test_search_planner_stage11_handoff.py
python -m unittest discover -s tests -p 'test_search_planner_stage11_handoff.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage10_task_planner.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage08_registry.py' -v
```

**QA not yet completed:** exact branch HEAD Linux/Windows Actions, PostgreSQL17 live approved C9→QV→SP11 source reads, full 117 SQL rows seeded/upgrade and source SHA corruption tests, Collector-ready schema parsing against actual *matching* version (Collector branch separately maintained), SP12 durable ACK/replay race/crash. These are actual next-stage/integration gates, **not** reasons to claim that the offline tests already PASS.

**Progress rule:** mark SP11 implementation `[x]` / struck through after commit; CI pending. Do not change main or Collector branch; no merge without explicit owner request.
