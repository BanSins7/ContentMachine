# UA-MOD-32 — Independent release and merge-readiness audit

**Audited:** 2026-10-28 · **Repo:** BanSins7/ContentMachine · **Head at initial audit:** `feature/unified-analytics-full-module` = `a2a558ba57f6386275ed446c9c1f391a1324ff8e` (before committing this audit) · **PR:** [#38](https://github.com/BanSins7/ContentMachine/pull/38) open / Draft / unmerged · **Baseline:** `main` = `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` · **Legacy:** [PR #23](https://github.com/BanSins7/ContentMachine/pull/23) open / Draft / unmerged, head `12921968ef3d5c6f5e706fd8e625c54733405f3b`, **preserved**.

## Verdict: BLOCKED, not merge-ready

At the audited head, GitHub compare reported **349 commits ahead / 0 behind `main`**, merge base equal to current `main` and **151 changed files**. GitHub PR metadata showed `mergeable=true`, which indicates no immediate Git merge failure, **NOT** production-readiness, test passage, data-contract compatibility or safe PostgreSQL rollout. `Unified Analytics standalone module` and other relevant GitHub Actions were still **queued**, with **no successful conclusion**. Code authored in UA01–UA31 and static checks are not substitutes for actual Python, disposable PostgreSQL17, authenticated QDP/SRE/A/B/C, Windows/Linux runtime tests and qualified 10k/50k load evidence. Current UA085 explicitly blocks direct `qualified` snapshot and `complete` evidence inserts; removal without a new independently reviewed attestation gate is forbidden.

The read-only local `release_preflight.py` added in this stage **always reports `ready_for_merge:false`**. It does not fetch remote data, write files, merge, alter Git branches, change database roles, use secrets or connect to paid models. Even if its static diff checks pass, final authorization needs separately verified human sign-off and genuine upstream authority.

## Cross-branch changed-file comparison

Source: live GitHub `compare main...branch` for each feature branch during UA32 audit, not guessed code history.

| Owner | Audited head | Changed files | Exact same *changed* files as UAE | Integration consequence |
|---|---|---:|---|---|
| UAE | `a2a558ba` | 151 | — | Adds UAE-owned `intelligence/unified_analytics/**`, migrations 080–090, tests, docs, local UI/HTTP hooks |
| Group A | `88befd74` | 161 | None | No exact write conflict; source-backed receipt adapter/producer authority still requires negotiation |
| Group B | `ba660a8d` | 171 | `app.py`, `site/index.html`, `site/projects.js` | **Manual UI/backend three-way merge required**; cannot choose one branch's host glue wholesale |
| Group C | `f033ab5c` | 108 | None | No exact file conflict; stronger reviewed/activated C cohort approval protocol still must be bound |
| SRE | `0da0bfa4` | 182 | `app.py`, `site/index.html` | **Manual host route/UI merge required**, independently recheck origin/CSRF and script loading |
| PIE | `983c6f19` | 119 | None | PIE must recheck current UAE pin/source rights before any downstream consumption |

Additional **Group B ↔ SRE** shared files: `app.py`, `site/index.html`. Other peer-pair comparisons did not show exact changed-file overlaps at these heads. No exact same *migration filename* between the compared changed-file sets was identified. **However** multiple peers reuse numeric migration prefixes **054–064** under *different full names*. `intelligence/migrate.py` sorts `[0-9]*.sql` by full filename and hashes each **full filename** as the migration version, so equal **numeric prefixes are not automatically a schema_migrations primary-key collision**. Do not infer safe combined ordering or dependencies from this: execute main→GA→GB→GC→SRE→UAE on one **disposable PG17** with the actual combined migration folder, verify FK/trigger/owner compatibility and then test upgrade from a populated current-main fixture. Never renumber/rewrite historical deployed migrations just to sort conflicts.

### Exact source/peer contract reconciliation

| Edge | Confirmed source contract | UAE interpretation | Release condition / owner action |
|---|---|---|---|
| QDP→A | Group A `intelligence/group_a/handoff.py` accepts `qdp.preparation_ready/v1`; frozen validation expects `qdp-handoff/v1` and `ga-media-manifest/v1`. | UAE QDP adapter tracks an independent exact A/B/C preparation and UA082 three handoff receipts. | **QDP owner** must expose authenticated, source-current handoffs; Group A source team must prove verified actual media bytes and valid carousel kinds. UAE/host integration glue must bind consumers; receiving a synthetic receipt alone is not qualification. |
| A→B | Group A `group-a-to-b/v1` has kind-specific video/carousel analysis ID, streams and method version. | UAE `GroupAVerifiedReader` uses own `uae-group-a...` source-backed joins and strict slide ordering. | **UAE integrator + Group A/B owners** must agree stable ID lineage, source media checksum and live reviewed A result; no unapproved `complete` flags. |
| B→UAE | Group B `intelligence/group_b/integration/analytics_contract.py` publishes `group-b-uae-handoff/v1` plus peer wire `uae-group-b-exact/v1`, eight B1–B8 components, `UAECompatibility` with explicit negotiated-version flags, and a `candidate` state (NOT qualified). | UAE `B_READER_WIRE = 'uae-group-b-exact/v1'`; source-backed reader still expects older B005 tables and does **not** automatically accept B25 producer receipt. | **UAE-owned adapter/integration update needed** to validate and negotiate B25 against actual provider-owned tables, source rights, review quality, carousel N/A; **Group B owner changes only if the published receipt cannot prove missing contract semantics**. No automatic opt-in or score promotion. |
| C→UAE | Group C `group-c-human-review/v1` defines identity-verified human review of exact frozen evidence, config/alpha/k, peer sizes, source version; request ≠ approval. | UAE C adapter `uae-group-c-persisted/v1` still expects `frozen_unreviewed` and hardcodes `approved=False`, reflecting earlier GC057-only baseline. | **UAE-owned C adapter update required** after peer merge. **Group C owner** must confirm activated, source-current, externally verifiable positive reviewer decision/receipts and revocation semantics; Group C branch modifications may be needed if that delivery contract is not yet exported. Never forge approval from existing human review request. |
| SRE→UAE | SRE branch has additional live runtime/verified coverage/human reviews/revocation migrations **054–068** beyond main. | UAE SRE reader explicitly describes M13 `not_evaluated/blocked` live pair evidence as nonpositive; requires a current effective reviewer/pair match to declare available. | **SRE owner** must supply authenticated effective live pair settlement and current source policy; **UAE-owned SRE adapter** needs compatibility audit against new SRE 054–068, including new human reviews and source revocations. No `match` inferred from taxonomy approvals alone. |
| UAE→PIE | PIE is in its own feature branch, migrations **200–206**. | UAE UA25 prepares constrained research handoff; live UA19 qualified pins are unavailable. | **PIE owner** must authenticate and reauthorize any pin, respect source revoke and not treat a signed digest as permission. UAE must not give a public positive export while UA085 is blocking it. |
| RTB | Independent peer; not part of five-branch comparison here. | UAE research/ranking consumer must continue current readside checks. | Coordinate later exact consumer contract. Do not claim an RTB merge/approval from this audit. |

A meaningful peer compatibility test must compare **real** DB columns, enum status, normalized content kinds, source-policy version, SHA-256 lineage, reviewer authority, latest revision, immediate revocation and **no misleading A/B/C score** on unavailable/partial inputs. The release audit must not silently map `candidate`, `frozen_unreviewed`, `completed` or `review_request_created` to `qualified`.

## Read-only offline preflight

From the local clone with `main`, UAE and all five feature refs available (fetching refs yourself is separate; this tool never fetches):

```bash
python -m intelligence.unified_analytics.release_preflight
```

Reports `uae-release-preflight/v1` JSON with pinned local SHA refs, UAE changed-file count, unexpected non-UAE paths, shared host-surface overlaps, migration names, peer–UAE exact filenames, peer–peer numeric prefix overlap, unchanged UA085 prohibition and fixed unresolved acceptance blockers. Exit **2** means **blocked**; zero exit is deliberately unsupported until a separately designed trusted release-evidence verifier is reviewed. A missing local branch is **`peer_git_ref_missing`** and cannot be auto-interpreted as compatible. Never use this result to press Merge.

The command only calls `git rev-parse` and `git diff --name-only` via argument arrays, never uses shell interpolation or `git reset/fetch/merge/push/checkout`. Version inventory is bounded; error output is sanitized. `tests/test_unified_analytics_release_preflight.py` has **17 authored** isolated fake-git tests including unauthorized files, shared file conflicts, numeric-prefix vs exact-filename migration collisions, missing peers, missing positive approval gate, lack of green verdict and no git writes. **Not executed in this GitHub-only session.**

## Canary and rollback — manual, non-destructive to existing history

**Preflight prerequisites (all mandatory):** manual review of exact changed 151+ files at current HEAD after peer merge; DB schema fresh + upgrade tests with checksums; all UAE unittest/Node/Chromium tests on Windows+Linux; isolated PG17 16-way worker, duplicate/concurrent, review-revoke E2E; measured actual 10k/50k workload and memory/throughput targets; current source policy/consent and validated live SRE/GA/GB/GC/QDP positive approval; operator identity + audit retention/erasure; explicit budget gating for paid models; documented SLA, owner acknowledgement and secrets isolation.

1. **Prepare:** snapshot/backup disposable and production schema separately under approved DBA procedure; capture migration manifest SHA-256 and current `schema_migrations(version,sha256)`. Ensure no existing version is modified and no unknown applied migrations. Do not assume a `DROP TABLE` rollback.
2. **Integration branch only:** reconcile all `app.py`, `site/index.html`, `site/projects.js` edits from UAE/Group B/SRE with targeted additions. Review host origin/CSRF/version headers, Studio assets/routes and non-UAE project context mount. Stage together with GA/GB/GC/SRE migrations and run **full PG17 migrations against a disposable copy** before any real data.
3. **Canary read-only first:** leave qualification worker **off**, no paid/model triggers; enable project-scoped token/operator `GET` diagnostics for an explicitly allowed test project only, verify no secret/source/score leaks, disabled/partial state, empty positive qualified view and stable blocked 409 aggregates. Record source rights, outbox lag, job queue occupancy and DB resource budgets.
4. **Canary processing only after authentic approvals:** a trusted candidate resolver plus reviewed QDP/SRE/A/B/C decision wiring must be independently audited. Enable a single authorized project/worker with bounded throughput, cost/consent and manual observation; verify exact replay, rights change and negative cases before expanding any batch. No automatic `qualified` publication from this standalone UAE branch.
5. **Rollback / stop:** immediately disable UAE worker/canary route and Studio mount, revoke project API credentials, stop leases gracefully, let fenced transactions roll back and observe outbox/recovery. For schema incidents, restore a verified DB backup / forward-only correction under DBA, **not** blind `DROP TABLE` or destructive down-migrations. Immutable existing snapshots/invalidations/audit must remain intact unless separately authorized retention/erasure. Re-check peer consumers/PIE readside so stale signed/exported pins are not displayed after revocation.

**Abort thresholds (not fabricated metrics):** any score from a nonqualified snapshot, token/DSN leakage, bypass of UA085, cross-project read, missed source revocation, duplicate completed lease, corrupted historical migration checksum, unapproved paid model call or unbounded retry is an **immediate NO-GO**. Quantitative p95/p99, throughput, memory, database IO and restoration RTO/RPO thresholds must be established by the operator *before* the canary; no measurements are available from this session.

## Required work before true merge readiness

- **UAE-owned**: reconcile newer Group B B25, Group C reviewed C and SRE effective-live attestation into UAE read adapters with independent current-project authority. Implement only additive migrations for new authenticated approval gates. Do not edit other feature branches in the UAE branch.
- **Other owner action potentially required**: QDP authoritative three-port receipt and media revalidation; Group A truthful media payload claims; Group B approved/fresh B25 receipt semantics if current candidate protocol insufficient; Group C independently verified active human-review attestation; SRE actual live pair settlement; PIE/RTB independent current-read invalidation. These are outside the current UAE branch and must be coordinated rather than assumed ready.
- **Local QA evidence**: actual Python compile/unittest, Node/Chromium, fresh+upgraded disposable PG17, 10k/50k measurements, Windows/Linux execution, concurrent and revoked-source negative tests, peer combined migration ordering and human signed release approval. Previous UA-MOD-31 benchmark only measures synthetic `blocked` storage/keyset, **not** qualified end-to-end throughput.
- **GitHub CI**: queued is not a pass; source commit must match the tested HEAD when checks eventually execute. User prefers primary QA on their own machine.
- **Release status**: **NOT MERGE-READY**. Keep PR38 Draft, PR23 untouched. Next implementation should begin with this audit's blockers and resolve them, not auto-merge.

**Python unittest, PostgreSQL17, Windows/Linux runtime QA, CI suite, throughput benchmark, actual peer producer integration and canary were not executed in this GitHub-only review.**
