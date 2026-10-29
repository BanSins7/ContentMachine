# UA-MOD-10 — Group C persisted score adapter

**Canonical branch:** feature/unified-analytics-full-module only. Implementation: intelligence/unified_analytics/group_c_adapter.py. Tests: tests/test_unified_analytics_group_c_adapter.py. No Group C migration, side effects, provider calls, score recomputation or main merge.

## Audited sources and ownership

Group C peer HEAD at source audit: 1a71910a6055d4b40c0300d1cef02b7038f7caac on feature/group-c-full-module. Read canonical baseline 003_performance_c.sql / 004_performance_c_integrity.sql and peer 056_group_c_runtime.sql / 057_group_c_cohort_freezes.sql, as well as group_c/c0.py, d1.py, pure_math.py, cohort_rules.py and cohort_freeze.py. These tables are owned by D1/Group C; UAE must not duplicate or mutate them.

Canonical C0 is metric_snapshots (not an imaginary metrics_snapshot table). Exact current and previous snapshots have the same video/source/policy, source_metric_definition = platform_post_views, the same nonempty source_metric_version, data_quality_status = ok, precision = exact, observation_time_assumed = false, complete nonnegative raw counters, valid chronological observed/collected times and unexpired rights. A later raw snapshot, even retracted or changed in quality, blocks using an older "latest good" score.

Canonical C1–C7 score values are PERSISTED in calculated_metrics. Require calculation_status = complete and score_status = complete; non-null existing component fields and raw/smoothed rates; exact config ID/immutable version/alpha/Bayesian k/fixed weights and 48-hour freshness half-life. Verify breakout_score equals the sum of four stored weighted components as an integrity check only. Do NOT recalculate C1–C7. Breakout Score is unnormalized and **not capped at 100**; freshness_score itself remains 0..100.

GC056 group_c_runs has exact UUID/project/video/source/policy, accepted group_c_inbox, QDP Group C preparation SHA (NOT global qdp_runs.input_sha256), current+previous snapshot IDs, published metadata ID, config ID, saved metric result ID, cohort IDs (creator and prior required; niche optional), completion timestamps and revision ledger. Current QDPQualifiedSetReader verifies the actual QDP C handoff, ordered previous/current pair and exact config under the same injected transaction.

GC057 group_c_cohort_freezes is immutable frozen peer provenance with legacy comparison_cohorts membership, raw per-peer group_c_cohort_freeze_members, source policy, metric unit/version, population count, rules/selection/evidence hashes, median/prior statistics and seal time. **Observed freeze state CHECK permits ONLY frozen_unreviewed**; source migration expressly defers human approval to GC-MOD-11/12. A frozen cohort is NOT approved, so there is presently no authenticated source to elevate a complete persisted score into a qualified UAE input.

## API and trust policy

GroupCPersistedPins explicitly includes C run UUID, exact calculated_metrics row ID, current/previous D1 snapshot IDs, metadata and config ID/version, creator+engagement prior cohort IDs, optional niche ID, all applicable cohort evidence SHA-256 values, source metric version, QDP Group C preparation SHA-256 and caller-provided positive minimum peer counts for each required cohort. The minimum sample thresholds are **never guessed or silently defaulted**; callers must obtain them from an independently reviewed policy. A positive numeric count alone is not a human review.

GroupCPersistedScoreReader(conn, pins, qdp, as_of=UTC) implements the GroupCReader port on a caller-owned authorized dict-row PostgreSQL connection under repeatable-read. No automatic DB connection, job dispatch, scoring function or SQL writes. Missing peer GC056/057/QDP returns UNAVAILABLE.

inspect_persisted_score(scope) checks: (1) QDP current qualified membership and exact C preparation; (2) Group C run/inbox/score/source/policy/content kind/config/version and completed statuses; (3) source metadata, current+previous raw snapshots and current rights/retention; (4) latest raw D1 snapshot wins even if partial or retracted; (5) full C1–C7 non-null persisted components and exact stored component sum; (6) creator and prior cohort freeze ID/target/purpose/rules SHA/evidence SHA, optionally niche, with no self-inclusion, correct source/unit/policy/metric version, matching frozen legacy population and explicit minimum peer count; (7) each peer's current rights and raw D1 counters; (8) no superseding Group C revision and no effective pending reviewer decision.

The successful diagnostic result is PersistedScoreFacts with status integrity_checked_unapproved, approved=false, persisted breakout score and cohort counts and deterministic lineage SHA. This is **not** authorized for aggregation or user exposure. The actual UAE GroupCReader port read_persisted_metric returns EvidenceRead.MISSING with c_effective_approval_unavailable even if all underlying values are intact. There is no invented EvidenceRead.LOCATED, approved flag or producer signature. Real approval requires new durable reviewed Group C cohort/config/score attestation and updated versioned adapter after GC-MOD-11/12.

## Verification and limitations

tests/test_unified_analytics_group_c_adapter.py contains 23 mock/conditional-PG17 test methods for: positive integrity-checked yet unapproved stored score, golden numeric components, optional C2 niche and omitted niche, raw Breakout Score above 100, complete C1–C7 fields and real-zero distinctions, wrong project or QDP C SHA, missing/expired/retracted snapshots, newer raw D1 observation, edited/incorrect config, explicit creator/prior/niche sample minima, poisoned/duplicate cohort membership, mismatched per-peer current policy and raw counters, invalid media rights, superseding revisions, malformed pins, unavailable feature schemas and sanitized DB errors. The disposable PG17 case applies ONLY standalone main + UAE migrations and correctly expects GC056/057 not installed. No LIVE GC score or approval exists in this test.

Required real execution:
- python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
- python -m compileall -q intelligence/unified_analytics

Source-level inspection is not a substitute for executed Python/PG17 or LIVE E2E PASS. No source branch merges.

## Next

UA-MOD-11 — rights/TTL/invalidation guards on every UAE read and handoff, preserving all upstream release blockers (SRE LIVE settlement, QDP carousel, Group B effective review, Group C approved cohorts/config/score).
