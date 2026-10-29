# UA-MOD-21 — Eight typed segmentation dimensions, exact Group B lineage

**Canonical branch:** `feature/unified-analytics-full-module` · **Module:** `intelligence/unified_analytics/typed_segmentation.py` · **Tests:** `tests/test_unified_analytics_typed_segmentation.py`, `tests/test_unified_analytics_typed_segmentation_pg17.py`.

## Contract and boundaries

`TypedSegmentationReader(conn).summarize(tuple[VisibilityPins,...], dimension, min_sample=5)` is an internal, *project-authorized* read contract. It uses the UA19 `VisibleSnapshotReader._read` and the UA20 exact stored persisted Group C metrics and compatibility logic, all in **one fresh PostgreSQL REPEATABLE READ transaction**. It **never** accepts untrusted feature blobs, public status flags, arbitrary SQL, a second C score calculator, score renormalization or direct self-declared `qualified` status.

**Eight exact dimensions** (no other key is accepted):

| Dimension | Producer-backed provenance | Canonical value | Multilabel |
|---|---|---|---|
| `content_kind` | UA19 canonical `InputScope.content_kind` | `video`, `slideshow_video`, `photo_carousel`, `mixed_carousel` | No |
| `hook_mechanism` | B1 `content_b_hooks.mechanisms` iff `classification_status='inferred'` and matching B taxonomy | One of the 10 canonical B1 mechanism keys | Yes |
| `video_format` | B2 `content_b_formats.video_primary` for video or `carousel_format` for carousel, iff inferred and taxonomy current | One of the producer's 12 video / 8 carousel formats | No |
| `story_role` | B3 `content_b_story_frames` and `content_b_story_intervals` for video; `content_b_story_slides` for carousel | One of 26 canonical B3 roles | Yes |
| `editing_type` | B5 `content_b_editing.editing_type` | `single_take`, `slow_cuts`, `medium_cuts`, `fast_cuts` | No (not applicable to carousel) |
| `subtitle_type` | B6 `content_b_subtitles.subtitle_type` | `none`, `standard`, `dynamic` | No (not applicable to carousel) |
| `cta_type` | B7 `content_b_ctas.cta_type` iff its analysis really completed with no `needs_review` occurrences and matching taxonomy | 12 canonical CTA types; **`none_observed` only for completed B7 with zero CTA** | Yes |
| `topic_id` | B8 `content_b_topic_assignments.topic_id` with matching taxonomy, no review, and no topic/category merge | `b_topic:<id>` (distinct from D1/search topics) | No |

Versioned Group B features are read by the fixed, parameterized `GROUP_B_DIMENSIONS_SQL` joining **exact** `group_b_runs` + `content_b_analyses` on project, video, source, source policy, pinned `b_run_id`, pinned `b_analysis_id`, completed state, immutable version/taxonomy and `needs_review=false`. The UA19 peer verification is performed **before** this read, so a structural B row never substitutes for LIVE SRE/QDP/A/B/C effective qualification. B reads return no raw screen/spoken text, prompts, media paths, transcripts or model outputs.

When classification is unknown, not applicable or absent, the member contributes **no segment** for that dimension. These are not guessed as `none`, `no hook`, or a synthetic topic. A truly completed B7 with zero CTA is the sole known negative observation (`none_observed`), distinguished from missing analysis. Unknown labels, outdated taxonomy and merged/unreviewed topics fail closed.

## Multilabel, exact one-video dedup and privacy

A video may belong to several distinct hook/role/CTA categories, but each `(compatible stratum, dimension, canonical value, video_id)` contributes **exactly once**, regardless of how many frames, intervals, repeated CTAs or duplicated labels mention it. Example: one video with `['follow','follow','like','like']` contributes one sample to `follow` and one to `like`, **not four**. To preserve the baseline's comparability, any source/metric definition or version, C configuration/cohort, A/B/SRE version disagreement suppresses the *entire* request.

Protection is all-or-nothing: at most **50 unique videos**, one project, no repeated snapshot or video, explicit **min_sample=5..50**, same DB transaction, exact current source rights, peer state and snapshot SHA. If **any** requested member fails visibility or exact C/B lineage, return `None` (no partial counts). Before any IO, 0..4 sample requests return `None`. Each distinct category with fewer than `min_sample` distinct videos is omitted **entirely**, without disclosing the hidden value, count or suppression count. If none survive, return `None`. Result `TypedSegmentation` contains only the project ID, compatibility digest, dimension and deterministic order of safe `TypedSegment` values; each segment contains eligible sample size and Decimal p25/median/p75/min/max/mean of stored Group C Breakout Score and `metric_snapshots.views`. No raw posts, score normalization, per-video references, causal attribution or statistical significance.

**Risk not solved by min-N:** adaptive/overlapping cohort queries may be combined to infer low-frequency properties. There is no user-facing query endpoint. Before any eventual public/API exposure, require authenticated project access, fixed approved cohort definitions, query-rate/overlap controls, and a separate privacy review.

## QA and gated activation

- 31 authored unit/mock tests: eight exact dimensions; B1/B3/B7 duplicate label normalization; completed zero-CTA vs missing CTA; carousel B5/B6 N/A; B8 topic namespace, review/merge/version guards; 5-video golden Decimal stats; 1..4 suppression; rare labels; wrong versions; duplicate videos; source mismatch; single hidden member prevents any output; static SQL selection.
- 2 conditional disposable real PostgreSQL17 tests: historical UA15 `partial` snapshot cannot leak B1 labels or C scores, including through the `content_kind` path. QA uses the real UA85 guarded schema but contains **no actual positive signed producer review**.
- Python source/PG tests and live peer E2E are **not verified** here without a local checkout and disposable PG17. A source re-fetch/static invariant check is not a test execution.

```sh
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# Optional isolated PostgreSQL 17:
# export CM_TEST_PG_DSN='postgresql://...disposable-only...'
```

**Release blockers remain:** UA13/UA84/UA85 still forbid effective `qualified` positives and historical checked/complete evidence, so no production segment currently becomes visible. LIVE SRE/QDP/Group A/B/C approvals, project authorization, real PG17 migration integration and full read/retention/invalidation QA must be independently confirmed. No peer/main changes, publication, HTTP hook or broad SELECT grants are made.

**Next:** UA-MOD-22 — compatible strata and cohort comparisons, including author/time controls and deterministic bins. Never compare incompatible providers or claim causality.
