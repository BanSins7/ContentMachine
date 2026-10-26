# VC-MOD-14 — Standalone Video Collector operator deployment

**Feature branch:** `feature/video-collector-v1` · **PR:** #33 (DRAFT). **Code status:** IMPLEMENTED · **CI status:** pending exact-head Linux/Windows/real PG17 QA. This document does not claim SRE M35 receiver integration or live DTK test success.

## Packaging boundary

The Collector lives in a **single, importable Python package** `intelligence.collector`, using the existing PostgreSQL/DTK/RTB/QV infrastructure through scoped adapter/port modules. There are **no manual file copies**, alternative DTK SDK, second D1 videos table, second SRE queue or hard dependency on the unmerged SRE/QDP branches.

Install/merge the **entire branch in one ordinary PR** after completion of VC-MOD-15/16. Its changed/added files include `intelligence/collector/*`, versioned forward-only SQL `054…057`, the existing main-provided DB/transport interfaces, `app.py` same-origin handlers, `site/collector.js` + Discovery panel, tests and documentation. A standalone *module* here means independently disabled/configurable/mergeable **inside the Content Machine repository**, **not** a separate independently installable wheel outside the repository. Do not claim this as a standalone external product.

**Entrypoints:**

| Purpose | Command | Provider/network risk |
| --- | --- | --- |
| Offline module self-check | `python -m intelligence.collector doctor` | No DB, DTK, SRE or provider calls |
| Opt-in local DB/migration check | `python -m intelligence.collector doctor --db-read` | PostgreSQL SELECT only; no auto migrate |
| QV operator publisher help | `python -m intelligence.collector publisher-help` | Offline |
| Existing QV publisher | `python -m intelligence.collector.publisher_cli --project-id <PROJECT> --outbox-id <QV_UUID>` | Read-only preview by default |
| Explicit QV publish + ACK | Add `--publish` to the publisher command | Writes PostgreSQL only; never provider POST |
| Existing bounded Worker | `python -m intelligence.collector worker --once` | **May call live provider only when operator separately released a run and every live gate is enabled** |
| Cancelled-run recovery dry-run | `python -m intelligence.collector cancel-reconcile --project-id <PROJECT>` | Read-only |
| Apply cancel recovery | Add `--apply --confirm-project <PROJECT>` | Internal PostgreSQL task/receipt status writes; NO provider calls |
| Expired/legacy metadata inventory | `python -m intelligence.collector retention-audit --project-id <PROJECT>` | Read-only, no metadata title exposure |
| Metadata scrub preview | `python -m intelligence.collector retention-scrub --project-id <PROJECT>` | Read-only |
| Confirmed expired metadata scrub | Add `--apply --confirm-project <PROJECT>` | PostgreSQL mutable-source-metadata scrub only; NOT global video deletion |
| SRE expired-lease preview | `python -m intelligence.collector sre-recover --project-id <PROJECT>` | Read-only |
| Confirmed SRE expired-lease recovery | Add `--apply --confirm-project <PROJECT>` | SRE **outbox** leased→pending/blocked in PostgreSQL, **NOT** inbox delivery |
| DTK adapter self-test | Existing local DTK UI or `python -m intelligence.collector.worker --once` | Verify flags before any operator release; NEVER substitute this for cost/rights approval |

**Note:** all `--project-id` values are 32 lowercase hex, not project names. The CLI intentionally exposes **no public HTTP mutation endpoints**, and every state-changing recovery/scrub command requires a second, exact project ID confirmation and is capped at 1..100 candidate rows. Default recover and scrub are dry-run/read-only.

## Windows PowerShell

From the cloned Content Machine repository root (one checked-out branch):

```powershell
git checkout feature/video-collector-v1
python -m intelligence.collector doctor
# Existing local Postgres17 must be healthy; use deployment secret store for DSN.
python -m intelligence.collector doctor --db-read
python -m intelligence.collector publisher-help
python -m intelligence.collector retention-audit --project-id <PROJECT>
python -m unittest discover -s tests -p "test_video_collector_operator_cli_v1.py" -v
```

The checked-in `examples/collector.env.example` contains only **zero/off** values. Copy settings into the EXISTING repo-root `.env` or secure environment **without committing real tokens, DSNs or passwords**. The existing `SETUP_POSTGRES_WINDOWS.bat` / `docker-compose.yml` owns the PostgreSQL 17 deployment. Never embed secrets in PowerShell command output or a tracked example file.

## Linux shell

```sh
git checkout feature/video-collector-v1
python3 -m intelligence.collector doctor
python3 -m intelligence.collector doctor --db-read
python3 -m intelligence.collector publisher-help
python3 -m intelligence.collector cancel-reconcile --project-id <PROJECT>
python3 -m unittest discover -s tests -p 'test_video_collector_operator_cli_v1.py' -v
```

Both OS use the same Python CLI, no shell-specific import path. Only `worker --once` / `worker --interval 4` is the provider-capable entrypoint and inherits the **existing** strict DTK/no-charge/lease/source-policy/quota gates. Run Worker only in a trusted private server environment, **not from public app traffic, a frontend action, or a generic jobs worker**.

