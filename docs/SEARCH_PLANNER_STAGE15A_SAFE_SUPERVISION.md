# Search Planner — SP-15A: read-only observability and safe inbox supervision

**2026-10-26 · Search Planner branch only · PARTIAL SP-15 IMPLEMENTATION (not release approval).**

## Implemented

- Added intelligence/search_planner/supervision.py with read_operational_snapshot(project_id) and run_safe_inbox_cycle(project_id,limit=10).
- New GET /api/projects/{project_id}/search-planner/operations: existing local Host/Origin and real project lookup apply. One PostgreSQL REPEATABLE READ READ ONLY snapshot.
- Aggregates only generation states; SQG consent totals as Decimal strings (or null if absent); production QV pending count/age; quarantined fixture count; SP inbox and Collector attempt states. No API keys, prompts, raw responses or query text.
- Machine-readable warning codes distinguish stale backlog, uncertain outcome, operator attention and test-fixture quarantine.
- Explicit bounded foreground mode can transfer existing validated production QV events into SP12 durable inbox; it never performs OpenRouter POST, Collector accept(), source search or receipt polling.
- Added test_search_planner_stage15_supervision.py with project-scoping, SQL query-only, Decimal, failure redaction, explicit mode and HTTP query rejection cases; added to focused workflow.

## Operator commands

Run from repository root with runtime dependencies, proper PostgreSQL credentials/migrations, and an actual local 32-hex project ID.

    # Read-only observation, zero mutations
    python -m intelligence.search_planner.supervision --project 0123456789abcdef0123456789abcdef

    # Explicitly consume at most 10 verified QV events to SP-owned inbox
    python -m intelligence.search_planner.supervision --project 0123456789abcdef0123456789abcdef --mode inbox --limit 10

    # Bounded foreground observation; max 100 cycles; stops with process
    python -m intelligence.search_planner.supervision --project 0123456789abcdef0123456789abcdef --cycles 3 --interval-seconds 30

Example project ID is a placeholder, not an actual database identifier.

GET /api/projects/<project_id>/search-planner/operations is read-only. It uses the existing local application transport check and project lookup; no credential material is returned and remote multiuser auth is not supported.

Exit status: 0 successful, 1 redacted operational_cycle_failed, 130 KeyboardInterrupt. Importing SP starts no worker. The CLI never enables the paid HTTP flag.

## Safety and restart invariants

1. Every SQL aggregation is project scoped in one repeatable-read/read-only transaction. A missing financial summary is null, never falsely zero.
2. pending_validated_outbox excludes test_fixture, and SP12 re-verifies immutable source with row locking before writing inbox and ACK together.
3. Crash before SP12 commit keeps outbox pending; crash after commit is replay-idempotent. Real PostgreSQL17 race/WAL behavior still requires SP16 verification.
4. This process does not invoke paid-batch execute_one or recovery. Unknown OpenRouter POST outcomes remain reserved, with no blind retry.
5. Collector submit remains uncertain without authentic separate read-only receipt reconciliation. HELD plans are not automatically released.

## QA status and remaining SP-15 work

**SP-15A code implemented.** Exact-HEAD GitHub Actions Python/Node/Windows/Linux PASS NOT CONFIRMED. No real PostgreSQL17 tests here; no live paid POST or Collector receipt.

**SP-15 overall remains open:** durable per-worker lease and heartbeat; graceful process supervision across supported workers; evidenced financial reconciliation; real Collector capability publisher/readiness; production redacted log/safe recovery/metrics; Windows service stop-start and restart tests. No paid automatic scheduler is authorized. SP16 still requires all-up release QA.
