# UA-MOD-28 — Observability & Operational CLI (v1)

**Branch:** `feature/unified-analytics-full-module`.  
**Implementation:** `intelligence/unified_analytics/ops_observability.py`, `ops_cli.py`, explicit optional `SafeEventCounters` injection into the existing UAE worker, UAE package exports.  
**New migrations:** none. **External branches/main:** unchanged. **Status:** IMPLEMENTED CORE, executable Python and real PG17 tests not run in this session.

## Security boundary

This is a **local, operator-authorized, project-scoped** view, NOT an HTTP route, public Grafana endpoint, customer UI, score API, or authorization for accessing historical `qualified` records. The status reader accepts a **trusted already project-authorized PostgreSQL dict_row connection**, performs one **fresh REPEATABLE READ** transaction with DB clock and checks `project_registry`, then executes ONLY parameterized `project_id`-scoped fixed queries. It does **not** list project IDs, media/creator IDs, source IDs, video titles, job or lease IDs, request payloads, score components, SRE decisions, raw blockers, SQL errors, secrets, raw producer reason codes or peer evidence digest values. An operator's CLI output contains aggregate counts, age of oldest waiting job, a randomly generated trace ID and fixed schema names only.

The CLI does not reuse the UI token. It requires **separately provisioned operator credentials**:

- `CM_UAE_OPS_TOKENS_SHA256` — JSON map of **authorized 32-hex-lowercase project IDs** to SHA-256 hex of distinct 32-byte operator secret tokens. Maximum 100 projects. An absent/invalid map means deny all.
- `CM_UAE_OPS_PROJECT_TOKEN` — the authorized operator's secret token **as 64 lowercase hex**, in the process environment only; never on the command line, in output or an HTTP URL. This secret must be rotated and restricted to authorized local operators.
- `CM_UAE_OPS_DSN` — explicit standalone ops PostgreSQL DSN. **There is no default fallback** to `CM_DB_DSN`, production DB or a guessed connection. SQL sessions use 5 s connect timeout, 10 s statement timeout and 3 s lock timeout.

`--project` and environment token must match exactly. Project token checks happen **before DB connection**, using `hmac.compare_digest(SHA256(bytes.fromhex(secret)), expected)`. The CLI takes a single action at a time; no background process, watcher, scheduler, automatic retry or self-restarting worker is created. The database user must have least-privilege project-scoped permissions from the operator's environment; the JSON token does not restrict arbitrary direct SQL executed outside the CLI.

## Read-only status

```bash
python -m intelligence.unified_analytics.ops_cli \
  --project <authorized_32hex_project> --inspect
```

The output is one JSON line with `cli_schema_version`, short random `trace_id` (generated within UAE) and `report`:

| Field | Grounded meaning |
|---|---|
| `queue_states` | Current PostgreSQL job rows for `pending`, `leased`, `retry`, `succeeded`, `dead`, `cancelled`. **Succeeded only means queue acknowledgement**, not a qualified Group C result. |
| `queue_lag_seconds` | DB clock minus oldest `pending` or `retry` `created_at`; `null` means none. This is a queue-age proxy, **not processing SLA latency** or peer-completion time. |
| `delayed_jobs`, `expired_leases` | Currently scheduled availability delay and leased jobs past lease deadline. |
| `retried_jobs` | Jobs with `attempts > 1`, including both active and terminal; it **does not count actual PostgreSQL serialization retries**. |
| `snapshot_diagnostics` | Counts **only persisted `partial`/`blocked` snapshots**. Stored `qualified` does not prove current visibility and is intentionally excluded. |
| `missing_ports`, `partial_ports`, `revoked_ports` | Aggregated port-state evidence counts for fixed seven ports `source_policy,d1,qdp,sre,group_a,group_b,group_c` on persisted partial/blocked snapshots only. Missing port count 0 can mean **no observed rows**, not complete coverage. No raw reason codes or IDs. |
| `source_stale_evidence` | Historical noncurrent `source_policy` evidence rows in persisted partial/blocked snapshots; **not a fresh upstream rights check**. |
| `invalidated_snapshots`, `read_revocation_events` | Count of stored UAE snapshot invalidations; useful proxy for revoked historic accessibility. **Not per-request authorization denials**. |
| `queue_transition_counts` | Aggregated immutable job transition audit events by fixed to-state, not per-worker identifiers or lease tokens. |
| `outbox_states` | Pending/leased/delivered/failed counts from UA16 revision outbox; this does not guarantee delivery correctness for unseen peers. |
| `qualified_live_readable` | Intentionally always `false` to prevent equating current database counts with effective UA19 qualified visibility. |

All reported groups are **internal privileged project-scoped operations only**. Do not expose low-cardinality/group metrics in a public multi-tenant endpoint; ensure query, source-policy and anti-differencing audits if ever released to a wider audience.

### Fixed-label in-process telemetry

`SafeEventCounters()` accepts only an explicit finite enum such as `join_failed`, `stale_source`, `group_a_missing`, `group_b_missing`, `group_c_missing`, `read_revoked`, `pg_retry`, `queue_retry`, `queue_dead`, `queue_succeeded` and lease lifecycle codes. Raw caller-provided labels, exception messages, SQL strings, URLs, user IDs, media IDs, source providers, worker owner IDs and arbitrary token fields are **never recorded**. Counters have fixed cardinality and bounded latency buckets (<10ms, <100ms, <1s, <10s, ≥10s), not per-request timing histories. No global singleton or default logger exists.

