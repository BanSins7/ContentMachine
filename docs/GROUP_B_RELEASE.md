# Group B — GB-MOD-29 Standalone Release & Merge-Readiness

**Stage:** GB-MOD-29 implementation and release audit COMPLETE; **production merge-readiness BLOCKED**. No pull request has been merged, auto-merge is NOT enabled, and no other owner branch was edited. The existing [draft PR #36](https://github.com/BanSins7/ContentMachine/pull/36) targets `main`.

## Pinned branch state at audit, 2026-10-28

| Ref | Verified commit at audit | Meaning |
|---|---|---|
| `main` | `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` | Baseline, already owns SQL005 |
| `feature/group-b-full-module` | `114a4b3d83c9df9afeab198760660e3b1444abb3` **before GB29 commits** | GB-MOD-00 through GB28, not a QA-approved release |
| `feature/group-a-full-module` | `88befd74b102e1ff09c46ba674406c9ba31b476d` | Separate A producer; not in main |
| `feature/group-c-full-module` | `3a99deae39e579f42d3825c653f0ae02b2b85c95` | Separate C producer; not in main |
| `feature/unified-analytics-full-module` | `c627af9508c00bc9414cbceddcaac533fb4eed18` | Separate UAE consumer; not in main |
| `feature/analytics-engine-qualified-pipeline-20261023` | `12921968ef3d5c6f5e706fd8e625c54733405f3b` | Separate legacy Analytics; not in main |
| QDP producer | **No QDP module/tree found in audited `main`** | Real trusted QDP receipt owner/commit MUST be identified and reverified independently |

**These SHA values are historical snapshots only**, not permanent dependencies. Do not assume A/C/UAE or main remain unchanged at actual merge time. Re-fetch all SHA and rerun compatibility probes. In particular, GitHub SHA of an unrelated sibling branch is NOT a trusted QDP receipt.

## Independently verified Git tree scope

At the start of GB29, `main` and Group B trees were enumerated with the GitHub Git Trees endpoint and compared by **Git blob SHA**. Result: **167 changed paths = 164 additions + 3 modifications + 0 deletions**.

Modified existing shared files: `app.py`, `site/index.html`, `site/projects.js`. All other changed paths are owned by Group B: `intelligence/group_b/**`, additive migrations `055_group_b_runtime.sql` through `059_group_b_observability.sql`, dedicated `docs/GROUP_B_*`, `tests/test_group_b_*`, `tests/fixtures/group_b_*`, `scripts/group_b_*`, `site/group-b.js`, `site/group-b.css`, and Group B's workflow. No changed/deleted foreign `intelligence/group_a`, `group_c`, QDP, SRE, Analytics, PIE, Search Planner or main-owned `intelligence/migrations/005_content_features_b.sql` files. Existing Group B-owned PR may later include new release/testing files; recalculate the exact diff at release, never reuse 167 as a final immutable count.

**Audit implementation:** `intelligence/group_b/release.py` `audit_scope` uses a strict owned-path allowlist and denies foreign paths, deletes, unverified diff and malformed/stale commit SHA; `gate_release` is a separately trust-attested release status, never promoted by raw CI JSON or synthetic fixture hashes. `scripts/group_b_premerge.py` executes an actual `git diff --name-status --no-renames origin/main...HEAD` on a full checkout and writes a safe, commit-specific scope/blocked-readiness report, but never merges, posts models, changes branches, or claims integration. Linux/Windows/PG17 workflow reruns this scope audit with `fetch-depth: 0` and uploads the resulting nonsecret report.

## Migration order and cross-branch integration risk

`main` already owns `005_content_features_b.sql`. Group B adds only:
1. `055_group_b_runtime.sql`: inbox/run/states/ledger under signed upstream references.
2. `056_group_b_qdp_receipts.sql`: immutable QDP input acknowledgment/receipts.
3. `057_group_b_budget_ledger.sql`: explicit quote/approval/reserved/uncertain paid attempt ledger.
4. `058_group_b_worker_jobs.sql`: queue, fenced lease generations, immutable worker events.
5. `059_group_b_observability.sql`: immutable metrics and per-run source admission.

The migration runner `intelligence/migrate.py` sorts migration filenames lexicographically and enforces **per-filename SHA drift guards**. A/C/Search Planner also have distinct **same-number prefixes** `055`–`059` with different filenames. This is **not** the same file-path conflict, but the interleaved combined migration order, shared PostgreSQL object names, DDL prerequisites, foreign keys and branch ownership must be tested together. For example, A owns `054_group_a_module_core.sql` and `055_group_a_handoff_inbox.sql`; C owns `056_group_c_runtime.sql` and follow-on 057–062; B's 055–059 will be alphabetically interleaved when all are merged. **Do not renumber/edit committed applied files just to change order** without an explicit forward-only migration strategy; schema_migrations SHA forbids retroactive edits.

**Required before activation:** On a *new disposable PostgreSQL 17 database*:
- Apply the exact merged migration set (not only B's present branch), and verify all registered per-file SHA against tree contents.
- Run a full **incremental upgrade** from the deployed main schema, then merged A/QDP/B/C/UAE owner branches in a controlled integration branch; inspect DDL prerequisites in actual sorted order.
- Re-run migration idempotently; audit indexes, FK joins, state transitions, rights/retention and no silent rollback of immutable tables.
- Never remove production 055–059 tables on an application rollback; switch off routes/workers and preserve uncertain ledger/outbox/review evidence until independently reconciled.

## Concrete upstream/downstream handoff blockers

1. **QDP → B intake:** authenticate true source event UUID, input and media policy SHA, original rights/current media bytes, event replay and source revocation with production producer. This exact QDP owner is not currently pinned in `main`.
2. **A → B:** Group A exact version/FK and SHA for video vs carousel, current GA receipt and observation manifest; do not reuse latest rather than pinned analysis. The A branch must be integrated/available before B execution.
3. **B1–B8:** local B5 measurer exists; paid OpenRouter POST remains blocked, and B1/B2/B3/B4/B6/B7/B8 require a production authenticated model dispatcher, approved exact USD ledger spend, authentic signed provider receipts and independently reviewed source evidence. **Do not fabricate model outputs**. Carousel B5/B6 N/A must be agreed across B/UAE.
4. **B → UAE:** current UAE consumer `GroupBExactReader` has documented version-sensitive QDP event ID vs event_key, gold-only review annotation, durable invalidation and carousel N/A mismatches (`docs/GROUP_B_ANALYTICS_HANDSHAKE.md`). B can offer a provenance-bound **candidate**, not self-grant complete/qualified. Actual source rights and quality ports must be installed by UAE owner.
5. **C → UAE:** real qualified, temporally valid Group C cohort/metrics and SRE/D1/policy/QDP sources; Group B may never supply a fake C score. Unified Analytics seven-port `evaluate_qualification` must be run against original source + B and C versions.
6. **Operator Studio:** local-only per-project metadata may read with local profile permission. All paid approval/start/QDP create/reviewer corrections/result/evidence/metrics privileged actions require real trusted `GroupBStudioService` operator capabilities, not HTML fields.

## QA and merge readiness — immutable decision rules

- GB28 `scripts/group_b_qa.py` with **completed green** Linux Python+Node, Windows Python+Node, disposable PostgreSQL17 full schema/upgrade, and no unexpected skip at the **exact same branch SHA**.
- B1–B8 real video/carousel E2E using **original** rights-verified media, QDP/A pinned source; independent expert-reviewed gold dataset with predeclared per-class precision/recall/F1 (calibration/evaluation split). Existing frozen 18-case JSON is **synthetic-only**.
- Proper paid dispatcher/receipt verifier and approved price budget must exist before enabling actual paid POST. Never retry uncertain submitted requests or release reserved headroom without trusted reconciliation.
- Signed/attested producer, source rights, review invalidation, reprocessing and seven-port downstream UAE/Group C compatibility on pinned peer SHAs.
- No introduced foreign code or changed SQL005, no provider credentials in CI/Studio, no tenant cross-read, no mutable original analysis/ledger.
- **Independent release verifier** `trusted_attestor.verify_real_release(main_sha,b_sha,evidence)` must verify actual GitHub run conclusions, artifacts, original real-model gold, source permissions and cross-owner E2E, *not merely fields or hashes submitted by the person calling*. Without it, `gate_release` always returns BLOCKED.

**At the time of this document: NOT READY TO MERGE.** GitHub Actions are queued, real model QA/actual runtime sources are unavailable, and external owner adapters are not integrated. A green synthetic QA alone would still be insufficient. This statement holds until all independently verified acceptance criteria are met.

## Rollout (after all external gates are satisfied)

1. Freeze and verify `main`, B, QDP, A, C, UAE SHAs; obtain author approval for an integration branch and full clean database QA.
2. Create a **separate** integration branch from latest `main`. Bring in owner modules in independently audited commits; resolve only explicitly approved conflicts. Never edit another owner's feature branch from this B branch.
3. Run merged 005+054... migrations on fresh disposable PG17 and deployed-schema clone, record SHA, use reversible feature toggles. Preserve SQL005 provenance.
4. Start read-only project-scoped Studio and QDP/A candidate preflight. Paid transport remains OFF during shadow mode; measure source completeness, memory, latency, invalidation and budget without submitting paid requests.
5. After independent per-class model quality and real approval, enable paid dispatcher with fenced lease/ledger receipt, rate limits and uncertain-charge monitors. Verify local B5 CPU queue and event outbox retries.
6. Perform authentic B1–B8 video/carousel and Group C/UAE E2E, test source revocation/correction; only then authorize outbound consumer snapshots.
7. Review exact PR diff and signed release report; **user explicitly approves merge**. Draft PR #36 can then be switched to ready, but not earlier.

## Rollback / incident response

- Immediately disable Group B worker claiming, QDP intake, Studio privileged actions and UAE publication; disable paid provider dispatch first. Do not revoke unrelated modules.
- Expire/fence pending local leases via owned owner-controlled operations; never silently mark old analysis rows completed. On rights revocation, enqueue outbox invalidation and block reads.
- Persist `group_b_model_attempts` reserved/submitted/uncertain rows and authenticated receipt audit. No automatic retry/refund on timeout or rollback. Resolve each uncertain charge via trusted provider reconciliation.
- Maintain immutable `content_b_analyses`, `group_b_review_events`, `group_b_execution_metrics`, `group_b_outbox` and `schema_migrations`. Roll back application routing/feature exposure only, not by deleting applied data or rewriting 005–059 checksums.
- Restore previous exact application SHA or revert B-specific route registration via a *separate approved integration commit*. Re-run main service smoke and incident source policy checks; keep donor branches untouched.

## What is done here and what remains

**Implemented on Group B branch:** 30/30 planned development work items including this release gate, ownership audit and rollout/rollback docs. **Independently verified production integration:** BLOCKED. **Merged:** NO. A full production release requires actual CI, real-model QA and sibling owner integrations; those tasks cannot be completed by making claims inside Group B.
