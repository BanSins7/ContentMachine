# PIE-MOD-26 — Safe Pattern Retrieval, Explanations and Dashboard Queries

Owner: intelligence/patterns_insights, branch feature/patterns-insights-full-module.
Schema: pie-safe-pattern-registry-query/v1.
Source: append-only stage25 candidate registry and a NEW current UAE provenance check for every result.

## Safe capability

query_pattern_registry(connection, query, authorizer, reader, gate, signer) is an explicitly **latest-revision-only** project-owner-scoped query API. It filters by project, exact hypothesis digest, Group B feature name, platform, candidate status, minimum matched discovery pairs (min-N), supported predeclared numeric bins and page size (1..80). Typed RegistryQuery refuses unbounded pagination, malformed hex IDs, raw evidence view, all-history fallback and a supported status.

RedactedPattern returns an opaque registered series/hypothesis identifier, stage25 revision, feature family/name/kind, homogeneous video platform, candidate status, matched pair count, supported-bin count, exact original descriptive raw mean (numerator/denominator plus source unit), noncausal monotonic direction and a fixed human-readable explanation. It NEVER returns raw publication/video IDs, creator IDs, source URL/media, UAE row snapshot IDs, source SHA, transcripts, confidential lineage, a fabricated p-value or a causal/publication claim.

All outputs are candidate only: inferential_p_value=null, causal_claim=false, publication_allowed=false and can_publish=false. Descriptive numerical difference is not a causal effect.

## Mandatory current authorization

A trusted injected CurrentProjectAuthorizer must execute current authenticated project membership/owner access BEFORE any SQL; the caller must bind principal_id to a real authenticated actor, not a user-submitted name. Missing authority or any non-True result fails closed and cannot reveal whether the project exists. Every page rechecks access.

A trusted injected FreshUAEReader must supply a newly acquired typed UAEReadResult for the frozen manifest. SourceEligibilityGate.evaluate() rechecks current project scope, source policy, retention expiry, rights and EVERY original row's QDP/SRE/A/B/C and metric eligibility. Suppressed rows or missing/altered original matched high/low video IDs, UAE snapshot IDs or row SHA values hide that entire candidate. Source and rights checks happen for EACH read; no stored permission token is sufficient. Fresh source reader or authority unavailable => no visible patterns, no shortcut from existing registry data. The default project workspace currently has no real authenticated UAE producer, so production read eligibility is NOT established.

After rights confirmation, lookup original matched video IDs in videos to verify platform. ALL matched publications must exist and have one homogeneous platform; mixed/missing/mismatched platform is suppressed rather than guessed. Rights and project membership are necessary independently of the platform lookup.

## SQL and pagination

The SQL query is parameterized and always checks project_id on registry series AND revisions and restricts s.owner_id to the authenticated principal. A DISTINCT ON latest-revision selection is done AS OF a frozen DB timestamp, and only afterward are status, hypothesis, B-feature, minimum sample N and supported-bin filters applied. This prevents showing a revoked old revision when the newest revision has changed status. Supported-bin count comes from the full registered stage22 JSONB bin array, not a cherry-picked subset.

Keyset scanning orders by hexadecimal series_id. The DB clock watermark is frozen on the first page, so reanalyses inserted later cannot appear mid-pagination. A short-lived (15-minute) HMAC-SHA256 cursor binds project, principal, filters, watermark and last scanned series ID to a server-secret key of at least 32 bytes. Changed filter/owner/project, forged token or expired watermark are refused. The token is **integrity protected but not encrypted** and contains only an opaque series hash, no source row identity; clients must treat it as opaque. Rights may suppress rows and generate an empty visible page with a next cursor: no hidden-denial counts are disclosed.

JSON and CSV export only accept a typed safe PatternQueryPage that has passed the mandatory checks; export_redacted_pattern_page serializes exclusively the public RedactedPattern fields. Raw registry JSONB, original member IDs, source SHA and other sensitive evidence are not exportable.

This is a synchronous Python data-layer API, not an HTTP route, authenticated production principal manager, validated external UAE connector or publishing service. It does not itself grant content rights, implement full platform authentication or create supported research.

## Security and negative controls

- Current project authorizer denies before SQL; even zero-item pages do not disclose project existence.
- Missing live UAE or provenance authority denies individual results; revoked rights between page 1 and 2 hide subsequent results.
- Changed original member row SHA, UAE snapshot ID, manifest or missing row blocks candidate.
- Mixed platform, missing video metadata, wrong platform filter block candidate.
- Cross-tenant/owner/filter cursor replay, malformed Base64/HMAC and expired cursors fail closed.
- Stored JSONB project, owner, payload SHA, snapshot SHA, rights and status mismatch fail typed.
- Supported or unredacted query, all-history fallback, invalid filters and unsupported export formats fail typed.
- No inferential significance, causal claims or unauthorized source details are created in output.

## QA and durable integration limits

Files: intelligence/patterns_insights/research/pattern_queries.py, tests/test_pie_pattern_queries.py (27 authored synthetic tests including optional disposable PostgreSQL17 query compile and schema check), research/root package exports, and docs/PIE_IMPLEMENTATION_TRACKER.md. No migration is needed in stage26 because migration 201 already created suitable immutable project-scoped tables and indexes.

The synthetic tests intentionally MOCK the fresh current UAE/provenance responses when checking query output, and separately exercise original-row matching and no-authority denial. They import stage25 synthetic fixture whose stage24/23/22 and earlier upstream replay is mocked, NOT a production test.

Run locally on a full checkout:

    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_pattern_queries.py

Opt-in real PG17 tests require explicitly disposable CM_TEST_PG_DSN and skip without it. No local unittest/compileall/Postgres17 fresh/upgrade/UAE live read, authenticated rights or passing CI is confirmed in this environment; 27 authored tests are not 27 observed passes. stage26 data-layer code is committed but full release access is BLOCKED until real auth, UAE and provenance adapters are available. No main or other module branch edits.

Next PIE-MOD-27 — invalidation, drift and source rights expiry propagation; old evidence must remain immutable and its current read eligibility must be suppressed.
