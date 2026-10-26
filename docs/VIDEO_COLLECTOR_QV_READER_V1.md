# VC-MOD-03 — Trusted Query Validation Reader and Converter V1

## Implemented on feature/video-collector-v1

- Python: intelligence/collector/qv_reader.py.
- Tests: tests/test_video_collector_qv_reader_v1.py (offline negative and positive DB stub QA).
- Persistent QV manifest and ACK are **NOT** part of this stage. They are explicitly VC-MOD-04.
- This is backend-only. No public HTTP endpoint, no caller-provided trusted=true switch, no file upload route and no live DTK action.

## Authoritative PostgreSQL source

One explicit read_trusted_qv(project_id, outbox_id, connect=...) invocation does a REPEATABLE READ READ ONLY PostgreSQL transaction:

1. Locks nothing; selects an existing qv_outbox ID with a matching project-scoped qv_run, completed sqg_generation and source sqg_outbox.
2. Validates exact event_type validated_queries_ready/v1 and payload pointer schema validated_queries/v1, selected_count/generation/validation identifiers, embedding_mode semantic_local, generation and QV completed status. The source sqg_outbox must already have the QV's ACK.
3. Reconstructs SQG query_candidates/v1 from actual sqg_candidates joined to sqg_batches in canonical batch/position order, SQG coverage and pinned generation config/model. Recomputes source_bundle_sha256 and checks it equals qv_runs.source_bundle_sha256. No second Search Planner selection algorithm.
4. Validates qv_runs.config and embedding model fingerprint against qv_runs.config_sha256 through the upstream validate_policy + policy_fingerprint functions.
5. Reads approved hierarchical C9 RTB publication through the existing Search Planner input_loader.read_published_snapshot(conn,...), checks run ID, publication ID, taxonomy SHA, taxonomy version and two human review gates (the upstream loader enforces them).
6. Validates every SQG context hash and actual hypothesis/direction/branch ancestry, with facts matching the approved context. Rejects missing context coverage, candidate source_fact_ids mismatch and wrong approved direction.
7. Loads ALL qv_decisions (selected and rejected) in the same transaction. Every original SQG UUID candidate must have exactly one decision. Rejected/duplicate/excluded candidates never become accepted.
8. Loads all qv_coverage and checks source_counts per hypothesis, selected sums and selected QV query decisions. Runs strict QvBundleShapeV1.parse on the assembled canonical bundle.
9. Returns a sealed TrustedQVSource; its nested queries/coverage/decisions are stored as canonical JSON strings. Calling its properties creates defensive copies. A changed/fake sealed source fails the subsequent fingerprint check.

## Conversion to Collector

convert_trusted_qv(source, candidate_ids=None) requires the sealed TrustedQVSource; it cannot take raw client JSON or a guessed ID.

- All selected ID values remain canonical QV candidate UUID strings (Collector query_id).
- query_mode→mode, hypothesis/direction, language, exact query text; accepted only after DB checks.
- Keeps original QV branch IDs, source_fact_ids, angle, relevance/negative/MMR scores and source decisions in source.selected for the future manifest ledger (VC-MOD-04).
- Maps immutable approved publication, taxonomy version/hash, project and generation to the Collector validated_query_set/v1 parser; provenance uses validation_version qv:<validation_run_id>.
- ≤50 queries can form a Collector QuerySet. Greater than 50 never truncate silently: caller must request explicit selected candidate_ids groups of 1..50. Actual deterministic splitting and durable publisher belong to VC-MOD-04.
- selected_count=0 → no accepted QuerySet, and future publisher creates no_queries manifest. No invented keyword, no DTK request.
- Explicit conversion IDs must be approved selected IDs; duplicates and rejected UUIDs fail closed.
- This stage NEVER updates qv_outbox.delivered_at. A future publisher must revalidate and ACK atomically with writes.

## Error/IO behavior

- Missing or wrong-project QV outbox → qv_event_not_found (404).
- Wrong mode, generation, approval or C9 hash, tampering → CollectorError machine-readable 409.
- Already acknowledged QV event rejected unless backend caller explicitly passes allow_acknowledged=True for integrity-checked publishing replay. This is not a trust bypass.
- No connection is opened during import and no source provider/LLM calls are made.
- Read-only transaction and QV immutability stop races changing the source during conversion. VC-MOD-04 must lock/recheck the same event when persisting.

## QA and remaining limitations

Run:
- python -m unittest discover -s tests -p test_video_collector_qv_reader_v1.py -v
- python -m unittest discover -s tests -p test_video_collector_ports_v1.py -v
- python -m unittest discover -s tests -p test_video_collector_*.py -v
- python -m unittest discover -s tests -v
- python -m compileall -q intelligence/collector

Tests reject wrong project, test_fixture, incomplete generation, altered source bundle SHA, wrong config/model fingerprint, changed C9 taxonomy SHA/version/run, source outbox not ACKed, wrong facts/context, forged unsealed event, replay without permission, zero results, manually promoting unselected UUID and mutation after source read.

This read-only implementation itself is *not* evidence of live approved C9→SQG→QV production DB results. Positive PG17 seeded full-source and publisher atomicity are VC-MOD-04 and VC-MOD-15 gates.

**Next stage: VC-MOD-04**, durable manifest/shard publisher and qv_outbox ACK in one DB transaction, including all 480 selected and zero-query cases.
