# UA-MOD-09 — Group B B1–B8 exact-version reader

**Ownership:** `feature/unified-analytics-full-module` only. **Implementation:** `intelligence/unified_analytics/group_b_adapter.py`. **Tests:** `tests/test_unified_analytics_group_b_adapter.py`. **Current state:** implementation committed; real Python/PG17/LIVE runtime tests must be observed separately. **No new migration**: Group B owns its own B1–B8 tables and 055/056 runtime schema; UAE must not clone them.

## 1. Audited source of truth

Actual Group B branch `feature/group-b-full-module` audited at HEAD `23f188d66b8d585fb0e4e226cecbcee59758a77c`. Reviewed:
- `intelligence/migrations/005_content_features_b.sql`: immutable canonical B1–B8 content tables, B1 `content_b_hooks/parts`, B2 `formats`, B3 `story_sampling/frames/intervals/slides`, B4 `visual_content`, B5 `editing`, B6 `subtitles`, B7 `cta_analyses/ctas`, B8 `categories/topics/subtopics/topic_assignments`; video/carousel-only placement rules.
- `055_group_b_runtime.sql`: `group_b_runs`, `group_b_inbox`, eight `group_b_block_states`, human `group_b_review_events`, per-run result analysis FK, pinned Group A snapshot SHA, media SHA, config SHA, and consumer intake identity.
- `056_group_b_qdp_receipts.sql`: accepted QDP handoff receipt and conflict quarantine, **not** proof B1–B8 completed.
- `group_b/contracts.py`: B1–B8 vocabularies and distinction between `unknown`, `not_run`, `partial`, genuine observed zero.
- `group_b/dependency_rules.py`, `readiness.py`, `lineage.py`, `intake.py`: actual cross A→B and QDP input rules. `ReadinessReport` is not model spending authorization or completed output; `group_b_runs.state` and `group_b_block_states` must be read separately. 
- The Group B module has **not** published a verified outward UAE wire schema for a completed analysis in the inspected materials. `uae-group-b-exact/v1` is only a **UAE-local evidence reference schema**, not a fabricated signed producer event.

## 2. Typed pins, source-backed read and status

`GroupBExactPins` pins `InputScope`, exact `group_b_runs.id` UUID, `content_b_analyses.id`, **QDP group B preparation SHA** distinct from global QDP run SHA, A003/A004 ID, GA result SHA and media-manifest SHA, Group B config SHA, analysis/taxonomy/method version, QDP consumer event UUID+SHA, optionally explicit model/prompt identifiers, plus **eight ordered block evidence SHA pins**. For a carousel B5/B6 must be `None` and for all applicable blocks actual 64-hex digests are required.

`GroupBExactReader(conn, pins, group_a, qdp, as_of=UTC)` uses caller-injected, authorized, `dict_row` PostgreSQL connection under the same repeatable-read transaction. It does not connect or import peer code, call a model, create B component rows, rescore C or interpret delivery ACK as analysis completed. The constructor binds the exact injected `GroupAVerifiedFactsReader` and `QDPQualifiedSetReader` by **connection identity, video/source scope, GA A003/A004 ID/result SHA/manifest SHA and QDP B preparation SHA**. All reads use constant SQL with parameterized IDs; no `latest` substitution.

- `capability()`: probe 005/055/056 B tables plus A/QDP reader capability. Missing feature-only 055/056 on standalone UAE `main` => `UNAVAILABLE/gb_schema_missing`, never synthetic success.
- `read_completed_analysis(scope)`: invoke **both source-backed A and QDP readers now**, then read exact B run joined to accepted consumer inbox, QDP handoff receipt, B analysis, active source and policy. Require both run and analysis `state=completed`, `needs_review=FALSE`, no `unavailable_reasons`, exact project/video/source/policy, video vs carousel content type, A parent FK, B/QDP input SHA, Group A result SHA/manifest SHA, B config SHA and pinned method/taxonomy/model/prompt revisions. A past `accepted` inbox alone cannot qualify.
- Human review: `group_b_review_events` currently has generic `decision JSONB`, SHA and revision but **no observed authoritative decision-schema mapping to an approved B1–B8 snapshot**. A present review event is treated as potentially superseding and **blocks** with `gb_human_review_unresolved`, even if untrusted JSON contains `"status":"approved"`. A future peer version can explicitly publish signed/effective review semantics and lift this blocker; current UAE must not invent them.
- Require exactly eight `group_b_block_states` rows B1..B8, with each applicable status `complete`, no error and **exactly pinned evidence digest**, not merely a non-null SHA. For carousels B5/B6 are *not applicable*: `not_run/unavailable` with NULL digest and no corresponding B5/B6 source rows. They must not appear as fabricated zero editing cuts or absent video subtitles.
- Re-read applicable B1–B8 **actual component data by pinned analysis ID**, require each to have a defensible result, version and absence semantics, then bind a hash of each source row to the UAE EvidenceRef lineage. Local hashes are traceability only, **not** producer signatures.

