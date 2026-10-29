# PIE-MOD-27 — Pattern Invalidation and Drift Monitor (candidate v1)

Branch: feature/patterns-insights-full-module. Main and other module branches are unchanged.
New wire contract: pie-source-invalidation-notice/v1.
Database migration: intelligence/migrations/202_pie_pattern_invalidation_ledger.sql.
State: implemented event-ingestion/invalidation primitives and read/write integration, but NO trusted upstream producer integration or recurring daemon is running.

## Scientific and authorization boundary

A stage25 immutable candidate must become **read-ineligible** as soon as any of its source evidence is independently known to be stale, denied, replaced or expired. Never UPDATE prior evidence, never take the newest event by timestamp to override an older revocation, never infer rights from a claimed source fingerprint and never promote stale findings to stage28 Insights.

This stage does **not** confirm the actual upstream UAE/QDP/SRE or Group A/B/C producer contracts. It offers typed adapter boundaries for authenticated external event delivery. A notice is a claim with provenance_claim=unverified_external_producer_claim. An injected CurrentInvalidationAuthority must return the exact Boolean True for that notice, independently based on current producer authentication and project scope, **before any SQL**. A connector/payload returning a truthy string, 1 or missing a method is refused. There is no live adapter installed here and no periodic background monitoring claimed.

## Exact source tombstones and upstream families

A SourceInvalidationNotice freezes project_id, target_kind, target_sha256, optional distinct replacement_sha256, producing module, enumerated reason, external source_event_id and timezone-aware event_effective_at. The immutable notice is encoded as sorted UTF-8 JSON and hashed to a stable SHA256 event ID. Its source event identifier is unique per project+producer, so retransmission of the same source_event_id with different content cannot silently rebind a revocation. A late but valid older event is accepted: the event timestamp is evidence metadata, not precedence.

Accepted target identifiers and their stage25 registry source:
- uae_manifest — source dataset manifest fingerprint; upstream UAE refresh, source-policy revocation, retention expiry.
- dataset_snapshot — stage25 frozen snapshot fingerprint; UAE new snapshot.
- matched_rows — SHA of all exact matched row/version identities; QDP, Group A, Group B, Group C or SRE revised upstream observations. A producer must supply the affected ORIGINAL matched_rows digest; changing single raw video without knowing original study membership requires upstream provenance mapping in the adapter, which remains unconnected.
- quality_report — stage24 quality fingerprint; corrected quality gate.
- holdout — original stage17 holdout SHA.
- numeric_report — stage22 numeric report fingerprint.
- robustness_report — stage23 sensitivity fingerprint.
- study_design — research design/configuration digest; changed model parameters or study config.
- stratum — matched grouping definition fingerprint.

Reasons are enumerated: upstream_revision, source_rights_revoked, source_retention_expired, configuration_changed, new_source_snapshot, source_evidence_invalidated. Impossible producer/reason combinations fail closed; only a qualified host adapter can attest actual source changes.

The target is the SHA of the **previous** affected evidence. The replacement_sha256 is a description, NOT proof that a new source is eligible. No event ever sets approved_live or supported.

## Append-only, out-of-order-safe PostgreSQL semantics

Migration 202 is additive after migrations 200 and 201; neither earlier file is edited.

1. pie_pattern_registry_invalidations — project-scoped immutable tombstone keyed by (project_id,event_id), with separate unique (project_id,producer,source_event_id), exact target kind/SHA, replacement, event effective timestamp, reason and canonical notice JSONB. Immutable UPDATE trigger. Index (project_id,target_kind,target_sha256).
2. pie_pattern_registry_recompute_requests — one immutable request per affected project/event/series/source revision, with a stable request_key SHA and initial state awaiting_trusted_source_and_review. Foreign keys bind the event, series and original revision to the SAME project; UPDATE forbidden. A request is a **planning signal only**, not a leased worker, payment approval, actual UAE refresh or guaranteed recomputation.

record_source_invalidation first checks the current authority. In one transaction it acquires the project-scoped PostgreSQL advisory lock, checks source_event_id replay identity, appends the tombstone (idempotent exact retransmit), and uses a set-based INSERT SELECT to enqueue a recomputation request for **every matching stored revision**. A single source may affect many factors and revisions; there is no arbitrary truncation of the invalidation backlog. Failed transactions do not append partial events/requests. All request identifiers are deterministic from the event SHA and revision UUID; uniqueness makes retransmission idempotent.

