# FIX-14 compatibility and migration collision matrix

This is source-derived from actual peer branch heads on 2026-10-26:

| Peer | Pinned SHA | QDP intake contract | Actual acceptance result | Remaining blocker |
|---|---|---|---|---|
| Group B | `63ee084feb39a960bce0aa260dd0835627887122` | QDP `qdp-event/v1` with `qdp-preparation/v2` | **INCOMPATIBLE**. GB-MOD-04 SQL reader checks `event_payload == pointer-as-JSON` and `inputs.result_schema=qdp-preparation/v1`; no transport conversion fixes the peer's authoritative SQL equality. | Group B must upgrade its own reader/contract; `group_b` delivery remains pending, no ACK |
| Group C | `37d78b7663c21904bd8cd07dde6b5243e2faae52` | QDP `qdp-event/v1`, `qdp-handoff/v1` pointer, `qdp-preparation/v2` | **COMPATIBLE** through `GroupCConsumerAdapter`. C itself validates current D1 source/policy/metric history, commits real `group_c_inbox/group_c_runs/group_c_jobs`, returns true ACK after commit. | Scoring computation/cohort approval/outbound UAE result are Group C's later stages, not done by QDP |
| Unified Analytics | `3c616f9ae1b461bb7ab439f89b3ef36a082bc419` | Declared `uae-qdp-reader/v1` wrapping `qdp-handoff/v1` | **IDENTITY ONLY / UNAVAILABLE** for completed results. UA-MOD-05's canonical lineage reader has `qualified=false` and has not established current Group results; QDP 'ready' and delivery ACK are not completed analyses. | UAE-MOD-07..13 and real result publishers; no phantom scores |

## Group B incompatible SQL and schema coexistence

Current QDP row `qdp_outbox.payload` is a committed `qdp-event/v1` envelope with `schema_version,event_type,project_id,run_id,video_id,analysis_group,state,reason_code,input_sha256`. Group B GB-MOD-04's read checks exact dict equality against seven fields of `QDPHandoff`. The persisted QDP row **cannot** be changed to satisfy this without breaking QDP event-hash/provenance and Group A/C. Furthermore Group B requires `qdp-preparation/v1`, while QDP pins `qdp-preparation/v2`. Source and media rights must never be weakened for a compatibility shortcut. `group_b.py` therefore refuses to ACK; Group B's own independent branch must implement a new version-negotiated QDP reader or explicitly compatible adapter.

**Migration collision:** both B and C independently own different files named `056_*.sql` (`056_group_b_qdp_receipts.sql` vs `056_group_c_runtime.sql`). While the filenames are distinct, the numerical prefix is shared. `intelligence.migrate` keys by complete filename, not just number: co-installation still requires ordering/checksum/dependency review and any globally enforced unique-number rules, especially with B's 055 and Group A's 055. For combined build, do NOT rename or overwrite applied files; reconcile by additive forward-only migration catalog and prove fresh+upgrade PG17 before merging. The separate Group C CI only installs its own 056 and does **not** prove Group B+C co-installation.

## Current security boundary

- C receipt `ack=true` must be checked with exact event ID, canonical C run UUID and positive `score` job ID. Translation to QDP `state=committed` occurs **only after** the Group C receiver's own transactional commit. It never sets calculated score or UAE qualification.
- Missing peer modules, missing SQL, wrong target group, bad SHA, uncommitted receipt or revoked source are blocked, not silently accepted.
- Group B compatibility is read-only negotiation; no rewrite of Group B migrations or SQL/branch from the QDP module.
- UAE reader flag alone never upgrades status. Completed Group A/B/C immutable results and current rights must be separately source-backed by UAE.
- Cross-branch peer packages are checked out to disposable CI workspace at pinned SHA; QDP code never imports peer code automatically and never merges peer branches.

## Validation

- QDP unit: `tests/test_qdp_downstream_compatibility.py`.
- Group C separate actual consumer PG17: `tests/test_qdp_group_c_peer_pg.py`; full workflow `qdp-group-c-peer`.
- Group B and UAE are intentionally incompatibility/fail-closed unit tests, **not live consumer or completed analytics E2E**.
- QA pass/fail evidence belongs in `docs/QDP_RELEASE_EVIDENCE.md`; this document does not assume CI has passed.
