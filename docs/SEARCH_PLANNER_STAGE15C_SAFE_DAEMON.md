# Search Planner — SP-15C: foreground safe process and continuous heartbeat

**Date:** 2026-10-26. **Implementation in the standalone Search Planner branch only.** Status: SP-15C *safe QV supervisor lifecycle CODE IMPLEMENTED*; release/real PostgreSQL QA remains open. This is **not** a paid-generation daemon and does not execute Collector source tasks.

## What was delivered

- `intelligence/search_planner/safe_daemon.py` implements `SafeInboxSupervisor`, validated `SupervisorSettings`, controlled structured JSON operational events and an explicit CLI.
- Persistent **one-per-project** PostgreSQL fencing lease uses existing `lease_ops.py` and additive migration `057_sp_supervisor_leases.sql` from SP-15B. The thread heartbeats separately (using its own DB connection per heartbeat), including while an inbox projection is slow. Every new processing cycle additionally verifies the matching owner and fence synchronously.
- Graceful stop uses `threading.Event` and platform-standard `SIGINT`/`SIGTERM` handling from a main-thread CLI. The running SP12 transaction finishes before the session releases its lease; no new cycle is started after the stop request. The CLI restores original signal handlers on exit.
- Strict upper bounds: 1–50 QV events/cycle, lease 30–300 seconds, heartbeat 5–60 seconds with 3x margin, poll 1–3600 seconds, up to 100,000 bounded cycles (or explicit indefinite mode `--max-cycles 0`). Import/construct do not create threads, bind routes, read secrets, or begin external operations.
- A failed or stuck heartbeat, a lost fencing lease, or a failed release is **not** reported as a successful stop. A successor's lease is never deleted or force-unlocked. The safe worker can restart after TTL expiry, with SP12 PostgreSQL row-lock + immutable inbox/QV ACK replay invariants preserving exactly one logical ingest.
- JSON diagnostic events contain only status codes, project ID, cycle/event counts, and explicit zero paid/provider/Collector attempts. Raw PostgreSQL exceptions, token values, model prompts, external receipts and query payloads are never emitted. Current `GET /operations` still provides project-scoped database-backed counts and lease heartbeat age.

## Operator start/stop — Linux and Windows

**Preconditions:** full current SP migrations including `057_sp_supervisor_leases.sql`, a valid `CM_TEST_PG_DSN` only when running on disposable QA database (never point destructive tests at production), normal application DB connectivity and a real local project identifier. The safe supervisor itself does not need the OpenRouter API key or a Collector provider key.

From the repository root:

```bash
# Explicitly enabled foreground safe QV->SP inbox service.
# MAX 5 QV events per cycle, 20-second heartbeat, no paid/network dispatch.
python -m intelligence.search_planner.safe_daemon \
  --project <32_hex_project_id> --enable-safe-inbox \
  --limit 5 --lease-seconds 120 --heartbeat-seconds 20 \
  --poll-seconds 10

# Finite safe smoke (one cycle, exit without daemon)
python -m intelligence.search_planner.safe_daemon \
  --project <32_hex_project_id> --enable-safe-inbox --max-cycles 1
```

The above commands work with Python on Linux/macOS shells and PowerShell, although PowerShell line continuation is different: enter as a single line or use PowerShell's backtick instead of backslash. **Do not auto-start via `app.py` or on package import.** To stop a foreground worker, press Ctrl+C; on a Linux service manager use SIGTERM and give enough graceful shutdown time for the current PostgreSQL inbox transaction. On Windows console use Ctrl+C or a compatible process manager with console-control support; forcibly terminating a stuck process does not assert clean shutdown, and lease takeover waits for expiry.

To check state without mutation, call the existing local-app project route:

`GET /api/projects/<32_hex_project_id>/search-planner/operations`

This route is read-only and never triggers QV consumption/paid operations.

## Failure handling

- **Another process owns lease:** `lease_busy`, no QV work or provider calls.
- **Heartbeat failure while idle or in transaction:** mark lease lost/stop; current transaction may finish but is idempotent, and no new cycle begins. Nonzero CLI status; error is redacted.
- **Process killed after SP12 inbox INSERT and QV ACK:** both are committed together; replay discovers the durable event rather than duplicating it.
- **No Collector adapter:** SP inbox remains `awaiting_collector`, not fake external acceptance.
- **Unknown OpenRouter charge:** remains reserved/uncertain. SP-15C never invokes `GenerationExecutionController.execute_one`, retries paid POST, or reconciles invoices.
- **Collector submit timeout:** remains uncertain until separate owner-authorized, receipt-only reconciliation. SP-15C has no Collector adapter.
- **Lease release failure:** nonzero exit; an expired lease can only be replaced by a new fenced owner, never by blind force.

## Unit tests and evidence

`tests/test_search_planner_stage15_daemon.py` covers settings bounds, successful bounded cycles, exclusive lease, preexisting/during-cycle graceful stop, malformed/fake output rejection, heartbeat loss before continuation, backend exceptions, a real independent background heartbeat during a simulated slow projection, lease release failure and redacted CLI errors. Focused GitHub workflow now includes these tests.

**No claims of successful actual CI/PG17 service tests:** source was committed through the connected GitHub integration; full repository was not available as a runnable checkout in this execution environment. GitHub-hosted Windows/Linux, disposable PG17 fresh/upgrade, multi-process WAL crash/restart, and browser E2E remain explicit SP-16 release gates.

## What remains

SP-15C is **only the safe QV inbox lifecycle slice**. Remaining SP-15 concerns:
1. Evidence-based OpenRouter invoice reconciliation and documented operator approvals without fabricating zero charges or silently freeing reserved amounts.
2. Real Collector capabilities/readiness publisher + authenticated adapter/receipt; do not copy its standalone implementation into Search Planner.
3. Production process/service-manager validation, alerting threshold review and real Windows/Linux start-stop tests.
4. Reconcile SP-04 supervised paid work separately; this safe daemon must never be repurposed into a paid worker without an additional explicit, idempotent, owner-approved design.

Parent SP-15 stays open until these software tasks are implemented and reviewed; SP-16 remains a separate release-test gate.
