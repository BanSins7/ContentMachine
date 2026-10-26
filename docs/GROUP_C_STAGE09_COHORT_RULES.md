# Group C — GC-MOD-09: Explicit cohort compatibility rules

**Branch:** `feature/group-c-full-module` (standalone; main unchanged)  
**Code:** `intelligence/group_c/cohort_rules.py`  
**Tests:** `tests/test_group_c_cohort_rules.py`, CI `.github/workflows/group-c-module.yml`  
**Schema version:** `group-c-cohort-rules/v1`  
**Delivery state:** CODE IMPLEMENTED; CI PG17 NOT VERIFIED; **cohort freeze, review and C7 scoring NOT implemented**.

## Source of truth and explicit strata

GC-MOD-09 defines **who may be considered** a peer, not what the cohort median/prior should be and not whether those candidates are human-approved.

`CohortRules` is an immutable versioned description with **all required values explicit**, including `project_id`, target `videos.id`, `purpose` (`creator/niche/engagement_prior`), `platform`, `c_content_type`, `source_id`, `source_policy_version_id`, `source_metric_definition`, `source_metric_version`, `evaluated_as_of`, publication interval `[published_from,published_before)`, inclusive post-age interval `[min_age_hours,max_age_hours]`, creator/topic identity and a bounded `max_candidates`. There are **no defaults** for audience strata, source definitions, sample sizes or age windows.

- Creator cohort requires an explicit `creator_id` matching the target and each peer.
- Niche cohort requires an explicit `topic_id` matching the target and each peer. The topic is the actual canonical `creators.topic_id`, **not** an LLM-estimated topic label or a new Group C taxonomy.
- Engagement-prior cohort requires an explicitly selected creator and/or topic stratum, with verified positive views and non-NULL L/C/S for peer observations. Global priors are not assumed.
- Peer platform, content type, metric `platform_post_views`, metric **version**, source and source policy revision must **exactly** match the target's signed facts; reach/impressions never silently substitute for views.
- Legacy `videos.content_kind` must correspond to verified `videos.c_content_type` using the existing content-kind mapping. Image-only/unknown kind, mismatched creator/topic, provider/source/policy/metric version are rejected with reason codes.
- **The target post is always excluded**, by the SQL `s.video_id<>target` predicate and by an independent pure validator.
- Every peer must be linked to the **same project** through `sre_project_videos(project_id,video_id)`; the pure contract also checks the exact `project_id`.

## Snapshot and publication time comparability

For each project video + pinned source, the SQL first selects its **latest raw** `metric_snapshots` record at `evaluated_as_of` using `DISTINCT ON (video_id) ... ORDER BY observed_at DESC,id DESC`, **before** testing metric version, quality or policy. Thus a newer partial, retracted, `counter_correction`, new-version or policy-drift observation cannot silently be ignored to substitute an older apparently valid one.

The SQL next joins each candidate's **latest source-specific** `source_video_metadata` by `fetched_at DESC,id DESC`, including null, deleted and revoked rows. No older metadata fallback is used. The pure selection rejects records unless `is_current=True`, `deleted_at=NULL`, metadata/source policy exact, `precision='exact'`, `data_quality_status='ok'`, `observation_time_assumed=False`, counter type/quality valid, publication time in the explicit interval, snapshot age within the specified age window, and `published_at <= fetched_at <= evaluated_as_of` plus `published_at <= observed_at <= collected_at <= evaluated_as_of`. Metric/metadata expiry and optional retention policies are checked separately.

`PostgreSQLCohortCandidates.select` runs one independent `REPEATABLE READ, READ ONLY` transaction, requires `conn.autocommit=True`, verifies the target project/video/source/policy, exact metric definition and version, **the target itself fits the specified publication and age stratum**, and that the target snapshot was also the latest raw source observation at the evaluation cutoff. Current source permissions and retention are enforced using GC-MOD-05 `read_scoped_d1_bundle`, with `SourcePolicyReader.assert_eligible` also run on **every admitted peer**. A revoked peer cannot be silently included.

A historical `evaluated_as_of` is used to define reproducible cohort observations, **not** to override current permission/retention checks. Evaluation time cannot be in the future. PostgreSQL scope is limited to the requested project; no unauthorized provider calls, SQL writes or new global shadow fact tables.

## Deterministic results; no implicit sample truncation

