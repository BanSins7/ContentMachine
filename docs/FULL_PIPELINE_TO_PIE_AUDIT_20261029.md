# ContentMachine — full pipeline to PIE: integrated staging and actual readiness audit

**Date:** 2026-10-29. **Source:** current main at `dd28cd2b4075672286790e825fd6c95a53cb659d`.
**Staging tree:** integration of prepared A→B, B→C, C→UAE and UAE→PIE branches.
**Verdict:** **SOURCE PRESENT / CONTRACTS PARTIALLY CONNECTED / REAL E2E BLOCKED**.
No successful real PostgreSQL17/real-media/Opus/ONNX/UAE→PIE execution observed.
This branch must NOT be merged to production main under a false PASS claim.

## Assembled code path

```text
Search Planner → Video Collector/D1 → SRE M11 LIVE/M30/M32
  → SRE M36 durable outbox → QDP modern UUID receipt/Qualified Set
  → QDP A/B/C persisted preparations
       ↳ Group A → Group B via GA outbox + Group B inbox
       ↳ QDP B → Group B worker
       ↳ QDP C → Group C C7 metric producer
       ↳ Group B + C exact pair/joint receipt
       ↳ Group C outbox → UAE durable reception
  → UAE joined SRE/QDP/A/B/C/source qualification
  → UAE signed row-level research export → PIE signed intake/rights gate
  → PIE analysis and release (NOT AUTHORIZED)
```

### Stage composition performed

- Added 603 missing files from `integration/group-a-group-b-pipeline-20261029`,
  `integration/group-b-group-c-pipeline-20261029` and
  `integration/group-c-uae-pie-pipeline`, including UAE/PIE source and
  forward-only migrations. Existing `main` SRE M30/M32, QDP 072–074,
  Group A, Search Planner/Collector/RTB files were preserved.
- Mounted **default-deny** local UAE and PIE endpoints and static UI through
  the current `app.py` without dropping SRE/QDP routes.
- Installed the QDP → B/C and Group A → B dispatch adapters from the
  prepared integration branches, not fake ACK implementations.
- Updated local `.env` opt-in whitelist for
  `CM_QDP_GROUP_B_ENABLED`, `CM_QDP_GROUP_C_ENABLED`,
  `CM_QDP_GROUP_BC_AUTO_DISPATCH`,
  `CM_GROUP_A_B_ENABLED`, `CM_GROUP_A_B_AUTO_DISPATCH`.
- Added `scripts/pipeline_to_pie_preflight.py`: read-only source and
  disposable DB readiness scanner, optional unit/contract suites.
  It **returns nonzero until real E2E is independently verified**.

## Concrete source-level failing gates (not speculative)

1. **SRE → UAE:** `intelligence/unified_analytics/sre_adapter.py` currently
   requires `JOIN sre_classification_runs c ON c.id=lr.run_id`. The actual
   SRE LIVE module emits `sre_live_runs` + M30 UUID results without creating
   a fake legacy classification row. Therefore a genuine modern run does not
   satisfy this UAE join.
2. **QDP → UAE:** `intelligence/unified_analytics/qdp_adapter.py` joins
   `sre_classification_runs` through `r.sre_run_id` and verifies positive
   **integer** result IDs. The integrated modern QDP source has
   `qdp_runs.sre_run_id=NULL`, `sre_live_run_id=UUID`,
   `qdp_item_lineage.sre_live_result_id=UUID`. Thus current UAE QDP
   reader cannot certify real modern QDP runs.
3. **UAE qualified output:** `unified_analytics/joined_context.py`
   deliberately implements `qualified=False` at the current source
   boundary. `unified_analytics/worker.py` rejects attempted positive
   qualification via `worker_unapproved_qualification_denied`. Receiving
   a Group C metric is therefore not a completed unified snapshot.
4. **PIE admission:** `patterns_insights/intake/uae.py` and
   `intake/provenance.py` deliberately return
   `verified_live=False` and `ready_for_analysis=False`.
   An HMAC-signed dataset is integrity evidence, not independent
   license, authority or full provenance approval. Do not change these
   booleans to True without the actual reviewed certification flow.
5. **Deployment:** No real PG17 connection, retained D1 video, approved C9,
   SRE M30 authenticated paid receipts, M32 review, executor, media-policy
   approval or PIE signing keys were supplied or available to this remote
   GitHub-only session. Source files and test scripts alone do not prove an E2E.

## How to RUN a safe preflight locally

In a **checkout of this integrated staging branch**:

```bash
python -m scripts.pipeline_to_pie_preflight
python -m scripts.pipeline_to_pie_preflight --contract-tests
```

For a **disposable PostgreSQL 17 database only**, after an audited migration
check and backup, configure separately (never commit real credentials):

```bash
export CM_TEST_DISPOSABLE_DB=YES
export CM_TEST_PG_DSN='postgresql://USER:REDACTED@localhost:54329/contentmachine_disposable'
python -m scripts.pipeline_to_pie_preflight --database --contract-tests
```

The database probe uses `default_transaction_read_only=on` and examines
existing relations. It does not create migrations, start workers, pay for
Opus, download videos or override approvals. For a **real positive E2E**
you must additionally load authorized source media, complete SRE LIVE,
deliver/ACK to QDP, create+process a QDP run, execute actual Group A/B/C
workers, review and explicitly authorize a positive UAE qualified snapshot,
verify signed UAE export with independently provisioned PIE rights/
provenance authorities, run approved PIE analysis and confirm persisted
output. None of that is simulated by the preflight.

## Required fixes before production merge

- Add *read-only* UAE adapters for actual modern SRE M11/M30/M32 and QDP
  `sre_live_run_id` plus UUID per-leaf lineage, with SQL origin/current
  rights validation and migration-safe legacy compatibility.
- Design and review independent operator approval of the **positive**
  UAE `qualified` snapshot, including D1 and Group A/B/C revalidation;
  then allow that *specific* fence-controlled action, not a global bypass.
- Design and independently attest real-live PIE admission and release,
  including signing key rotation, rights and row-provenance re-reads,
  retention, revocation and redaction. Signed receipts alone do not certify
  datasets.
- Run PG17 fresh + upgrade checks across migrations 016–074, 080–091,
  200–207, including SRE 062 previously reconstructed and deployed SHA
  reconciliation. Verify SRE→QDP→B/C FK constraints with actual data.
- Execute on a local machine: media files, FFmpeg/ONNX provider assets,
  explicit spend/consent budgets, project authorization and deterministic
  negative/positive/revocation E2E, replay+concurrent worker fencing.

**No CI jobs were relied on. The source-wide checks do not equal a live run.**
