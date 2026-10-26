# Group C — GC-MOD-06: C0 exact D1 snapshot validation

**Owner:** Group C / C0. **Branch:** `feature/group-c-full-module`. **Stage:** CODE IMPLEMENTED. **No scoring or persistence.**

## Source of truth

`intelligence/group_c/c0.py` is a pure, dependency-free validator plus a *read-only*, version-pinned PostgreSQL consumer. It uses the canonical `videos`, `metric_snapshots`, `source_video_metadata` and project/source rights from GC-MOD-05. It does not create new facts, change 001–004 migrations or mutate a historical D1 snapshot. No configuration, priors, fake peer cohort or provider API is called.

### Contracts / signatures

- `validate_c0_snapshot(row, *, video_id, source_id, source_policy_version_id, metric_definition, metric_version, now=None, require_score_fields=False) -> C0Snapshot`
- `validate_c0_pair(previous, current, *, require_score_fields=True) -> C0Pair`
- `read_c0_pair(conn, *, project_id, video_id, source_id, source_policy_version_id, previous_snapshot_id, snapshot_id, metadata_id, metric_definition, metric_version, ports=None, now=None) -> C0Pair`

`C0Snapshot` is immutable. It preserves *exact* D1 BIGINT counters `views/likes/comments/shares/saves/followers` as `int|None`. Zero is never converted into `None`; `None` is never converted into zero. Boolean/float/negative/overflow values (`>2^63-1`) and missing counter **keys** cannot be treated as valid source facts. Nullable values are allowed for individually readable C0 facts, but a full future score requires non-NULL `views,likes,comments,shares`, and views must be strictly positive for C4/C5 rate denominators. `saves` and `followers` remain nullable, informational C0 fields and are not silently added to ER. These validators **do not** emit C7 scores.

`C0Pair` has exact previous/current validated facts and `elapsed: timedelta` with `current.observed_at > previous.observed_at`; no growth rate calculation occurs at GC-MOD-06. Both snapshot IDs must differ and both records must agree on `video_id`, `source_id`, `source_policy_version_id`, `source_metric_definition`, `source_metric_version`. A decreasing view count causes `counter_decreased` (never clamped to zero). NULL previous views block C3 even if current views are populated.

### Source definition and metadata time

Allowed canonical view unit in v1: `platform_post_views`. Its provider-defined version must be a nonempty **exact** pinned string (`v1`, `v2`, etc., never inferred). `reach`, `impressions` and similarly-named definitions must NOT be treated as views merely because their counter is named `views`. Additional future units require **explicit metric semantic review + a versioned contract update**, not adding aliases invisibly.

`observed_at`, `collected_at` and non-NULL `expires_at` must be timezone-aware instants normalized to UTC. `observed_at <= collected_at <= now` and `expires_at>now` are enforced. `observation_time_assumed != False`, `precision != exact` and `data_quality_status != ok` all block completeness; `retracted` and `counter_correction` have distinct failure reasons. This catches timestamps from guessed publication/discovery/ingestion; `collected_at` cannot replace `observed_at` in C3.

**No publication freshness formula here:** the trusted `source_video_metadata.published_at` timestamp is the subject of GC-MOD-08; the current C0 module never uses `now()` as a fallback to generate a missing publication timestamp.

### DB read semantics

`read_c0_pair` requires a dedicated connection with `autocommit=True`, owns a transaction, and executes `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`. It calls `read_scoped_d1_bundle` (project-video link, explicit C content type, current D1 source/metadata/policy permission/expiry) and then the pure C0 validators.

The new `D1FactsReader.read_recent_raw_observations(conn, video_id, source_id, limit)` method fetches the last source-local raw observations **without filtering quality, metric definition/version or policy revision**, ordered `observed_at DESC,id DESC`, limited 2..64. The C0 reader requires the two newest rows to be *exactly* `[current_snapshot_id, previous_snapshot_id]`. A newer `retracted`, `counter_correction`, `partial`, version change or policy version change is not discarded to revive an older apparently valid pair. It causes `metric_history_changed`; current producer must re-prepare or quarantine. Unrelated provider/source observations do not automatically invalidate this source's pinned event.

A returned C0 pair proves **current raw eligibility for the given transaction only**, not approval of cohorts, config, publication freshness, future source eligibility or persisted C7 output. Subsequent worker stages MUST re-read source permissions and latest metric lineage at execution time.

### New test coverage

`tests/test_group_c_c0.py` contains **16 pure tests** and **5 opt-in disposable PostgreSQL 17 tests**:
- SQL NULL vs literal zero, mandatory C4/C5 counters, valid raw `views=0` vs undefined denominator;
- missing BIGINT keys, booleans/floats/strings, negative or overflowing counters;
- metric definition vs reach/impressions; exact version and source/policy identity;
- partial/unverified/retracted/corrected/assumed/rounded observations;
- naive, future, expired, post-collected timestamps and timezone conversions;
- strict increasing interval, distinct IDs, counter correction and missing previous views;
- project-aware SQL C0, newest raw source record ordering, newer retracted and changed-version observations, source retention/metadata revocation.

CI `.github/workflows/group-c-module.yml` now invokes Stage06 tests on standalone **fresh** and **upgrade** PostgreSQL 17 test DBs. PG tests require both `CM_TEST_PG_DSN` and `GC_TEST_DISPOSABLE_DB=YES`, and cannot be pointed at production. Running the pure tests outside the DB is independent of QDP.

### Truthful QA evidence

- Pure `intelligence/group_c/c0.py` copied into a local disconnected mirror and matched byte-for-byte by **Git blob SHA** (recorded in tracker). **12 independent local smoke tests PASS** against that exact module (Python 3.13). These test pure C0 invariants; they are *not* the entire published 16-test file.
- GitHub-authored Stage06 `tests/test_group_c_c0.py`: not run on full repo locally; PostgreSQL 17 5 cases NOT RUN locally (no service).
- GitHub Actions `pure-contracts`, `pg17-runtime`: QUEUED at latest query, not a PASS. Real live QDP/D1 data and cross-branch E2E **BLOCKED_EXTERNAL**.
- Historic migrations `003_performance_c.sql`, `004_performance_c_integrity.sql`, `056_group_c_runtime.sql` unchanged.

## Follow-up, next stage

**GC-MOD-07 — pure Decimal arithmetic isolation:** create deterministic, well-tested high-precision operations for future C1..C7 with no provider read or SQL side effects. Do not implement C1..C7 production formulas ahead of their individual stages and do not invent alpha/k/priors. Stage08 is publication-time evidence; Stage09/10 frozen cohort comparability and approvals; Stage20 persistence.

C0 `ContractError.code` values `invalid_raw_counter` and `metric_history_changed` are **diagnostic codes**, not new `score_status` SQL CHECK values. A future persistence/reason mapping must explicitly map them to the existing DB enum and must never treat this stage as `complete` merely because local validation returned a pair.