## Strict staged rollout

1. **Offline preflight:** read `docs/VIDEO_COLLECTOR_MASTER_IMPLEMENTATION_PLAN.md`. Keep `CM_COLLECTOR_LIVE_ENABLED=0`, `CM_COLLECTOR_QV_CONSUMER_ENABLED=0`, `CM_COLLECTOR_SRE_DELIVERY_ENABLED=0`, `CM_DTK_ARCHIVE_DISABLED_CONFIRMED=0`. Run `doctor` and the offline module tests. No provider request.
2. **DB upgrade:** back up the database, inspect the existing `schema_migrations` ledger, run `python scripts/collector_schema_audit.py` as described in `docs/VIDEO_COLLECTOR_SCHEMA_V1.md`, then use the existing `python -m intelligence.migrate` only when intentionally approved. Migrations **054→055→056→057** are forward-only/additive, checksum-verified. If an old Collector branch applied **016/017** in conflict with main's historical SQL, **stop** and manually reconcile from approved backup: NEVER auto-rename migration ledger rows.
3. **Read-only enablement:** set `CM_DB_ENABLED=1` and configure DB DSN via the existing secret store; check `doctor --db-read`. Browse the same-origin owner Discovery panel; only persisted project QV manifests and D1 video results are trusted.
4. **Publish a specific QV event:** inspect a trusted QV outbox ID read-only, then explicitly `--publish` or use the existing owner Preview→Held Commit→Explicit Release UI. A published QuerySet is not an instruction to run DTK and no_queries must result in no tasks.
5. **Live Worker is a separate opt-in:** only after a reviewed source policy, zero-charge commercial confirmation, active quota window, local read scope, upstream archive disabled, and explicit release grant, enable `CM_COLLECTOR_LIVE_ENABLED=1` and `CM_DTK_ARCHIVE_DISABLED_CONFIRMED=1`. A mismatch means stop. Run `worker --once` first. **No real provider test was performed as part of this stage.**
6. **SRE handoff stays OFF** until an independently authenticated/durable SRE M35 Collector inbox is implemented and tested. `CM_COLLECTOR_SRE_DELIVERY_ENABLED=1` **does not install that adapter**. Existing outbox events remain `pending`; do not report `SRE match` or qualified data.
7. **Review results:** UI `QV → DTK → D1 → SRE` trace shows actual committed video/checkpoint and per-event delivery. Failed or unknown remote POST outcomes require manual reconciliation: never auto resubmit based on a missing task ID.

## Disable, rollback and failed partial deployments

**Fast safe off-switch**: set `CM_COLLECTOR_LIVE_ENABLED=0`, `CM_COLLECTOR_QV_CONSUMER_ENABLED=0`, `CM_COLLECTOR_SRE_DELIVERY_ENABLED=0`, restart dedicated Worker only. This stops new local provider execution, QV consumer and SRE delivery; it does **not** cancel already submitted remote provider jobs, retract historical data or refund spent provider requests. Use explicit run cancellation and bounded `cancel-reconcile` for outstanding submitted tasks. An in-flight HTTP request could finish after flags change; treat uncertain operations as `unknown`, not as safe-to-resubmit.

**Database rollback**: do **NOT** DROP applied `054…057` tables or rewrite schema migration checksum/history after a release. Roll back the application code/UI to a previous known-good commit while preserving stored data and migration ledger; deploy a reviewed *forward-only* remediation migration if necessary. On old PR #13/#20 histories with mismatched 016/017 checksums, stop, take a database backup and manually reconcile.

**Recovery and retention**: dry-run by default; apply requires matching project ID. Never delete immutable D1, QV manifest or outbox audit rows. Metadata scrub is NOT comprehensive right-to-be-forgotten (global `videos.title` or other derived content may remain). No automatic SRE inbox ACK can be forged by a CLI.

**Operational logs**: built-in CLI prints only machine-safe error codes on failures; `doctor` prints booleans and SHA checksums, never CM_DB_DSN, password, API key, DB URL, provider raw response or full exception text. Configure server logs with the same secret-redaction standard. Diagnostic outputs are informational; even when flags are ON, the CLI reports `live_execution_authorized=false` because active source policy/quota/DTK rights must be checked in the actual Worker transaction.

## QA status

`tests/test_video_collector_operator_cli_v1.py` adds **16 offline tests** for no-DB default, no-secret diagnostics, migration ledger checksum reads and mismatches, exact project confirmation, dry-run, explicit scrub/recovery, delegated Worker, QV help and absence of public mutating routes or provider calls. Dedicated Linux and Windows CI steps are added. **Exact current commit CI and disposable seeded PostgreSQL17/concurrency tests remain PENDING**, not PASS; those belong to VC-MOD-15.

**Next:** VC-MOD-15 integration/fault-injection/real PostgreSQL17, followed by VC-MOD-16 final PR review, deployment runbook approval and merge-ready audit.
