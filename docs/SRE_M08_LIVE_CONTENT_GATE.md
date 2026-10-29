# SRE M08 — Read-time Rights, Expiry & Immutable Content Versions Gate

**2026-10-26 · \`feature/sre-production-v1-20261023\` · Draft PR #22**

**Implementation:** committed. **Acceptance:** CI pending / not proven green. No public paid execution, main merge or modification of historical migrations.

## Purpose and authority

M08 unifies the existing S3 D1 source-rights reader, M07 canonical project/discovery/video/source/request/query/observation verifier and an immutable S1 content/media-fidelity check into a **mandatory service gate** for existing S4 planning, worker settlement and results retrieval. This is not a one-time permission token: each request consults actual D1/S3 PostgreSQL rows again. The D1 discovery, SRE project membership, reviewed active source policy and source-specific observation are NOT deduced from client JSON, Collector UUID, filename or global video title. These external subsystems are not duplicated or modified.

The core lives at \`intelligence/relevance/live_content_gate.py\`:

- \`read_live_content_gate(conn, project_id, content_version_id, phase=..., pins=..., external_llm=False)\` — use inside an existing correctly scoped/consistent PostgreSQL transaction; **the trusted caller remains responsible for authenticating the project owner**.
- \`load_live_content_gate(...,connect=...)\` — opens one read-only, repeatable-read transaction and returns an auditable \`sre-live-content-gate/v1\` object. No write/network/model dispatch.
- \`verify_pinned_content(content, d1_provenance, pins=...)\` — pure self-consistency only: **not authorization**. Verify explicit project/video/content version/SHA/source/policy/discovery/observation/completeness/expiry pins when provided; reject partial pins and unknown keys.
- \`validate_media_fidelity(content)\` — accepts S1-approved \`metadata_only\`, \`partial\`, \`full\` content without fabricating transcripts or scenes. A metadata-only snapshot must NOT contain measured media or a media asset. A partial/full snapshot must have a source-backed media asset and at least one actually present measured segment (for silent media, no artificial speech requirement). The upstream S3 reader and reviewed policy must authorize media usage.

**Read phases:** \`planning\`, \`before_offline_compute\`, \`before_external_send\`, \`before_publish\`, \`result_read\`. \`before_external_send\` **always** sets the external provider permission check even if caller supplies \`external_llm=False\`. This is a prerequisite, **not paid spending consent**; future LIVE FSM/budget/reservation/dispatch are distinct stages (M10+).

## Integrated code paths

1. S4 \`create_offline_run\`: all selected content versions pass M08 gate at planning; no writes occur before the owned durable transaction.
2. S4 \`finish_item\`: revalidates the exact pinned video/content SHA and full current D1 lineage **inside the same result settlement transaction** before evaluating the explicitly fake, zero-cost classifier; stale D1 request/source/query, revoked policy and changed content cannot generate completed results. The claim's lease/fencing check remains in force.
3. S4 \`get_offline_results\`: loads pinned \`sre_offline_items\` metadata and rechecks M08 gate per unique content version before exposing any saved reason/evidence; revoked/mismatched/expired content is **hidden**, not marked as a negative classification.

No transition from \`offline_fixture_s4\` to a paid run was added. This stage does not retroactively purge physically retained historical data (future retention/invalidation work).

## Test matrix / execution

- \`tests/test_sre_live_content_gate.py\`: nullable media boundaries, silent-video capability, source SHA/revision/observation pins, self-signed forged proof, bad enum/bool/UUID-type data, forced external permission at send, read-only PostgreSQL mode, absent provider imports.
- \`tests/test_sre_intake_pg.py\` M08 PostgreSQL17 cases: exact D1 content revision, linked policy, source-specific observation expiry, before-send permission change, foreign project denial, wrong pinned SHA, dynamic policy revocation.
- \`tests/test_sre_offline_pg.py\`: changing D1 discovery's referenced query between durable run planning and worker settlement refuses saving any label/trace, despite matching previously stored content SHA; response hiding checked.
- \`.github/workflows/sre-contracts.yml\`: Linux/Windows pure suite and existing PostgreSQL17 S3/S4 scenarios. No extra duplicate workflow, paid calls, or SQL migration.

Local reproducibility (using **only** disposable PostgreSQL17):

\`\`\`sh
python -m unittest discover -s tests -p test_sre_live_content_gate.py -v
python -m unittest discover -s tests -p test_sre_intake_unit.py -v
CM_TEST_PG_DSN="<disposable>" python -m unittest discover -s tests -p test_sre_intake_pg.py -v
CM_TEST_PG_DSN="<disposable>" python -m unittest discover -s tests -p test_sre_offline_pg.py -v
\`\`\`

**Additional M08 hardening:** `source_policy.verified_rights` revalidates the currently stored retention policy JSON and strictly numeric positive durations on every S3 read. This closes the case where an initially reviewed policy is later edited to a nonnumeric `retention_days` that the SQL numeric-only expiration predicate would otherwise treat as unbounded. Negative coverage was added to `tests/test_sre_intake_unit.py` and disposable PostgreSQL17 `tests/test_sre_intake_pg.py`. A typed validation bug for unhashable phase input was fixed and is covered by `tests/test_sre_live_content_gate.py`. Code commits `63044ad`, `ffe7926`, `5483e01`, `f29da24`. These amendments are also **CI PENDING**.

**Outstanding acceptance conditions**: observe exact-code GitHub Actions PASS on Linux, Windows and real PostgreSQL17, inspect inherited full regressions; don't claim done for M01–M08 while queued. Full concurrency authorization fences for future paid dispatch, authenticated owner HTTP routes, physical deletion, budget approval and late external send/publish hooks beyond the current S4 workflow remain separate later planned stages. Any future live provider **must** invoke the M08 \`before_external_send\` gate immediately before transmission and still require spending approval.

**Next:** M09 — read-only version-pinned dataset preview with complete coverage and no hidden skipped pairs, only on the user's next request.
