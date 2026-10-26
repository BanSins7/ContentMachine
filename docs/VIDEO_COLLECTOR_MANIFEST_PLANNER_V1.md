# VC-MOD-05 — Provider Registry and technical Task Planner

Status: **IMPLEMENTED IN CODE / QA PENDING** on `feature/video-collector-v1`; workflow runs for the latest commit are not yet PASS. This stage is an **offline-only technical planning layer**; no external DTK POST, no execution consent, no stored run and no video ingest.

## Implementation and entry points

- `intelligence/collector/registry.py` is the ONLY capability registry. `dtk_tiktok` supports **keyword → `search_posts`**. Hashtags and creator search are unsupported; no implicit keyword fallback. YouTube, Instagram and Apify entries are future adapters with **zero verified operations**.
- `intelligence/collector/planner.py` remains the ONLY task planner for an individual QuerySet, with Unicode NFKC/casefold deduplication, query→hypothesis/direction lineage, deterministic task fingerprints and explicit 1-page provider bound.
- `intelligence/collector/manifest_planner.py` adds **`build_manifest_technical_preview(TrustedQVSource, ...)`**, composing the existing planner over ALL backend-trusted QuerySet shards from VC-MOD-04. This is not a second SP planner. Its snapshot carries per-shard tasks, rejected unsupported candidates, all query IDs, source properties and deterministic `plan_hash`.
- A source of 480 selected QV candidates with default `shard_size=20` yields **24 independent shard plans** containing all 480 query UUIDs. The aggregate manifest request cap defaults to **40** and blocks excessive execution, instead of truncating source queries. Raising that cap (max 250 in V1 planner) is an explicit offline input; it is NOT provider quota permission.
- **Every** selected UUID must be either in a technical task or the explicit `rejected` list. A mismatch, overlap, repeated task hash, wrong count or forged source rejects with a machine-readable `CollectorError`.
- `query_set_not_server_verified`, `source_policy_not_verified`, and `provider_connection_not_verified` remain preview-only blockers. Source's trusted DB provenance is NOT a persisted/run-released QuerySet authorization. VC-MOD-06 will consume real sealed `collector_query_shards.query_set_id` and owner consent.
- Preview keeps `execution_authorized=false`, `external_calls=0` even when a source policy or provider connection is configured. No new HTTP endpoint publishes or releases from this function.

## Exact input/output scope

Input: `TrustedQVSource` verified by VC-MOD-03 (not client JSON), optional source_id (registry only), shard_size 1..50, validated `CollectorLimits`, total_request_cap 1..250. The same immutable `manifest_sha256` from VC-MOD-04 is exposed for cross-stage reconciliation.

Output schema `collector.manifest_technical_plan/v1`: project and QV run, source/registry/planner version, manifest fingerprint, query/shard/task/rejection/coverage counts, all technical task descriptors and UUID lineage, explicit blocked/unsupported operations, bounded external request estimate and zero provider calls.

## Tests and CI

`tests/test_video_collector_manifest_planner_v1.py` checks:

- registry source capabilities, no false operational/policy readiness;
- exact one-keyword task and no DTK submit;
- all 480 candidate UUIDs retained in 24 shards, aggregate global quota gate;
- shard boundary 20/21 and explicit 50-sized shard;
- genuine zero-result manifest / no task;
- hashtag rejected instead of keyword coercion;
- no capabilities invented for future providers;
- bad caps / forged unsealed source rejected;
- stable output digest;
- query UUID/language/platform provenance retained.

```sh
python -m unittest discover -s tests -p 'test_video_collector_manifest_planner_v1.py' -v
python -m unittest discover -s tests -p 'test_video_collector_qv_publisher_v1.py' -v
python -m unittest discover -s tests -v
```

CI status is tracked separately in `VIDEO_COLLECTOR_MASTER_IMPLEMENTATION_PLAN.md`. All feature branch workflows use GitHub Actions `concurrency` to cancel only obsolete commits from **the same workflow and ref**, reducing queued redundant runners. Neither main nor other feature branches are cancelled.

## Next

VC-MOD-06 binds these technical previews to **persisted project-scoped trusted query_set_id** rows, then held commit and **explicit owner release**. The current function alone is NOT an executable grant, and a Publisher ACK is not a TikTok search.
