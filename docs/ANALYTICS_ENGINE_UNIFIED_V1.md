# Content Machine — Unified Analytics Engine (A + B + C)

**Branch:** feature/analytics-engine-qualified-pipeline-20261023
**Version:** analytics-qualified/v1, 23 October 2026.
**Scope:** deterministic source-first qualification, calculation and immutable assembly of existing PostgreSQL observations. No platform crawler, inference, OCR/ASR, media download or OpenRouter call is performed by the new engine.

## 1. Architecture

~~~text
Approved Project + RTB Taxonomy
            |
Search Planner → Collector → Discovery D1 → videos (canonical ID)
                                    |
                      ┌─────────────┴───────────┐
                      |                         |
            source policy / rights         SRE approved relevance
                      |                         |
              media assets (opt-in)        approved match / human review
                      |
             ┌────────┴────────┐
             |                 |
          Group A           Group B
       video/carousel    hook/format/story/visual
       media measures    editing/subtitles/CTA/topic
             |                 |
             └────────┬────────┘
                      |
              Group C (C0–C7)
       metric snapshots → frozen cohorts
       approved alpha/k/priors → Breakout Score
                      |
         QUALITY + RIGHTS + LINEAGE GATE
                      |
          ┌───────────┴────────────┐
      qualified               partial / blocked
          |                        |
          └──── append-only analytics_unified_snapshots
                           |
                  rights-filtered read view
                           |
                   project-scoped API
                           |
              future comparative analytics
~~~

No stage guesses missing data or assumes a user permission based on a technically functioning scraper.

## 2. Source and identity invariants

- videos.id is the only canonical post identity, including TikTok/Instagram photo and mixed carousels. One project-specific membership is stored in sre_project_videos.
- Source right is the intersection of sources and reviewed active source_policy_versions. Explicit can_store_metadata, can_analyze_media, can_store_metrics and can_derive_scores permissions are required. No derived data is saved when denied.
- A, B, C, metadata and metrics must match the same video, source and source-policy version. Only non-expired metric and publication evidence is eligible; no cross-source comparisons.
- SRE category is matched against approved taxonomy: a current human positive label, or completed classifier positive with no overriding non-match/uncertain human label.
- Qualified means full A/B/C/SRE + permissions. Partial is diagnostic (never ranked). Blocked means revoked rights, invalid metric facts or missing project membership; unauthorized requests are rejected with 403/404 before persistence.
- A new run uses a new immutable request_key. SHA-256 over canonical JSON gives a content fingerprint. Completed facts cannot be edited in place.

## 3. Group A detailed stream

Group A video uses video_feature_a_analyses and video_feature_a_summary, selecting actual measured duration, aspect ratio, scene count/timelines, first screen text timestamp, spoken words, speech WPM, subtitle flag, speech coverage, CTA time, transcript segment timestamps, OCR screen-text spans, first speech time and audio events. Existing underlying interval records remain in the source tables; unified result keeps their analysis ID and summary (no duplicate raw media).

For photo_carousel and mixed_carousel, Group A uses carousel_feature_a_analyses / carousel_feature_a_items and carousel_feature_a_summary: slide count/order, per-slide type/text and OCR status, first-text slide, screen words, audio flag and CTA slide. No fictional swipe time. image_post does not qualify B2 because no approved B2 image-post contract exists. No A average scene duration, resolution or source FPS is invented.

A qualification needs completed A analysis and, for video, completed scene/speech/OCR measurement streams with known subtitle status. Carousel needs completed slide/text coverage and >=2 slides. Missing, partial, unknown and explicitly unavailable data are not reinterpreted as measured false/zero.

## 4. Group B eight distinct blocks

| Group B block | Canonical tables | Qualified check |
|---|---|---|
| B1 Hook | content_b_hooks, content_b_hook_parts | inferred classification |
| B2 Format | content_b_formats | inferred video/carousel classification |
| B3 Story | content_b_story_frames/intervals/slides | reviewed semantic units, no open review flag |
| B4 Visual | content_b_visual_content | measurement exists; unknown AI flag remains null |
| B5 Editing | content_b_editing | video-only, required |
| B6 Subtitles | content_b_subtitles | video-only, required |
| B7 CTA | content_b_cta_analyses, content_b_ctas | completed scan; zero CTAs is a legitimate result |
| B8 Topic | content_b_topic_assignments | typed hierarchical category/topic and no pending review |

Parent B must be completed and needs_review=false. It must reference the **exact A record** through the video_a_analysis_id or carousel_a_analysis_id FK, same source/video/policy version. A semantic B role does not replace a technical A scene. B5 avg_shot_duration is kept separate from deleted A avg_scene_sec.

## 5. Group C mathematical contract

