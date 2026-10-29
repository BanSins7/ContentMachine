# QDP FIX-18 — acceptance/chaos QA audit (2026-10-27)

## Decision: NO_GO (not a release approval)

The entire original QA-001..QA-120 source specification is carried **verbatim** in
[`QDP_QA_001_120.json`](QDP_QA_001_120.json). The matrix does not
turn source-code existence, test names or synthetic receipts into real LIVE acceptance.

**Last observed green baseline:** [GitHub Actions 40386206468](https://github.com/BanSins7/ContentMachine/actions/runs/40386206468),
exact QDP SHA `53f721b6e141ee639f2b8d6e71f1f81dffee490f`,
**258/258 PASS**, no failed/errored/skipped tests, disposable PostgreSQL17,
Python/JS syntax; two separate pinned Group A and Group C *inbox* PG17
integration jobs succeeded. That does NOT verify running Group A extraction or C scoring.

**New implementation commits (this audit):**
- `9f5dd598`: `scripts/qdp_release_gate.py` — explicit fail-closed 120-case assessment.
- `deed3ec0b`: `docs/QDP_QA_001_120.json` — all 120 original acceptance requirements.
- `225cdd8b`: exact git SHA and per-test results emitted by `scripts/qdp_verify.py`; any skips cannot certify complete QA.
- `16e1758c`: `tests/test_qdp_release_gate.py`, negative gate regressions.
- `38564ca7`: separate Windows offline CI and evidence-correlation job.
- `e65e967a`: malformed manifest must fail CI even with expected NO_GO.
- `4e347b4b`, `b25d1776`: actual localhost Host/Origin and X-ContentMachine POST guard tests integrated into QDP suites.
- `91084cf7`, `fd280ae9`, `c6964aea`: seeded `main` → QDP PostgreSQL17 upgrade/migration-noop check and separate CI job.
- `d9c768fc`: QA-103/104 linked to their live Handler guard tests.

**Evidence classification in the matrix:** 67 cases linked to direct automated
regression candidates; 46 cases have only partial/correlated evidence;
7 cases explicitly require real external upstream/downstream evidence
(QA-021/022/026/100/116/118/119). This is a
*traceability inventory*, not 67 independently verified PASS marks.
CI must run exact HEAD with no skipped tests and matching Linux/Windows commit SHA
before even the automated cases can be individually promoted by the gate.

## Acceptance runs and remaining limitations

[Latest QDP Actions](https://github.com/BanSins7/ContentMachine/actions):
`qdp` Linux disposable PG17; pinned Group A and Group C PG17 inbox;
`qdp-windows` Python/JS offline (PG17 deliberately NOT RUN);
`qdp-main-upgrade` seeded main→QDP PG17; and
`qdp-fix18-acceptance` exact SHA case matching + strict NO_GO.
Do **not** mistake a green `--expect-no-go` CI job for release approval.
New run `40485662857` was **PENDING** when recorded, with no
test PASS/FAIL yet on code commit `c6964aea`.
Subsequent documentation commits have their own exact SHA/CI.

Commands on a real disposable runner:
```sh
QDP_DISPOSABLE_TEST_DB=YES CM_TEST_PG_DSN=postgresql://... \
  python scripts/qdp_verify.py --with-postgres --report qdp-qa.json
python scripts/qdp_verify.py --report qdp-windows-qa.json  # on Windows
python scripts/qdp_release_gate.py \
  --linux-report qdp-qa.json --windows-report qdp-windows-qa.json \
  --report qdp-fix18-gate.json --expect-no-go
```
`--require-ready` is a strict fail-closed release assertion and **must fail**
until all original 120 acceptance cases have exact evidence.
An unavailable peer must not be replaced by a fake production success.

## Currently unclosed external/P0 acceptance

- QA-021/022/026: original Search Planner / Collector / C provenance,
  dedup and versioned live publication across actual linked peers still
  require the upstream owners.
- QA-100: peer Group A/C `revoke()` and execution-time cancellation barrier
  are not yet present in the pinned tested receivers.
- QA-116: 10,000 *real qualifying videos* processed through the actual QDP
  worker with throughput and bounded-memory measurements NOT MEASURED.
  The existing 10k/30k-row PG query proxy is not equivalent.
- QA-118: real Group A FFprobe result after actual A1 worker invocation NOT OBSERVED.
- QA-119: real C→SP→Collector→D1→SRE LIVE→QDP→A1 chain and lineage NOT OBSERVED.
  No paid LLM/platform calls were authorized by this QA work.
- Group B's pinned intake ABI remains incompatible with QDP v2;
  Unified Analytics has no validated completed-results handoff.
- `feature/qualified-data-pipeline-v1-20261023` was 143 commits
  behind current `main` at branch comparison before this audit;
  integration and peer migration reconciliation remain a release gate.
- QA-112 new populated main→QDP upgrade CI has been authored but not yet
  verified; it must be treated as **NOT RUN** until an actual successful job.
- Manual interactive browser, Linux/Windows integrated suite, original
  individual QA trace and owner release sign-off remain open.

**NEXT:** read the results of the latest exact-SHA CI, fix observed FAILs,
attach case-level evidence, close only cases actually proven,
then complete FIX-18. FIX-19 and merge remain unchecked; no main changes.
