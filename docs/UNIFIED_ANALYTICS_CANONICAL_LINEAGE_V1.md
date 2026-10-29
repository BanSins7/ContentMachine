# UA-MOD-05 — Canonical ID / Project / Source Lineage

**Branch:** `feature/unified-analytics-full-module` only. **Status:** implementation committed; actual PG17 and Python test execution must be confirmed separately. **Owner:** `intelligence/unified_analytics/lineage.py`.

## Scope and source-of-truth

This stage establishes the **exact canonical identity** of one publication using an injected D1 PostgreSQL connection. It does not decide whether the publication is qualified, nor whether content/metric permissions are currently usable for a UAE snapshot. Never turn a successful identity join into an automated `qualified` result.

| Source table on baseline main | What is checked | Not yet checked |
|---|---|---|
| `project_registry` + `videos` | Explicit project ID and global canonical `videos.id`, supported content kind | No URL-as-ID or inferred content kind |
| `sre_project_videos` | Composite `(project_id, video_id)` membership; never project existence alone | Group-specific QDP qualified membership |
| `sources` + `source_policy_versions` | Exact active policy ID bound to the exact source ID; source enabled | Effective permissions/TTL/media revocation in UA-MOD-11; current reads must revalidate |
| `source_video_metadata` | **Pinned** metadata ID, project video, source and policy version; current, non-deleted, not expired | Full media/content digests and provenance |
| `metric_snapshots` | **Pinned** metric snapshot ID, video, source, policy version, definition/version | C score quality or compatible cohort |
| `sre_workspaces` + `sre_taxonomy_versions` | Exact active, approved, project-bound taxonomy ID and snapshot checksum | Human override, latest classification, LIVE execution |
| `sre_classification_runs` | Exact SRE run UUID, project and taxonomy FK | SRE run completion, LIVE vs offline, effective reviewed match (UA-MOD-06) |
| `sre_content_versions` | Exact content-version ID, project/video, positive revision | Whether this run classified this content version; UA-MOD-06 must prove that link |
| `video_feature_a_analyses` or `carousel_feature_a_analyses` | Exactly selected A ID, video/source/policy, explicit analysis version; carousel-specific kind | Measured coverage and media manifest (UA-MOD-08) |
| `content_b_analyses` | Exactly selected B ID, same video/source/policy, correct video/carousel type, parent A FK, explicit B analysis and taxonomy versions | B1–B8 completeness/human review/model provenance (UA-MOD-09) |
| `calculated_metrics` joined to `metric_snapshots` | Exactly selected persisted C ID, canonical post/snapshot/source/policy, compatible metric version and config ID | C scorer, score status, approved cohorts/config (UA-MOD-10) |

All read SQL is static (no dynamic input table names) and parameterized. `A_VIDEO_SQL` vs `A_CAROUSEL_SQL` is selected from an already validated four-valued content kind. `image_post` remains unsupported pending Group B B2 contract.

## Input and output

`CanonicalLineagePins` extends the existing `UnifiedAnalyticsInputPointer` / `InputScope` with mandatory explicit `metadata_id`, `metric_snapshot_id`, `sre_run_id`, `sre_taxonomy_version_id`, `sre_content_version_id` and B taxonomy version. A/B/C identifiers, QDP run and QDP prep SHA plus SRE decision digest are immutable pins, not inferred from `MAX(created_at)` or newest successful rows.

`CanonicalLineagePins.from_pointer(pointer, ...)` never dereferences a peer. It requires the additional IDs rather than choosing defaults. The **identity fingerprint** covers all pins and their source version.

`resolve_canonical_lineage(conn, pins, as_of=...)` requires a caller-injected dict-row D1 connection and an explicit aware UTC clock under a caller-managed repeatable-read transaction. It does not connect to a DB, choose timestamps, mutate data or invoke external modules. On missing/contradictory facts it raises a typed `AnalyticsBoundaryError` such as `not_in_project`, `source_policy_lineage_mismatch`, `metric_lineage_mismatch`, `sre_taxonomy_not_current`, `a_b_lineage_mismatch`, `b_taxonomy_version_mismatch`, `c_metric_lineage_mismatch` or `lineage_read_unavailable`. Exceptions from DB libraries are sanitized, not surfaced with connection secrets.

The returned `CanonicalLineage` contains only checked row identities and input-version fingerprints: selected A/B versions, B taxonomy version, SRE taxonomy checksum/content revision, metric definition/version, metric snapshot ID and C score config reference. **`qualified == False` and `qdp_verification == 'not_checked'` always.** An authenticated QDP set/handoff cannot be inferred from the presence of a UUID in the baseline schema because QDP is a standalone feature branch at this stage.

**Hash disclaimer:** `identity_sha256` and `lineage_sha256` are deterministic metadata fingerprints, not producer signatures; real right-to-use / signed event validations belong to the respective adapters.

## Additive DB constraint: 081

`intelligence/migrations/081_unified_analytics_project_membership.sql` installs one composite FK:

```sql
ALTER TABLE uae_qualification_requests
  ADD CONSTRAINT uae_request_project_video_membership_fk
  FOREIGN KEY (project_id, video_id)
  REFERENCES sre_project_videos(project_id, video_id)
  ON DELETE CASCADE;
```

This ensures a user cannot submit a video that globally exists but is **not attached to their project**. FK cascades allow membership removal to purge UAE requests and their dependent snapshots. It is additive to `080_unified_analytics_core.sql`, does not rewrite any old checksum and does not depend on feature-branch QDP/Group A/B/C tables. Existing `080` constraints still separately check exact source and policy binding.

**Caveat:** membership deletion cascading on a historical snapshot is distinct from checking whether a still-existing item has valid current source-policy rights. Later read-side visibility must always check both current rights and explicit invalidation state; a FK does not authorize disclosure.

## QA and limitations

`tests/test_unified_analytics_lineage.py` adds negative and positive mock-D1 tests for:
- strict positive IDs, UUID, SHA, version strings, lack of defaults;
- deterministic fingerprints and idempotent read;
- source/policy/project/video swap and metadata retention rejection;
- SRE taxonomy/run/content-version misbinding;
- A video vs carousel table, exact A→B FK and independent B taxonomy-version pin;
- C snapshot/source/metric definition/version mismatches without any scoring;
- missing, malformed, unreliable DB rows and sanitized dependency errors;
- optional disposable PG17 `EXPLAIN` on all five static main-schema queries.

`tests/test_unified_analytics_schema.py` adds a real PG17 negative case for a **second** project whose requested `videos.id` exists but is not in `sre_project_videos`; its original fixture was updated to seed valid membership before its own request so migration 081 does not break earlier schema tests.

`.github/workflows/unified-analytics-module.yml` runs `python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v` on Windows/Linux and with a disposable `postgres:17` CI service. It includes both `080` and `081` in pull-request path filters. A queued CI job is not a PASS; no production database and no peer are operated by this stage.

### Required execution

```bash
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
python -m compileall -q intelligence/unified_analytics
```

For actual SQL tests, use only `CM_TEST_PG_DSN` pointing to a **disposable PG17** (the test refuses matching `CM_DB_DSN`), or inspect the GitHub Actions job's logs. Future UA-MOD-06/07/08/09/10/11/12/13 must provide real authenticated, source-rights-aware adapters and complete qualification before UAE data may be exposed.

## Next stage

**UA-MOD-06 — SRE current approved relevance adapter**: match source-backed LIVE SRE run, taxonomy and exact content version; disallow offline fixtures/superseded decisions; check current human effective review, project, rights and TTL. Do not mistake the identity-only SRE run join in this stage for approval.