~~~text
C0: V, L, C, S, saves, followers at exact observed_at with source/version
C1: creator_ratio = (V+alpha)/(creator_median_views+alpha)
C2: niche_outlier = (V+alpha)/(niche_median_views+alpha) [optional]
C3: growth = (V2-V1)/(t2-t1 in hours)
C4: engagement=(L+C+S)/V; weighted_raw=(L+1.5*C+3*S)/V
C5: L_s=(L+k*p_L)/(V+k); C_s=(C+k*p_C)/(V+k);
    S_s=(S+k*p_S)/(V+k)
    weighted_smoothed = L_s + 1.5*C_s + 3*S_s
C6: freshness = 100 * 2^(-hours_since_published_at_observation/48)
C7: breakout_score = .45*creator_ratio + .30*growth
                   + .15*weighted_smoothed + .10*freshness
~~~

There is **no normalization**, no forced [0..100] scale, no invented alpha, Bayesian k or prior values. All are required from explicit approved configuration and frozen compatible cohorts, excluding the current post. C2 may remain null. Timestamp pair must be increasing, same post/source/view definition; counter corrections, unknown metrics, zero views, missing publication evidence, expired data or unfrozen cohorts block C. Calculations use Decimal and 12 fractional digits to match original NUMERIC(30,12), and persist to existing calculated_metrics with the original integrity triggers.

## 6. Runtime implementation

1. analytics.contracts.validate_request: strict project/video/request-key validation.
2. analytics.service._source: current reviewed policy + explicit rights; rejects unauthorized calls.
3. analytics.service._project_video: project/video isolation via sre_project_videos.
4. analytics.service._a_and_b: find latest eligible A/B pair; avoid mixing versions.
5. analytics.service._b_payload: read typed B1–B8 evidence without semantic invention.
6. analytics.service._relevance: check approved SRE human/machine match.
7. analytics.service._metrics: select metric pair, publication timestamp, active C config and frozen cohorts.
8. analytics.metrics.compute_c_metrics: calculate C1–C7 or return a specific reason.
9. analytics.service._store_c: idempotent C persistence to calculated_metrics.
10. analytics.contracts.evaluate_qualification: machine-readable blockers, readiness and state.
11. analytics.service.run_qualification: one DB transaction and advisory lock, immutable JSON snapshot, versioned fingerprint.
12. analytics.service.list_unified: project-filtered analytics_visible_snapshots, rechecking rights at READ time.

Schema migration: intelligence/migrations/016_analytics_unified.sql. New table stores source ID + policy version; original A, B, C, SRE IDs; readiness/blockers, Group A/B/C payload, lineage and fingerprint. An update trigger forbids rewriting history. Deletion for expiry/revocation remains permitted.

## 7. Local HTTP

All paths inherit app.py localhost Host/Origin checks and mutation header X-ContentMachine: 1.

~~~http
POST /api/projects/{project_id}/analytics/qualify
Content-Type: application/json
X-ContentMachine: 1

{"video_id":123,"source_id":"licensed_source","request_key":"aql-123-source-20261023"}
~~~

Returns snapshot or a typed refusal. A POST runs *one bounded synchronous local PostgreSQL evaluation*, with no network/model billing.

~~~http
GET /api/projects/{project_id}/analytics?limit=30
GET /api/projects/{project_id}/analytics?include_partial=1&limit=30
~~~

Default GET includes only qualified. Operational mode includes partial/blocked snapshots that remain legal to read. All data comes from analytics_visible_snapshots, which hides records when a policy is revoked or evidence expires.

## 8. Testing and deployment

~~~bash
python -m intelligence.migrate
python -m unittest discover -s tests -p 'test_analytics_engine.py' -v
python -m unittest discover -s tests -v
python -m compileall -q intelligence/analytics app.py
~~~

CI uses PostgreSQL 17 and CM_TEST_PG_DSN pointing to a disposable test DB. The new tests require no platform/LLM calls. Do not execute schema QA on a production DB.

## 9. Explicitly not supplied by this phase

Group A FFmpeg/ASR/OCR extractors; Group B semantic multimodal classifier; fresh Collector-to-SRE jobs; automatic metric observation monitoring; user-approved cohort construction and alpha/k/prior setup; async batch scheduling; automated legal retention cleanup and revocation deletion; comparative statistical inference between features and high scores; live licensed end-to-end benchmarks.

**Important:** The qualified pipeline can fully join, calculate and gate facts *once they exist*. Merely having schema and tests cannot demonstrate any real qualified records on a fresh installation. The next independent integration should connect a licensed source's real A/B observations plus vetted C config/cohorts and confirm live PostgreSQL E2E on a disposable/legal test dataset.
