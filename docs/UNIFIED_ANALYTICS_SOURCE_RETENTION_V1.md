# UA-MOD-29 — Source lifecycle & retention purge (v1)

**Branch:** `feature/unified-analytics-full-module`. **Status:** IMPLEMENTED CORE; actual Python/PG17 tests NOT RUN. No migration, public route, release or peer branch modifications.

## Security model and lifecycle

**Current readability is independent of historic storage.** Existing UAE11/UA19 source-policy/metadata/metric/media/peer proof must be freshly validated on every qualified read. Older `qualified` status, ranked manifest, signed PIE receipt or project ID is NOT a permission to read. Disabled sources, invalid policies, expired observations, missing membership and newer Group A/B/C/SRE/QDP revisions remain fail-closed.

**Soft invalidate** — `SourceLifecycle.invalidate_unreadable(project,source,limit=50)` reads source rights and checked effective retention in PostgreSQL time, in a bounded source/project scope. It verifies the source's explicit permissions, one active reviewed policy, retention days (valid derived 1–365, min source/policy), and exact metric observation/expiry. It selects only currently invalidation-free snapshots, using `FOR UPDATE OF sn SKIP LOCKED`. In one transaction it inserts append-only `uae_snapshot_invalidations` and matching `uae_revision_outbox` events. It never rewrites an immutable snapshot or fabricates a new `qualified` result. A second run should add no duplicate rows. Live UA19 denies access even before this maintenance operation. Per-asset media TTL and A/B/C/SRE/QDP revisions continue to require UA19/UA16 checks; this operator path is not a universal rights evaluator.

**Hard purge** — `SourceLifecycle.preview(project,source)` locks the project and returns source-scoped record counts, potentially affected project-wide counts, a clear collateral flag, and a deterministic SHA-256 plan digest. The digest includes canonical project/source scope, counts and SHA-256 digests of ordered *exact row identities + evidence fingerprints* across seven UAE tables. It detects same-size row replacement. It is a stale-plan check, **not an authorization signature**. The purge re-locks the project, recomputes the digest, requires exact project/source confirmation, refuses stale plan, and verifies **all relevant counts are zero before committing**. A fresh preview after success allows a no-op repeated purge.

### Exact deletion boundaries

| Scope | State erased |
|---|---|
| Same project and source | `uae_qualification_requests` and `uae_revision_inbox`; linked revision quarantine via FK cascade |
| Existing request/snapshot ON DELETE CASCADE | `uae_unified_snapshots`, `uae_snapshot_payloads`, `uae_snapshot_evidence`, `uae_snapshot_invalidations`, `uae_revision_outbox`, `uae_qualification_jobs` and immutable `uae_qualification_job_events` |
| **Broad project-only cleanup requiring extra consent** | All same-project `uae_qdp_handoff_inbox`, `uae_qdp_handoff_quarantine`, `uae_audit_events`, `uae_project_aggregate_runs`. These are not reliably source-attributable, so other sources in the project may need re-ingestion and recomputation. |
| **NOT erased or modified** | `sources`, `source_policy_versions`, `source_video_metadata`, `metric_snapshots`, `media_assets`, `videos`, SRE project membership/workspaces, Group A/B/C/QDP/SRE producer records, RTB, PIE and any independently cached downstream results. |

Project-wide collateral requires `accept_project_wide_collateral=True` **in addition** to a matching plan SHA and exact source/project confirmations. The CLI exposes this as an extra `--accept-project-wide-collateral` flag. Never add it blindly: inspect preview's `project_wide_collateral` first. When a source has no remaining source-scoped UAE rows, source purge intentionally no-ops, even if unrelated or orphaned project-level evidence exists. **A separately authorized project-wide erasure process will be needed for orphaned, un-attributable records; this stage does not implement one.**

Because UAE does not own original media, metrics, producer research, SRE, RTB or PIE storage, **this is an UAE-only purge, not complete product-wide erasure**. Other owners must add their own erasure/revocation integrations and prove downstream non-leakage. No other branches changed here.

### Trusted operator CLI

