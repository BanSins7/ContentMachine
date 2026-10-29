# Group B — authoritative B1–B8 contract matrix (GB-MOD-01)

**Frozen against:** `intelligence/migrations/005_content_features_b.sql` (Git blob `e203aab384af65ae11e9960860bee4715bb21e4d`) and `tests/test_content_features_b_schema.py`, as observed 2026-10-26. **Migration 005 must stay byte-for-byte unchanged.** Code vocabulary mirror: `intelligence/group_b/contracts.py`. Regression tests: `tests/test_group_b_contracts.py`.

This is an audit of **storage**, not proof of a working LLM, QDP/A handshake, actual evidence, or B→Analytics delivery. Runtime validators intentionally need stronger proof than SQL: GB-MOD-04/05/06/07/21/22.

## Shared parent / source-first lineage

| Authority | Real fields and checks in schema 005 | Missing runtime validation |
|---|---|---|
| `content_b_analyses` | `id` BIGINT; canonical `video_id` FK; `source_id` FK; optional `source_policy_version_id`, `video_a_analysis_id` or `carousel_a_analysis_id`; unique 1–250 char `analysis_key`; 1–80 char `analysis_version`; `taxonomy_version`, `method_name`, optional method/model/prompt versions; `content_type=video\|carousel`; `state=pending\|partial\|completed\|failed`; `needs_review`; two JSON objects; time columns. | SQL permits both A FKs NULL, **including for completed**; runtime requires completed A snapshot, media SHA, QDP input SHA, policy/version, exact per-block coverage. No SQL state implies valid evidence. |
| `videos.content_kind` | `video,slideshow_video` → B video; `photo_carousel,mixed_carousel` → B carousel; `image_post` unsupported. SQL trigger rejects contradictory **known** kind, but allows NULL. | No runtime analysis of unknown media kind. URL-only metadata does not establish accessible media. |
| Source and policy | `sources.status=enabled` and `can_analyze_media=TRUE` required on relevant writes. Optional `source_policy_version_id` must belong to source. | Recheck current policy at read, execution, revocation. FK nulling during retention is not complete lineage. |
| Group A | Video A analysis FK or carousel A analysis FK, same video/source checked. A carousel items checked for B3 slides and B7 CTA when pinned. | No peer contract handshake / A completeness verification yet. Never guess source/asset identifiers or versions. |

Parent indexes: `idx_b_video_versions(video_id, created_at DESC, id DESC)`, `idx_b_source_versions(source_id,created_at DESC)`. Completed parent and child outputs immutable except narrowly allowed A FK nulling on parent; new analysis means new `analysis_key`.

## Frozen B1–B8

