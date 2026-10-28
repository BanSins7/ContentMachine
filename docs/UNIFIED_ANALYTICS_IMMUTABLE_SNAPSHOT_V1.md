# UA-MOD-14 — Immutable unified analytics snapshot assembly

**Canonical branch:** `feature/unified-analytics-full-module`. **Production module:** `intelligence/unified_analytics/snapshot_assembly.py`; **tests:** `tests/test_unified_analytics_snapshot_assembly.py`; **additive PG17 DDL:** `intelligence/migrations/084_unified_analytics_immutable_payloads.sql`. **No main/peer modifications.**

## 1. Source, intent and trust boundaries

UA-MOD-14 consumes the source-backed UA12 `JoinedContextAssembler` and the UA13 `QualificationOrchestrator` in **one joined selection**, one freshly started project-authorized repeatable-read PostgreSQL transaction and an explicit UTC clock. Input `QualificationObservation(checked=True)`, caller-provided score JSON and user-supplied approved flags are **not inputs**. No Group C formulas are recalculated and no Group A/B/C result text, personal data, raw videos, image bytes, transcripts or scores are cached in UAE.

The current production peers cannot deliver full effective approvals: GC057 cohorts have only `frozen_unreviewed`, and effective Group B/SRE/QDP completion and carousel readiness remain blocked. Thus this stage **only creates immutable historical `partial` or `blocked` snapshots**. Neither seven LOCATED claims, `SourceRightsPins`, a SHA-256, a prior `qualified` record nor a source-rights SQL candidate view can elevate them. Its `qualified` properties are always false. Other stages must not publish these rows.

`ImmutableSnapshotAssembler(assembler).assemble()` is a pure in-memory draft constructor. It calls `assembler.select()` **once** and reuses the exact same trusted selection via UA13's private `_from_selection` decision factory. A selected complete-looking bundle remains `partial` with `qualification_effective_approval_unavailable`; rejected bundles contain no source data or borrowed peer IDs. Seven ordered ports are always represented: source_policy, d1, qdp, sre, group_a, group_b, group_c.

A located but not independently approved reference is persisted as `state=partial, checked=FALSE, is_current=FALSE` with exact `record_key`, `producer_schema/version`, payload SHA and lineage SHA. Missing ports are persisted as `state=missing`, no references/hashes, and a typed reason. Missing is **not** a measured zero, completed B7 CTA scan, passed Group C score or approval.

## 2. Immutable normalized reference-only payload

`ImmutableSnapshotDraft` wraps a normalized minimal JSON metadata envelope including:

- Canonical project/video/source/source_policy/content kind and UTC as_of, exact input and decision fingerprints and the source-rights digest when a complete joined candidate exists.
- Exact SRE run/taxonomy/content version and decision SHA, QDP run and **independent A/B/C preparation SHAs**, A analysis/result/media digest/version, B analysis/model/prompt/taxonomy and eight block pins, Group C metric IDs/current+previous raw snapshots/source metric version/config version/frozen peer evidence SHAs. All values are references/pins; no fabricated metrics or score computation.
- Per-stage typed blockers, seven readiness states and seven unapproved evidence-reference descriptors. The original `uae-qualification/v1` gate column of UAE080 remains unchanged for compatibility, while the historical reference-only envelope advertises `uae-snapshot-assembly/v1` and the actual UA13 decision schema separately.
- `payload_sha256=canonical_hash(normalize_payload(payload))`, plus distinct `evidence_set_sha256` and `decision_sha256`, all stable for an identical scope/version/as_of/peer result. Canonical normalizer rejects unsupported objects, floats/NaNs/infinities and huge collections. If exact Decimal fields are ever used by a separately verified producer adapter, `canonical_decimal()` serializes `Decimal('1.25000')` and `Decimal('1.25')` identically as `'1.25'`, preserves full coefficient precision independent of Python decimal context, canonicalizes negative zero to zero, and bounds exponent/size **without recalculating score**.

