# PIE-MOD-03 — `uae-to-pie/v1` proposed versioned research dataset

**Status:** PIE-owned strict *proposed* receiver contract and synthetic tests implemented, 2026-10-26. **Not** a UAE-released API, authenticated snapshot, validated source rights, qualified live analytics, or statistical inference. Repository `BanSins7/ContentMachine`, branch `feature/patterns-insights-full-module`. Future UAE handshake at UA-MOD-25 / PIE-MOD-05 and current policy eligibility at PIE-MOD-06.

## Frozen envelope and identity

`PIEDatasetPointer` from PIE-MOD-00 is a shape-only pointer with required `project_id`, `source_id`, `source_policy_version_id`, `uae_engine_version`, `uae_dataset_sha256`, `uae_manifest_sha256`, `calculation_config_id`, `source_metric_definition`, `source_metric_version`, timezone-aware `as_of`, `schema_version=pie-dataset-pointer/v1`. A real UAE publisher must provide a trusted versioned receipt for those values later. None is available from the new UAE at this stage.

`validate_dataset_manifest(pointer, manifest)` requires **exactly** six root fields:

| Object | Required fields and interpretation |
|---|---|
| `schema_version` | Literal `uae-to-pie/v1`, not historical `uae-pattern-handoff/v1` descriptive summary |
| `producer` | `engine_version` matching pointer, canonical UUID `run_id`, explicit `dataset_build_version` |
| `scope` | Exact match to pointer for `project_id`, `source_id`, `source_policy_version_id`, `calculation_config_id`, `source_metric_definition`, `source_metric_version`, timezone-aware `as_of` |
| `versions` | `group_a_version`, `group_b_version`, `group_b_taxonomy_version`, `sre_taxonomy_version`, `creator_cohort_version`, `prior_cohort_version`, `niche_cohort_version` (the last may be null; otherwise explicit version) |
| `selection` | `rule_version`, `population_sha256`, `scanned_count`, `included_count`, `excluded_count`, typed `exclusions` list of `{video_id, reason_code}`. Counts must reconcile; exclusions canonical ascending, disjoint from inclusions |
| `observations` | Nonempty ordered row-level array; each canonical `videos.id` appears exactly once and UAE snapshot IDs cannot repeat. **No synthetic expansion** of descriptive sample counts |

Each observation has **exactly** these fields (no implicit fallback):

* Canonical row and source scope: `video_id`, `uae_snapshot_id`, `uae_snapshot_sha256`, `project_id`, `source_id`, `source_policy_version_id`, `creator_key`, `platform`, `content_kind`, `observed_at`, `published_at`.
* Exact upstream lineage: `qdp_run_id` (canonical UUID), `qdp_preparation_sha256`, `group_a_analysis_id`, `group_b_analysis_id`, `metric_snapshot_id`, `calculated_metric_id`, `sre_decision_sha256`, `group_a_sha256`, `group_b_sha256`, `metric_snapshot_sha256`, `calculated_metric_sha256`.
* Claimed statuses and outcome: `status="qualified"`, `score_status="complete"`, `breakout_score` as **finite canonical decimal string**, **not** clipped to 0–100, never inferred from CTA or C2 niche_outlier.
* Frozen component revisions: all seven `versions` fields exactly matching manifest-level versions; mixing analysis/taxonomy/cohort versions in one manifest is blocked rather than silently pooled.
* Typed, flat, nullable feature claims `features_a` and `features_b`, and `features_sha256 = canonical_hash({"a":features_a, "b":features_b})`. Valid values: null (explicit unknown), booleans, bounded signed integers, bounded string, ordered list of strings. Lists (including B3 roles) can repeat. `False` and `null` are distinct. Floating point and nested objects are rejected. **Semantic** feature dictionary/quality and missingness belong to PIE-MOD-11/12; data are not made trustworthy by structural validation.

Limits: max 50,000 included and max 50,000 excluded identities (parser resource limits only, **not** statistical minimum/sample significance criteria), 64 MiB raw UTF-8 JSON transport, positive BIGINT IDs, bounded tokens, strict object keys, finite numeric strings, `published_at <= observed_at <= as_of`. No minimum N/FDR/alpha/threshold is silently defined.

