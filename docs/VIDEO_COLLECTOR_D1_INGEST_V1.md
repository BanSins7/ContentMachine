# VC-MOD-09 — Atomic canonical Discovery D1 ingest

**Module branch:** `feature/video-collector-v1` · PR #33 · **code implemented / exact-head QA pending**. This page records actual code, not verified live ingestion. No provider POST, SRE scoring, QDP release, user-file import, metric history or cross-project authorization was performed.

## Authoritative schema and data ownership

VC-MOD-09 writes to the **existing D1 catalog** from main: `videos`, `source_video_metadata`, `discoveries` and uses the *already applied, immutable* VC-MOD-02 schema in `057_collector_discovery_outbox.sql` for scoped receipts and SRE-ready outbox. **No new video or source catalog tables, no edits to migrations 001/002/054–057.**

`intelligence/collector/ingest.py` exports `ingest_page(ctx,inspected,page_ordinal=0,connect=...)` for the **in-process dedicated Worker**, not a browser / public JSON API. `ctx` is an actual previously claimed `submitted` Collector job with `provider_task_id`, source_request BIGINT, job ID, worker ID and lease generation. Input is the ephemeral `InspectedPage` of VC-MOD-08; `serialize_inspected_page` + `parse_inspected_page` revalidate the exact project, task UUID, canonical video IDs/URLs, metadata, counters and provenance. The SHA fingerprint is an integrity check only, *never an independent authorization credential*. A user-uploaded or self-hashed JSON page is not accepted as a production ingest call.

## Single PostgreSQL transaction

```text
Bounded in-memory InspectedPage (approved 1st page, <=50 candidates)
  |
  V
BEGIN
  SELECT search_runs FOR UPDATE (must not be cancelled)
  SELECT collector_runs FOR UPDATE (project, stage='released')
  SELECT provider task, jobs, source policy + project-scoped QuerySet
  Check exact running job worker ID + lease generation + lease_until>now()
  Check currently reviewed active source: can_store_metadata,
      permissions.can_store_metadata=true, automated_collection_authorized=true,
      upstream_archive_disabled_confirmed=true
  Check source task is submitted, provider_task_id and generation/publication
  SELECT actual collector_task_queries by source_request_id; enforce
      same QV candidate IDs and hypothesis IDs as InspectedPage
  SELECT existing collector_ingest_pages FOR this task/page ordinal:
      exact project/hash/snapshot/counts/lineage => idempotent reused receipt;
      different hash or incomplete ledger => fail closed
  For each verified candidate:
      INSERT videos ('tiktok', string platform_video_id, canonical URL,
                     observed title/published_at/duration or NULL)
        ON CONFLICT(platform,platform_video_id) DO NOTHING RETURNING id
      SELECT preexisting global video_id when duplicate (never overwrite)
  INSERT immutable collector_ingest_pages ONE TIME with FINAL counters
      (provider task, project, request, page=0, SHA, worker lease generation)
  For each canonical video:
      INSERT (or safely reuse) source_video_metadata by (video,source,fetched_at)
      INSERT (or safely reuse) discoveries keyed by hash of
          source_request+platform_video_id; no BIGINT cast for QV UUID
      INSERT collector_ingest_page_videos
      INSERT collector_video_hypotheses and collector_page_video_queries
          for EVERY verified QV candidate→hypothesis→video relationship
      INSERT collector_sre_outbox project-scoped durable pending event
          with verified D1 video/discovery/metadata IDs and
          relevance_state='not_evaluated', evidence_scope='metadata_only'
COMMIT
  |
  V
Return real D1 receipt (page_id, saved/new/existing/rejected/overflow counts,
                       page_sha256, sre_events, d1_persisted=true,
                       provider_completed=false)
```

**Critical**: `collector_ingest_pages` is immutable via a trigger. The code *never UPDATEs it*: real existing/new video counts are computed by `videos` INSERT/ON CONFLICT in the *same transaction* **before** the one immutable receipt INSERT. Its `duplicate_count+inserted_count=saved_count` SQL check is satisfied. Any DB error in metadata/discovery/lineage/outbox rolls back even the prior video UPSERTs and receipt.

