# GB-MOD-07 — B1–B8 readiness, incomplete facts, and point-of-use checks

**Owner:** `intelligence/group_b/dependency_rules.py`, `intelligence/group_b/readiness.py`. Branch `feature/group-b-full-module`. No QDP/A imports, network, paid LLM, SQL migration or changes to `main`. Schema 005/055/056 remain frozen.

## States (NOT classified results)

- **ready**: this block has a verified, exact-version **input eligibility** set for the *current run*, not that its semantic output was inferred, saved, reviewed, approved, or paid for.
- **partial**: an upstream capability, observation, coverage, reference, or measurement is missing/unavailable. Never turn this into `false`, `no_cta`, `none`, `0 cuts` or an empty semantic label.
- **blocked**: incompatible kind, invalid identity/checksum, revoked policy, cancelled/invalidated QDP or a mismatched immutable A/media record. Carousels are **not applicable to B5/B6** by schema 005; these blocks remain `blocked` in a pure report (not a false output).

Each `BlockReadiness` has a stable block name, a tri-state, explicit reason codes, and the **exact** hash of the verified media evidence manifest only when ready. A `ReadinessReport` carries all eight decisions without modifying PostgreSQL. Note: existing `group_b_block_states.status` SQL 055 has `unavailable` but not `blocked`, therefore this stage **does not write** derived tri-state into that column. Persistence mapping/state transitions are a separate job-engine concern (GB-MOD-11/22).

## Frozen dependencies

| Block | Video prerequisites | Photo/mixed carousel prerequisites |
|---|---|---|
| B1 Hook | Completed A technical, speech, screen-text and audio *measurement status*; at least one verified speech/OCR/frame/audio hook source | Measured slide/screen-text, verified slide/OCR source |
| B2 Format | Technical coverage and one verified sampled frame | Complete slide stream with at least one verified slide |
| B3 Story | Technical coverage + actual sampled frame for sampling stage | All expected ordered slides present in manifest, not inferred from slide count alone |
| B4 Visual | Verified sampled frame | Verified slide image |
| B5 Editing | Video-only. Completed technical scene measurement, scene proof, measured positive duration | **blocked: editing_video_only** |
| B6 Subtitle | Video-only. Completed subtitle+screen-text A measurements and checked OCR; `none` cannot be inferred from 0 OCR. A measured subtitle absence still needs a real negative-proof reference | **blocked: video_subtitles_only** |
| B7 CTA | Measured speech+screen-text with verified transcript/OCR evidence; a complete scan is **not** implied by readiness | Measured slide/screen-text + verified slide/OCR evidence |
| B8 Topic | Measured speech/screen-text and actual transcript/OCR/frame evidence from this publication | Measured slide/screen-text + actual slide/OCR. Never substitute SRE or QDP hypothesis keys |

A **measured silent video** can be ready for B1/B7/B8 if the speech and audio streams are explicitly complete, their zero-evidence/absence states are proven and visual or text evidence is available. **Unknown speech** remains partial. Complete/no-CTA and subtitle absence are *output states* that only B7/B6 classifiers can produce after sufficient coverage, not the readiness engine's own result.

The prerequisites intentionally err toward incomplete. More granular sampling, visual coverage budgeting and block-specific evidence heuristics are delivered in GB-MOD-08/12–20; this matrix merely grants **eligibility to attempt** them. It never attests classification quality or semantic completeness.

## Exact fresh QDP/A/Media boundary

Call `assess_current_run(conn,project_id=...,b_run_id=...,ga_result_id=...,ga_result_sha256=...,media_roots=...,observation_catalog=...)` inside an **authorized, caller-owned transaction**. The function:

1. Verifies the requested canonical project+run, accepted Group B inbox, correct event/preparation hashes and **durable inbox receipt**.
2. Constructs the exact `QDPHandoff` for B from committed facts. **Rereads** QDP outbox, qualified item, current `preparation_ready`, revocation records, source/policy/media. QDP pending/old versions/expired/blocked/cancelled records do not enable work.
3. **Rereads** exact GA result (ID, SHA, content/video/source lineage, native A003/A004) and source-first `group-b-media-evidence/v1` manifest. Actual source/media/proof file bytes are rehashed; no stale snapshot or local filename is accepted as evidence.
4. Re-checks against the QDP-approved media ID, SHA and policy, the current B pin, and GA stream statuses from the immutable exact GA result—not caller-supplied booleans.
5. Returns eight readiness decisions; **never writes them** as completed output or starts a worker.

For actual entry to a block, use `require_fresh_block(conn,...,block,expected_manifest_sha256)` in the worker's own transaction; it reruns all validations and rejects a stale expected digest. `require_block_ready(report,...)` only checks an existing report and **is not a security authorization API**: clients cannot replay it instead of performing a fresh inspection. No cost/approval/model API is called; that remains GB-MOD-09/10/11.

A DB transaction does **not** guarantee rights persist after the connection closes. A QDP invalidation may be inserted concurrently, and a file may be modified after hashing; for a paid or asynchronous call, the future worker must check again, apply locking/lease protocols and cancel on revocation. This stage has no running worker, no recurring watcher and no race-tested cross-module producer.

## Known integration limits

- Group A `GA-MOD-18` completed immutable producer and per-proof authenticated catalog are not yet present. Unit fixture + temporary bytes ≠ end-to-end proof.
- The QDP B adapter currently rejects carousels without authoritative QDP per-slide semantics (`carousel_media_manifest_unavailable`). Pure dependency rules cover their geometry honestly; **the real intake remains partial/unavailable** until the peer completes its carousel handoff.
- The runtime checks assume Group A 054/055 and QDP integration migrations exist; they are on independent branches, not `main`. The two independent `055` filenames remain unchanged; reconcile first-boot migration ordering and PostgreSQL 17 end-to-end before merge.
- A published `ready` report does not unlock paid models. Cost quote, approval, reservations, retries and provider behavior need GB-MOD-09/10/11.
- A caller must have authenticated permission for the project and exact B run. Do not expose raw `assess_current_run` as an unauthenticated API.

## QA / negative scenarios

`tests/test_group_b_readiness.py` runs pure rule cases and a **mock SQL reader** for point-of-use. It tests valid eight-block video eligibility, silent-video measured absence, unknown speech, missing subtitle OCR, unmeasured scene cuts/frame samples, missing carousel slide, B5/B6 carousel not applicable, image-post B2 rejection, receipt tamper, swapped QDP video/policy, revoked QDP rights, absent QDP schema, changed A SHA, mismatched media IDs, altered evidence, and cached-ready invalidation.

Run:

```bash
python -m unittest discover -s tests -p 'test_group_b_readiness.py' -v
```

No completed local Python or PG17 run is asserted until real CI results are observed. The Group B workflow runs this pure test on Linux/Windows and in the PostgreSQL 17 job (mocked peer upstream; **NOT** an A/QDP producer/consumer E2E). These tests use no paid model credentials.

**Next:** GB-MOD-08 — Context Builder and budgetable manifests.