## Canonical SHA binding (integrity, NOT authentication)

`canonical_hash(x) = sha256(json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))` (PIE-MOD-00).

1. `pointer.uae_dataset_sha256 == canonical_hash({"schema_version":"pie-row-dataset/v1", "observations":manifest["observations"]})`.
2. `pointer.uae_manifest_sha256 == canonical_hash(manifest)` (pointer itself is **not** part of the manifest; no self-referential hash).
3. Each row hashes its own `features_a` / `features_b` with `features_sha256`; the row's `PIEObservation.row_sha256` is the canonical hash of the complete row. Source-anchored upstream `*_sha256` values are **opaque claims** until authenticated through live UAE, rights and artifact verification.

Rows and exclusions must be sorted by ascending `video_id`. Order changes alter hashes; no silent resort/dedup. `load_dataset_json` rejects repeated JSON keys, invalid UTF-8, nonfinite JSON constants and malformed/oversized input. Frozen `PIEValidatedDataset`/`PIEObservation` store **detached canonical serialized copies**, not references to mutable source dicts; `as_dict()` returns a fresh detached copy. `sample_size` is the number of validated observation entries, never unverified `summary.sample_size`.

## Current verified limitations and peer negotiation

PIE-MOD-03 validates a **proposed wire format**, not its issuer. Hashes can be recomputed by an attacker: neither SHA nor the test fixture key proves UAE authenticity. `verified_live` is always `False`. Future readers must first verify actual UAE capability/wire version, immutable selected snapshot IDs, current per-row qualified status, project membership, SRE LIVE mode or approved human override, QDP qualified membership, A/B completeness, approved persisted C scores and corresponding cohort metadata, source read rights/TTL at each access, and signed/authorized manifest provenance. A direct dict from a caller must never bypass those gates. No Pattern/Insight runtime, database migrations, model calls or score recomputation here.

As observed on 2026-10-26, UAE `feature/unified-analytics-full-module` at HEAD `12d937d2fbbae22fd4c2bfd910798078c1dfef63` is still in its own early standalone build, with no operational `uae-to-pie/v1` research publisher. Historical PR #23 `aggregate_project` summarizes 8 dimensions, but **its 2,000 descriptive sample size cannot be converted into 2,000 independent post observations**. For now `PIEPorts().require("uae")` returns `capability_unavailable` unless a mock is injected; a mock `declared` status cannot turn `verified_live=True`.

## QA and reproducing

- Synthetic positive: `tests/fixtures/pie/uae_to_pie_v1_positive.json` — 2 **invented** rows (videos 101/102), one excluded ID 103, 2 different raw C7 values including one above 100 and one negative. Synthetic source, creator, policy and SHA values are placeholders, not real permissions or UAE evidence.
- Negative summary: `tests/fixtures/pie/uae_to_pie_v1_aggregate_rejected.json` — claimed `sample_size=2000`, but no actual rows or trusted author; must be rejected.
- `tests/test_pie_dataset_contract.py` covers producer/schema/version mismatch, hash tampering, cross-project/source/policy, duplicate IDs/snapshot IDs, count tampering, unsupported kind, missing score, NaN, decimal syntax, feature hash, timeline errors, duplicate JSON keys, immutable replay and unknown-vs-false.
- Local: `python -m unittest discover -s tests -p 'test_pie_*.py' -v` (**41/41 PASS**, local PIE subset); `python -m compileall -q intelligence/patterns_insights` (PASS), Python 3.13. GitHub Windows/Linux CI must be checked separately; PG17/E2E **NOT RUN**. These tests prove validation behavior only, not actual UAE integration.

**Next:** PIE-MOD-04 — PIE-owned PostgreSQL migration design and migration conflict audit, retaining peer code isolation. Then PIE-MOD-05 adapter handshake, PIE-MOD-06 rights/provenance gate; no premature merge into `main`.