Existing `QualificationWorker(...,telemetry=SafeEventCounters())` optionally emits fixed-code results on successful, retried, dead, lost or recovered lease operations. The constructor is not a scheduler, and telemetry exceptions are suppressed so they cannot change the fenced lease/snapshot lifecycle. Without an injected sink there is no background recording. The `pg_retry` category currently means **classified storage failure/possible retry**, not a precise count of Postgres transaction retry cycles. Actual PostgreSQL errors continue to be sanitized by existing UAE worker boundary.

Current readside/API interfaces are NOT automatically instrumented, so `SafeEventCounters.read_revoked` counts only explicit calls from trusted internal callers; durable per-request visibility-denial telemetry will need a separate privacy-reviewed hook before it can be called a complete signal. No media, secret, personal data or raw error details should be persisted to dashboards or external observability agents.

## Manual recovery: only expired leases

Inspect first, investigate source/QDP readiness and existing queue/event histories in the authorized environment, then run ONLY if the operator explicitly authorizes this project and confirms recovery:

```bash
python -m intelligence.unified_analytics.ops_cli \
  --project <authorized_32hex_project> \
  --recover-expired --limit 25 --apply \
  --confirm-project <the_same_authorized_32hex_project>
```

This command reuses the existing `QualificationJobQueue.recover_expired(project_id,limit)` with PostgreSQL `FOR UPDATE SKIP LOCKED`, current queue lease expiration and max-attempt constraints. It can transition expired leased jobs to `retry` or `dead`; it does **NOT** directly create source approvals, rerun QDP/SRE/A/B/C, delete media, create snapshots, promote status, claim a new lease, start `QualificationWorker`, call an external model or publish scores. The returned JSON contains **counts only** (`recovered_jobs`, `states.retry`, `states.dead`), plus `qualified:false` and `producer_run_started:false`. Never print recovered job IDs in shared operator logs.

For an empty or already-recovered set, a repeated recovery returns zero new transitions (DB-guarded idempotence). Concurrent workers use PostgreSQL row locks/SKIP LOCKED to avoid double recovery; under operational connection failure the CLI returns only a stable safe error code and is **not** a guarantee of recovery. Default `--limit` is 25 and maximum 100. No broader purge/reset/dead-letter resurrection is authorized. The existing worker `--recover-only` path remains available for trusted server-managed environments, but this dedicated CLI adds separate operator authorization.

### Incident triage and shutdown

1. Missing/incorrect operator token => `ops_project_access_denied`, **no DB call**. Missing DSN => `ops_database_configuration_missing`; check environment without echoing values.
2. Rising `queue_lag_seconds`/oldest pending and `delayed_jobs` => check lease availability and source/QDP readiness; distinguish scheduled future backoff from stuck leases.
3. Rising `expired_leases` => inspect worker health/safely stop a compromised worker; run operator-confirmed bounded recovery. Never force-complete an unverified job.
4. A/B/C missing/partial or source stale strata => inspect exact trusted upstream producer records through existing separately authorized internal tools; no operator CLI override can make these ports current.
5. Increasing `invalidated_snapshots` or outbox pending => investigate source lifecycle/revocations. Invalidations are durable and should not be undone to make a score available.
6. A `pg_retry` event or CLI 2 exit => inspect PostgreSQL within secured infrastructure; do not automatically replay non-idempotent upstream work without a new current pin and explicit authorization.
7. Graceful shutdown: CLI is single-shot and exits on success/failure; explicit Ctrl+C returns exit 130 with only a fixed no-traceback message; no daemon to stop. Worker already supports stop-event and SIGINT/SIGTERM; instrumentation is in-memory only and does not block exit. Interrupting recovery is safe at PostgreSQL transaction boundaries; monitor the queue's durable states before retrying.

## QA and release status

**Authored test inventory:**

- `tests/test_unified_analytics_ops.py` — **37** synthetic unit/mock cases for SQL projection, golden queue lag (720 s), A/B/C missing/partial/revoked, stale source, invalidation/outbox, source scope, duplicate/unknown groups, fixed label/latency counters, trace ID, operator token, explicit project recovery, duplicate no-op replay, no DSN/token/media leakage.
- `tests/test_unified_analytics_ops_worker.py` — **6** synthetic worker opt-in instrumentation cases (no constructor event, success, join failure, PG storage failure, lost lease and recovery, no raw details).
- `tests/test_unified_analytics_ops_static.py` — **17** SQL/implementation guards, no public ops HTTP, exact project scoping, bounded timeout, no hidden ID logging, no new migration required.
- `tests/test_unified_analytics_ops_pg17.py` — **3 conditional** disposable PG17 tests for real persisted partial evidence, 2 parallel read-only inspections, empty recovery without new snapshots/qualified records. No producer-positive qualified scenario is fabricated.
- Existing UA-MOD-17/18 and earlier UAE tests must be regressed, especially queue lease fencing and source-invalidation behavior.

Local developer commands:

```bash
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_ops*.py" -v
python -m unittest discover -s tests -p "test_unified_analytics_worker.py" -v
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# Optional isolated disposable PostgreSQL17 only:
# export CM_TEST_PG_DSN=<an isolated DB which is NOT CM_DB_DSN>
```

**This remote session:** source-based static audit and commits only; actual Python/unit/full PG17/concurrent test execution and real operational CLI/DSN interaction are **NOT RUN**. GitHub Actions queued does not mean passed. No source provider/LLM/paid model actions performed. UA13/UA84/UA85 still refuse real effective `qualified` snapshots; producer/RTB/PIE integrations remain independently blocked. **No release claim** is appropriate until the developer executes the regression suite and attached trusted producer integrations pass.

**Next stage:** UA-MOD-29 — source lifecycle and retention purge: distinguish immutable historical records vs current readability, retention/erasure cascade semantics, revoke E2E and proven non-leakage.