## 3. Component-specific facts and unknown handling

| Block | Required source-backed conditions |
|---|---|
| **B1 — Hook** | `content_b_hooks` exists, `classification_status=inferred`, version matches B taxonomy, nonempty unique mechanics/delivery, video hook with observed start/end seconds or carousel hook with explicit slide index. Unknown is not absent. |
| **B2 — Format** | `content_b_formats` exists, `classification_status=inferred`; video has positive `video_primary` and no `carousel_format`, while carousel has positive `carousel_format` and no video format. `image_post` remains unsupported in UAE because B2 supports only `video/carousel`. |
| **B3 — Story** | For video, at least one actual frame/interval, no carousel roles, a real `content_b_story_sampling` record; for carousel, slide role count exactly equals Group A's media-item count with no video-only story rows. No unresolved frame/interval/slide review, no unknown interval boundaries. |
| **B4 — Visual content** | Exists with actual boolean `person_visible/product_visible/logo_visible`; `ai_generated=NULL` must stay unknown unless verified, not a fabricated `FALSE`. |
| **B5 — Editing** | Applicable to videos only: valid editing type, nonnegative cut count/rate, finite positive average shot duration and bounded first-five-seconds cuts. For carousels no B5 source row/digest is allowed. |
| **B6 — Subtitles** | Applicable to videos only. Source subtitle type is `none/standard/dynamic`; `none` requires NULL style fields, while detected subtitles need explicit style. Carousel B6 is inapplicable; don't derive absent subtitles from missing OCR. |
| **B7 — CTA** | A `content_b_cta_analyses` row with `analysis_status=complete`, known past scan timestamp, current taxonomy, no `needs_review` occurrence and consistent unique sequential occurrence indices. **Zero CTA is a true observed zero only when the scan was complete**; `not_run/partial/failed` are not negative findings. |
| **B8 — Topics** | A real `content_b_topic_assignments` row, current taxonomy version, no `needs_review`, exact category↔topic↔subtopic hierarchy and no merged/tombstoned category/topic/subtopic. |

In all cases the adapter can only return `EvidenceRead.LOCATED`, which remains a peer evidence *claim* pending authenticated UAE gating and current rights integration (later stages). `UAEPorts.status().qualified` always remains false.

## 4. Test coverage and limitations

`tests/test_unified_analytics_group_b_adapter.py` has **21** test methods, covering hypothetical fully completed eight-component video and six-applicable-component carousel, exact Group A/QDP scope and SHA, mismatched B parent/result/project/policy, model/taxonomy/prompt drift, missing complete block/digest pin, missing B3 slides/sampling and review, boolean unknown versus false, B5/B6 applicability, B7 complete-zero vs `not_run` CTA, B8 merged and wrong parent taxonomy, untrusted human-review `approved`, disabled producer tables, malformed pins and safe DB exception messages. Conditional disposable PostgreSQL 17 verifies missing feature branch GB055/056 produces `UNAVAILABLE` instead of a mock positive.

**Not yet authoritative:** the inspected B branch does not provide a published immutable signed output event/receipt or contract for effective human review after model execution. B review rows cannot be silently accepted; real end-to-end approval will remain blocked until Group B defines compatible reviewed B1–B8 output semantics. Source SHA pins and block-complete statuses are **not signatures**. The LIVE SRE terminal settlement and QDP carousel preparation peer limitations from UA-MOD-06/07 remain in force. This stage does not try to bypass them.

## 5. QA and next stage

```bash
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
python -m compileall -q intelligence/unified_analytics
```

Mark source-static inventory separately from executed unittest/PG17/E2E, which must be observed in a real runner. No consumer feature is auto-enabled. No changes outside UAE files.

**Next: UA-MOD-10 — Group C persisted score reader, exact config/cohort/sample and source metric snapshot pins, no metric recomputation.**
