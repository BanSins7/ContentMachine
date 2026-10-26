# VC-MOD-16 — final module packaging audit, merge gate and NO-GO decision

**Branch:** `feature/video-collector-v1`; **PR:** [#33](https://github.com/BanSins7/ContentMachine/pull/33) (**DRAFT**, intentionally not merged).

**Structural source audit completed on 26 Oct 2026.** At audit time: `main = 218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`; VC head before stage-16 fixes = `0043d36799f9f60cece9157dcbcdff7c52361ed5`; VC branch **54 ahead, zero behind**, GitHub `mergeable=true`. Both the target and the head are time-dependent and must be rechecked on the actual final merge candidate SHA.

**Final approval status: NO-GO / QA BLOCKED.** The static audit tool and human reviewer report are implemented, but latest-head Linux/Windows/PostgreSQL17 CI completion and full upstream C9→SP→real SQG/QV→Collector delivery remain unverified. SRE M35 authenticated receiver is also unavailable. Do **not** mark PR Ready for review/merge, and do **not** claim production delivery, SRE relevance or completed T01–T100.

## Source/merge audit — dedicated fail-closed tool

`scripts/collector_release_audit.py` is a read-only local Git/source preflight. It only invokes `git rev-parse`, `git merge-base`, `git diff --name-status`, `git rev-list` and reads on-disk tracked files; it never fetches or changes refs and never accesses PostgreSQL, DTK or SRE.

```sh
python -m scripts.collector_release_audit --base origin/main --strict-source
```

Checks:

- `origin/main` actually exists in the checked-out checkout, feature branch does not lag main (`behind_main_commits == 0`), diff is readable and reviewed.
- Only Collector package, DTK adapter, exact app/Discovery integration, 054–057 migrations, collector scripts/tests/docs, and explicitly approved frontend/workflows are allowed in PR. **Any unrelated SRE, QDP, KE, Group A/B/C, PIE, UAE or arbitrary schema changes block static source PASS.**
- No file delete or rename; all mandatory implementation/source/testing/runbook files present.
- New SQL migrations are **only** `054_collector_planning.sql`, `055_collector_worker.sql`, `056_collector_qv_manifest.sql`, `057_collector_discovery_outbox.sql`; old collision-prone Collector 016/017 filenames must not exist. Printed local SHA-256 values are for review; comparing against already applied ledger still requires `python -m intelligence.collector doctor --db-read` and `scripts/collector_schema_audit.py` before an authorized migration.
- All five relevant feature flags in `examples/collector.env.example` are exactly OFF, no credential/DSN/token env assignment in the committed example.
- Output `static_source_gate_passed=true` only for structure; independent flags for `exact_head_linux_pg17_ci_passed`, `exact_head_windows_ci_passed`, `full_upstream_c9_sp_qv_db_e2e_verified`, `authenticated_sre_m35_inbox_verified`, `provider_commercial_rights_verified` remain **false by design** until verified externally.
- **`merge_ready=false` always**: this script cannot authenticate Actions, real commercial terms, current SRE M35, external operator consent or production data backups. Review evidence, not a synthetic green flag.

Added `tests/test_video_collector_release_audit_v1.py` with 8 focused offline tests on approved/unauthorized paths, non-Collector migrations, deletion, missing inputs, flags, secrets, missing main ref and stale branch. CI runs `--strict-source` and the focused test.

### Removal of an unrelated Knowledge B workflow change

The existing VC PR included an incidental five-line concurrency change in `.github/workflows/ke-stage14-fast.yml` dating from its stage-00 baseline. This was **reset to exactly the current main version** in VC-MOD-16. Knowledge B is an independent subsystem and the VC PR must not carry its workflow customization. This reset is isolated to the VC branch; main and Knowledge B branches are untouched.

## Reviewed rollout/rollback sequence

1. **Checkout and review:** compare `main...feature/video-collector-v1`; run `scripts.collector_release_audit --strict-source`; keep PR draft on any failure or behind-main drift; fix only VC-owned files. Do not cherry-pick an old original VC PR as a separate module.
2. **QA prerequisites:** exact final head's Linux checks, PostgreSQL17 schema old→new upgrade, full tests and strict `scripts.collector_release_gate --strict-ci` must finish SUCCESS; Windows D1 offline smoke and all app/site guards must finish SUCCESS. Do not count queued/running/cancelled as PASS. Capture run URLs and SHA.
3. **Upstream end-to-end:** run at least one verified non-network C9/RTB→SP→SQG→semantic QV→real published QV outbox→all selected Collector shards→held plan→explicit release→mock DTK→Inspector→canonical D1→SRE-ready path in disposable PostgreSQL17, including 0/1/20/21/480 selectors, multiple projects/hypotheses and retry/crash boundaries. Existing downstream parent-contract PG fixture **does not cover** this full chain.
4. **Permissions/commercial prerequisites:** operator reviews DTK commercial contract, actual no-charge eligibility, local data rights, source-policy versions, no upstream archive and available quota. Do not equate `0.00` app budget with independently confirmed provider billing or terms.
5. **Migration preflight:** take reviewed database backup, inspect schema_migrations 016/017 historical collisions, apply forward-only 054–057 only through central migrator. Verify twice: first creates new tables, second is a no-op; check expected hashes and previous project data remain unchanged.
6. **Staged app deployment:** `CM_DB_ENABLED=1` only after DB is ready; keep collector QV consumer, live Worker and SRE delivery **OFF**. Owner project read-only UI first; explicitly publish QV, review held run, and only then authorize release. No automatic provider calls.
7. **SRE dependency:** deployment of Producer (Collector) may safely queue immutable metadata-only `pending` SRE events. Do not enable real SRE delivery until **separate** SRE M35 authenticated durable inbox with deduped event IDs, proper receiver-side rights/provenance checks and real ACK is independently implemented and verified. `delivered` != `relevant`; QDP must not treat Collector output as SRE evaluation.
8. **Roll-forward / emergency disable:** switch off `CM_COLLECTOR_LIVE_ENABLED`, `CM_COLLECTOR_QV_CONSUMER_ENABLED`, `CM_COLLECTOR_SRE_DELIVERY_ENABLED`; stop dedicated Worker. In-flight provider tasks remain potentially external/unknown. Use operator dry-run/reconcile explicitly, never blind POST/refund. Roll back **code** if necessary but retain already applied migration ledger and immutable D1/QV/SRE rows; use additive forward migration to correct schema, never DROP history.
9. **Release decision:** only one VC feature PR to main, after exact final source gate/CI/PG17/upstream evidence and reviewed dependencies. If any required proof is missing, **NO-GO**. No automatic branch merge or PR draft removal performed.

## What is currently done vs blocked

| Scope | Current evidence | Decision |
| --- | --- | --- |
| Independent VC code + no main modifications | One `feature/video-collector-v1`, PR #33; compare zero behind at audit time | **Implementation present** |
| Colliding old migration numbers | 054–057 new additive SQL; 016/017 old names absent; static source audit added | **Code packaged; applied DB ledger must be checked** |
| D1/DTK/SRE ready separation | Dedicated Worker, canonical D1 checkpoint, exactly one Collector SRE outbox; SRE M35 not installed | **Producer implemented; SRE external integration BLOCKED** |
| Static Git/source preflight | Audit script + eight offline tests, unrelated KE workflow change removed | **Code committed; CI pending** |
| Exact-head Linux/Windows/PostgreSQL17 | Workflow queued; previous runs frequently cancelled by new commits | **BLOCKED: no PASS evidence** |
| Full upstream C9→SP/QV real E2E in disposable PG17 | Downstream PG fixture and offline QV harness only | **BLOCKED** |
| Provider cost/rights + opt-in live smoke | Not performed; operator-controlled | **BLOCKED, do not auto-trigger** |
| Merge PR #33 into main | Draft; not authorized | **NO-GO** |

**Policy for next chat:** Inspect exact GitHub Actions statuses and job logs first. Fix failed tests and update this document with actual successful SHA/run IDs if and only if they are genuinely successful. Do not change PR to Ready or merge while any release blockers remain. Maintain `docs/VIDEO_COLLECTOR_MASTER_IMPLEMENTATION_PLAN.md`: strike through implemented source tasks, leave independent PG/Windows/upstream/live dependencies unchecked.

## Additional static CI diagnosis — 26 October 2026

Identified a definite offline assertion mismatch: positive `test_video_collector_status_v1.py` fixture reported 3 real D1 videos vs 1 SRE event, conflicting with `repository.get_run` integrity gate. The fixture and a negative SRE shortfall assertion are fixed. The PG17 integration fixture now pins both Inspector and candidate fetch timestamps to the same UTC observation. These are **source changes, not executed CI proof**. Global GitHub Actions backlog across VC, Group B/C, SRE, SP and RTB is observable; the VC exact-head Linux/Windows/KE jobs remain queued and have no failed-job logs to repair yet. The merge outcome is still **NO-GO**.

### Final source-audit allowlist tightening

JS integration filenames `site/collector.js` and `site/dtk-tool.js` are now allowlisted by exact match, not prefix. The regression test rejects suffix paths like `site/collector.js.evil` or `site/dtk-tool.js.backup`. Static auditor: **nine** focused offline tests after this change. This is source review hardening, not a CI PASS.
