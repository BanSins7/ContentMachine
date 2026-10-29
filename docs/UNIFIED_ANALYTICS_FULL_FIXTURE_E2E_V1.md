# UA-MOD-30 — Full fixture QDP → Group A/B/C → UAE: negative/structural E2E contract

**Branch:** `feature/unified-analytics-full-module`, Draft PR #38.  
**Status:** **TEST HARNESS IMPLEMENTED; EFFECTIVE POSITIVE QUALIFIED E2E BLOCKED** by current UAE13 and migration 085 and by independently verified upstream QDP/SRE/Group A/B/C peer capabilities not integrated into this branch. No production bypass. Real PostgreSQL17 QA not executed here.

## Exact accomplishment and honesty boundary

The stage includes tests that traverse **real UAE classes** `JoinedContextAssembler` → `QualificationOrchestrator` → `ImmutableSnapshotAssembler` using synthetic source/peer evidence adapters from existing UA12 tests. The video, slideshow-video, photo-carousel and mixed-carousel cases assert exact D1/source-policy/QDP/SRE/A/B/C pins, QDP three independent A/B/C preparation hashes, Group B's exact Group A parent result, current Group C snapshot/config, canonical provenance, Group A media slide count/order and deterministic decision/payload SHA-256. Even a fully populated synthetic seven-port candidate **only returns `complete_claims_unapproved` and a `partial` draft** (never `qualified`); missing or stale effective producer approvals are treated as blocked/incomplete. The test doubles do not prove production peer authenticity, rights, or content accuracy.

The PostgreSQL17 fixture actually **runs the existing migrations** on an isolated database (when an operator sets the disposable-only `CM_TEST_PG_DSN`) and uses the real UAE14/15 immutable repository with a synthetic `UnapprovedSyntheticAssembler` for both a video and actual `photo_carousel`/`mixed_carousel` records. That is a real **atomic SQL persistence E2E**, but not an actual source-backed complete A/B/C evaluation. It verifies seven persistently linked unapproved evidence rows, parent/payload digest identity, exact content kind, video vs carousel isolation/versioning, replay, concurrency, cross-project query scope and **zero** rows in the qualified visibility candidate views. It also attempts direct SQL `qualified` snapshot insertion and `complete` evidence insertion and requires PostgreSQL SQLSTATE `23514` from the active unapproved-producer guards.

The QDP-to-UAE subset is tested separately against the real queue: disposable fixture writes three **synthetic** verified-format handoff rows for A/B/C and validates project-scoped job enqueue and replay, but **enqueue is not an approval**. Removing the C handoff, revoking source rights or leaving the trusted candidate resolver unavailable must block claim/processing and never write an invented snapshot. For video and carousel the three receipts cannot substitute for independently authenticated peer result schemas/versions or human reviewed Group C.

The authorisation/review subset uses the actual UAE16 `RevisionEventProcessor` with explicit synthetic proof rows. Later SRE taxonomy, Group B review, Group C new cohort and **Group C rejected human review** invalidate only eligible earlier snapshots and issue project-scoped outbox notifications. Failed/no authentic proof or failed outbox cannot be treated as success. Historical parent snapshot status never becomes `qualified`.

The statistical subset uses independent Decimal golden numbers and the actual UA20 aggregation reader with an injected fake future-positive `VisibleSnapshotReader`. For five exact synthetic values it verifies median, mean, p25/p75, out-of-range C values and sample N≥5. For sample N<5 it must **not query storage**; a revoked fifth pin must suppress the ENTIRE result. Duplicate/cross-project/over-limit requests raise a stable validation error. This fake-authorized statistical exercise does not override UA19 or make actual five-video datasets qualified; no claims of significance, p-values, CI or causality are allowed.

## Files and test inventory

