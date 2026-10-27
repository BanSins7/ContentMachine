# QDP cross-branch integration preflight — 2026-10-27

**Status: QDP integration preparation ongoing. Release: NO_GO.** This report records source-inspected integration boundaries, not a LIVE end-to-end result, authorization for paid providers or approval to modify peer branches.

## Audited refs and findings

Repository: BanSins7/ContentMachine. **Only branch written here:** feature/qualified-data-pipeline-v1-20261023, PR #24 draft. At baseline QDP SHA 77b48e35779c19c7917146e96e091a5b62a221f0: 154 commits ahead and 143 behind main. Merge base: 4e51de5fc59f52d83e4fa71492d2d767689cd019. GitHub reported mergeable=false, mergeable_state=dirty.

| Module | Inspected HEAD | Source-backed finding / required owner |
|---|---|---|
| main | 218f5f31 | Existing PSI/Knowledge Extraction/Search Planner code and migrations must be preserved. |
| QDP | 77b48e35 (before this audit) | Events qdp-event/v1, pointer qdp-handoff/v1, inputs qdp-preparation/v2, durable A/B/C inbox receipts and current-read checks, SQL 040–048. |
| Group A | 39e56a90 | GroupAInboxReceiver exists. QDP group_a.py converts persisted event into separately signed qdp.preparation_ready/v1 envelope. Use QDP's guarded Group A adapter; old peer QDP reader may equate the *transport* envelope with persisted producer event. Recheck on current HEAD, not only earlier pinned test. |
| Group B | fe8e678a | **Confirmed incompatible:** current intelligence/group_b/adapters/qdp.py requires event_payload equal seven-field pointer and input schema qdp-preparation/v1. Actual QDP stores qdp-event/v1 and qdp-preparation/v2; translation alone cannot fix a direct SQL reread. B owner must update their reader. QDP returns 503, no fabricated ACK. |
| Group C | ddcbc1d4 | QDPGroupCConsumer durable inbox/scoring-job receiver exists; QDP group_c.py validates receipt. The receipt means queued, not completed C score. Fresh peer HEAD needs combined PostgreSQL17 verification; old pinned 37d78b76 was tested separately. |
| SRE | a9f1fc23 | LIVE pipeline and migrations 054–061 are evolving. QDP requires authoritative completed LIVE SRE run and real sre-analytics-event/v1 classification_run_completed publisher; cannot infer producer completion from SQL. |
| Video Collector | a4f658de | QDP reads only real D1 source, metadata, measured metrics and media with current rights; no provider fetch or synthetic assets. |
| Unified Analytics | 3a102f54 | UAE 080–088 adds QDP membership/handoff, joined context and QualificationOrchestrator. Production qualified state remains unproven without actual reviewed A/B/C completed evidence; QDP ACK is not analysis completion. |
| Search Planner | c1f2e50e | QDP only consumes indirectly via Collector/D1 and SRE; query label never becomes a classifier match. |
| Patterns & Insights | 6ee12056 | Downstream of actual qualified Analytics results, not raw QDP readiness. |

