# PIE-MOD-25 — Immutable Pattern Registry (candidate only)

Owner: intelligence/patterns_insights; branch feature/patterns-insights-full-module.
Schemas: pie-immutable-pattern-registry-batch-candidate/v1 and pie-immutable-pattern-registry-entry-candidate/v1.
Migration: intelligence/migrations/201_pie_immutable_pattern_registry.sql. Earlier 200 migration is unchanged.

## Scientific and security boundary

This stage stores **candidate-only**, **unverified** stage24 findings. It cannot claim actual qualified UAE data, source rights, reviewer approval, calibrated p-values, multiple-testing significance, causal effects or independent held-out confirmation. Registry supports only exploratory, rejected and needs_review. The status supported is permanently forbidden in this version, also enforced by SQL CHECK independent of application code.

There is no automatic publication, no source-read right, no physical image/video content, and no explicit production deployment in this change. Read-time rights and sensitive evidence redaction belong to stage26.

## Immutable identity

Every entry records stable candidate/series identity, hypothesis specification, experiment/feature/operator/contrast, exact predeclared numeric cutoffs and scale, source dataset snapshot/manifest hashes, C7-defined original matched high/low discovery video IDs, row SHA, UAE snapshot IDs, selected stratum SHA, technical/robustness/quality SHA, exact unweighted descriptive statistics, stage24 status/reason and 11 quality dimensions, and a claimed candidate owner ID.

- hypothesis_sha256 = canonical SHA of the exact source technical feature, metric kind, operator, contrast, cutoffs, unit, scale, factor ID and unverified origin.
- series_id = canonical SHA of project_id, owner_id and hypothesis_sha256. Different experiment evidence for the same hypothesis and owner keeps the series ID; changed hypothesis or owner produces a distinct series.
- source_rows_sha256 = canonical SHA of ordered discovery matched high/low original row IDs/hashes and UAE snapshot IDs.
- evidence_sha256 = canonical SHA of immutable source snapshot/quality/robustness/technical/holdout/controls/source rows/stratum and policy fingerprints.
- payload_sha256 = SHA of canonical exact payload with candidate status, evidence, provenance and statistical summary.
- validation_id = source stage24 quality report SHA. This ID is **descriptive validation provenance**, not an externally approved scientific validation.
- batch artifact_sha256 = canonical SHA of entire registered 1–16 factor family, in original order.

Changed source evidence yields a new fingerprint, not a silent UPDATE of the previous result. Identical evidence fingerprint MUST produce identical stored candidate bytes, or registration is refused.

## Database layout and transactional writes

201_pie_immutable_pattern_registry.sql adds two project-scoped tables without changing migration 200 or any other branch.

1. pie_pattern_registry_series: composite primary key (project_id, series_id), project_registry FK, immutable owner, factor and hypothesis. Unique project + hypothesis + owner ensures stable ownership series.
2. pie_pattern_registry_revisions: independent revision UUID, project/series/hypothesis composite FK, revision number, evidence SHA and payload SHA, snapshot/quality/stratum/row SHA, validation ID, immutable JSONB payload, status and created_at. Both (project, series, revision) and (project, series, evidence SHA) are unique.

Both tables reject UPDATE using the existing PIE immutable-evidence trigger. The revision status SQL CHECK allows only exploratory, rejected and needs_review. Authorized whole-project deletion may cascade during explicit retention cleanups; this append-only rule protects against silent UPDATE rather than blocking the project's retention lifecycle.

persist_candidate_pattern_registry(connection, ...source chain..., owner) first builds a candidate batch from replayed original stage24 and stage22/23 inputs. Inside a *single* SQL transaction for all registered factors, it obtains a per-project/series transaction advisory lock; inserts and checks immutable series identity; locks the series row; queries existing identical evidence.

