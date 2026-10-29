# SRE M06 — Authoritative C9 Approved Publication Port v1

**Date:** 2026-10-26. **Branch:** `feature/sre-production-v1-20261023`. **PR:** #22 (Draft). **Status:** CODE COMMITTED; exact-code GitHub Actions pending. This document describes implemented code, not yet a PASS claim.

## Scope and authority

The existing S2 deep-publication reader in `intelligence/relevance/taxonomy_adapter.py` remains the **only authority** for reading approved RTB/C9 v1 taxonomy in SRE. M06 exposes an ergonomic **single DB-authoritative read** via `intelligence.relevance.ports.taxonomy.load_verified_c9_publication(project_id, publication_id, connect=...)` (or `ports.approved_c9.load_approved_c9`).

The caller must specify the immutable `tb_taxonomy_publications.id`. The loader never reads `active_taxonomy_id`, does not use free-form search terms as hypothesis identity, does not accept draft/flat-only project snapshots and never writes or contacts a provider. Under the hood, S2 uses **one PostgreSQL REPEATABLE READ READ ONLY transaction**, checks actual project/run/publication lineage, approved hierarchical publication and deep runtime, human H1/H2 reviews (H2 matches final tree), zero severe/duplicate quality gaps, the approved fact quotes, projected categories, fact edges, examples and checksums.

No changes were made to RTB, C9, Search Planner, Collector, QDP, main or historical migrations.

## Stable `sre-approved-c9/v1` returned data

The transport bundle has:

- `schema_version`, `project_id`, `publication_id`, `rtb_run_id`, `taxonomy_version`, `sre_taxonomy_version_id`, `taxonomy_sha256`.
- `approved_snapshot`: all original reviewed C9 nodes with **eleven fields per direction/branch/hypothesis**, approved facts with original-source evidence, two human-review gate numbers and the exact tree digest.
- `taxonomy_reference`: M02 `sre-taxonomy-ref/v1` with **all and only approved hypothesis leaves**, `category_key=UUID5(rtb_run_id, source_node_id)`, canonical ordering.
- `node_projections`: all reviewed tree nodes (not just leaves), each with its stable UUID5, its parent source ID and its parent UUID5; needed for correct hierarchical category grouping.
- `hypothesis_keys`: sorted complete set of classification leaf UUID5 keys; **no drop or similarity threshold**.
- `contexts`: one complete source-grounded classifier context per approved hypothesis (root-to-leaf ancestry, optional branch, full source nodes, approved facts, sibling hypotheses, exact taxonomy digest, per-context SHA-256).
- `bundle_sha256`: deterministic canonical JSON SHA-256 of the whole bundle excluding its own hash.

The public pure `build_c9_bundle(snapshot)` and `validate_c9_bundle(bundle)` are **shape/consistency helpers, NOT authorization**. Anyone can generate a self-consistent hash. Production SRE must invoke the database-backed loader and recheck when the live workflow needs current approval/permissions. M06 does not make previously generated untrusted bundles authoritative. Historical C9 publications are pinned; a mutable "latest" pointer cannot silently redirect classification.

## Fail-closed negative cases

- Foreign project/publication/run, invalid UUID, bool-as-integer and schema version, missing/draft/flat-only C9 source.
- H1 missing, H2 missing or differing from final publication, empty reviewed_by, nonapproved SRE mirror.
- Stale source SHA, altered tree/leaf category UUID5, missing mirrored node, fact, edge or example.
- Dropped/reordered leaf contexts, altered ancestry, fabricated fact text, injected extra keys, altered hash or publication identity.
- A read that would mutate SQL or implicitly retrieve the active workspace is forbidden.

## Reproducible checks

```bash
python -m unittest discover -s tests -p test_sre_c9_approved_port.py -v
python -m unittest discover -s tests -p test_sre_taxonomy_adapter.py -v
CM_TEST_PG_DSN="postgresql://<disposable-ci-db>" python -m unittest discover -s tests -p test_sre_taxonomy_adapter_pg.py -v
```

The new pure suite is included in the standalone SRE workflow on Linux and Windows; the already existing PG17 real approved RTB fixture has additional M06 assertions. Full Linux/Windows regression remains required, along with the M01–M05 pending exact-SHA checks.

**DoD not yet demonstrated:** all relevant Actions completed successfully on the exact code SHA. Do not mark `M06` as fully verified or claim `MODULAR_READY` until they pass. No paid OpenRouter requests, autonomous running classifications, D1 source entitlement or downstream QDP events were implemented in this stage.

**Next:** M07 — D1 canonical video provenance reader, only upon a new user request.