Historical verified QDP CI: [40487405476](https://github.com/BanSins7/ContentMachine/actions/runs/40487405476) **SUCCESS** at exact pre-audit SHA 77b48e35. Previous pinned actual A/C PostgreSQL17 *inbox* jobs were green, but no current-head combined peer production smoke is proven. The new audit script and test commits are not covered by that CI.

## Merge and SQL reconciliation

Both main and QDP modified these five common files since merge-base: .github/workflows/tests.yml, README.md, app.py, docs/DOCS_INDEX.md, site/index.html. An overlap is not necessarily a textual conflict; however PR #24 is actually marked dirty. Integration owner must preserve main's newest PSI/KE/Search Planner host/UI/security changes and add only QDP routes/Studio in an isolated integration worktree or PR. **No shared or peer file was changed in this QDP preparation.**

The migration runner intelligence/migrate.py uses the **full filename** as the schema_migrations key and checks full SHA256. Therefore duplicate numeric prefixes are warnings, not necessarily duplicate versions. main has project and KE SQL at 040–053; QDP adds 040_qdp... through 048_qdp... following checksum-locked 016_qualified_data_pipeline.sql; Group A adds 054/055, B 055–058, C 056–059, SRE 054–061, UAE 080–088. Co-install can still reveal dependency order, object collisions or FKs missing even when filenames differ. Do not rewrite, rename or remove applied SQL. Test real peer SQL together on disposable PostgreSQL17: fresh install, seeded main upgrade, checksum preservation, no-op second apply, crash/retry and rights revocation.

## New QDP-only local audit

After fetching all branches into one local clone, run the following command (replace multiline shell continuation with a single line in Windows cmd/PowerShell):

~~~bash
python scripts/qdp_peer_integration_audit.py --repo . \
  --ref main=origin/main \
  --ref qdp=origin/feature/qualified-data-pipeline-v1-20261023 \
  --ref group_a=origin/feature/group-a-full-module \
  --ref group_b=origin/feature/group-b-full-module \
  --ref group_c=origin/feature/group-c-full-module \
  --ref sre=origin/feature/sre-production-v1-20261023 \
  --ref collector=origin/feature/video-collector-dev-20261023 \
  --ref analytics=origin/feature/unified-analytics-full-module \
  --ref search_planner=origin/feature/search-planner-full-integration \
  --ref patterns=origin/feature/patterns-insights-full-module \
  --output qdp-integration-audit.json
python -m unittest tests.test_qdp_peer_integration_audit -v
~~~

The audit only uses local Git object/merge/diff inspections: HEAD pins, shared changes, conservative merge markers, file-name + blob SHA migration collisions, advisory overlapping numeric prefixes, source-pattern check for old B ABI, available peer code indicators. It does not checkout/merge/modify refs, run migrations or touch databases, start workers, send ACKs or call paid providers. **release_decision is intentionally always NO_GO; a zero CLI exit means inspection completed, not release approved.**

Tested before upload in a disposable temporary local Git fixture: **3/3 offline unit tests PASS**, Python syntax PASS. Actual cross-branch Git source audit and PostgreSQL17 were not run locally in this environment; up-to-date peer SHAs above were inspected through GitHub.

## Hand-off to owners — changes outside QDP that are needed

1. **Group B owner:** upgrade intelligence/group_b/adapters/qdp.py and its receiver/DB tests to validate canonical persisted qdp-event/v1, per-group current qdp-preparation/v2, exact immutable input/event SHA, project/media/source/policy and human review status. Then negotiate the ABI with QDP group_b.py; never mutate QDP's original hashed outbox payload to fit B's old reader.
2. **Main integration owner:** reconcile the five shared files; ensure the current main host routes and local Host/Origin/X-ContentMachine security gates remain. Avoid a blind QDP branch merge or overwriting main files.
3. **SRE owner:** deliver actual completed LIVE SRE classification event/outbox, publication, full evidence and lineage. QDP does not treat per-video classifications_ready as an entire completed run.
4. **Group A/C owners:** verify real execution, media/metric rights and supported peer revoke() ports; QDP FIX-15 cannot claim an ACKed Group task was stopped without a committed revocation receipt.
5. **Unified Analytics owner:** add verified completed A/B/C reviewed outputs with exact QDP/SRE/source binding. Until then no success/qualified projection from delivery receipts.
6. **Integration owner:** run full PG17 combined-schema, live source veto/revoke, duplicate replay, crashes, 10k real worker throughput, browser/Windows acceptance, and full C→SP→Collector→D1→SRE→QDP→A/B/C→UAE scenario (paid calls only on explicit separate approval).

**Completed in this preparation:** peer-code audit, exact blockers, non-merging Git/migration/ABI audit tool, three regression tests and this handoff. **Not complete:** FIX-18 all 120 acceptance scenarios; FIX-19 release signoff; third-party module upgrades; main merge.
