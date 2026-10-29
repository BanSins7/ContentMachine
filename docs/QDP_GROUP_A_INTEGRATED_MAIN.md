# QDP → Group A integrated main pipeline (operator runbook)

**Scope:** Real QDP producer, Group A GA04 transactional inbox + media
authority, QDP durable acknowledgement, QDP compensating revocation. Code
is merged; deployment-level PostgreSQL17, FFmpeg and live SRE → Group A
analysis E2E must still be run on the operator's local machine.

## Data flow

1. Collector → D1 persists *real* video/media and provenance. SRE must
   complete a **LIVE, approved** classification run; no offline/legacy run
   may be promoted into a qualified run by QDP.
2. QDP qualification → data enrichment → group preparation creates
   authoritative `qdp_group_current_preparations` and immutable
   `qdp_outbox` events. Only a genuine, currently approved video,
   analysis-capable source policy and `media_assets.permission =
   'analysis_allowed'` can enter Group A.
3. The integrated dispatcher uses a separate PostgreSQL **dict_row**
   connection for QDP and a **tuple-row** connection for Group A, on the
   **same database**, and the pre-existing
   `from_installed_group_a()` concrete adapter.
4. The adapter reconstructs `qdp.preparation_ready/v1`, pins the original
   `qdp-event/v1` SHA and QDP pointer, then invokes the actual
   `GroupAInboxReceiver.receive()`. This verifies current QDP material,
   project, video, media/rights and on-disk bytes *within the Group A
   transaction* and writes `ga_runs`, `ga_media_refs`, `ga_steps`,
   `ga_inbox`. It commits a durable `group-a-receipt/v1`.
5. QDP verifies the receipt, records `qdp_delivery_receipts` and advances
   `qdp_outbox_deliveries` only after the GA commit. Lost responses are
   retryable with the *same* event+SHA and GA deduplicates the inbox/step.
   One QDP acceptance is **not** a completed technical analysis.
6. On revocation of project/source/policy/QDP preparation, QDP first
   delivers `preparation_invalidated` to
   `PostgresQDPRevocationReceiver` and requires GA durable revocation ACK
   before processing new preparation events. GA fences active work and
   GA→B outbox; an old accepted receipt never overrides newer rights.
7. Actual analysis is separately owned by GA20's
   `LocalGAOrchestrator`: it needs real local executors, DAG installation,
   current-rights verification, output SHA/bytes checks and explicit CPU/GPU
   resource budgets. Neither QDP ACK nor GA initial step starts a model.

## Deployment configuration

Apply the full repository migrator **only after a database backup and on
a disposable PostgreSQL 17 schema first**:

```bash
python -m intelligence.migrate
```

Verify both QDP 016/040–048 and Group A 054–059 migrations were applied with
their original immutable filename/SHA entries. Existing main SP/Collector/KE
migrations must NOT be rewritten or removed. Test an empty install and upgrade
from the prior main, repeat as a no-op, compare migration checksums.

Configure local `.env` (or process environment), without committing secrets:

```dotenv
CM_DB_ENABLED=1
CM_DB_DSN=postgresql://YOUR_LOCAL_USER:YOUR_PASSWORD@127.0.0.1:54329/contentmachine
CM_QDP_GROUP_A_ENABLED=1
CM_GROUP_A_ENABLED=1
CM_GROUP_A_QDP_HANDOFF_ENABLED=1
CM_QDP_GROUP_A_MEDIA_ROOTS=/ABSOLUTE/EXISTING/APPROVED/MEDIA/ROOT
CM_QDP_GROUP_A_AUTO_DISPATCH=0
```

If multiple roots are approved, separate them with the OS path delimiter:
`; ` on Windows **without the space**, `:` on Linux/macOS. These are local
server-only settings. The handler never exposes root paths or DSN.
**Do not enable until real media and current rights can be verified.**

### Manual delivery

Open **Qualified Data Studio** in the app, select the QDP project and use
**Проверить готовность Group A**. Then
**Передать одно готовое событие в Group A**. The POST is scoped to that
specific project and does not accept arbitrary browser event IDs or claimed
approved states. One click handles at most one pending revocation or one
pending preparation event.

Server endpoints (existing same-origin/CSRF host protection):

- `GET /api/projects/<project_id>/qualified-data/group-a/readiness`
- `POST /api/projects/<project_id>/qualified-data/deliver-group-a`
  with an empty JSON object.

For opt-in automatic dispatch after QDP preparation, set
`CM_QDP_GROUP_A_AUTO_DISPATCH=1` and restart the local app. QDP's dedicated
worker will process at most one QDP→GA event per cycle (revocations first).
It does not invoke the Group A extraction worker, paid provider or DTK.

## QA / release gates

- `python -m unittest tests.test_qdp_group_a_main_wiring`
- `python -m unittest tests.test_qdp_group_a_bridge tests.test_qdp_group_a_peer_pg`
- `python -m unittest tests.test_group_a_handoff tests.test_group_a_revocation_bridge`
- Real PG17 fresh + upgrade + 50x replay, two workers, foreign project, modified
  payload, revoked source policy, missing media bytes, expired media, unknown
  response, real `ga_inbox` and `qdp_delivery_receipts` consistency.
- Before declaring **full production**: successful SRE LIVE event, playable
  real local video, GA DAG/executor/FFmpeg/ASR/OCR completion, verified
  A003/A004 result/outbox and independent Group B re-read.
- Group A and QDP source branches historically had unrun QA. Do not
  reinterpret a static source audit or merge as a successful runtime test.

The main-repo runtime is fail-closed if env, database, rights, migrations,
media or Group A integration are unavailable.