| Test | Coverage | Status |
|---|---|---|
| `tests/test_unified_analytics_full_fixture_e2e.py` | **28** in-memory exact seven-port video/carousel structural, version, rights and blocked/partial status | Authored; not executed |
| `tests/test_unified_analytics_full_fixture_e2e_pg17.py` | **12** conditional real PG17 persistent snapshots, qualified/complete negative guards, FK, rights, concurrency | Authored; not executed |
| `tests/test_unified_analytics_full_qdp_queue_e2e_pg17.py` | **4** conditional PG17 A/B/C handoff → queue, missing C, revoked source, no candidate resolver | Authored; not executed |
| `tests/test_unified_analytics_full_fixture_statistics.py` | **12** independent decimal + min-N + all-or-none current-rights fake-reader golden values | Authored; not executed |
| `tests/test_unified_analytics_full_fixture_revisions.py` | **6** synthetic actual UA16 revision processor, rejected human review, source of proof and rollback | Authored; not executed |
| `tests/test_unified_analytics_full_fixture_static.py` | **21** static current UAE085/080/082/089/worker/UA19/API security invariants | Authored; not executed |

**Total: 83 authored tests**, including 16 requiring a disposable PG17 instance. None is evidence of passing local Python/PG17 validation until executed. Additional prior-stage tests for separate A/B/C adapters, QDP handoff, SRE, worker, source lifecycle, readside, PIE and ranking must still be regressed.

### Release readiness matrix

| Layer | Test result this stage | Hard blocker |
|---|---|---|
| Synthetic complete claim video + two carousel kinds | Deterministic exact-version structural *fixtures written* | Claims remain `located_unapproved`. Not an approved UAE decision. |
| Real QDP A/B/C handoff storage | Conditional PG17 real queue tests written | Independently authenticated QDP receipts and peers not merged/integrated as trusted source-backed ports. |
| Real immutable video/carousel snapshot storage | Conditional PG17 real transaction tests written | Only `partial`/`blocked` can be persisted until actual approval authorities are bound. |
| SQL positive `qualified` snapshot | Intentionally NEGATIVE assertion | UA85 `uae_v1_no_forged_qualified_insert` and `uae_v1_no_forged_complete_evidence_insert` correctly reject this. **Do not disable triggers, write fake approval flags, edit peer schemas, or introduce alternative shadow tables** merely for a green E2E. |
| Current post-revocation readable project aggregate | Tests enforce zero visibility or mock-authorized all-or-none stats | Requires genuine UA19 project-pinned source/assets/media/peer permissions and minimum five compatible qualified rows; UA26 aggregated HTTP responds `409`. |
| Review/revision human decision | Synthetic proof tests written | Real verified Group C rejected/approved human decision events must be delivered by peer owning branch with current provenance. |
| Signed UAE25 → PIE research | No direct consumer test added here | Requires independently authenticated rights/provenance receiver; not a substitute for QDP/SRE/A/B/C approval. |

**Upstream ownership:** SRE and Group A/B/C have separate feature branches. They were **not edited or merged**. An independent QDP handoff transport, strict peer schema/version data, signed/verified reviewed effective-approval wire, live QDP/SRE/A/B/C readable current source, and server-owned candidate resolver are still required for real positive E2E. UAE QDP receipts alone do not make the producer complete. The user needs to coordinate these owners when planning the future release migration; do not relax the current 085 safety triggers in UAE alone.

### Developer-machine execution

Set up an **isolated PostgreSQL 17** database and export `CM_TEST_PG_DSN` (never point to `CM_DB_DSN` or any production database). The reused base fixture checks server version and application migrations itself. Run:

```bash
python -m compileall -q intelligence/unified_analytics tests
python -m unittest discover -s tests -p 'test_unified_analytics_full_fixture*.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_full_qdp_queue*.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_group_a_adapter.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_group_b_adapter.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_group_c_adapter.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_revision_events.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_worker*.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
```

If a local QDP/SRE/A/B/C stack gains genuine provenance and effective approval, a **separately reviewed future positive E2E** must first prove the exact run lineage, approved human review decision, current source/asset rights and correct cohort sample; only then introduce a narrowly-scoped authenticated approval migration to permit true qualified inserts. A new migration and tests must remain gated against forged SQL inserts. This UA30 work must not anticipate that sign-off or silently override 085.

**No production services, paid models, persistent real media, scheduling or external provider calls.** GitHub source re-fetch/static checks are the only inspections performed remotely; executable Python, actual PG17 and runtime browser tests were **NOT RUN**.

**Next stage:** UA-MOD-31 — scale and adversarial QA (10k/50k load, concurrency, malformed JSON, SQL injection, memory/performance and Windows/Linux).