**Bounded page semantics**: Only page ordinal 0 is currently approved (source task max_pages=1). The provider may return more than `max_results_per_page` rows; overflow is **unexamined**, not an examined rejection. `collector_ingest_pages.rejected_count` stores rejected *within examined count* (`inspected.rejected_count - overflow_count`); `qa_summary.overflow_count` retains overflow separately with the total rejected count. No page 2 or raw cursor is stored. `cursor_sha256` is presence-only hashed data; opaque cursor cannot leak into D1.

**No metric claims**: `views/likes/comments/shares/saves` and `observed_at` from an ephemeral page are **not written** to `metric_snapshots`, even if temporarily allowed for inspection. Missing date, creator, duration and title stay NULL; no synthesized price, relevance, paid status, creator biography or transcript. Discovery remains an unclassified observation.

**Multiple contexts**: Physical `videos` IDs are global and unique by platform/video ID. Each Collector `source_request_id` has its own D1 `discoveries`, project checkpoint and candidate→hypothesis links. Two projects can discover the same canonical video without sharing project-specific classification. QV UUID identifiers remain TEXT, never cast to `search_queries.id BIGINT`.

**SRE**: One event per actual page/video association is inserted into the *existing* `collector_sre_outbox` only. Payload contains schema version, project, delivery key, page SHA, source request, verified D1 IDs, all candidate/hypothesis IDs, content kind, quality, `relevance_state='not_evaluated'`. This is a queued *handoff*, not a claim that relevance is approved, SRE workspace exists, or analytics delivery has occurred.

## Replay, collision, concurrency and operator checks

- Every new page transaction holds the locked `search_runs`, `collector_runs` and provider task/job rows and checks worker lease generation/expiry with DB `now()`. Revoked source rights/project/taxonomy or cancelled runs block before D1 writes.
- Identical `(provider_task_row_id,page_ordinal)` and identical SHA can replay once the stored videos/links/outbox count passes integrity checks; any different SHA/snapshot/counter/project/identity returns 409.
- DB `UNIQUE` constraints dedupe provider page receipt and event identity; SQL errors roll the **entire page** back. Worker recovery must never re-submit a provider POST to repair a database state.
- The function does NOT mark the job `completed`. **VC-MOD-10** must integrate this return value with dedicated Worker so `completed` occurs only after a committed receipt is verified. Currently the preexisting worker still finishes on provider `done`, a known outstanding cross-stage gap. Do not claim full end-to-end finished yet.

## Tests and QA

Added `tests/test_video_collector_ingest_v1.py` with ten offline transaction-fake tests for canonical writes, exact scope, immutable counters, replay, changed page SHA, empty/nonvideo input, globally preexisting D1 video, rollback from metadata/lineage/SRE failure, project/cancellation/lease/policy gating and QV TEXT lineage. The fixture does NOT prove PostgreSQL17 MVCC or concurrent uniqueness behavior.

Run dedicated Linux QA plus existing tests:

```sh
python -m unittest discover -s tests -p 'test_video_collector_ingest_v1.py' -v
python -m unittest discover -s tests -p 'test_video_collector_inspector_contract_v1.py' -v
python -m unittest discover -s tests -p 'test_video_collector_schema_v1.py' -v
python -m unittest discover -s tests -v
```

**QA PENDING** exact current SHA Linux/Windows workflow and disposable PostgreSQL17 seeded full C9→SQG→QV→Collector integration. Do not write PASS merely because prior commits succeeded or a run was queued/cancelled. Actual provider and user workspace smoke remains opt-in.

**Next:** VC-MOD-10: wire the completed provider inspector to this committed D1 receipt in the Worker, with lease fencing and safe recovery. No provider `done` must equal successful canonical D1 ingest without durable checkpoints.
