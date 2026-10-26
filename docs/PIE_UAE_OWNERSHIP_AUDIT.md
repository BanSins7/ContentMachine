# PIE-MOD-01 — UAE inventory, statistical ownership and provisional handoff dictionary

**Date:** 2026-10-26. **Owner / write scope:** `feature/patterns-insights-full-module` only. **Status:** completed source audit / synthetic tests, **not** a certified UAE integration.

## 1. Exact observations and pinned evidence

| Surface | Pinned revision | Verified reality | Action |
|---|---|---|---|
| main | [218f5f3](https://github.com/BanSins7/ContentMachine/commit/218f5f3163ccc7179a4809ba4386a1d1b6ff80f5) | canonical `videos.id`, `sources`, project registry, C schema; no PIE runtime | Read-only source-of-truth; no PIE migrations yet |
| legacy UAE / PR #23 | [1292196](https://github.com/BanSins7/ContentMachine/commit/12921968ef3d5c6f5e706fd8e625c54733405f3b) | `intelligence/analytics/aggregate.py`, `service.py`, `contracts.py`, migration 016–018 | **Historical reference only**; cannot cherry-pick runtime/SQL or claim deployment |
| new UAE / PR #38 | [d2ac64c](https://github.com/BanSins7/ContentMachine/commit/d2ac64cd4af04773988c6c6060345c69a61cf99d) | `intelligence/unified_analytics/contracts.py`, `__init__.py`, UA roadmap, ownership audit; tracker UA-MOD-01 complete / UA-MOD-02 next | No live qualified snapshot provider or row-level PIE handoff; UA-MOD-25 planned |
| PIE / PR #39 | [43f7ba8](https://github.com/BanSins7/ContentMachine/commit/43f7ba81ef16598440ec0bd81ba0739cd3df67e4) | `PIEDatasetPointer` has metadata shape validation only | Fail-closed without provider; subsequent stage must add capabilities |

Exact reference files: [legacy aggregate](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/aggregate.py), [legacy qualification](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/analytics/contracts.py), [legacy read gate 017](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/migrations/017_analytics_jobs_and_read_gates.sql), [read-gate hardening 018](https://github.com/BanSins7/ContentMachine/blob/12921968ef3d5c6f5e706fd8e625c54733405f3b/intelligence/migrations/018_analytics_audit_hardening.sql), [UAE independent contract](https://github.com/BanSins7/ContentMachine/blob/d2ac64cd4af04773988c6c6060345c69a61cf99d/docs/UNIFIED_ANALYTICS_INTEGRATION_CONTRACT.md), [C scoring semantics](https://github.com/BanSins7/ContentMachine/blob/218f5f3163ccc7179a4809ba4386a1d1b6ff80f5/docs/PERFORMANCE_C_DATABASE_V1.md). These are revision-pinned **design/implementation artifacts**, not attestations of live rows.

## 2. Strict responsibility / non-duplication register

| Concern | UAE descriptive owner | PIE inference owner | Rule |
|---|---|---|---|
| Qualified gate | UAE joins current SRE LIVE/human, QDP, verified A/B, persisted C and current source policy | PIE rechecks UAE authority/rights via port, never independently requalifies | `partial` / `blocked` are not research samples |
| Row selection | UAE chooses correct immutable version and eligibility | PIE freezes an analysis population, inclusion/exclusion, creator clustering and hypothesis family | Never silently choose latest unpinned row |
| Segmentation | UAE emits eight descriptive dimensions and compatible versioned strata | PIE constructs hypothesis/contrast, controls, interactions and multiple-test family | Multi-label dimension deduplicated once per `videos.id` |
| Summary | UAE median/p25/p75/mean of Decimal C7 in compatible strata | PIE computes its own effects, uncertainty, FDR, holdout and robustness from authorized observations | A segment `sample_size` is not a reconstructable list of observations |
| Outcome | Group C owns persisted C1–C7; UAE does not score | PIE compares configured C7/explicit alternate outcome without recalculation | C7 raw and unbounded; C2 niche_outlier excluded |
| Explanation | UAE describes factual aggregates | PIE provides evidence-linked candidate → validated reviewed insight | Association never automatically implies causation |
| Access | D1 source policy / UAE live read gates and scope | PIE read/export validity, invalidation and project isolation | Old snapshots can become unreadable; cached flags not permission |

**Historical aggregation observed:** `summarize_qualified(rows,dimension,min_sample)`: `min_sample` explicit 2–10000; `MAX_INPUT_POSTS=50000`; rejects not-qualified/score-not-complete/nonfinite/duplicate `(video_id,stratum)`; interpolation p25/p75; `descriptive_only=true`, `confidence_intervals_computed=false`, `causal_claims_allowed=false`. Its `aggregate_project` selects `DISTINCT ON (u.video_id)` ordered by newest and caps 50001 rows; this is **not** an immutable research partition or independent statistical holdout. The old SQL v017/v018 checks current rights, derived expiry, metric observations, A/B, SRE/C lineage/cohort eligibility. The future handoff must document the *actually deployed* read gate, not rely on old SQL being installed.

## 3. Data dictionary: current/proposed with authority level

| Item | Actual source/status | PIE action / schema decision |
|---|---|---|
| `project_id` | `project_registry.id`, lower-case 32 hex in UAE normalized pointer and PIE pointer | Require exact project and explicit current membership; never cross-project sample |
| `video_id` | `videos.id` BIGINT in UAE `UnifiedAnalyticsInputPointer` | Required **per observation** and dedup key; current PIE pointer does **not** include rows |
| `source_id`, `source_policy_version_id` | UAE normalized pointer | Verify source rights, retention, current policy and exact source version at read/export; metadata alone is untrusted |
| `content_kind` | UAE normalized pointer maps `video`, `slideshow_video` → B-video; `photo_carousel`, `mixed_carousel` → B-carousel | Require supported kind and corresponding A/B coverage; `image_post` unsupported |
| `qdp_run_id`, `preparation_sha256` | UAE input pointer, canonical UUID / lower hex SHA | Future handoff must freeze QDP lineage; **not** present in PIE pointer v1 |
| `group_a_analysis_id`, `group_b_analysis_id`, `calculated_metric_id` | UAE input pointer, positive ints | Future per-row manifest must pin exact completed/consistent PKs, B taxonomies and media version; **not** presently delivered |
| `sre_decision_sha256` | UAE input pointer lower hex hash | Handoff must prove live/current accepted relevance and taxonomy/version; hash alone not authorization |
| `source_metric_definition`, `source_metric_version` | C metric snapshot / PIE normalized pointer | Non-null explicit comparable source views; don't pool definitions/platform/kind/version |
| `calculation_config_id` | persisted Group C / PIE pointer | Approved per project/source and corresponding frozen cohorts; no invented defaults |
| `breakout_score`, `score_status` | persisted C7 score / `complete` required | Decimal-finite **unbounded** (never 0–100 clamp), not `niche_outlier`, null never zero |
| `uae_engine_version`, `uae_manifest_sha256`, `uae_dataset_sha256`, `as_of` | PIE pointer v1 metadata-only contract | Real UAE provider not yet exposes these as verified research output; no synthetic fallback |
| `creator_id/token`, `platform`, publication/observation time, snapshot IDs/hashes, missingness, inclusion, controls | Requested PIE research manifest, **NOT verified UAE output** | Required to negotiate for cohort comparability, creator-clustered inference, holdout and reproducibility |
| `B` feature dictionary | historical UAE `features_b`, taxonomy version | Eight dimensions: `content_kind`, `video_format`, `hook_mechanism`, `story_role`, `editing_type`, `subtitle_type`, `cta_type`, `topic_id`; B1 multilabel, B3 ordered roles; unknown != false |

The historical dimension list is an inventory, not a specification for PIE's future feature operators. Distinct video count for one hook tag cannot be multiplied into independent observations by adding tags.

## 4. Provisional UA→PIE interface / ownership freeze (NOT deployed)

- **Capability discovery:** UAE must explicitly advertise `uae-to-pie/v1` only after implementing a qualified-only research reader and versioned manifest. Until then typed `capability_unavailable`; never synthesize data from `aggregate_project`.
- **Input research design:** project/source/approved policy version; primary outcome, measurement age/time window/as_of, stratum (platform/content kind/source view+version, cohort definition versions, A/B/SRE taxonomy revisions, C config), creator cluster, missingness/censor rules, comparison/control, FDR/interval policy, versioned hypothesis family, holdout rules, algorithm/seed.
- **Required handoff contents:** frozen canonical `videos.id` sample IDs with exact UAE snapshot version; each row's C persisted score+metric/cutoff, same-source/reviewed rights, SRE/QDP/A/B/C provenance, feature values+unknowns, creator token, observation/publish time and observation hash; selection/exclusion receipts, manifest checksum, active access capability (rechecked per read).
- **Output authority:** UAE **factual qualified evidence**, PIE **candidate hypotheses + inference**. Do not promote exploratory to confirmed; a validated and human-reviewed version is needed before downstream Strategy publication. No direct LLM use of unverified descriptive aggregates.
- **Version handshake:** contractual schemas accepted only after explicit peer version/capability negotiation in PIE-MOD-02/03/05 and UAE UA-MOD-25; nothing here changes UAE.

## 5. Explicit blockers and validation decisions

1. **HARD:** New UAE currently at UA-MOD-01 with `contracts.py` only; lacks current qualified row-level research API. PIE-MOD-05 live handshake BLOCKED until implemented. Historic `analytics_visible_snapshots` is not installed by that fact.
2. **HARD:** No live current D1 rights proof, signed provider receipts, creator clustering or coverage/exclusion IDs in existing PIE input pointer. PIE-MOD-07+ real analysis BLOCKED by provider.
3. **HARD:** No approved study outcome, period, alpha/FDR, experiment/control or primary hypothesis. Cannot infer confirmation/support from descriptive group counts.
4. **NOT GUARANTEED:** historical code may be incompatible with future UAE migrations and with final QDP/SRE/Group A/B/C semantics. New UAE audit rejects simple legacy code copy.
5. **SECURITY:** test fixtures use a published **test-only** HMAC key and dummy `qualified` labels solely for deterministic unit testing. The HMAC proves fixture byte integrity in tests, **never** UAE authorship, current rights, provider signatures or a real data sample. Synthetic `sample_size:2000` summary is intentionally rejected as row-level data.

## 6. Audit fixture snapshots and QA

- `tests/fixtures/pie/audit_rowset.json`: exactly 2 invented observations with canonical **synthetic** IDs 101/102 and raw score 250.5 / 120.0; declared `pie-audit-rowset/v0`, deliberately distinct from future `uae-to-pie/v1`.
- `tests/fixtures/pie/audit_aggregate_only.json`: one invented aggregate segment with `sample_size=2000`; even with a valid test-only HMAC it **must** fail row-level intake shape.
- `tests/test_pie_ownership_audit.py`: checks deterministic signed-fixture replay, tamper rejection, duplicate and nonfinite-score rejection after reseal, aggregate-only refusal, metadata pointer compatibility and absent live/LLM APIs.
- Validate with `python -m unittest discover -s tests -p "test_pie_*.py" -v`; run `python -m compileall -q intelligence/patterns_insights`. Local test harness is not a live UAE test; PostgreSQL 17, end-to-end and model calls remain **NOT RUN / BLOCKED**.

**Next stage:** PIE-MOD-02 implements explicit typed ports/capabilities; this document is a schema negotiation input, not the final PIE-MOD-03 contract. Never mark a missing upstream port ready.
