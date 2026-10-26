# Search Planner SP-15B — Fenced PostgreSQL supervisor lease

**26 October 2026. CODE IMPLEMENTED. Full PostgreSQL17 and CI QA still unverified.**

## Purpose

An explicitly invoked SP-safe inbox supervisor must not rely on an in-process mutex: multiple local Windows/Linux workers may run concurrently or restart after a crash. SP-15B adds a durable per-project, single-kind lease with fencing and heartbeat. This does **not** dispatch Collector jobs or paid OpenRouter requests.

## Changes

- Additive migration intelligence/migrations/057_sp_supervisor_leases.sql with sp_supervisor_leases(project_id,worker_kind,owner_id,fence_generation,acquired_at,heartbeat_at,lease_until). project_id references project_registry, unique PK(project_id,worker_kind), worker_kind constrained to qv_inbox.
- Acquire atomically by INSERT .. ON CONFLICT DO UPDATE **only when expired**. Every takeover increments fence_generation. Busy existing lease returns a no-work status without querying QV.
- Heartbeat UPDATE requires the exact project, owner UUID, fence and unexpired lease. Stale owner receives fail-closed 409. Release is also fenced and cannot release a successor's lease.
- run_fenced_inbox_cycle executes only an existing bounded run_safe_inbox_cycle after a successful heartbeat; checks heartbeat after; attempts release in finally.
- Explicit --mode leased-inbox in CLI. Default remains --mode observe; --mode inbox remains the existing unleased, still SP12 transaction-idempotent manual path for diagnostics.
- Offline tests/test_search_planner_stage15_leases.py covers lease acquisition, contention, heartbeat and stale owner, safe no-work, explicit CLI selection, no provider calls, and release on failure. Included in focused workflow.

## Operator usage

Run from project root with dependencies and SP migrations 054-057 applied to a proper PostgreSQL 17 database:

    python -m intelligence.search_planner.supervision --project <32hex_project_id> --mode leased-inbox --limit 10

The placeholder must be replaced with an actual local project ID. With no pending events it returns ingested_events=0; with an already-held lease it returns lease_status=busy. In all cases there are zero paid or Collector accept calls.

A single cycle performs pre/post heartbeat, not an independent heartbeat thread. The 120-second lease may expire during unusually long work; **this does not imply duplicate provider submissions** because the work is only QV→SP inbox and SP12 uses a PostgreSQL row lock, immutable snapshots, unique keys and atomic QV ACK. Do **not** reuse this lease for paid batch or external Collector POST.

## Known limitations / non-claims

- Real PostgreSQL17 fresh+upgrade migration, two-process takeover race, crash before/after commit, WAL replay and Windows/Linux lifecycle have NOT been executed by this chat and remain SP16 release gates.
- The heartbeat is cycle-scoped. A true long-running daemon requires signal handling, dedicated heartbeat loop, graceful stop/drain and process/service supervision under SP15C.
- Invoice reconciliation is not provided and must be evidence-based. No reserved money may be silently refunded or marked actual.
- This mechanism does not supply real Collector capabilities or receipts; those remain external verified integration work.

**Next:** SP-15C production operations and remaining SP-04 recovery, then SP-16. Parent SP-15 stays unchecked until code completion; completed SP-15A/SP-15B substeps are crossed off in the master checklist.
