# FIX-10 — independent Group A/B/C handoff and current-read contract

QDP module: `intelligence.qualified_data.preparation`.

**Version:** `qdp-preparation/v2`. No new payments, provider calls or Group task scheduling in this stage. Historical preparation facts are immutable PostgreSQL rows `qdp_preparations` with group-specific `inputs` and `input_sha256`. All three group records are created in the same fenced QDP worker transaction; any combination of ready/blocked is allowed.

**Group A/B video:** `media_asset_id`, `media_sha256`, `media_kind='video'`, `media_size_bytes`, plus exact SRE/D1 source, policy, observation and discovery reference. QDP does not send local file paths or invent carousel slide manifests. The peer must reauthorize and decode on its own worker.

**Group C metrics:** exactly two measured `metric_snapshot_ids` only if compatible, `metric_history_sha256`, metric definition+version, active `score_config_id`; no media pointers, guessed zero counts or `breakout_score`. Score permissions and config revocation affect only C.

**Current view:** `qdp_current_base_enrichments` checks D1 source + metadata rights, independent of group state; `qdp_group_current_preparations` checks each group's *own* rights (current SQL). `read_current_preparation(project,run_id,video_id,group)` checks schema installed, project scope, stored input digest, current SRE selection hash, exact D1 metadata, and local media bytes / D1 metric history and config. If blocked/revoked it returns only `state=blocked`/reason without hidden input JSON. The other groups continue to work.

**Migrations:** `042_qdp_group_current_preparations.sql` then `043_qdp_metadata_readability_fix.sql`, forward-only. Never alter applied 016/040/041/042. Missing 043 fails QDP preflight with 503.

**Verification:** 11 `tests.test_qdp_preparation` pure unit tests and 4 PostgreSQL tests in `tests.test_qualified_data_db`. `scripts/qdp_verify.py --with-postgres` requires a caller-confirmed disposable PostgreSQL 17 DB.

**External limits:** real SRE producer and Group A/B/C consumer integration (including live rights revocation mid-job) remain peer stages. `get_run` groups use database-level current view; **execution consumers must call `read_current_preparation` and recheck again**, since SQL alone cannot prove local file bytes or the latest SRE review. FIX-15 extends invalidation propagation.

**QA at documentation creation:** code committed; previous CI found one overly restrictive historic metadata read. Forward-only 043 patch committed, current exact-SHA PG17 retest still pending. No unsupported PASS claim.
