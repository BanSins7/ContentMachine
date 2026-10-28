# VC-MOD-12 — project-scoped Collector results, D1 trace and operator UI

**Branch:** `feature/video-collector-v1` · **Code status:** IMPLEMENTED · **QA:** exact-HEAD Linux/Windows/PG17 pending. **No provider POST/GET, real SRE delivery, unreviewed claim or database migration in this stage.**

## Owner site (Discovery)

The existing Content Machine `site/index.html` *Discovery → Video Collector* panel still offers QV manifests, coverage, shard selection, read-only preview, separately confirmed Held Commit and Explicit Release. VC-MOD-12 adds an explicit, read-only results area after those controls:

- **Показать запуски проекта** — latest Collector runs from PostgreSQL for the project explicitly loaded in that panel, not global search runs; separate **Ещё запуски** keyset pagination.
- **Выбери запуск** — chooses a server-returned run ID. Automatically reads the current project/run's truthful run status, QV→DTK→D1→SRE trace and first page of **actually saved D1 videos**. A missing D1 checkpoint yields an empty result, never an invented video card.
- **Трасса** — refreshes safe provenance on demand: SQG generation, RTB taxonomy publication/version/hash, QV manifest/validation run/shard or an explicit `not_linked`, provider-task and source-request state, D1 page/inserted/duplicate counts, SRE event pending/leased/delivered/blocked. `C9/SP verified here = false` is explicit, because the endpoint does not re-read historical C9 inputs.
- **Реально сохранённые видео / Следующие видео** — keyset-paged canonical D1 video ID/URL/title/type, source metadata timestamp, discovery occurrence, QV candidate UUIDs and hypothesis IDs, independent SRE delivery state. No raw TikTok response, signed media URL, metric snapshot, unverified trend score, invented classification or paid cost.
- UI never automatically executes a provider POST, never opens page 2 of DTK, never schedules polling and never infers a relevance match. Only user interactions read the local PostgreSQL status. DOM is populated with `textContent`/created nodes, never `innerHTML`; outbound links must match strict TikTok canonical URL and use `noopener noreferrer`.

**Important:** Existing `site/discovery.js` remains separate; VC-MOD-12 does not silently rewrite the generic D1 discovery interface or change source-policy rules.

## Read-only HTTP API

New `intelligence/collector/results_api.py` exposes project-scoped, bounded read functions. All routes are served by the existing **same-origin** `app.py` GET authorization, and return `{"ok":true,...}` on success; the same backend error boundary uses bounded messages. Unknown params, wrong project, invalid cursor and excessive limit are rejected.

| Route | Backing SQL truth | Pagination |
| --- | --- | --- |
| `GET /api/intelligence/collector/runs?project_id=&limit=&before_run_id=` | `collector_runs` joined to `search_runs`, Collector plan, published QV manifest if linked, independent D1 page/video and SRE event counts | numeric `before_run_id`, newest first, 1–50 |
| `GET /api/intelligence/collector/runs/{id}?project_id=` | existing `repository.get_run` exposes persisted D1 pages/video counts, provider/lease and integrity warnings | exact run |
| `GET /api/intelligence/collector/runs/{id}/results?project_id=&limit=&after=` | **only** `collector_ingest_pages JOIN collector_ingest_page_videos JOIN videos JOIN discoveries JOIN source_video_metadata`, optional SRE event; actual page/video query and hypothesis associations | cursor `page_id.video_id`, ordered ascending; 1–50 |
| `GET /api/intelligence/collector/runs/{id}/trace?project_id=` | DB-stored `collector_query_sets/sqg_generations/collector_query_shards/collector_qv_manifests`, approved `collector_task_queries`, provider states; D1 and SRE counts from independent correlated subqueries | exact run |
| Existing manifests list/get | previously implemented immutable QV/RTB coverage and shard IDs | no change |

The /results and /trace endpoints first call the existing `_run(conn,project_id,run_id)` project gate; another project's run behaves as not found. List queries always filter `collector_runs.project_id`; D1 references are project-scoped. No SQL write anywhere in these read methods.

**No multiplication bug:** counting pages or saved videos by joining page receipts to one event per video can multiply sums. The /trace SQL instead computes independent correlated counts/sums per provider task and separate event-state counts. No hypothetical aggregate success from task `done`. `SRE delivered` means only durable transport ACK; it is **never** classified, approved, relevant, or SRE completed.

**Empty/recovery states:** `no_queries` manifests and all_rejected coverage remain visible through existing published manifest APIs; no shards means no executable preview. A run with tasks but zero D1 checkpoints shows zero videos and pending/unknown/error task states. `d1_integrity_warning` from `get_run` is surfaced instead of claiming completeness. An outbox row with no SRE adapter stays pending and `relevance_state=not_evaluated`. A historical run without a published manifest is marked `not_linked`, not a fabricated QV publication. If the same QuerySet occurs in multiple historical manifests, a LATERAL single-row lookup selects the latest matching manifest and marks its linkage as `latest_matching_query_set`, rather than multiplying run rows or claiming a directly stored operator-selected manifest.

**Security and privacy:** paths and cursors are type-checked; arbitrary query-set JSON is not accepted in these new GET routes. DB-backed title is displayed as text; web links are validated to HTTPS TikTok official canonical pattern. Provider transport data, tokens, raw bodies, quota internals and ephemeral worker lease material are not serialized.

## Tests and verification status

- New `tests/test_video_collector_results_v1.py` — 12 offline tests for latest run paging, numeric cursors, project scope, DB-only results, exact next video, empty runs, original QV/SQG provenance, noninflated SRE counts, malformed HTTP params, safe operator UI and **zero provider interactions**.
- Existing `tests/test_video_collector_operator_ui_v1.py` and `test_video_collector_manifest_access_v1.py` still cover Preview→Held Commit→Explicit Release. Original D1 receipt and SRE outbox workflows are unchanged.
- Dedicated Linux CI step: `python -m unittest discover -s tests -p 'test_video_collector_results_v1.py' -v`, UI test, manifest access test and `node --check site/collector.js`; full project Python regression/migration checks remain in CI.
- **QA PENDING:** GitHub Actions exact SHA, real disposable PostgreSQL17 tenant-scoped seeded SQL tests, browser walkthrough and live SRE M35 handshake. A queued/cancelled run is **not PASS**.

**Next:** VC-MOD-13 recovery/quotas/cancellation and fault injection, then VC-MOD-14 standalone module packaging/flags, VC-MOD-15 comprehensive PG17 regression, VC-MOD-16 merge-ready gate.
