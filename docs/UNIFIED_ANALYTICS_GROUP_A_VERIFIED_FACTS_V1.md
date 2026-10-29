# UA-MOD-08 — Group A exact-version verified facts reader

**Branch:** `feature/unified-analytics-full-module`. **Implementation:** `intelligence/unified_analytics/group_a_adapter.py`. **Tests:** `tests/test_unified_analytics_group_a_adapter.py`. **This stage only:** source-backed exact A003/A004 + GA054 output read, NO Group B/C integration, no C scorer, no auto activity, no main merge.

## Actual Group A peer audit

At audit time `feature/group-a-full-module` HEAD was `2b405ef1fa22ca50b7d5bab6f2ac1ca2d3ac7d8f`. Inspected `003_video_features_a.sql`, `004_carousel_feature_a.sql`, `054_group_a_module_core.sql`, `055_group_a_handoff_inbox.sql`, Group A `contracts_v1.py`, `media_manifest.py`, `handoff.py` and `repository.py`, all **read-only**.

- **A003 video**: `video_feature_a_analyses` + `video_feature_a_scenes/speech_segments/screen_text/audio_events`; explicit `state`, `analysis_version`, `scene_status/speech_status/screen_text_status/audio_status`, `duration_sec`, `measured_at`. `video_feature_a_summary` distinguishes incomplete `NULL` from observed true zero.
- **A004 carousel**: `carousel_feature_a_analyses`, `carousel_feature_a_items` and `carousel_feature_a_summary`; `slide_index` ordered from 1 with unique IDs, `text_status`, `on_screen_text`, observed word counts, optional duration only for video items. **No canonical swipe-time observation exists; UAE must not invent one.**
- **GA054**: `ga_runs` carries project, QDP run, video/source/policy, *Group A preparation* digest, verified media-manifest SHA, state and content kind. `ga_media_refs` has the producer-pinned ordered media item SHA/bytes/verified-at. `ga_results` carries immutable `group-a-to-b/v1` snapshot, result SHA, method version, A003/A004 source row ID. `ga_steps` carries actual execution-step output hashes. `ga_local_readable_results` checks source media rights and persistent local asset identity, but **explicitly says external QDP verification is still required**. `ga_outbox.delivered` by itself is NOT a completed or current Group A fact.
- **Group A contract `group_a_to_b_v1`:** requires exactly the appropriate stream set and `stream_truth_v1` fields `status`, `evidence_count`, `verified_absence`, `reason_code`; `status=complete` alone is insufficient, and a stream with `evidence_count=0` is valid only with a separate verified-absence proof. Video streams: technical/speech/screen_text/audio/subtitles. Photo carousel: slides/screen_text. Mixed carousel: slides/technical/speech/screen_text/audio/subtitles.

## UAE reader API and trust model

`GroupAVerifiedPins` requires validated `InputScope`, exact GA UUID, GA result ID, exact A003/A004 analysis ID, **QDP A-preparation input SHA** (not to be confused with `qdp_runs.input_sha256`), media manifest SHA, published result SHA, analysis and method versions and 1..100 expected media item count. Videos require exactly one media item; carousel kinds need at least two.

`GroupAVerifiedFactsReader(conn, pins, qdp, as_of=...UTC)` requires an authorized caller-owned PostgreSQL dict-row transaction and the **same** connection/scope + Group A preparation digest in an injected `QDPQualifiedSetReader`. No connector, provider, media worker, or filesystem is opened. This is a read-only `GroupAReader` protocol implementation.

