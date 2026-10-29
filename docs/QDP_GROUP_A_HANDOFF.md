# QDP-FIX-13 — real Group A GA-MOD-04 intake compatibility

**Code status:** CODE_IMPLEMENTED. **QA:** [GitHub Actions 40321549312](https://github.com/BanSins7/ContentMachine/actions/runs/40321549312) **PASS** on QDP `a39459dc031ef63377321dbb6af2099c8265e7c2` with pinned peer GA `cc1d43497aa70b6f1831b104d835c8674861e9ab`. QDP PG17 **206/206** (0 failures/errors/skips) and separate **real GA inbox PG17 E2E test PASS**.

## Exact peer that was inspected

Group A `feature/group-a-full-module` at SHA `cc1d43497aa70b6f1831b104d835c8674861e9ab` implements:

- `intelligence.group_a.handoff.GroupAInboxReceiver` — opt-in versioned durable transactional inbox, `ga_runs`, `ga_steps`, `ga_inbox`, conflict quarantine. Returns `group-a-receipt/v1 state=recorded,analysis_state=not_started` after transaction commit.
- `intelligence.group_a.adapters.media_assets.VerifiedMediaCatalog` — validates existing authorized `media_assets`, checks bytes, source/media ownership, local approved path and manifest. Requires `permission='analysis_allowed'`.
- `intelligence.group_a.config.GASettings` — opt-in flags required, automatic worker/API remain disabled during peer bootstrap.
- GA-owned migrations `054_group_a_module_core.sql` and `055_group_a_handoff_inbox.sql`. QDP does not copy these into its repository or edit their migration checksums.

The old GA `repository.create_task()` and `group_a.work_once()` use `require_live_handoff()`, which is deliberately disabled on that standalone branch. FIX-13 therefore calls the newer GA-MOD-04 durable inbox port, **not** legacy `create_task`, and does not bypass its gate or promise A1 processing.

## QDP adapter and exact canonical transport

QDP `intelligence.qualified_data.integrations.group_a.from_installed_group_a` explicitly constructs a real GA receiver with a QDP `QDPGroupAReader` inside the **same Group A transaction** and an authorized GA media catalog.

```python
from intelligence.qualified_data.integrations.group_a import from_installed_group_a
from intelligence.qualified_data.outbox_delivery import dispatch_once

# The integration host MUST install Group A package and GA 054+055 migrations,
# and supply independently configured explicit factories:
# ga_connect: psycopg connection factory using default tuple-row cursor
# qdp_connect: psycopg connection factory using dict_row
receiver = from_installed_group_a(ga_connect, media_roots=[shared_media_root], enabled=True)
result = dispatch_once("group_a", receiver=receiver, connect=qdp_connect)
```

This code sample illustrates explicit opt-in configuration. It is not automatically enabled by a file import. QDP's normal startup must work with no Group A installed; unavailable peer → 503, **no ACK**.

QDP FIX-12 stores `qdp-event/v1` outbox and its SHA. GA's actual receiver expects **another envelope** `qdp.preparation_ready/v1`. The adapter builds a strictly versioned GA envelope with `qdp-handoff/v1` pointer and original `event_payload_sha256` inside the signed pointer; it signs that separate transport. `QDPGroupAReader` rechecks original persisted QDP event SHA, exact project/run/video/group/input hash, authoritative `qdp_group_current_preparations`, real D1 media ownership, asset SHA, consent and expiry **in the same GA PostgreSQL transaction**. Group A then rechecks local bytes in `VerifiedMediaCatalog`.

On success, the real Group A receiver commits `ga_runs + ga_steps + ga_inbox` before returning `group-a-receipt/v1 state=recorded`. QDP verifies `event_id`, project, run, video, GA envelope hash and valid GA run UUID/step ID. Only then does it translate to FIX-12 `state=committed`, original QDP payload SHA and immutable receipt key `ga:<ga_run_uuid>:<ga_step_id>` for producer ACK.

No source/media/metric data are fabricated, copied to a new QDP catalog or sent to providers.

## Tests and exact evidence

`tests/test_qdp_group_a_bridge.py`: QDP-side pure tests for signed transport, original hash binding, receipt validation, cross-group rejection, receiver missing/crash fail-closed and malformed payload. `tests/test_qualified_data_db.py`: project-scoped current GA reader with real QDP outbox, pinned real source/policy/media ID, tampering and rights-revocation negative cases.

`.github/workflows/qdp-tests.yml` includes an additional **cross-branch isolated job**:

1. Checkout QDP current branch.
2. Read-only checkout of pinned Group A SHA `cc1d43497aa70b6f1831b104d835c8674861e9ab` into `.peer-group-a`.
3. Copy GA code and GA-owned SQL 054/055 **only into runner's disposable checkout**; apply combined forward-only migrations in disposable PostgreSQL 17.
4. Execute `tests.test_qdp_group_a_peer_pg.PeerQDPGroupAHandoff.test_real_peer_inbox_job_ack_and_fifty_replays`.
5. Verify QDP prepared A with real local asset permission, actual GA `ga_runs=1,ga_steps=1,ga_inbox=1`, QDP receipt=1, and 50 repeated `receive` calls still leave exactly one GA run/step/inbox.

[GitHub Actions 40321549312](https://github.com/BanSins7/ContentMachine/actions/runs/40321549312) succeeded in both jobs. QDP 206/206 and one actual peer test PASS; both are limited to synthetic MP4 container **header+bytes verification**, not decoded/real playable MP4. No FFmpeg, OCR/ASR, multi-stage A extraction, paid calls, production deployment or actual SRE LIVE producer handoff was tested.

## Remaining external blockers

- The independent GA branch still has later unfinished GA-MOD-07..24; no guarantee of full Group A analysis execution or complete feature coverage.
- This is not a merge: Group A package/migrations are composed **only inside CI**. Deploying to main requires forward-only reconciliation and all current/peer check gates.
- Production must configure explicit shared roots and the correct connection factory row shapes (GA tuple rows vs QDP dict rows); missing/unknown peer capability remains unavailable.
- FIX-14 still owns Group B/C + Unified Analytics adapters; FIX-15 invalidation propagation; FIX-18/19 release QA and final gate.

No changes to `main` or Group A branch were made by FIX-13.