- Pure `select_compatible_peers(rules, rows)` returns immutable `CohortSelection`: `admitted`, `rejected` with reason codes, `selection_sha256`, `rules`, `post_count`, `status='candidates_only'`, `approved=False`.
- Peer identifiers are sorted and deduplicated. Duplicate candidates for one post raise `duplicate_peer_video`; malformed IDs reject the batch. Empty cohorts are represented as empty **candidates_only**, never mean/median zero or complete.
- Caller must explicitly specify `max_candidates` in 1..500. SQL fetches `max_candidates+1` rows, and a population larger than the declared limit raises `cohort_population_overflow`; it **never silently truncates or samples**.
- Both rule and selection SHA256 are over canonical inputs and IDs; timezone offsets normalize to UTC. Decimal ages canonicalize without depending on external Decimal context precision.
- Pure compatibility checks only establish eligible input candidates; they cannot establish their actual source permissions. The DB-backed selection alone may read authoritative D1, and it still returns `approved=False`.

## API

```python
from decimal import Decimal
from intelligence.group_c import (
    CohortRules, PostgreSQLCohortCandidates, select_compatible_peers,
)

rules = CohortRules(
    project_id=project_id,
    target_video_id=video_id,
    purpose="creator",  # or "niche", "engagement_prior"
    platform=platform,
    content_type="video",
    source_id=source_id,
    source_policy_version_id=source_policy_version_id,
    metric_definition="platform_post_views",
    metric_version=metric_version,
    evaluated_as_of=verified_cutoff,
    published_from=reviewed_start,
    published_before=reviewed_end,
    min_age_hours=Decimal("0"),
    max_age_hours=reviewed_max_age,
    creator_id=verified_creator_id,
    topic_id=None,
    max_candidates=reviewed_max_candidates,
)

selection = PostgreSQLCohortCandidates().select(
    conn, rules=rules, target_snapshot_id=target_snapshot_id,
    target_metadata_id=metadata_id,
)
assert selection.approved is False
assert selection.status == "candidates_only"
```

The example `min_age_hours=0` is **explicitly caller-supplied**, not a hidden default, and no one should assume it is an approved/ideal statistical age window.

## Integration gap to resolve at GC-MOD-10 / 11 / 12

Existing `comparison_cohorts` schema in the immutable historical `003_performance_c.sql` has `source_metric_definition`, **but no `source_metric_version` nor `project_id` or full pinned source policy version**. Accordingly, GC-MOD-09 makes *no writes to comparison_cohorts* and **does not pretend that old cohort records prove full compatibility**.

GC-MOD-10 must add the necessary **additive versioned persistence** for explicit rule hash, source metric version, exact frozen member snapshot IDs, peer policy and source metadata lineage, observed_at/published_at windows, immutable sample evidence and reproducible median/prior derivation. Preserve original 003/004 checksum and do not overwrite old frozen cohorts.

GC-MOD-11 requires human review of the candidate composition and prior estimates; GC-MOD-12 activates only genuinely approved versioned configuration. No automatically inferred alpha/k/prior rates, normalized scores, C7 results, worker activation or Analytics dispatch are permitted during this stage.

## QA

- `tests/test_group_c_cohort_rules.py`: **15 pure unit tests + 6 optional disposable PG17 tests**. They cover exact creator/niche/prior rules, source/metric/versions/policy, cross-project and target-self exclusion, age/publication time windows, timestamp quality, retraction, alternate source, stable UTC/Decimal SHA, invalid IDs, overflow and refusal to substitute older snapshots, changed target metadata and revoked rights.
- Existing CI `pure-contracts` runs the new unit suite on Linux and Windows by the `test_group_c_*.py` glob. Explicit PG17 **fresh** and **upgrade** steps added to `.github/workflows/group-c-module.yml`. DB tests run **only** with `CM_TEST_PG_DSN` and `GC_TEST_DISPOSABLE_DB=YES`.
- GitHub code+tests were committed through the GitHub connector. Because the local environment has **no PostgreSQL17**, no executable full-branch checkout and GitHub Actions are **QUEUED** at last observation, tests on exact committed HEAD remain **NOT VERIFIED**, not PASS. Only static source/persistence-boundary inspection was performed.
- No applied SQL migration edited, no new migration in Stage09, no changes to `main` or sibling Group A/B/Analytics/QDP branches.

**Next:** GC-MOD-10 — freeze cohort membership and full versioned provenance; one stage per user request.