Use the **separate** UA28 operator credential environment: `CM_UAE_OPS_TOKENS_SHA256` (authorized project -> SHA256 of 32-byte secret), `CM_UAE_OPS_PROJECT_TOKEN` (raw 32-byte token as 64 lowercase hex, environment only) and explicit `CM_UAE_OPS_DSN`. Do not reuse Studio's token or put secrets/DSN on argv. These variables are mandatory; the operator token is checked before DB opening. CLI does not start workers, poll, run models or publish.

```bash
# 1. Read-only planning
python -m intelligence.unified_analytics.retention_cli \
  --project <32hex_project> --source <exact_source_id> --preview

# 2. Append source revoked/expired invalidations (up to 50 each run)
python -m intelligence.unified_analytics.retention_cli \
  --project <32hex_project> --source <exact_source_id> \
  --invalidate --limit 50 --apply \
  --confirm-project <32hex_project> --confirm-source <exact_source_id>

# 3. Irreversible UAE-only purge. Copy SHA from a FRESH preview.
# Include last flag ONLY after accepting unrelated project data cleanup.
python -m intelligence.unified_analytics.retention_cli \
  --project <32hex_project> --source <exact_source_id> \
  --purge --apply --plan-sha256 <fresh_64hex_plan> \
  --confirm-project <32hex_project> --confirm-source <exact_source_id> \
  --accept-project-wide-collateral
```

The CLI prints one safe JSON record with random trace ID, counts and `external_source_data_deleted:false` after purge. No source ID, project ID, job ID, media name, input SHA or token is echoed. Error codes are sanitized; Ctrl+C returns 130 without traceback. A matching digest alone is NOT authentication. Python API users must inject an independently project-authorized DB session and apply their own operator ACL.

### Concurrency, lifetime and coverage limits

A project-row `FOR UPDATE` scope lock serializes concurrent source retention requests and conflicts with referencing FK changes. A fresh plan digest must match inside the deleting transaction. UA086's deferred immutable-evidence guard allows deleting the entire snapshot and its payload as an FK cascade, but refuses deleting just one evidence member while retaining the payload. If any deletion leaves relevant records, `lifecycle_purge_incomplete` causes transaction rollback. No public SQL GRANT, new migration or lifetime-based automatic purge is introduced.

Soft invalidations use current DB clock and retention expressions; UA19 remains responsible for live per-asset permissions. The source-scoped purge does not delete a source's original upstream metrics or media. A later source deletion by a peer can cascade requests away *before* project-only QDP/audit facts are source-attributable: the source-specific workflow then intentionally cannot erase those ambiguous orphans; use a separately reviewed global/project erasure procedure rather than infer ownership. The preexisting source/API/PIE/RTB rights boundaries remain strict.

## Test inventory — authored, not executed

- `tests/test_unified_analytics_source_lifecycle.py` — **39** synthetic tests: scope rights, read-only plan, exact identity SHA/collision-by-count prevention, safe revoke/outbox idempotency and rollback, plan-stale rejection, incomplete-deletion rollback, explicit unrelated-data consent, and CLI auth.
- `tests/test_unified_analytics_source_lifecycle_static.py` — **20** SQL/source guards for rights, retention, immutable evidence, no peer-table writes and CLI authorization.
- `tests/test_unified_analytics_source_lifecycle_pg17.py` — **5** conditional disposable PostgreSQL17 tests: revoke after a source permission change, no duplicate outbox, concurrent operators, full UAE source cascade and stale-plan rejection. Synthetic stored partial/blocked snapshots ONLY; no fake qualified evidence.

Developer-machine commands:

```bash
python -m compileall -q intelligence/unified_analytics
python -m unittest discover -s tests -p 'test_unified_analytics_source_lifecycle*.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
# Disposable PostgreSQL17 only, CM_TEST_PG_DSN must differ from CM_DB_DSN
```

**No Python/unittest/disposable PG17/real operator CLI execution was performed here.** Re-fetch/static checks are not runtime QA. UA13/UA84/UA85 continue to disallow effective positive qualified producer data; real end-to-end read revocation on positive qualified A/B/C remains blocked and should not be faked. The standalone UAE branch and Draft PR38 remain unmerged.

**Next:** UA-MOD-30 — full fixture E2E QDP → A/B/C → UAE without fictional peer approvals.