- Existing evidence with byte-equivalent JSONB payload, same SHA/status/source provenance: returns existing revision and idempotent=True.
- Existing evidence fingerprint with changed stored payload/status/provenance: fails with typed collision; no silent overwrite.
- New evidence: INSERT MAX(revision)+1 and return stable UUID and idempotent=False.
- Failure anywhere in the batch aborts all inserts.
- Revision numbers are derived by the locked transaction, never accepted from the caller.

The project_id predicate is present on every database lookup and the composite FK prevents references to another project's series. PostgreSQL advisory locks and unique constraints serialize concurrent writes. Actual source read authorization is **not** granted by a write receipt.

## Replay and provenance checks

build_candidate_pattern_registry_batch requires original stage07/08/09/10/11/17 sources, stage22 numerical report/policy, stage23 robustness report/policy, stage24 quality report/policy and RegistryOwner. It replays the full stage24 candidate, checks pinned source snapshots, registered factor order, numerical and robustness policy identity and all release flags. No supported candidate, fake rights or valid inferential p-value can be smuggled into the registry by replacing the stage24 report.

replay_candidate_pattern_registry_batch parses duplicate-free finite JSON with valid UTF-8, verifies canonical SHA, independently rebuilds original candidate entry payloads, then compares exact bytes. Fake status/rehashed source, wrong policy, noncanonical whitespace and forged live flags fail closed.

The owner ID is a claimed local candidate owner, NOT a reviewer authorization or platform identity proof. It is immutable within a series; a changed owner means a distinct series.

## Synthetic arithmetic/revision example

The fixed earlier toy sample has four discovery matched pairs, high raw values (2,6,8,9), low raw (1,3,4,7), and mean paired raw contrast 5/2. Five source technical features produce five stable candidate series. Calling persist twice with the same source and owner produces five initial revisions numbered 1; the second call returns those exact revision UUIDs and idempotent=True, without extra rows.

Changing only the stage24 review-protocol **claim**, while rebuilding the stage24 report, changes the evidence fingerprint but NOT the hypothesis definition; persist creates revision 2 for each original series without modifying revision 1. Changing cutoff/metric definitions without recomputing the full source pipeline is rejected. Changing the registered hypothesis itself, if fully recalculated later, results in a new series identity.

## Tests, QA and limits

Files:
- intelligence/migrations/201_pie_immutable_pattern_registry.sql
- intelligence/patterns_insights/research/pattern_registry.py
- tests/test_pie_pattern_registry.py — **23 authored synthetic/static/optional PostgreSQL tests**
- intelligence/patterns_insights/research/__init__.py and intelligence/patterns_insights/__init__.py exports
- docs/PIE_IMPLEMENTATION_TRACKER.md

Test cases cover identity and lineage, deterministic replay, forged supported/release/review/rights data, immutable same-evidence semantics, changed evidence, owner identity, SQL tenant scope, constraint and UPDATE guards. PostgreSQL contract tests use only an explicitly disposable database whose name contains test and CM_TEST_PG_DSN is set. They apply the full migrations and verify idempotence, revision 1/2, tenant isolation and UPDATE refusal. No SQL action is executed without that environment.

Run on a full checkout:

    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_pattern_registry.py

PostgreSQL17 opt-in (disposable test DB only):

    CM_TEST_PG_DSN=postgresql://.../contentmachine_test python -m unittest discover -s tests -p 'test_pie_pattern_registry.py' -q

**QA truth:** GitHub accepted files but local unittest, compileall, actual PostgreSQL17 fresh/upgrade/immutability and real UAE end-to-end have NOT RUN. PostgreSQL integration tests skip absent CM_TEST_PG_DSN. The synthetic fixture mocks upstream quality replay (and upstream stage23/22/controls/matrix/holdout), not real authorized UAE. This is committed IMPLEMENTED code with QA NOT RUN, NOT a verified green or production deployment.

NEXT PIE-MOD-26: project-isolated querying, pagination and evidence redaction, mandatory current source-rights reevaluation.