Stage25 persist_candidate_pattern_registry takes the SAME project advisory lock before reading and writing. It checks all nine frozen input fingerprints against the tombstone ledger **before** idempotent resend or new revision INSERT. An already-revoked source cannot be reintroduced under revision 2. If the append operation wins the lock first, invalidation sees the committed revision and enqueues a request. If revocation wins first, persistence detects the tombstone and refuses the write. Existing revisions stay immutable and available solely for authorized audit/retention.

Out-of-order event semantics are a persistent **OR set** of exact-source tombstones. A later delivery of an event with earlier event_effective_at still invalidates matching evidence, even if a newer unrelated event was processed first. A new qualified source/reanalysis may create a fresh revision with a different source fingerprint, but only after all stage22–25 preexisting source and authorization checks are satisfied.

## Read-suppression, no fallback, and future Insight preflight

Stage26 query_pattern_registry now performs the original owner/project-scoped latest-revision selection and then adds a project-scoped NOT EXISTS check against all nine exact-source invalidation target kinds. This anti-join happens AFTER choosing the latest revision; it may not resurrect an earlier favorable revision. The query already performs fresh UAE manifest and per-row current-rights checks and redacts source IDs. Missing invalidation migration or database failure causes a query failure rather than a silent unfiltered fallback. A revocation received between page requests is checked on the next request even when the cursor's revision watermark predates the event; no tombstone-time filtering is used.

is_revision_invalidated queries the exact project, series and revision and refuses nonexistent/foreign revisions rather than declaring them safe. Any unavailable ledger also fails closed. require_noninvalidated_pattern_for_insight is an explicit future consumer preflight: a tombstoned revision raises pattern_evidence_invalidated. **Passing this preflight does not authorize Insight creation, LLM use or supported publication**; current stage24 rights, reviewer, real-UAE and inferential evidence remain unverified. Stage28 must require independent authorization and stage24 restrictions in addition to this check.

Already exported or copied metadata from earlier responses cannot be retroactively erased; fresh reads and exports in the live service must always use the newly checked query. Stage27 implements no cache invalidation transport, no webhooks, no external job launcher, no background process and no automatic provider poll. Those integrations will require real authoritative peer adapters and further work.

## Negative tests and reproducibility

The 25 authored unittest methods in tests/test_pie_pattern_invalidation.py include: exact event digest/replay identity, late older event, UAE/QDP/A/B/C/SRE/source-policy/config/retention target categories, forbidden producer/reason/version/fingerprint/future time claims, mandatory authority-before-SQL, false/truthy authorization denial, project-isolated revocation lookup, missing ledger fail-closed, non-existent revision rejection and static migration/query/write checks.

Optional PostgreSQL17 tests (only with CM_TEST_PG_DSN pointing to a disposable database whose name includes test) cover fresh/upgrade migration, idempotent exact notice, five affected synthetic candidates, immutable recompute requests, out-of-order older notice, denial of stage25 stale reinsertion, stage26 empty result despite test-granted mock rights, conflicting producer source_event_id, immutable UPDATE and basic tenant-scoped queries. The stage26 existing tests were also adapted for the extra parameterized invalidation anti-join (28 authored methods there).

Synthetic source fixtures still mock prior stages' source replay. PostgreSQL tests are **not** evidence of actual live UAE/source-policy event support. The real peer branches and their event capabilities must be checked before any adapter is deployed.

Recommended commands on a full checkout:

    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_pattern_invalidation.py

Opt-in disposable PG17:

    CM_TEST_PG_DSN=postgresql://.../contentmachine_test python -m unittest discover -s tests -p 'test_pie_pattern_invalidation.py' -q

QA TRUTH: Source, SQL and test files are committed on the PIE branch, but local Python unittest, compileall, actual PostgreSQL17 fresh + upgrade + concurrent transaction testing, real authenticated UAE/QDP/A/B/C/SRE source events and passing CI were not observed. Authenticated producer, project rights, reviewer, independent holdout and publishable statistical evidence are BLOCKED. This stage's code may be marked IMPLEMENTED, not production verified.

NEXT PIE-MOD-28 — grounded pattern-to-insight semantic specification and prompt templates with strict original evidence IDs, noncausal caveats and injection protections. Stage28 must keep an invalidation check before any Insight creation attempt.
