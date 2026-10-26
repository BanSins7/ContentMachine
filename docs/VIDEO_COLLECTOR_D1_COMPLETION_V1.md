# VC-MOD-10 — Worker completion **only after** committed Discovery D1 receipt

**Branch:** `feature/video-collector-v1` · **Scope:** Collector code, independent of main / Group A/B/C. **Status:** IMPLEMENTED IN CODE; exact-head Linux/Windows/PG17 QA PENDING. Nothing calls live DTK or creates real provider charges in tests.

## The former bug and explicit changed guarantee

Previously the Collector Worker saw a completed DTK GET (`state=done`), inspected its raw response and immediately called `_update_after_provider(next_state="completed")`. This set `collector_provider_tasks.state=completed`, `source_requests.state=succeeded` and `jobs.state=completed` without a single D1 video, source metadata, discovery occurrence or durable QV/SRE provenance. The Stage9 summary explicitly still had `ingestion_pending=true`; operator status nevertheless claimed completion.

That path is **disabled** in VC-MOD-10: `_update_after_provider(next_state="completed")` now fails with `d1_receipt_required`. The **only** completion transition is `intelligence/collector/completion.py::finish_from_checkpoint`.

## Worker state machine

```text
already-released Collector job (only dedicated Worker may claim)
    |
    | source_request=started, provider task=submit_intent → submit once
    |
  submitted  -- current known provider_task_id (never blind POST retry)
    |
    +---- check existing committed D1 receipt *BEFORE provider GET*
    |        |  page 0 checkpoint matches project, run, source_request,
    |        |  provider_task_id, saved_count and all QV/SRE links
    |        +---- yes → lease-fenced COMPLETE in one PostgreSQL transaction
    |
    +---- no checkpoint → GET existing task_id
               | queued/running → deferred GET only
               | unknown / GET failure → deferred GET only
               | failed/cancelled → FAILED, never completed
               | done → pinned DTK Inspector
                         | invalid → inspection_failed (no D1 claims)
                         | valid → VC-MOD-09 ingest_page
                                      | transaction failed → ingest_pending
                                      | committed receipt → finish_from_checkpoint
                                                | atomic ACK → completed
                                                | DB ACK lost → ingest_pending
                                                  (next claim reuses checkpoint)
```

**No double submit:** a crash after provider POST and before task ID persistence still creates `unknown` and requires operator/manual reconciliation (VC-MOD-07). A known existing DTK task uses GET only. A crash after D1 commit but before Worker ACK never re-POSTs or re-inspects the GET response with a new timestamp; the next claim detects the immutable page checkpoint and can ACK safely without another provider call.

## Exact SQL terminal gate

`finish_from_checkpoint(ctx,receipt=None,connect=...)` serializes cancellation and terminal ACK under the same lock order: `search_runs FOR UPDATE → collector_runs FOR UPDATE → provider task/jobs FOR UPDATE`.

The one database transaction verifies:

- Run is not cancelled; `collector_runs.stage='released'`.
- Task and job identity match the live Worker (`provider_task_row_id,run_id,source_request_id,job_id,locked_by,lease_generation,lease_until>now()`).
- A genuine `collector_ingest_pages` immutable row exists for `page_ordinal=0`, same project, run, task/request, provider task UUID and SHA. A matching optional VC-MOD-09 in-process receipt cannot override missing actual SQL evidence.
- D1 checkpoint `saved_count=accepted_count=inserted_count+duplicate_count`.
- Exactly `saved_count` canonical `collector_ingest_page_videos` links; exactly `saved_count * approved_QV_query_count` `collector_page_video_queries` rows; exactly `saved_count` durable `collector_sre_outbox` events. No synthetic SRE decision or media evidence is required.
- Only **after** those assertions, `collector_provider_tasks` becomes `completed` with a safe bounded `collector.d1_completion/v1` result, `source_requests` becomes `succeeded`, and `jobs` becomes `completed`; every SQL update has a `RETURNING` check so an unexpected race rolls back the entire transition. `_finalize` computes completed/partial/failed/cancelled/blocked from resulting durable task states.
- Legacy provider-only `completed` rows without a D1 checkpoint are explicitly blocked by `_finalize`; no manufactured data is inserted.

**Important transaction boundary:** the D1 page write and the terminal job ACK are **two separately committed transactions**. This is intentional for safe after-crash recovery and is not a claim that the external provider GET is atomic with PostgreSQL. All observable terminal states require the existing page receipt; if the ACK fails after committed D1, the next Worker claim uses the checkpoint and executes only the SQL ACK.

## Status API: actually committed counts

`repository.get_run(project_id,run_id)` now reads `collector_ingest_pages` and `collector_sre_outbox` scoped by the actual project and run, exposing:

`d1_persisted_pages`, `d1_saved_videos`, `d1_new_videos`, `d1_duplicate_videos`, `sre_ready_events`, `ingest_pending_tasks`, `d1_completion_verified`, `d1_integrity_warning`.

A DTK `done` response alone contributes **zero** persisted videos. A legacy row reporting terminal `completed` but missing receipts exposes an explicit integrity warning, rather than pretending that it saved results. The SRE-ready events are queued and **NOT** evidence of classification, indexing, human review or analytics completion.

## Tests and evidence

- `tests/test_video_collector_completion_v1.py`: 14 new offline tests on actual checkpoint requirement, atomic three-entity ACK, crash-then-replay **without GET or POST**, cancelled/held runs, stale lease, missing D1/query/outbox rows, forged SHA/project/page count, rollback on DB update failures, forbidden legacy direct completion, invalid Inspector, failed ingest and lost ACK.
- `tests/test_video_collector_status_v1.py`: 4 new offline tests on read-only D1 status/counts, pending provider without persisted D1, false completed legacy integrity warning and project scoping.
- `tests/test_video_collector_worker_stage8.py`, `tests/test_video_collector_worker_resilience_v1.py`, `tests/test_video_collector_inspector_stage9.py`: changed to assert real D1 receipt gate or mocked safe completion, rather than old provider-only success.
- Dedicated Linux CI step runs Stage8, Stage9, resilience, D1 ingest, 14 completion and 4 status tests, plus the repository's full unit+PostgreSQL17 migration regression jobs.
- Real parallel PostgreSQL17 cancellation/reclaim race, end-to-end media observations and live DTK opt-in smoke are **NOT yet verified**. Do not mark unexecuted CI as PASS. The project baseline CI has been queued on latest prior SHAs; cancelling obsolete runs is not a test success.

**Next:** VC-MOD-11 — reliable project-scoped SRE-ready delivery/ACK from the single `collector_sre_outbox`, never a second queue or fake relevance assertion.
