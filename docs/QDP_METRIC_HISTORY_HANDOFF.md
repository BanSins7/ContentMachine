# QDP FIX-08 — D1 metric snapshots / Group C handoff

**Status:** CODE_IMPLEMENTED, QDP-owned disposable PostgreSQL 17 QA_PASS on SHA `8135b7620dcd73b4c6e91e18e8c854f7f13ba9c5`: [135/135 tests](https://github.com/BanSins7/ContentMachine/actions/runs/40310914445). True independent Group C computation and live SRE/Collector multi-branch merge remain separate.

## Ownership

QDP owns a bounded **read-only** adapter `intelligence.qualified_data.metric_history.read_metric_history(conn, metadata)` and a pure selector `resolve_metric_history(rows, metadata, *, now=None)`. D1 owns `metric_snapshots`, `source_metric_definition` and `source_metric_version`. The Group C owner will compute views growth/breakout only with its own validated score formula, priors, cohorts, and configuration; QDP does **not** compute `breakout_score`, growth rates, engagement scores, or assume missing values are zero.

## Data selected

- Exactly the already-verified QDP SRE/D1 video + `source_id` + `source_policy_version_id`; no source, platform, or video fallback.
- At most 64 latest D1 raw rows by `observed_at DESC,id DESC` per worker lookup. The selector rechecks IDs/source/version, valid timezone-aware `observed_at`, `observation_time_assumed = false`, non-expired observation, and `data_quality_status = 'ok'`.
- Persist **only legitimate valid** raw history observations in an oldest-first array; keep existing NULL metrics NULL. Never fabricate a timestamp, zero views, creator count, or source-specific metric definition.
- Choose the **latest two** chronological observations with exact `views` (integer ≥ 0), the same non-empty (`source_metric_definition`, `source_metric_version`) and strictly increasing `observed_at`. Counter must be nondecreasing. A newer `retracted` / `counter_correction` observation blocks Group C rather than invisibly reusing older data.
- A verified `published_at` must exist and be no later than the older selected observation. Unknown publication date blocks the Group C growth pair, but does not invent a date or force Group A/B to block.
- The QDP active calculation config is a **pointer** to a real configured object with non-null `alpha`/`bayesian_k`. Config/rights and two compatible measured snapshots are independent prerequisites. Their existence does not imply Group C has calculated anything.

## Immutable enrichment envelope

`qdp_enrichments.metrics` holds the `qdp-metric-history/v1` object:
`
{
  "schema_version": "qdp-metric-history/v1",
  "available_count": 2,
  "observations": [
    {"id": 101, "observed_at": "<D1 timestamp>", "views": 75,
     "precision": "exact", "source_metric_definition": "platform_post_views",
     "source_metric_version": "v2", "...": "<other actual nullable D1 facts>"},
    {"id": 102, "observed_at": "<later D1 timestamp>", "views": 140,
     "precision": "exact", "source_metric_definition": "platform_post_views",
     "source_metric_version": "v2", "...": "<other actual nullable D1 facts>"}
  ],
  "metric_snapshot_ids": [101, 102],
  "growth_pair_ids": [101, 102],
  "growth_definition": {"name": "platform_post_views", "version": "v2"},
  "growth_reason_code": null,
  "counts": {"read_count": 2, "valid_count": 2, "exact_view_count": 2},
  "history_sha256": "<canonical SHA-256>"
}
`

**This is an illustrative contract shape, not actual user/provider D1 data.** Persisted `metric_snapshot_ids` include only legitimate current-quality D1 observations; `qdp_preparations[C].inputs.metric_snapshot_ids` include only the selected *chronological pair*. Group C also receives `metric_history_sha256`, `metric_definition`, `score_config_id` and the existing exact SRE content/observation/discovery pointer fields. Always recheck current D1 permissions, quality/expiry and input SHA before downstream work.

## Blocking reasons and independence

- `score_rights_missing`, `score_configuration_missing` remain highest precedence.
- `metric_history_insufficient` for fewer than two comparable measured points.
- `metric_definition_incompatible` for mismatched or absent names/versions.
- `metric_timestamp_incompatible` for duplicate/non-increasing times.
- `metric_counter_correction` for decreasing view counts.
- `metric_observation_revoked_or_corrected` for a newer corrected/retracted observation.
- `published_at_unknown` / `metric_publication_time_invalid` for missing/invalid publication evidence.

Group A and B are evaluated **independently** using media asset and media permission checks. A metric readiness failure cannot create a fake media asset or block valid A/B preparation; likewise a missing A/B media asset does not automatically block valid Group C input readiness.

## Verification / limitations

Run `python -m unittest tests.test_qdp_metric_history -v` for pure tests. With a **disposable PostgreSQL 17 DB**, use `QDP_DISPOSABLE_TEST_DB=YES CM_TEST_PG_DSN=<TEST_DB> python scripts/qdp_verify.py --with-postgres`. The full suite succeeded on SHA `8135b762`: [Actions 40310914445](https://github.com/BanSins7/ContentMachine/actions/runs/40310914445) with 135 tests, 0 failures/errors/skips, Python/JS syntax PASS.

FIX-08 introduces **no SQL migration**: D1's original versioned `metric_snapshots` and QDP v1's immutable `qdp_enrichments.metrics` + `qdp_preparations.inputs` store the results. The standalone module does not require an enabled remote provider. LIVE producer, final Group C consumer and all peer branches are separately scoped integration work.
