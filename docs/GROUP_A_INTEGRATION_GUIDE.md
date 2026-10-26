# Group A standalone module — integration guide (GA-MOD-00 bootstrap)

**Branch:** `feature/group-a-full-module` • **PR:** [#35](https://github.com/BanSins7/ContentMachine/pull/35), draft into `main`.
**Status:** bootstrap only, **not yet a deployable QDP→Group A pipeline**.
**Canonical plan:** `docs/GROUP_A_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md`.

## What is already in this branch

Only Group A-owned A1 extraction code, offline tests, its historical SQL reference
and this module's docs/CI have been transferred from the old PR #26.
`intelligence/group_a/extractor.py` measures verified local video duration,
display aspect ratio and *technical* FFmpeg scene boundaries. It does not
perform ASR, VAD, OCR, subtitles, audio events, carousel extraction or CTA
interpretation. Its A003 feature payload keeps all unmeasured observations
`NULL/unknown/not_run` and analysis state partial.

The original `017_group_a_technical_extraction.sql` is **archived under**
`intelligence/group_a/legacy_sql/`. The common migrations runner loads only
`intelligence/migrations/[0-9]*.sql`, so this legacy SQL **must not be run
manually in a clean main database**. GA-MOD-03 will define an additive,
versioned schema with a safe upgrade path. The original A1 repository code is
preserved for later adaptation, **not for live invocation**.

## Wiring and capability negotiation

```text
QDP (separate branch; v1 handoff not yet joined)
     → [currently unavailable: no inbound event/inbox]
Group A capabilities (explicitly unavailable for QDP intake)
     → [offline A1 technical extractor remains independently testable]
GA-MOD-04 + later stages: versioned adapter + jobs + immutable A003/A004
     → downstream Group B / Analytics (future consumers)
```

A host may query the opt-in `intelligence.group_a.capabilities.capability_snapshot()`
or, if the `http_api` router is explicitly mounted later,
`GET /api/projects/{project_id}/group-a/capabilities`. It reports:

- `qdp_handoff.state=unavailable` and `runtime.state=disabled`, code
  `qdp_handoff_not_integrated` until the actual GA-MOD-04 handshake.
- `technical_a1.state=tools_detected/tools_unavailable` depending on local
  `ffmpeg` and `ffprobe`; this is **not** a pipeline-ready declaration.
- Legacy HTTP task creation/read operations return 503 during bootstrap, and
  the Group A automatic worker is disabled.

**No host app hooks are installed in GA-MOD-00.** Mounting the existing
`http_api` is deliberately postponed until version-pinned schema/intake
and capability checks exist. Neither this branch nor the PR changes
`main`, QDP, SRE, PSI, Group B, Analytics or the common migrator.

## Safe developer commands

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_*.py
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
python -c "from intelligence.group_a.capabilities import capability_snapshot; print(capability_snapshot())"
```

No PostgreSQL credentials, API tokens or internet access are required for
the bootstrap core tests. `tests/test_group_a_db.py` is an **archival A1
integration fixture**: its true QDP/PG17 integration tests are skipped
unless a disposable `CM_TEST_PG_DSN`, `psycopg` and the legacy QDP module
are present. Even if present, this does not prove that the *new* handoff
contract is operational.

**Do not** run a worker against production data from this bootstrap branch.
Do not manually merge the legacy QDP migration. Use a separate disposable
PostgreSQL 17 instance once GA-MOD-03 has supplied proper GA migrations.

## Module boundaries / planned compatibility

| Boundary | GA-MOD-00 behavior | Future owner/stage |
|---|---|---|
| QDP `qdp-handoff/v1` | `unavailable`, no silent best-effort SQL fallback | GA-MOD-04 inbox |
| Media asset ID/hash/slide manifest | A1 local path only, no verified manifest yet | GA-MOD-05 |
| A003 technical video schema | A1 output contract available, no new GA schema installed | GA-MOD-03/18 |
| A004 photo/mixed carousel | Not implemented | GA-MOD-16/17 |
| PSI hash bridge | Not implemented; never equate PSI and D1 IDs | GA-MOD-08 |
| Group B facts/CTA | Not implemented; Group A never infers CTA | GA-MOD-19 |
| Analytics reader | Not wired | GA-MOD-19/24 |
| User-facing UI | Not mounted | GA-MOD-21 |

## Backout and review

Revert GA-only commits on the feature branch or keep the PR as draft. No
production migration or `main` write is performed by GA-MOD-00. The
A1 source and its legacy schema are retained for recovery from
`ae9fa80f6b53eb4ba2fe16eb3ecf65102e13bc7d` (old PR #26).
Fresh schema installation, upgrade roll-forward/rollback planning, host wiring
and worker deployment are explicitly out of scope of this bootstrap stage.

## GA-MOD-01 — Frozen public contract interface

See [Group A v1 contract matrix](GROUP_A_CONTRACT_MATRIX_V1.md) and the four machine-readable schemas under `intelligence/group_a/schemas/`. Pure validators are exported by `intelligence.group_a.contracts` and `intelligence.group_a`; external consumers must additionally verify cross-project and media digest lineage. This freeze does **not** activate the QDP adapter or the legacy repository.

## GA-MOD-02 — Isolated port architecture

The standalone port declarations, flags and non-activating planning service are documented in [GROUP_A_PORTS_AND_ADAPTERS.md](GROUP_A_PORTS_AND_ADAPTERS.md). All peer adapters remain unavailable until their contractual integration stages; no auto-wired modules are imported, and A1 legacy DB execution is still disabled.

## GA-MOD-03 — PostgreSQL storage schema

See [Group A PostgreSQL migration](GROUP_A_POSTGRES_MIGRATION.md). New additive `054_group_a_module_core.sql` runs against main without QDP; legacy A1 017 is still archive-only. Public writer/queue adapters remain unavailable, although GA-local schema metadata can be read. CI PG17 migrations are queued; do not activate Group A until GA-MOD-04 handoff and later release gates pass.

## GA-MOD-04 — Durable QDP inbox

The standalone opt-in QDP reader/receiver, exact event envelope and committed ACK are documented at [GA QDP handoff integration](GROUP_A_QDP_HANDOFF_INTEGRATION.md). The new `055_group_a_handoff_inbox.sql` is an additive GA-only migration. The QDP branch remains separate. No automatic host/API/worker is installed and no live cross-branch test has run; default intake stays disabled until explicitly configured with QDP and a byte-verifying MediaCatalog.
