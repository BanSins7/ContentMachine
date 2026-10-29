# UA-MOD-24 — Project-level qualified ranking and versioned manifests (v1)

**Branch:** `feature/unified-analytics-full-module` · **Core:** `intelligence/unified_analytics/project_ranked_views.py` · **Additive PG17 migration:** `090_unified_analytics_project_aggregate_runs.sql` · **Status:** implemented core, executable QA not yet verified, LIVE positive qualification blocked.

## Purpose and exact internal API

A trusted server-side caller with an **already project-authorized, dict-row PostgreSQL 17 connection** supplies a bounded tuple of exact `VisibilityPins` and a non-user-controlled aggregate run key. There is **no public REST endpoint, GRANT, dashboard publication, cron or autonomous discovery**.

```python
store = ProjectRankedRunStore(project_authorized_db)
run = store.create(
    tuple_of_pinned_current_qualified_snapshots,
    "my.project.run.1", run_revision=1, sort_mode="breakout_desc"
)
if run is not None:
    page1 = store.page(run.run_id, tuple_of_pinned_current_qualified_snapshots,
                       limit=10)
    page2 = store.page(run.run_id, tuple_of_pinned_current_qualified_snapshots,
                       limit=10, cursor=page1.cursor) if page1 and page1.cursor else None
    internal_export = store.export(
        run.run_id, tuple_of_pinned_current_qualified_snapshots
    )
```

**Crucial:** The run receipt is **not authorization**. Every `create`, `page` and `export` performs a fresh `REPEATABLE READ` transaction, verifies **all requested UA19 snapshots' effective current source rights and peer-qualified lineage before ANY Group C metric read**, reads the exact existing persisted `calculated_metrics.breakout_score` and `metric_snapshots.views`, and rejects any missing, revoked, inaccurate, stale, incompatible or duplicate data. It never recomputes Group C formulas, adds score components or normalizes values.

Only **5..50 unique videos** from **one project and one UA20-compatible platform/source/content kind/source metric definition/version/C config/creator-prior-niche frozen reference/A/B/SRE taxonomy stratum** are eligible. There is no cross-source or cross-platform ranking or silent per-video latest-version choice. The caller supplies **exact snapshot pins**; there is NO `list all videos in project` or `status='qualified'`-only shortcut. Partial/blocked rows and their diagnostics remain in separately governed UA14/15 internal evidence workflows; this ranking deliberately has **no partial/fallback view**. On missing/currently denied any member, return `None` for the entire run/page/export (no partial results or hidden count).

## Source lineage and RTB limitation

Every run's `lineage_sha256` binds the sorted (video_id, snapshot_id, **JoinedCandidatePins.fingerprint**) tuple. The existing joined fingerprint references D1 canonical scope, QDP run/input and exact membership, SRE run/taxonomy and current decision, Group A/B immutable analysis versions, Group C metric + frozen cohorts, source rights and source policy, and media/metric evidence IDs. These are **identity digests, not producer signatures**; real authority is rechecked via UA19 on each read.

**RTB is not connected in the current UAE `JoinedCandidatePins` schema, and no verified RTB producer receipt/table is available in this branch.** The manifest must explicitly contain `rtb_evidence_state='unavailable_not_bound'`; it NEVER fabricates a claimed RTB SHA, producer approval, topic linkage or verified benchmark. A future independently reviewed RTB adapter would require a new versioned wire/migration after inspecting that upstream module. This is an **integration blocker for a full RTB-backed release**, not a reason to change another module's branch.

## Versioning and immutable run snapshots

The additive migration creates only `uae_project_aggregate_runs`, storing:

- project ID, database run UUID, explicit `request_key` and positive `run_revision`;
- source compatibility SHA, exact pinned combined lineage SHA, aggregate stats SHA and sorted ranking SHA;
- selected sample size 5..50, fixed sort mode and explicit RTB unavailable state, DB timestamp.

**There are no persisted individual video scores, copied views, raw provider payloads, media, transcripts, title text or Group C score cache.** The table is not a producer state transition; a manually inserted row does not become `qualified`. On new creation, `INSERT ... ON CONFLICT DO NOTHING` and an unconditional `SELECT` of the complete immutable row enforce idempotent replay: the **same** (project, request key, revision) must match all exact digests and versions or an explicit conflict is raised. To update data, mint a **new revision** rather than mutate a previous run.

