# Group B — Implementation Tracker
**Canonical branch:** `feature/group-b-full-module` (created from main `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` 2026-10-26).
**Plan:** `docs/GROUP_B_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md`.
**Source schema:** `intelligence/migrations/005_content_features_b.sql` is already owned by main; never modify.
**Last implemented stage:** GB-MOD-06 — Media Evidence Manifest v1 (IMPLEMENTED: byte-level SHA/signatures, exact A proof catalog, timeline/slides and path-free deterministic manifest; unit/CI QUEUED, live GA producer/catalog BLOCKED).
**Next stage:** GB-MOD-07 — Group B B1–B8 readiness/dependency gates and point-of-use revocation checks.
**CI status:** QUEUED (GB-MOD-06 Group B standalone run #40319982355 on stage implementation SHA `72ea8f1876d9fc92b50a14d1ffc6f63385a7bfa3`; not PASS). **Local Python test execution:** NOT RUN (source/CI committed; runner queued). **Joint A+QDP+B PG17:** NOT RUN. **Real LLM paid E2E:** NOT RUN (approval required).

## Rules for continuing chats
Read this tracker and plan before every code change. One user request = one stage. Mark implementation as checked/struck-through even if the final tests fail, but record exact failed tests and continue repairing QA in the next request; never claim a PASS without observed evidence. Update current stage, commit SHA, files, test commands, blockers, next stage. Preserve all existing stages and actual evidence. Do not edit main/peer branches.

## Stage checklist
- [x] ~~GB-MOD-00 — Самостоятельная ветка и bootstrap~~ (IMPLEMENTED: bootstrap branch + docs/package/pure unit; QA: NOT RUN/CI PENDING; PG17 NOT RUN)
- [x] ~~GB-MOD-01 — Аудит реального кода и заморозка B1–B8~~ (IMPLEMENTED; unit/SQL audit tests committed; QA: CI QUEUED, PG17 NOT RUN)
- [x] ~~GB-MOD-02 — Структура независимого модуля и Ports~~ (IMPLEMENTED: typed ports, fail-closed statuses, 7 runtime domains, inert config, adapters opt-in, pure tests and CI; local mirror 8/8 PASS; GitHub CI QUEUED, PG17 NOT RUN)
- [x] ~~GB-MOD-03 — Собственные SQL migrations и upgrade~~ (IMPLEMENTED: additive 055, 8 tables, scoped FKs, read-only schema inspector, tests, PG17 CI; remote static file audit PASS; PG17/CI awaiting results)
- [x] ~~GB-MOD-04 — QDP Inbox и versioned preparation~~ (IMPLEMENTED: exact QDP v1 parser + DB current-state adapter, atomic inbox/draft/8 blocks/immutable receipt, post-commit ACK, quarantine migration 056, negative/PG17 tests+CI; unit/PG17 results NOT OBSERVED; live producer handshake BLOCKED)
- [x] ~~GB-MOD-05 — Group A exact-version adapter~~ (IMPLEMENTED: peer 054/schema audit, strict completed snapshot/version/hash/stream native A003/A004 media and policy gates, transactional one-time B pin; tests and CI added; true GA A003/A004 producer/joint PG E2E BLOCKED)
- [x] ~~GB-MOD-06 — Media Evidence Manifest~~ (IMPLEMENTED: source-first GA evidence references + authorized local byte SHA/size/file identity, media magic, source policy and ordered slide checks; normalized absolute/clip-relative times, OCR-frame lineage, deterministic SHA manifest without text/path copies; 13+ pure regression cases committed; CI queued, real GA producer/catalog blocked)
- [ ] GB-MOD-07 — Readiness & dependencies по B1–B8
- [ ] GB-MOD-08 — Context Builder и budgetable manifests
- [ ] GB-MOD-09 — Model Provider и capability probe
- [ ] GB-MOD-10 — Cost Manager и ledger
- [ ] GB-MOD-11 — Job engine и orchestrator
- [ ] GB-MOD-12 — B5 Editing — полностью локальный
- [ ] GB-MOD-13 — B1 Hook semantic classifier
- [ ] GB-MOD-14 — B2 Format classifier
- [ ] GB-MOD-15 — B3 Story sampling engine
- [ ] GB-MOD-16 — B3 Story semantics & patterns
- [ ] GB-MOD-17 — B4 Visual content
- [ ] GB-MOD-18 — B6 Subtitle style
- [ ] GB-MOD-19 — B7 CTA occurrences
- [ ] GB-MOD-20 — B8 Topic registry & resolver
- [ ] GB-MOD-21 — Unified evidence/schema validator
- [ ] GB-MOD-22 — Atomic write B1–B8 в schema 005
- [ ] GB-MOD-23 — Human Review, gold set, quality
- [ ] GB-MOD-24 — Downstream invalidation & refresh
- [ ] GB-MOD-25 — Analytics adapter & contract handoff
- [ ] GB-MOD-26 — API & Studio
- [ ] GB-MOD-27 — Instrumentation, throughput & recovery
- [ ] GB-MOD-28 — Полный QA и cross-platform CI
- [ ] GB-MOD-29 — Final merge-readiness & standalone release

## Execution log
| Stage | Commit SHA | Implemented scope | Unit | PG17 | Model smoke | CI | Blockers |
|---|---|---|---|---|---|---|---|
| GB-MOD-00 | `baa097ffe199b85b071ca89e351144ab59c2c751` | branch, full plan MD/TXT, contract README, initial pure Python boundaries, CI | NOT RUN | NOT RUN | NOT RUN | QUEUED (run #40305301323) | Group A/QDP/Analytics independent branches; no live adapters yet |
| GB-MOD-01 | `b5676ed4693301212961eda9a7b7c8ae9d0a9fc0` (code+CI) | Frozen vocab/semantic guards in `contracts.py`, authoritative B1–B8 matrix in `docs/GROUP_B_CONTRACT_MATRIX.md`; SQL 005 exact SHA+17 tables/2 views/14 triggers/9 indexes independently checked by `test_group_b_schema_audit.py` (`ebff34d`) and added to workflow (`b5676ed`). Other suite `test_group_b_contracts.py`. SQL 005 unchanged; no external peer imports or media/model calls. | NOT RUN (suite committed; Actions queued) | NOT RUN (source schema tests require disposable DSN) | NOT RUN (no paid model approval) | QUEUED ([run 40308098084](https://github.com/BanSins7/ContentMachine/actions/runs/40308098084)) | No runner result yet; group A/QDP external, no live integration. Implementation checked separately from actual QA. |
| GB-MOD-02 | `8a983c2b26d182fc9e6d9c5d0ff987864dbe4f23` (last implementation commit; tracker commit follows) | Added `errors.py`, `configuration.py`, `ports.py`, `architecture.py`, empty opt-in `adapters/__init__.py`, `docs/GROUP_B_DEPENDENCIES.md` and `tests/test_group_b_ports.py`; 8 Protocols, statuses unavailable/incompatible/declared, 7 domains, fail-closed paid model guard and Linux/Windows CI step. Also exports DI API from `group_b/__init__.py`; rejects exceptions from structural protocol inspection. No peer auto import or external calls. | 8/8 PASS on isolated local mirror; committed suite awaiting CI | NOT RUN | NOT RUN | QUEUED ([run #40309934697](https://github.com/BanSins7/ContentMachine/actions/runs/40309934697)) | Actual peer QDP/A/Analytics handshakes, PG17, paid approvals and E2E verification deferred; DECLARED is not READY. |

| GB-MOD-03 | `a2722a6d18a3aa7904e52802571ad3658da207c9` (last implementation; tracker commit follows) | Added `055_group_b_runtime.sql` with 8 B-owned tables, compound project FKs, A/result lineage guard, immutable inbox/quotes/approvals/reviews; `group_b/repository.py` read-only SHA/table inspection; `tests/test_group_b_db.py` static+disposable PG17 tests; `docs/GROUP_B_RUNTIME_SCHEMA.md`; PostgreSQL17 CI service, package API exports. No existing migration rewritten. | GitHub source static audit PASS (legacy blob unchanged, 8 owned tables, zero content_b recreation); Python unittest NOT RUN pending CI | QUEUED/NOT OBSERVED | NOT RUN | QUEUED (branch Actions) | No completed PG17 result, no first-install race result; real QDP/A/Analytics contracts and paid approval gates not yet connected. |

| GB-MOD-04 | `0b759b5ecf620c2392a47f2212148c7dd0046c82` (latest implementation/test commit; tracker commit follows) | QDP `qdp-handoff/v1` exact pointer parser, authoritative outbox+items+preparation+source-policy-media reader (QDP probe alias corrected), `group_b/intake.py` atomic DB inbox/draft+8 not_run blocks/receipt, optional QDP ACK strictly after outer commit, deterministic replay and append-only different-hash quarantine via additive 056, namespace/source guards. Added `tests/test_group_b_qdp.py` and `tests/test_group_b_qdp_db.py`, independent Windows/Linux+PG17 CI, `GROUP_B_QDP_HANDSHAKE.md`. 005/055 Git blobs unchanged. | NOT RUN (committed negative/simulated SQL tests; workflow queued). Source-only contract/static review completed. | NOT OBSERVED (PG17 suite committed) | NOT RUN | QUEUED ([run #40315136444](https://github.com/BanSins7/ContentMachine/actions/runs/40315136444)) | QDP branch provides proposed DTO + SQL but real producer/dispatcher Group B ACK not yet confirmed; peer integration blocked. Full QDP/SRE/Group A combined PG install and carousel media manifest deferred; no paid calls. |

| GB-MOD-05 | `6916ae1d4b312c2375286c6741788c622673460f` (latest stage code/docs commit; tracker follows) | Source-audited Group A `group-a-to-b/v1` and GA054 A003/A004. Added `adapters/group_a.py::read_exact_a` and `validate_a_snapshot`, `lineage.py::bind_exact_group_a`, opt-in root exports, `tests/test_group_b_a.py`, Linux/Windows CI, `docs/GROUP_B_A_HANDSHAKE.md`. Enforces exact GA result ID/hash + QDP project/run/video/source/input SHA, GA completed version, native A003/A004 completed statuses, source-policy identity, media asset/source rights and carousel ordered slides, stream coverage and negative observations, snapshot evidence anchored to media SHA. B draft binding one-time under row lock; no GA import or paid calls, no migration edits. | NOT RUN (new unit suite committed and CI queued; source contract cross-check performed) | NOT RUN joint A/QDP/B (GA054+055 not on this branch) | NOT RUN | QUEUED ([run #40317492351](https://github.com/BanSins7/ContentMachine/actions/runs/40317492351)) | Group A GA-MOD-18 atomic final producer not yet implemented; GA migration `055_group_a_handoff_inbox.sql` and GB `055_group_b_runtime.sql` have distinct filenames but same numeric prefix; joint migration ordering/DB QA required before merge; QDP live event dispatch unverified. |

| GB-MOD-06 | `ef7520bf4a63f10e27a7b1d480a4802860720b4f` (latest implementation/docs commit; tracker follows) | Added `intelligence/group_b/context/evidence.py` bounded real-byte SHA256 + file-size/inode/mutation, allowlisted roots, symlink/path rejection and video/image signatures; injected authoritative A proof catalog, strict project/video/source/asset/reference SHA, no duplicate proof ID, timestamp and clip-relative bounds, OCR→real-frame proof and carousel slide-no-fake-time. Added `context/manifest.py` deterministic `group-b-media-evidence/v1` read-only source-first payload, current D1 rights/expiry, original GA pins and already-bound B native A ID/SHA, with zero copied source bytes/raw paths/text. `GroupAExactPin.evidence_refs` added additively; package exports, `test_group_b_evidence.py` pure mock+temp-file negative tests and Linux/Windows/PG17 CI, `GROUP_B_MEDIA_EVIDENCE_MANIFEST.md`. No new SQL migration; main untouched. | NOT RUN (unit cases committed; source static/contract inspection done) | NOT RUN real GA+QDP+B | NOT RUN | QUEUED ([run #40319982355](https://github.com/BanSins7/ContentMachine/actions/runs/40319982355)) | GA-MOD-18 real completed A evidence producer and authoritative per-proof catalog unavailable; combined PG17/wired QDP still unverified, files can change after read, GB07 must revalidate point-of-use. |

## Peer integration and known blockers
| Module | Observed branch | Available integration | Notes |
|---|---|---|---|
| QDP | feature/qualified-data-pipeline-v1-20261023 | GB v1 consumer implemented (producer still unverified) | QDP proposed DTO + 040 outbox audited; no proven event dispatch/ACK production handshake; latest peer SHA must be repinned |
| Group A | feature/group-a-full-module | Group B exact-version A read/bind + evidence manifest consumer implemented | GA source-audited @ 4b39c44144797f30dbddfaea9051fa44f1914a4a; GA-MOD-18 producer and per-proof catalog not implemented; joint GA 054/055+GB 055/056 PG17 gate pending |
| SRE | feature/sre-production-v1-20261023 | indirect via QDP | embeddings+Opus5.5 in SRE, no B classifier reuse |
| Unified Analytics | feature/analytics-engine-qualified-pipeline-20261023 | reads B1-B8 from main schema | must verify exact A/B relationship |
| Group C | Analytics branch | independent metrics | B must not manufacture C output |

## QA truth
IMPLEMENTED != PASSED. A failed or skipped test is documented as FAIL/BLOCKED/NOT RUN and is never silently treated as success.
