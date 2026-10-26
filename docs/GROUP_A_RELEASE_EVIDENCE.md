# Group A — release evidence ledger

## Current verdict

**NOT RELEASE READY** — GA-MOD-00 and GA-MOD-01 implemented of a 25-stage project; their GitHub Actions checks remain queued.
A1 local FFprobe/FFmpeg duration, aspect and technical scene extraction
is present as a historical starting point. QDP intake, PostgreSQL upgrade
path, end-to-end workers, A003/A004 complete streams, Group B/Analytics
interop and release checks are **not implemented in this branch**.

## Provenance (snapshot at bootstrap)

- Repository: `BanSins7/ContentMachine`
- Canonical branch: `feature/group-a-full-module`
- Branch bootstrap commit: `42889c094d52b4051e5a545aef4cada1171d2339`
- Main baseline/merge base: `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` (2026-10-26)
- Recovery A1 commit: `ae9fa80f6b53eb4ba2fe16eb3ecf65102e13bc7d`
- Old PR #26: closed as superseded by draft PR #35; no old history force-rebased
- Current QDP branch checked: `4054a3ecc739b467c8ffcc10fc99dfa5e1868101`
- Canonical GA draft PR: https://github.com/BanSins7/ContentMachine/pull/35

## CI and QA matrix — do not invent PASS

| Check | Status during GA-MOD-00 | Evidence |
|---|---|---|
| main→GA diff scope | PASS at bootstrap | only GA code/tests/docs; no QDP/PSI/Analytics diffs |
| Python GA unit contracts | QUEUED | [bootstrap run #40298383330](https://github.com/BanSins7/ContentMachine/actions/runs/40298383330) |
| Python syntax | QUEUED | same workflow |
| FFmpeg real synthetic media | QUEUED | `test_group_a_core.py` conditionally runs if tools present |
| PostgreSQL 17 QDP→GA | NOT RUN/NOT INTEGRATED | legacy QDP not present in clean main |
| Windows E2E | NOT RUN | scheduled GA-MOD-23 |
| QDP v1 inbox delivery/ACK | UNAVAILABLE | scheduled GA-MOD-04 |
| Full five-kind media extraction | NOT IMPLEMENTED | scheduled GA-MOD-06..18 |
| Group B/Analytics handoff | NOT IMPLEMENTED | scheduled GA-MOD-19..24 |

Bootstrap implementation commit: `6966ce38b270a2d1f6099825b52984192a98d530`. Checks are **queued**, not passed, at time of this update. Latest checks and commit SHAs should be added after each real CI result.
Migrations are forward-only and checksum-protected by
`intelligence/migrate.py`; the original 017 GA SQL is archival, outside
the runner migration directory. No applied SQL file was changed.

## External dependency matrix

- QDP v1 contract is developed on another branch: do not copy its migration
  or infer readiness from shared SQL table names.
- SRE result selection (Embeddings + Opus 5.5) is outside GA ownership.
- PSI `project_source_id` is not D1 `video_id` / `media_asset_id`;
  re-use requires a verified hash bridge in GA-MOD-08.
- Group B supplies semantic interpretation and CTA, while GA owns
  measured temporal/visual/audio facts.
- Analytics consumes immutable, version-pinned A snapshots.

Revalidate the branch heads, statuses and matrix at the next stage.

## Bootstrap hardening pending CI

Direct A1 repository APIs (`create_task`, `list_tasks`, `task_details`, `work_once`) now use the same unavailable handoff guard as the worker and HTTP entrypoints. Prior legacy DB integration tests remain archival and skipped until a real version-pinned handoff exists. This is a bootstrap fail-closed safeguard, not proof of production QDP interoperability.

## GA-MOD-01 contract freeze evidence

- Strict Python v1 QDP/manifest/stream/result parsing and SHA lineage; wire format pinned by four JSON Schemas in `intelligence/group_a/schemas/`.
- Adversarial tests committed but GitHub-hosted runner has not executed them. CI state: **QUEUED**, **NOT PASSED**, not an observed FAIL.
- [GA contract matrix](GROUP_A_CONTRACT_MATRIX_V1.md) records real QDP, A003, A004, B, PSI and Analytics schema limitations.
- Repeated GA workflow runs now use `concurrency.cancel-in-progress` to reduce redundant queued jobs; not a guarantee of immediate runner capacity.

## GA-MOD-01 current QA evidence

- GitHub connector fetched four committed JSON Schema 2020-12 files; each passed JSON parsing and required/extra-field schema metadata checks (**STATIC PASS**).
- No local direct Git checkout possible (container DNS to github.com unavailable). Python unit/FFmpeg/PG17 validation NOT RUN by this session. GitHub Actions [current scheduled run](https://github.com/BanSins7/ContentMachine/actions/runs/40302252202) is PENDING; no CI PASS or FAIL conclusion yet.
- Whole repository has many unrelated queued workflow runs. New GA workflow concurrency cancels older duplicate GA runs; observed cancellations are due to deduplication, not test failures.

## GA-MOD-02 — Port isolation evidence (2026-10-26)

- Modules: `intelligence/group_a/{ports.py,config.py,service.py,adapters/unavailable.py}`.
- Pure injected `GAPorts` defines QDPReader, MediaCatalog, TranscriptReader, VisionReader, ResultWriter, HandoffConsumer, Clock, TaskQueue. All missing peers explicitly report unavailable, `GroupAService.start()` refuses execution.
- Offline fake tests are committed in `tests/test_group_a_ports.py`, including full positive **planning only**, peer version mismatch, status revocation, cross-project/asset mismatch, and read exceptions; no live queue/writer possible.
- `capability_snapshot()` exposes `adapter_readiness` and preserves the existing public fail-closed bootstrap contract.
- `compare_commits(main, feature/group-a-full-module)` verified GA-only diff: PASS. Python unittest, full `jsonschema` validation, FFmpeg smoke, PG17: **NOT VERIFIED** at this time, workflows still QUEUED. A cancelled outdated CI run is not a test failure. [Stable workflow URL](https://github.com/BanSins7/ContentMachine/actions/workflows/group-a-module.yml).

### Post-implementation review — GA-MOD-02

- Public exports and environment-aware diagnostics added in `c41e459` and `b92f3da`.
- Structural read-only audit of port declarations and forbidden peer imports: STATIC PASS.
- GitHub compare main→Group A shows only GA files, own workflow/docs/tests; main unchanged.
- CI continued queued across repository workflows. No executed Python test report is available; PG17 E2E still NOT RUN. Do not merge into main until later release gate.
