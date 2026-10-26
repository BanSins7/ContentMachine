# GB-MOD-05 — exact-version Group A → Group B admission

**Branch:** `feature/group-b-full-module`. **Implementation stage:** GB-MOD-05. **Peer Group A inspected:** `feature/group-a-full-module` @ `4b39c44144797f30dbddfaea9051fa44f1914a4a` (2026-10-26).

## What was verified

The peer branch contains the frozen `group-a-to-b/v1` JSON schema and `intelligence/group_a/contracts_v1.py::group_a_to_b_v1` validator, PostgreSQL 054 `ga_runs / ga_results / ga_media_refs`, A003 `video_feature_a_analyses` and A004 `carousel_feature_a_analyses / carousel_feature_a_items`. `ga_runs` owns exact project, QDP run, video, source, current policy, QDP preparation SHA and ordered-media-manifest SHA. `ga_results` has a pinned immutable `result_sha256`, snapshot JSON, method version, and canonical A003/A004 analysis pointer. The `ga_local_readable_results` view is **only locally gated**, explicitly not evidence of current QDP rights.

Group A also independently added `055_group_a_handoff_inbox.sql`; Group B owns `055_group_b_runtime.sql` and `056_group_b_qdp_receipts.sql`. The two `055_...` filenames are different and sort by full filename, but they share a numeric sequence. **No already-committed migration is renamed or modified**; joint PG17 installation, first-boot ordering, checksum reconciliation and merge-time sequence review are cross-branch release gates.

## Implemented Group B adapter

`intelligence/group_b/adapters/group_a.py::read_exact_a(conn,b_run_id,ga_result_id,expected_sha256)` accepts only an explicitly specified positive **GA result ID** and lowercase result SHA256; there is no `latest` lookup, source guessing or fallback to a partial A analysis.

A positive read requires all of the following:

1. The B run and its committed QDP inbox exist, with a matching GA run **project_id, qdp_run_id, video_id, source_id and qdp_input_sha256**.
2. GA assembly `group-a-assembly/v1`, immutable result `group-a-to-b/v1`, exact GA result ID and matching hash of canonical JSON snapshot; both GA run and result are `completed` (not `partial`, `queued`, `invalidated`).
3. The **frozen GA stream truth** validates exact stream names, statuses `complete`, nonnegative evidence counts, true measured-negative `verified_absence`, and at least one hash-pinned proof for each completed stream (including verified zero). Missing or `not_run` facts remain unavailable, never false/zero.
4. Exact canonical A003 or A004 analysis ID, video and source match; A003/A004 state `completed`. A003 native scene/speech/screen-text/audio stream statuses are also `complete`; its source-policy version must match GA, and measured `has_subtitles=true/false` must agree with the GA subtitle verified-absence claim (`unknown` is refused). A004 carousel slide/text statuses are `complete`, with source-policy version and carousel kind matching the GA run.
5. Each ordered GA media reference matches current D1 `media_assets` video/source/media kind/SHA, active source policy and media permission/expiry. Video A003 `media_asset_id` equals the sole GA media pin; A004 ordered slide media IDs/types equal GA media refs, with appropriate photo/mixed carousel composition.
6. Every A evidence asset SHA must appear among the pinned media SHAs. A snapshot and database evidence/method version must agree. The original GA A source policy and manifest SHA must not conflict with any existing B pin.

`intelligence/group_b/lineage.py::bind_exact_group_a(connection_factory, ...)` takes an **owned transactional connection**, locks the B draft row, repeats exact GA admission, and atomically writes the existing `group_b_runs` columns:
`video_a_analysis_id / carousel_a_analysis_id, a_snapshot_sha256, media_manifest_sha256, source_policy_version_id`. Same pinned version is idempotent; different analysis/version, partially written legacy pin or non-draft state is rejected. **No new DB migration** is necessary and neither 005 nor 055 is touched.

No automatic Group A import or producer execution occurs; `require_a_schema()` emits a typed unavailable result on baseline main, which does not yet contain GA 054. The optional GA A1/HTTP APIs are not called or used as a substitute.

## Important limitations / future gates

- **A v1 producer readiness:** Group A branch still evolves separately; no actual completed A003/A004 snapshot/producer dispatch can be claimed from this read-side adapter. Data are accepted only after real completed immutable GA snapshots appear in a jointly installed database. A read using mock snapshots cannot demonstrate that.
- **Media bytes:** D1/GA SHA matching, rights and canonical media DB pointers are not the same as rehashing files or verifying frame/audio extraction. Byte-level manifest verification is GB-MOD-06. Media expiry or QDP invalidation can occur immediately after any read; revalidate before B block execution/payment.
- **QDP live/currentness:** GB-MOD-04 only creates a durable QDP inbox/draft, not permanent upstream authorization. GB-MOD-07 readiness must recheck current QDP preparation+invalidation and rights, not trust this one-time GA pin.
- **Semantic validity:** A evidence stream statuses describe measured A facts, not B1–B8 labels, Group B completion or Analytics availability. No model calls or paid authorization are enabled by binding.
- **Runtime persistence guards:** the owned B `055` schema permits later changes to nullable A fields; this adapter is the explicit one-time binder, not a universal database trigger that prevents privileged direct SQL updates. DB-level immutability hardening should be considered at GB-MOD-21/22.
- **Host trust boundary:** the caller must be authenticated/authorized to the specific project and run. This internal API must not be directly exposed through an unauthenticated HTTP route (GB-MOD-26).

## Test plan and truthful QA

```bash
python -m unittest discover -s tests -p 'test_group_b_a.py' -v
python -m unittest discover -s tests -p 'test_group_b_db.py' -v
```

The pure suite covers complete/partial/unavailable/not-run streams, verified negative evidence, omitted or wrong IDs, wrong GA schema versions, digest mismatches, revoked source/media, source/video/project substitution, carousel types/order, and idempotent/locked binding. GitHub Actions on Windows/Linux runs the suite.

Joint A+QDP+B PostgreSQL 17 tests **require an explicit migration reconciliation and disposable DB with both peer schemas**; standalone B CI cannot invent GA 054 tables. Until those tests actually run, DB joint migration/A snapshot E2E remains **BLOCKED_EXTERNAL / NOT RUN**, not PASS.

**Next:** GB-MOD-06 — Media Evidence Manifest, byte-level media/hash/rights coverage and extraction manifest verification.
