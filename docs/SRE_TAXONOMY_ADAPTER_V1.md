# SRE S2 — Approved RTB Taxonomy Adapter V1

Status: IMPLEMENTED; release completion requires actual CI evidence in tracker. Branch: feature/sre-production-v1-20261023. No main merge, paid API calls, SQL migrations or LLM execution.

## Contracts and functions

- intelligence/relevance/taxonomy_adapter.py: load_approved_taxonomy(project_id, publication_id, connect=None), load_classifier_contexts(project_id, publication_id, connect=None), read_authorized_taxonomy(conn,...). The latter is an advanced injectable entry point and requires a pre-existing consistent transaction; do NOT pass user-managed connections into HTTP handlers.
- intelligence/relevance/taxonomy_mapping.py: expected_flat_projection; verify_authorized_mirror (deterministic, no DB or network).
- intelligence/relevance/hypothesis_context.py: build_classifier_contexts; summarize_by_direction (pure, no DB or network).
- intelligence/relevance/v1_taxonomy.py: S1 validator now also understands source_field, review_status and nullable human-added source_quote/source_field from real RTB fact review.

## Database authorization and query isolation

The public loader validates project and publication IDs and opens a single REPEATABLE READ READ ONLY PostgreSQL transaction. Every read is scoped by project, run and approved deep publication, not by mutable active_taxonomy_id. It calls the existing authoritative Search Planner read_published_snapshot to verify tb_deep_publications, tb_taxonomy_publications, tb_runs, tb_deep_runs and human-approved fact review.

In the SAME transaction it reads tb_sre_taxonomy_links and checks that it points to the exact same RTB publication and SRE taxonomy version; both true ordered tb_deep_reviews with reviewer attribution; every projected SRE category, each SRE fact, every approved category-fact edge and category_example row.

No fallback to flat-only Taxonomy or unapproved categories is allowed, even when the project has a different active version.

## Defense-in-depth invariants

- Run and SRE taxonomy approved; both reviewed_by values and publication approvers present.
- Final second reviewed tree identical (canonical SHA) to deep publication. First review may be different due to expansion.
- Final quality_gate has zero severe gaps and zero duplicates.
- Reconstruct all published flat topics from source tree using EXACT existing RTB algorithm: UUID5(run UUID, source node ID); keep all directions, branches, hypotheses and order.
- Flat topics JSON, immutable tb_sre_taxonomy_links.source_sha256 and immutable SRE taxonomy checksum must match reconstructed flat projection.
- Every SRE category must match name, relation, full definition, include/exclude arrays, illustrative examples, typed hierarchy (source_node_id/parent_id/kind/rationale), fact IDs, review state, origin and independent flag.
- Every RTB approved fact must match mirrored SRE fact kind, normalized text, source quote or documented fallback, origin, approved status; exact edges from category_fact_links.
- Every mirrored SRE category example must match expected positive/negative illustrative text, polarity, example_kind=illustrative and absent video ID. Added verified_video, extra, duplicate and missing examples are forbidden.
- All 11 original RTB node fields are checked by S1 validate_taxonomy_snapshot; every hypothesis must have unique verified category_key and valid direction/optional branch ancestry.

Tampering, source mismatch, missing reviews, wrong project, unknown publication or missing flat projection fail closed. No auto-repair and no AI-generated approvals.

## Deterministic SRE classifier context

One item per RTB hypothesis (never parents), in sorted full-path order:
- schema_version: sre-hypothesis-context/v1
- project_id, rtb_run_id, publication_id, taxonomy_version, sre_taxonomy_version_id, taxonomy_sha256
- source_node_id and verified UUID category_key
- ancestry_path
- exact full 11-field direction, optional branch and hypothesis
- sorted union of approved facts cited by node and ancestors, carrying id/kind/text/source_quote/source_field/origin
- all same-parent sibling hypotheses: id/name/definition/include/exclude
- context_sha256 of canonical JSON of all other fields.

All data are copied; no shortening, summarization, locale guess, new fact or LLM call. A context is an input to future S4, not proof that content/media rights allow classification. Runtime must call database-authorized loader, not the standalone pure shape builder for untrusted snapshots.

## QA and tests

- tests/test_sre_taxonomy_adapter.py: fake PostgreSQL response cases verifying read-only, repeatable-read, strict tenant scope, full fields, hashed contexts, deterministic ordering, negative tamper matrix across reviews, checksums, all categories, facts, edges and examples, and no use of active_taxonomy_id.
- tests/test_sre_taxonomy_adapter_pg.py: *real* disposable PostgreSQL 17, existing DeepDB fixture with fake provider executes RTB fact extraction, manual fact review, first deep generation, H1 human review, second pass and saturation expansion, final H2 human review and atomic SRE mirroring. Adapter reads the true published records and builds leaf contexts. Checks cross-project denial, missing publication, no classifier run created, stable repeat reads.
- .github/workflows/sre-contracts.yml: S1+S2 pure tests Linux and Windows Python 3.12; dedicated real PostgreSQL 17 integration on Linux; repository-wide CI and Windows D1 smoke remain separate.
- Existing Search Planner published loader and relevance.pipeline preserved. Existing migration 001–015 unchanged. No new SQL table/view or provider I/O.

Commands:
python -m unittest discover -s tests -p test_sre_contracts_v1.py -v
python -m unittest discover -s tests -p test_sre_taxonomy_adapter.py -v
CM_TEST_PG_DSN=<disposable_postgres_dsn> CM_DB_DSN=<same_dsn> python -m unittest discover -s tests -p test_sre_taxonomy_adapter_pg.py -v
python -m unittest discover -s tests -v

## Subsequent milestones

S3 adds trusted D1 video intake, current rights checks and content versioning. S4 adds fake LLM worker and durable runs. S5 budgeting, S6 live LLM calls and S10 Analytics handoff remain separate. S2 never initiates paid requests. Do not merge draft PR until final S12.