The SQL disallows in-place `UPDATE` through an immutability trigger, but intentionally permits owner-controlled deletion and `ON DELETE CASCADE` when the project is erased. There are **no public write/read grants**. Under concurrent same-revision creation, PostgreSQL's unique constraint ensures one row; `REPEATABLE READ` can legitimately require transaction retry on a conflicting concurrent writer. The implementation fails closed with a storage error rather than claiming a successful replay. The calling scheduler may retry a fresh transaction with the same key.

## Ranking, deterministic pagination and export

Exact allowed sort keys, **never interpolated into raw SQL**:

| Sort | Primary | Tie breaker |
|---|---|---|
| `breakout_desc` | Persisted full-precision Group C Breakout Score descending | D1 `video_id` asc; then `snapshot_id` asc |
| `views_desc` | Exact `metric_snapshots.views` descending | D1 `video_id` asc; then `snapshot_id` asc |
| `video_id_asc` | D1 `video_id` ascending | `snapshot_id` ascending |

No rank is returned when score status/calculation status is noncomplete, unknown score, missing views, incompatible source precision or current permissions. The SQL `QUALIFIED_METRIC_SQL` and `_compatibility` from UA20 are reused, never replaced with a weaker view. Overall (N≥5) is required before ranking; a private page may contain 1–20 **individually verified** items because no public ranking interface exists. Such per-post statistics require additional product privacy/ACL approval before public/UI access.

`PageCursor` carries the exact run UUID, ordered ranking SHA, fixed sort key, next offset and deterministic checksum. The checksum detects accidental/forged-structure cursor corruption, **NOT** cryptographic authorization. A cursor from another run, order, or historic ranking is denied. Every page rechecks the **entire** pinned cohort with the same historical score/ranking SHA. If a video is newly revoked or scores/source versions change, an old cursor yields no data, rather than silently shifting a page or duplicating/omitting a video. Successful pages traverse an unchanged deterministic sorted snapshot with no overlap.

`RankedExport` is a **typed, project-authorized, current-data** in-memory descriptive export (all ≤50 authorized ranked items). There is no CSV upload, file write, public URL, data broker, external model call, publication to PIE or raw assets. Project `MetricStatistics` attached to the authenticated run uses exact Decimal p25/median/p75/mean/min/max for Breakout Score and views. No statistical significance, confidence band or causal claim (UA23).

## Privacy and release constraints

**Current production state:** UA13 source-backed effective producer approval cannot pass until full independently reviewed upstream QDP/SRE/A/B/C adapters; UAE084/085 actively forbid forged qualified snapshots. No positive live project run, ranked page or export currently exists. The synthetic positive tests are NOT proof of production qualification. A manually injected manifest cannot authorize a view without current live UA19 peer rechecks.

Ranked page/export can expose per-video metrics to privileged callers, which is more revealing than UA20's min-N aggregate. They therefore remain **internal only**. An actual project UI API needs verified per-project roles/permissions, anti-differencing/query-overlap controls, audit logging, data minimization, source provider contractual checks, and RTB lineage capability review. No public endpoint, direct GRANT or user-facing export is implemented.

## QA plan and authored fixtures

- `tests/test_unified_analytics_project_ranked_views.py` — **29 authored** unit/mock tests: golden rank math, tie order, fixed sort modes, replay/new revision/version conflict, cursor page boundaries and digest drift, revoked last/first member no Group C reads, missing precision/score, unknown run, one-project/50-video bounds and no public data export.
- `tests/test_unified_analytics_project_ranked_migration.py` — **8 authored** static migration tests for PG17 schema, unique versions, update immutability, project erasure compatibility, fixed ranking and zero GRANT/public routes.
- `tests/test_unified_analytics_project_ranked_views_pg17.py` — **3 authored optional** disposable PG17 tests for UA15 `partial` blocked before score or run insert, exact key idempotency and two concurrent direct manifest inserts with one winner (DDL/uniqueness, NOT positive producer-E2E). No PG17 data is claimed approved by these tests.
- For a local checkout on the developer machine:

```bash
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# Optionally set CM_TEST_PG_DSN to isolated disposable PostgreSQL 17.
```

No actual Python compile/unit or disposable PG17 execution is available in this session. A source re-fetch/static invariant review is **not** a runtime test. Until these and real signed producer approvals pass, status is **IMPLEMENTED CORE, QA NOT RUN, LIVE E2E BLOCKED**.

**Next UA-MOD-25:** Patterns & Insights consumer handoff with traceable evidence manifest, no invented publisher signatures or unchecked insight claims.