These hashes bind data lineage, not authentic producer signatures or signed review approval.

## 3. Historical tables, DDL and idempotency

Existing UAE080 `uae_qualification_requests`, `uae_unified_snapshots` and `uae_snapshot_evidence` retain their checksums and constraints. Migration **084** creates `uae_snapshot_payloads` with PK (project_id,snapshot_id), FK back to the canonical snapshot, JSONB, metadata envelope schema, payload/evidence/decision hashes and a parent-identity BEFORE INSERT trigger. Payload/parent status, request scope, canonical video/source/policy, input SHA and decision SHA must match. Rows explicitly prohibit `qualified` (only partial/blocked) and the standard UAE immutable-update guard rejects UPDATE. DELETE is reserved for privacy/TTL purge. No public SELECT privilege, materialized score cache or read-side permissions are added.

`ImmutableSnapshotRepository(assembler,request_uuid).assemble_and_save()` is the **only proposed production write path**, in a source-authorized fresh transaction. It:
1. Opens an explicit transaction, requests REPEATABLE READ, checks DB clock against explicit UTC as_of (no future time and at most five-minute staleness); inside that transaction calls source-backed selection + UA13 exactly once, constructing a deterministic draft.
2. Locks the exact UAE request row and checks project/video/source/policy/QDP run+SHA/content kind; no implicit request creation from arbitrary user inputs.
3. Takes a transaction-scoped advisory lock scoped to project/video/source across **different request UUIDs** to serialize monotonic `snapshot_version` allocation.
4. Before inserting, checks UNIQUE project/request/input_sha replay. On identical existing envelope hash/decision/status and actual normalized stored JSON + all seven evidence rows (state, checked/current false, exact reference fields), returns the same immutable ID/version with replayed=true. A conflicting or modified replay is **rejected**, never silently overwritten. Resolved requests only permit replay of identical input.
5. Otherwise appends one `uae_unified_snapshots` row, seven `uae_snapshot_evidence` rows and one `uae_snapshot_payloads` row atomically. The caller owns the DB connection and must not expose an unauthorized direct SQL writer. Failure of any insert rolls back the whole transaction. No `qualified` row is ever emitted.

This does not automatically update request status, start work, enqueue outbox events, join Group C peers or create a Patterns handoff: those belong to later stages. A rights revocation after the current transaction becomes observable to subsequent new transactions through UA11 checks; long-lived MVCC sessions **cannot** guarantee instantaneous cross-transaction visibility. No public read gate is active until the appropriate later stage.

## 4. Verification and known blockers

`tests/test_unified_analytics_snapshot_assembly.py` contains **24** authored methods: hypothetical seven-ref unapproved draft, all-port missing without fake numbers, exact versioned SRE/QDP/A/B/C metadata, deterministic same-input payload hashes, full-precision finite Decimal normalizer, bad JSON depth/size/float/NaN, stable replay ID/version, new-as-of version, wrong project/request, resolved-request collision, missing transaction, expiry/future clock, atomic rollback on evidence or JSON write failure, modified stored JSON with unchanged claimed hash, forged checked=true replay detection, missing evidence replay and credential-free DB errors. One disposable PG17 test applies/replays migrations, checks table emptiness and refuses an orphan/noncanonical payload insert; it is not a production acceptance test.

Required real commands (must be observed before PASS):
```sh
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
python -m compileall -q intelligence/unified_analytics
```

Static source verification is not an executed Python/PG17 test. Runtime CI and real cross-module positive tests must be reported separately. Readiness is **not release-ready** while SRE LIVE approved terminal record, QDP carousel membership, Group B effective reviewed B1–B8 completion and Group C reviewed config/cohort/score approval are unavailable. Mock positives do not count.

**Next UA-MOD-15:** idempotent snapshot write repository hardening/version arbitration, records and API retries under PG17 concurrency; exact substage scope must be checked against the canonical roadmap.