The reader:
1. Capabilities-probes GA054 tables/views, A003/A004 and media assets. Missing GA feature migrations on standalone UAE `main` => `PortCapability(UNAVAILABLE, ga_schema_missing)`. It separately checks live QDP capability.
2. Re-reads `qdp.read_qualified_membership(scope)` **now**, not the ACK/ready status from a transported event. Unless QDP locates the currently qualified exact source-backed run, the Group A output is missing. `EvidenceRead.LOCATED` is not an independently authenticated cross-module qualification.
3. Joins **exactly** `ga_runs.id` + `ga_results.id` through `ga_local_readable_results`, checking same project/video/source/policy, exact QDP run and **group A preparation SHA**, exact media-manifest SHA, kind, completed/noninvalidated run and result, `group-a-assembly/v1` stage and `group-a-to-b/v1` result-schema versions, method version, expected count and A003/A004 FK.
4. Recomputes the producer's canonical JSON digest of the versioned `ga_results.snapshot`, verifies it matches the pinned result SHA, checks `snapshot.evidence` equals persisted `ga_results.evidence` and verifies every required stream is COMPLETE, its measured count vs verified_absence is consistent, and the evidence list contains at least `max(1,count)` of the appropriate event-kind proofs. An unexecuted, partial or unavailable stream never collapses to zero.
5. Re-reads the **exact** A003 or A004 row by ID and validates source/policy/analysis version, completed state, measured timestamp `<= as_of`. Wrong media/kind or missing completed source facts => typed failure.
6. Re-reads every `ga_media_refs` row against its actual `media_assets` record: unique/ordered 1..N positions, same project-scoped video/source, exact asset ID/type/hash, non-`metadata_only` permission, known positive byte size, verified timestamp, nonexpired asset. Image-only photo carousel and mixed image+video item kinds are distinguished. Every GA proof must reference a SHA from these exact media refs; each carousel slide requires its *own* unique asset proof, and mixed-video scene/speech/audio events must reference a **video** asset.
7. Requires recorded GA processing steps to be completed with positive input/output SHA and known past end time. For video, checks completed A003 scene/speech/screen-text/audio statuses, `duration_sec>0`, known subtitles, and ordered bounded real scene intervals. For carousel, checks completed A004 slide/text statuses, observed positive slide count and exact ordered `carousel_feature_a_items` with complete per-slide OCR and non-null numeric word counts (an actually empty slide may legitimately have zero). For photo carousels an unmeasured audio stream remains `unknown`, not fabricated false. Mixed carousels require completed audio.
8. Only then returns `EvidenceRead(group_a, LOCATED, EvidenceRef(producer_schema='group-a-to-b/v1', ...))` with SHA of all validated pins, QDP lineage, stream-result digest, persisted step outputs and media hashes. This is an exact-version *source-backed evidence claim*, **not** UAE `qualified` or proof of file bytes at this moment.

**Known peer limitations:** current QDP `042_qdp_group_current_preparations` still permits Group A/B only video/slideshow_video; the photo/mixed carousel positive test fixtures are **structural hypothetical future-peer tests**, not deployable QDP qualified rows. Even the GA054 `ga_media_refs.verified_at` and `media_assets.sha256` assert prior file verification, not that bytes are still present and identical now: GA `media_manifest.verify_file/open_verified` performs real fd-level verification at producer/processing time. This UAE read path does not reopen files and must not claim refreshed byte proof. An authenticated current QDP A media selection and repeat-open byte proof are remaining integration work before promoting data into a trusted production gate.

Group A's `image_post` is supported as a standalone kind, but current UAE `InputScope` deliberately rejects it because downstream Group B B2 does not publish an `image_post` content type compatible with UAE.

## Typed failure examples

| Condition | Expected state |
|---|---|
| GA054 absent | `unavailable/ga_schema_missing` |
| QDP feature absent/not qualified/revoked | `unavailable/ga_qdp_unavailable` or `missing/ga_qdp_not_current` |
| Wrong GA run/result, project/source/policy, QDP A SHA, unknown version or revoked | `missing/ga_exact_run_or_result_mismatch` |
| Partial/not_run/unknown stream, no positive or negative evidence | `missing/ga_stream_or_snapshot_incomplete` |
| Wrong A003/A004 ID or analysis version | `missing/ga_a003_a004_version_missing` |
| Swapped/expired/untrusted media or unlinked stream proof | `missing/ga_media_manifest_mismatch` or `missing/ga_evidence_media_unverified` |
| Partial/failed execution step | `missing/ga_steps_unfinished` |
| Missing real scene coverage or inflated/out-of-order slide count | `missing/ga_scenes_incomplete` or `missing/ga_slide_positions_incomplete` |

## Test and release truth

`tests/test_unified_analytics_group_a_adapter.py` contains **21 unittest methods**, exercising positive simulated pinned video/slideshow/photo/mixed kind, missing QDP and GA schema, media swaps, invalid SHA, unknown status, missing streams, scene intervals, OCR null vs actual zero, unique slide order, exact asset SHA evidence, project/version and A003/A004 mismatches, malformed pins and sanitized errors. A conditional disposable PG17 test checks the correct **GA054 unavailable** behavior on standalone `main + UAE`, not a real GA E2E result.

Required execution:
```bash
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
python -m compileall -q intelligence/unified_analytics
```

Python, PG17 and Github Actions must be reported as observed (not assumed passed). No new migration in stage 08 because all persisted GA tables/columns are owned by Group A and existing UAE `080–082` schemas already support versioned evidence references. Adding shadow Group A tables would create ownership conflicts.

**Next: UA-MOD-09 — Group B exact-version completed B1–B8 reader and human-reviewed taxonomy/lineage.**
