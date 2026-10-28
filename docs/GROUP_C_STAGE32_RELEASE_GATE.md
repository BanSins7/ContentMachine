# GC-MOD-32 — Final release gate / Group C integration handoff

**Canonical branch:** \`feature/group-c-full-module\`. **Existing draft PR:** [#37](https://github.com/BanSins7/ContentMachine/pull/37). **Base at audit:** \`main\` @ \`218f5f3163ccc7179a4809ba4386a1d1b6ff80f5\`. **Stage:** release-preflight implementation CODE IMPLEMENTED; **MERGE STATUS: BLOCKED_EXTERNAL_AND_QA**. This is **not a successful release gate**, and this document **does not authorize merging**. Do NOT move PR #37 out of Draft solely because GC-MOD-00..32 code files exist.

Source audit conducted 2026-10-28 using GitHub PR metadata and file inventories. Draft PR #37 at initial audit showed 116 changed files, base unchanged, mergeable=true but mergeable_state=unstable, 362 historical commits. A completed GitHub workflow \`.github/workflows/group-c-module.yml\` for preceding GC31 SHA \`493c495201b287755c0093c7fc73df012523f938\` had conclusion **failure** (run [40698584236](https://github.com/BanSins7/ContentMachine/actions/runs/40698584236)); its jobs API exposed **zero jobs**, so no failed step can be responsibly asserted. Other actions may be queued and are NOT green. Any newly written GC32 commit needs **new** SHA-specific QA. A Draft PR being mergeable at GitHub's merge engine is not sufficient for safe deployment.

## New GC32 artifacts

- \`docs/GROUP_C_STAGE32_PEER_AUDIT.json\`: exact immutable *snapshot* of six Draft PRs/branch heads, their independently owned SQL migration names and blocking integration responsibilities. This is NOT an automatic live GitHub fetch or proof that peer code has not changed since inspection.
- \`intelligence/group_c/release_gate.py\`: offline, cross-platform, **read-only** strict audit. Uses local clean Git checkout and explicit \`main...HEAD\` diff, allows only Group C paths, compares immutable 003/004 migration blob SHA to baseline **byte-for-byte**, requires all 9 exact Group C migration filenames 056..064 and tracked runbooks, calculates peer exact filename collisions and overlapping numeric prefixes, emits a redacted JSON report pinned to real 40-hex Git SHA. Additional GC31 synthetic E2E report can be attached only to document test result; it can NEVER attest real peer IAM/Analytics. Default exit code is **2=blocked**, **3=audit error**, **0=only if actually merge-ready**; current known blockers will always yield 2. \`--audit-only\` exit 0 means **only that report was generated**, not that release passed. Script DOES NOT merge, checkout, fetch, change feature flags, apply migrations, modify PR or query external services.
- \`tests/test_group_c_release_gate.py\`: pure adversarial baseline-/scope-/manifest-/blocked-readiness tests and explicit-opt-in disposable PG17 checksums of original 003/004 + nine Group C migrations / repeated checksum-safe migration application. No production database.
- \`.github/workflows/group-c-module.yml\`: separate pure GC32 preflight-contract checks on Linux/Windows and optional disposable PostgreSQL17 fresh/upgrade migration verification steps. Presence of a step is not evidence it executed.

## Concrete migration collision & ordering audit

\`intelligence/migrate.py\` applies every \`intelligence/migrations/[0-9]*.sql\` in **lexicographic filename** order inside a single transaction under global advisory lock \`82931674013\`, storing version = **entire filename** and SHA-256 in \`schema_migrations\`.

Actual observed peer PR inventory:

| Owner, PR | Migrations | Consequence |
|---|---|---|
| Group C, [#37](https://github.com/BanSins7/ContentMachine/pull/37) | 056–064, 9 files | Group C additive schema/guards, owns its original C7 and all Group C new SQL |
| Group A, [#35](https://github.com/BanSins7/ContentMachine/pull/35) | 054–059, 6 files | numeric prefixes 056–059 overlap C |
| Group B, [#36](https://github.com/BanSins7/ContentMachine/pull/36) | 055–059, 5 files | numeric prefixes 056–059 overlap C |
| SRE, [#22](https://github.com/BanSins7/ContentMachine/pull/22) | 016, 017, 054–069, 18 files | prefixes 056–064 overlap C; SRE rights and source revocation are upstream dependencies |
| Search Planner, [#34](https://github.com/BanSins7/ContentMachine/pull/34) | 054–058, 5 files | prefixes 056–058 overlap C |
| Unified Analytics, [#38](https://github.com/BanSins7/ContentMachine/pull/38) | 080–090, 11 files | non-overlapping with GC, but its reader currently cannot qualify signed C7 |
| Legacy Analytics, [#23](https://github.com/BanSins7/ContentMachine/pull/23) | 016–018, 3 files | separate older Analytics model; must not blindly combine with UAE 080–090 |

**Important distinction:** as of this pinned inventory, there is **no identical complete filename** between Group C 056..064 and these peer PRs; there are however identical **numeric prefixes 056–064** with other owners. The current migration runner keys by **complete filename**, not number, so calling these exact-file SQL conflicts would be incorrect. Nevertheless, global lexicographic ordering interleaves owner migrations, and database dependencies, table locks, PK/FK references, triggers and index timing may be incompatible. An actual **combined branch/temporary integration checkout** with every selected PR merged must be tested against disposable PG17 *fresh* and *upgraded main* after peer heads are refreshed. No migration numbers or prior SQL are silently renamed by Group C. SRE/DB owner must approve: cross-module dependency topological order, applied checksum unchanged, transaction lock budget, GC064 non-CONCURRENTLY CREATE INDEX lock/window, downgrade no schema drop, rollback and data retention.

If combined fresh/upgrade PostgreSQL17 fails, report the exact failed migration/SQLSTATE in integration review and let the owning branch fix it. Do not edit already applied 003/004, silently drop legacy tables, skip failed migrations or "mark PASS" for not-run tests.

## Critical external peer handshakes (OWNERS other than Group C)

**Unified Analytics must change.** In Draft PR [#38](https://github.com/BanSins7/ContentMachine/pull/38), \`intelligence/unified_analytics/group_c_adapter.py\`, method \`GroupCPersistedScoreReader.read_persisted_metric()\`, returns \`ReadState.MISSING(reason_code="c_effective_approval_unavailable")\` even for structurally valid persisted original C7 because it assumes only \`057 frozen_unreviewed\` exists. Group C has already added \`058\` externally signed human decision, \`059\` global config activation, \`060\` certified score receipt and \`062\` live source/retention rights. **Required UAE change (NOT made here):** verify those original current approval/activation/receipt/source-cohort provenance under one authorized snapshot before emitting a qualified Group C \`EvidenceRead\`. Never approve \`frozen_unreviewed\` by itself, recalculate C7 or clip Breakout Score to 100. Add real cross-branch PG17 fixtures from a merged integration checkout. PR #38 is currently Draft.

**QDP producer owner must verify** an actual \`qdp_outbox\`, \`qdp_group_current_preparations\`, \`qdp-preparation/v2\`, exact \`qdp-event/v1\` and \`qdp-handoff/v1\` project/video/snapshot/source/policy/config SHA identity, plus durable ACK after commit, replay, revocation and retargeted preparation. GC31 synthetic provider does not qualify as a real producer.

**Analytics production receiver owner must verify** its actual transaction-local durable insert + exact \`idempotency_sha256\` ACK; the GC25 synthetic receipt table is disposable test-only and not a real UAE endpoint. In particular, any HTTP call inside the GC25 same-DB atomic transaction breaks its current port contract; a changed asynchronous handoff protocol needs its own explicit versioned reconciliation and acceptance, not a silent adapter swap.

**SRE, IAM and policy owners must verify** signed approvals (real, current external principal/scope/expiry, not the unit-test verifier), live source grants, retention/revocation, data lineage, update races and project rights. These are prerequisites to safe C7 persistence and publication.

**Windows QA owner must run** actual disposable PostgreSQL17 E2E on Windows, not only Python pure tests; a Linux-only PostgreSQL action plus Windows pure action does not meet cross-platform PG17 E2E.

## Configuration and safety flags

There is **no "production-ready=true" feature flag** in Group C. The following are **test-only opt-ins**; NEVER put these in production environment configuration:

| Variable | Usage and guard |
|---|---|
| \`CM_TEST_PG_DSN\` | Explicit disposable PG17 connection for tests; never production DSN |
| \`GC_TEST_DISPOSABLE_DB=YES\` | Gate for PG17 fixtures; a flag alone cannot prove the DB is disposable |
| \`GC31_ALLOW_SYNTHETIC_QDP=YES\` | Explicit synthetic producer and same-DB disposable Analytics table setup in GC31; required with safely named DB |
| \`GC32_ALLOW_DB_MIGRATION=YES\` | Allow only GC32 opt-in checksum + apply_migrations tests on disposable PG17 |
| \`GC_PERF_ALLOW_WRITE=YES\` | GC29 real 10k synthetic INSERT/ANALYZE, never production |
| \`GC_PERF_RUN_SMALL=YES\` | GC29 64-row smoke, not real 10k result |
| \`GC_PERF_RUN_10K=YES\` | GC29 10k SQL rows actual benchmark, explicit only |

Product runtime is wired by **explicit injected capabilities**: QDP current-preparation reader, D1 source-policy & creator/topic registry, IAM signed reviewer and global config approval, Analytics durable same-DB sink, and HTTP authorization/CSRF/origin with host-scoped project grants. No implicit production authorizations. HTTP monitoring/recovery has separately scoped \`group_c:ops_read\` / \`group_c:ops_recover\` and external recovery verifier. Do not enable recovery endpoints without a real IAM provider, scope, audit sink and SRE operational ownership.

## Strict staged integration and release (no automated merge)

1. **Freeze exact heads:** compare current PR #37 head, main base and all peer PR heads to pinned \`docs/GROUP_C_STAGE32_PEER_AUDIT.json\`. If any peer advances, redo migration/diff/contract inventory before relying on this document.
2. **Run Group C pure tests** (C0–C7, independently checked gold vectors, release preflight) and PG17 disposable original migration + signed approval/worker/backpressure/Analytics-boundary tests on **this exact commit**; record SHA/status/errors, not just workflow URLs.
3. **Run combined SQL migrations** of Group A/B/C, SRE, Search Planner and selected UAE in one temporary integration checkout **without modifying peer source branches**; require fresh and upgrade PostgreSQL 17 plus repeated checksum apply, cross-schema FKs/permissions/DDL lock budget, and complete test rollback.
4. **Request UAE owner update** for signed Group C decision/activation currentness (058–062) and actual SQL C7 qualified reader. Independent QDP/Analytics handoff and source/IAM rights remain separate owner sign-offs.
5. **Run actual real-peer E2E** (not synthetic doubles) at a fixed SHA: QDP emits current preparation; GC obtains approved raw D1 facts, reviewed cohorts, signed global activation, original score; UAE consumes exact approved persisted C7 with verified source rights and Analytics durable acknowledgement. Negative tests: other project, stale source, newer raw retraction, duplicate ACK, QDP revision and old worker lease.
6. **Run Windows PG17 + Linux PG17** with exact-SHA evidence reports, realistic GC29 10k benchmark including planner/index choices and GC30 Decimal numeric/error parity gold green, SRE alert routing and rollback rehearsal.
7. **Review Draft PR #37** against \`main\` for scope; sign off release plan and production feature/IAM flags with actual owners. *Only after* all blockers cleared may a human reviewer propose changing PR Draft state and merging. No merge is performed by this stage.

### Offline local final release audit

On a **clean** local checkout of this exact Group C branch with \`main\` ref available, from repository root:

\`\`\`bash
python -m unittest discover -s tests -p "test_group_c_release_gate.py" -v
python -m intelligence.group_c.release_gate --repo-root . \
  --base-ref main --report ./gc32-release-audit.json --audit-only
\`\`\`

PowerShell:

\`\`\`powershell
python -m unittest discover -s tests -p "test_group_c_release_gate.py" -v
python -m intelligence.group_c.release_gate --repo-root . --base-ref main --report .\gc32-release-audit.json --audit-only
\`\`\`

The **audit-only** success exit code merely means a read-only audit JSON was produced; inspect \`merge_ready=false\` and the blockers. Without \`--audit-only\`, expect exit **2** because true release is not approved. The CLI refuses a dirty worktree, missing \`main\` ref, any changed code outside Group C's ownership, modified 003/004 migration Git blobs, missing mandatory runbooks, or malformed pinned peer manifest. It does **not** fetch GitHub or invent current peer heads.

Optional separately gated GC32 PG17 checksum/idempotence tests on disposable DB only:

\`\`\`bash
export CM_TEST_PG_DSN='postgresql://<test-user>:<test-password>@127.0.0.1:5432/contentmachine_gc_release'
export GC_TEST_DISPOSABLE_DB=YES
export GC32_ALLOW_DB_MIGRATION=YES
python -m unittest discover -s tests -p "test_group_c_release_gate.py" -v
\`\`\`

If combining peer branches, start with different separately disposable PG17 fresh/upgrade databases, record exact merge source hashes, and never use operational credentials.

## Recovery / rollback, not dropping source truth

- **Before deploying:** keep current \`main\` + applied migration SHA catalog, source-policy snapshot, database backup/PITR readiness, worker/outbox drain and human approval inventory, CI logs and 10k measurement host metadata. Migration 064 creates regular (non-CONCURRENTLY) indexes, so review lock/maintenance window first.
- **If SQL migration or tests fail in a disposable DB:** discard the **entire disposable DB** and recreate it; do not delete production immutable C7 or raw D1 snapshots to make tests pass.
- **If production deploy is attempted later and fails:** halt workers and QDP/Analytics new score delivery, disable/withdraw the Group C app/API route at its host, retain all original raw/score/outbox/audit records, and restore the prior application build. For schema changes that are not backwards compatible, use an approved database backup/PITR restore in a controlled maintenance workflow — **never** \`DROP TABLE\`, \`DELETE\` audit or rewrite an applied migration SHA. Retry pending Analytics only with actual dedup/ACK reconciliation; do not blind-ack remote state.
- **If source rights or human review are revoked:** stop new computation and delivery, follow documented GC24 revocation/retention invalidation, preserve immutable evidence and check authorized deletion process separately (not a blanket cleanup).
- **If GC31/GC30 QA fails:** keep PR #37 Draft and release gate blocked. Correct code/test assumptions in the owning branch, rerun exact-head QA and update documented evidence; no "green" from test code existence.

## Stage completion truth

GC-MOD-32 is **CODE IMPLEMENTED (preflight/report/docs/tests/CI harness)** and total **GC-MOD-00..32 = 33/33 implemented by code checklist**. But the project is **NOT MERGE READY**. Exactly which blockers remain is machine-readable in the local \`group-c-final-release-gate/v1\` JSON and the pinned peer manifest. Closure requires independent migration + QA + live QDP/UAE/IAM/Analytics production proof. A future human release review must decide if it is merge-ready, not this test-only branch.
