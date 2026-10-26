# VC-MOD-04 — QV publisher, manifests, sharding, atomic ACK

**Branch:** `feature/video-collector-v1` · **PR:** #33 · **Entry points:** `intelligence/collector/query_publisher.py` and `publisher_cli.py`.

## Purpose

After a completed, production `validated_queries_ready/v1` event arrives from **main Search Planner**, persist its entire selected set as immutable, project-scoped Collector shards and acknowledge `qv_outbox` **only inside the same PostgreSQL commit**. No second query generator, no manual JSON handoff and no DTK requests.

## Core implementation

- `prepare_qv_publication(TrustedQVSource, shard_size=20)` is deterministic and offline, requires the backend-sealed VC-MOD-03 source (never an unverified browser JSON). It sorts by `hypothesis_id, rank_in_hypothesis, candidate_id`.
- Defaults to **20 selected per shard** so a source with 480 selected queries creates 24 shards. An explicitly configured shard size must be in **1..50**; no loss/truncation of candidates. The resulting `validated_query_set/v1` uses the validated original UUID IDs and approved RTB publication ancestry. `query_set_sha256`, `shard_sha256` and `manifest_sha256` are deterministic.
- Every shard retains its original candidate source details (branch, source facts, research stance, scores, ranks and language) in `collector_manifest_queries.source_payload`. Also stores **all** `qv_coverage` rows, including hypotheses with no selected query.
- A 0-selected production event creates a single immutable `collector_qv_manifests(status='no_queries')` with **zero** shards, **zero** QuerySets, **zero** provider tasks. It is still acknowledged durably.
- `publish_qv(project_id,outbox_id)` uses `read_trusted_qv(...,allow_acknowledged=True)` only for integrity-checked existing publication replay; it does not relax production embedding/project/C9 checks.
- The commit transaction locks `qv_outbox` with `FOR UPDATE OF o`, checks project, IDs, mode, status and **exact** event pointer again; inserts immutable `collector_qv_manifests`, `collector_query_sets`, `collector_query_shards`, `collector_manifest_queries` and `collector_manifest_coverage`, and then updates **the same** `qv_outbox.delivered_at` with an `IS NULL` condition. If any write or ACK fails, PostgreSQL rolls the entire transaction back.
- Concurrent publishers are serialized at the outbox lock; unique indices for outbox/run, manifest query identity and shard ordinal prevent duplicate durable publication. Repeated identical completed event returns `reused=true` and existing QuerySet IDs after comparing manifest SHA, source fingerprint, shard digests and counts. Different shard sizing or altered verified content returns 409, not a silent rewrite.
- A QV event ACKed without a Collector manifest, or with a manifest but without ACK, is a serious integrity inconsistency: **409 fail closed**. Never auto-repair the database ledger.
- `publish_due_qv(project_id,limit=20)` polls the production-only Search Planner backend adapter, and requires the **separate** `CM_COLLECTOR_QV_CONSUMER_ENABLED=1` flag. It commits each due event independently and does not perform provider search.
- `python -m intelligence.collector.publisher_cli --project-id <hex> --outbox-id <uuid>` is read-only by default; `--publish` must be specified to write/ACK. `--pending --publish` requires consumer flag. CLI does not initiate a background worker, DTK or paid LLM.

## Correct transaction boundary

```text
Trusted REPEATABLE READ QV source → canonical sharding → PostgreSQL transaction:
  SELECT qv_outbox FOR UPDATE
    ├─ validate current pointer + project + completed production run
    ├─ check existing manifest SHA for idempotent replay
    ├─ INSERT manifest
    ├─ INSERT all 1..50-query QuerySets and shard links
    ├─ INSERT every selected candidate UUID and every coverage row
    └─ UPDATE qv_outbox SET delivered_at=NOW() WHERE delivered_at IS NULL
COMMIT
```

**Never ACK before durable publication**, never acknowledge from preview or direct `Search Planner` call. `qv_runs.source_outbox_id` is an earlier SQG source event — it is *not* the event that this publisher ACKs.

## Tests

`tests/test_video_collector_qv_publisher_v1.py` includes offline QA for:
- 0, 1, 20, 21, 480, 481 selected;
- exact deterministic 20-shard default/50 max, no IDs lost;
- all selected UUID links and hypothesis coverage;
- no_queries with no empty fake QuerySet and one ACK;
- replay returning original manifest/shards and one ACK only;
- changed shard size conflict, already-ACKed but missing manifest fail-closed;
- simulated transaction rollback before shard/coverage insert or ACK;
- bad project/UUID inputs, rejected-only coverage and disabled publisher consumer;
- no provider calls from any operation.

PG17 schema migrations and composite FKs are additionally covered by `test_video_collector_schema_v1.py`. True **concurrent PostgreSQL writers + full upstream approved C9→SQG→QV dataset** require a dedicated seeded PG17 integration test before this stage can be crossed off in the master plan. An offline fake transaction is **not** proof of concurrency behavior on live PostgreSQL.

Run:
```sh
python -m unittest discover -s tests -p 'test_video_collector_qv_publisher_v1.py' -v
python -m unittest discover -s tests -p 'test_video_collector_schema_v1.py' -v
python -m unittest discover -s tests -v
python -m compileall -q intelligence/collector
```

## Limits and next stage

No QuerySet list UI is introduced here; VC-MOD-05/06 will use real `collector_query_shards.query_set_id` for trusted plan/commit/release. This stage does not submit DTK `search_posts`, ingest a video, or classify relevance. Real provider smoke and end-to-end E2E are separate gates.