| Block | Tables, stored fields | Enumerations and checks | Null/unknown and evidence meaning |
|---|---|---|---|
| **B1 Hook** | `content_b_hooks`: seconds or slide, source `spoken_text,screen_text,visual_description`, unique mechanisms[], five nullable booleans `is_question,has_number,direct_address,has_negation,is_command`, delivery[], inferred/unknown status, taxonomy_version. `content_b_hook_parts`: ordered index, verbatim text, mechanisms, position. | Ten mechanisms: `problem,promise,curiosity,myth_busting,mistake,surprise,story,challenge,comparison,proof`. Delivery: `speech,on_screen_text,visual_action,sound`. No primary hook. | Nullable flag is unknown, not measured false. Verbatim must match observed source. Video seconds only; carousel positive slide index only. |
| **B2 Format** | `content_b_formats`: `video_primary`, optional distinct `video_secondary`, or `carousel_format`; classification `inferred,unknown,needs_review`. | Video (12): `talking_head,dialogue_interview,skit,demonstration,product_showcase,screen_recording,pov,reaction,voiceover_montage,animation_motion_graphics,slideshow_video,vlog_bts`. Carousel (8): `explainer,listicle,step_by_step,comparison,storytelling,case_study,product_catalog,visual_gallery`. Inferred requires relevant format. | Cross-type labels forbidden. No approved UGC/people/slide_layout/text_density enums. Unknown ≠ new invented label. |
| **B3 Story** | `content_b_story_sampling` defaults 2 fps first 5s, 1 fps later, target 60; flags/notes. `content_b_story_frames` indexed time/reference/role/phase/review; `content_b_story_intervals` spans with boundary quality; `content_b_story_slides` indexed visual/text/role/review. `content_b_story_patterns` view returns ordered intervals for video, slides for carousel, preserving repeats. | 26 roles: `hook,context,problem,consequence,goal,myth,mistake,cause,explanation,fact,tip,step,demonstration,comparison,example,proof,testimonial,product_intro,product_demo,before_after,result,reveal,summary,offer,cta,other`. Phase `before,after,both` only for `before_after`. `other` → needs_review. Interval boundary `known,estimated,unknown`. | Video sampling/frames/intervals only; carousel slides only. SQL allows NULL frame reference/time: no claim of having seen a frame until exact delivered media manifest exists. Technical cuts ≠ semantic story intervals. |
| **B4 Visual** | `content_b_visual_content`: `ai_generated` nullable and mandatory boolean `person_visible,product_visible,logo_visible`. | Exactly four fields. | **No row** if no visual pass. `ai_generated=NULL` until independently verified; synthetic-looking style is not evidence. No default false for missing content. |
| **B5 Editing** | `content_b_editing`: `editing_type`, `cut_count>=0`, `cuts_per_minute>=0`, `avg_shot_duration>0`, `cuts_first_5_sec` between zero and cut_count. Video only. | `single_take`: 0 cuts and zero rate; `slow_cuts`: count>0 and rate 0–10 exclusive; `medium_cuts`: [10,30); `fast_cuts`: >=30. | Deterministic Group A cuts/duration; missing measurements ≠ 0 cuts. No paid LLM required. |
| **B6 Subtitles** | `content_b_subtitles`: `subtitle_type` and seven style fields `position,font_size,font_style,text_color,highlight_color,background_style,display_mode`. Video only. | `none,standard,dynamic`; none forces all style NULL. Other two require position `top,center,bottom`, size `small,medium,large`, font `regular,bold,extra_bold`, `text_color=#RRGGBB`, background `none,outline,shadow,box`, display `line,phrase,word_by_word`; highlight color nullable. | Text on a carousel slide is not video subtitles. If OCR evidence is unavailable, do not guess `none`. |
| **B7 CTA** | `content_b_cta_analyses` status `not_run,complete,partial,failed`; `content_b_ctas` 0..N unique `occurrence_index`, nonblank text, speech/screen evidence, distinct 1–2 delivery channels, seconds/slide, destination, extraction status, provenance fields. `content_b_cta_summary` tri-state view. | 12 CTA types: `follow,like,comment,share,save,profile_visit,link_click,app_install,purchase,sign_up,dm,other`; delivery `speech,on_screen_text`; 9 destinations: `current_post,profile,link_in_bio,website,app_store,comments,dm,other,unknown`; services: `tiktok,instagram,youtube,telegram,whatsapp,apple_app_store,google_play,other`. No CTA type `none`. | `has_cta=NULL` if scan not_run/partial/failed (even if zero rows); false only after complete scan with zero rows. Never fabricate URL/handle. |
| **B8 Topic** | `content_b_categories/topics/subtopics` stable IDs, `slug,name,merged_into_id`; `content_b_topic_assignments`: analysis/category/topic/optional subtopic, taxonomy version, review flag. Hierarchy is protected by composite FKs and unique parent/slug constraints; three assignment indexes. | CATEGORY > TOPIC > optional SUBTOPIC. Slugs match `[a-z][a-z0-9_]{0,119}`. | Topic of **observed publication** ≠ Research C taxonomy/SRE hypotheses/Search Planner keyword. Semantic duplicates, merges/new topic proposals require review. |

## DB trigger and index inventory

- Parent: `trg_b_validate_analysis` → `b_validate_analysis`.
- Children: `trg_b_hooks_guard`, `trg_b_hook_parts_guard`, `trg_b_formats_guard`, `trg_b_sampling_guard`, `trg_b_frames_guard`, `trg_b_intervals_guard`, `trg_b_slides_guard`, `trg_b_visual_guard`, `trg_b_edit_guard`, `trg_b_subs_guard`, `trg_b_cta_analyses_guard`, `trg_b_ctas_guard`, `trg_b_topic_assignment_guard` → `b_validate_component`.
- Shared immutable schema: `b_valid_labels`, parent/child CHECK + FK constraints, nine CREATE INDEX statements, views `content_b_story_patterns`, `content_b_cta_summary`.

## Known SQL-compatible cases that must **not** pass final runtime qualification

1. Parent `completed` with no A FK or publication with `content_kind=NULL`.
2. SQL-valid B1/B7 statements lacking actual word/frame IDs, hashes, seconds, or observed quotations.
3. B3 sampled frames with NULL `sampled_frame_ref`, unverified stream coverage, or semantics inferred from cut boundaries.
4. B4 `ai_generated=true/false` with no independent provenance; visual booleans from a model that saw no image.
5. B5 computed from nonmeasured cuts or duration, B6 `none` due to missing OCR, B7 `false` due to partial scan.
6. B8 new/merged topics without approved hierarchy and review.
7. B `completed` with flagged children, missing applicable B1–B8 blocks or policy/version mismatch to A and Analytics.
8. QDP, Group A, Unified Analytics branches have no proven live integration handshake yet.

**Next owners:** GB-MOD-02 pure ports, 04 QDP adapter, 05 A lineage, 06 media manifest, 07 readiness, 13–20 classifiers, 21 schema/evidence gate, 22 atomic persistence, 23 review, 25 Analytics adapter.

**QA truth:** `tests/test_content_features_b_schema.py` PostgreSQL tests require `CM_TEST_PG_DSN`; without that, they are **SKIP**, never database PASS. No real-model spending, writes, or migration changes occur in this stage.
