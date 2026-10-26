# VC-MOD-08 — DTK Page[Content] Response Inspector and safe Normalizer V1

**Branch**: `feature/video-collector-v1`. **Code status:** IMPLEMENTED, **QA status:** PENDING for exact head Linux/Windows CI. Main has not been edited. No live DTK search, paid provider call, SQL write, video download or SRE classification was performed.

## Authoritative response and scope

One **already completed** `dtk.task(task_id)` local transport response is passed to `provider_inspector.inspect_dtk_search_result(answer,source_task,provider_task_id,fetched_at=...,allow_metrics=False)`. This function does **not** submit, poll, follow links or persist records. It checks the exact page boundary from the pinned Evil0ctal DTK v5.1.7 `Page[Content]` contract:

- `answer.state == 'done'` and `answer.task_id == provider_task_id` (canonical UUID).
- If present, `tool_id == 'dtk_tiktok'`; `result_meta` must be an object or omitted; if present, its platform must be `tiktok` and endpoint `tiktok.search_posts` (keyword-only).
- `answer.data.items` is a list of at most 100 items; the approved Collector task can inspect only its configured 1..50 result cap. Every overflow item is counted explicitly as rejected `local_result_cap`.
- `has_more` is strictly boolean; opaque cursor may be valid but **must never trigger next-page scheduling automatically**, and must not be persisted.
- Each candidate requires platform `tiktok`, numeric-string `content_id`, safe tiktok.com `/@handle/video/<id>` identity, nonprivate/nondeleted status, appropriate content kind and bounded author. No URL is fetched or redirected.
- Photo albums and live broadcasts are **rejected as not_video**. Absent kind stays `unknown` / **partial**, not fabricated as verified video. Unknown kinds reject, not classify by guess.
- DTK `duration_ms` is an integer in milliseconds, mapped to seconds without guessing; `created_at` is a timezone-aware ISO timestamp (NOT epoch seconds), mapped to UTC. Missing values remain `None`; malformed values are rejected. Titles are bounded; a title may fall back to the first bounded line of a description, but the full description is never retained.
- Metrics are disabled by default and are **not persisted** in this stage. When explicitly enabled for an ephemeral inspection, five fields map `play_count → views`, `digg_count → likes`, `comment_count → comments`, `share_count → shares`, `collect_count → saves`. A missing count is **null**, distinct from measured **0**. Invalid negative/bool/fractional/oversized metrics reject the item. Observation time is the verified current GET clock.

The Inspector produces an immutable in-memory `ProviderPage` of `CandidateEnvelope`s. Every candidate retains exact project, approved taxonomy publication/version, generation, source ID, provider task UUID, QV query IDs and hypothesis IDs. Duplicate video IDs on the same page count as explicit rejections, not duplicates persisted to D1.

## Versioned safe DTO for future ingestion

`intelligence/collector/inspector_contract.py` provides:

- `serialize_inspected_page(inspected: InspectedPage) -> dict`
- `parse_inspected_page(dto, *, project_id, provider_task_id) -> (ProviderPage, receipt)`

Output schema is **`collector.inspected_page/v1`**. It contains only allowlisted normalized candidate fields and exact signed-internal bookkeeping: page scope, UTC timestamps, per-candidate quality/reasons, provenance, valid/partial/rejected/overflow counts, deterministic reason histogram, and opaque-cursor **presence flag only**. It **does not carry** raw DTK JSON, signed media links, music URLs, full captions, authorization tokens, opaque cursor text, unknown provider fields, provider_request_id or private profile details.

The DTO has `payload_sha256`, calculated from canonical content using the project's existing SHA-256 helper, and parse requires that digest to match exactly. This catches accidental mutation and replay inconsistencies, **not authenticity by itself**: callers could compute another SHA. It is **NOT** a user-trusted or bearer-authenticated ingest permission. VC-MOD-09 must accept only its own Worker-owned, lease-fenced, project-scoped prepared page inside a single SQL transaction, and revalidate the source checkpoint hash before creating canonical D1 rows.

The parser also checks exact allowlisted fields, complete counters, unique accepted TikTok IDs, valid/partial state, canonical URLs, no cross-project/task lineage, candidate timestamps, deduplicated rejection indices, recomputed reason histogram, `source_id='dtk_tiktok'`, and retained flags:

```text
raw_result_persisted = False
metrics_persisted = False
ingestion_pending = True
```

The parsed `ProviderPage.next_cursor` is always **None** by design. Only its boolean presence can survive serialization; this prevents mistakenly treating the serialized record as authorization to fetch page 2. D1 ingest must decide how to persist verified observations and set checkpoint/terminal state; serializing a DTO is NOT a database commit.

## Pinned fixtures and verification

New offline fixture: `tests/fixtures/vc_dtk_inspector_page_v1.json` — four DTK-format `Content` rows representing valid video, partial video (missing title/date/duration), photo album rejection, and exact duplicate rejection, with checked telemetry. It uses no real user/provider data.

- Existing: `tests/test_video_collector_inspector_stage9.py` (DTK v5.1.7 contract, URL/ID/date/duration/author/visibility, duplicates, partial quality, metric types, overflow, pagination, no-I/O regression and Worker inspector handoff).
- New: `tests/test_video_collector_inspector_contract_v1.py` (13 offline tests: exact DTO round-trip, empty/partial/metrics, null vs zero, QA histogram, truncated pages, safe no-leak serialization, project/task mismatch, tamper/recalculated hash, invalid provenance/status/counts, pinned fixture, unsupported endpoint and tool ID).
- CI: separate VC-MOD-08 Linux step with those tests; Windows D1 smoke runs offline Python and JS checks as configured.
- Commands:
  `python -m unittest discover -s tests -p 'test_video_collector_inspector_stage9.py' -v`
  `python -m unittest discover -s tests -p 'test_video_collector_inspector_contract_v1.py' -v`
  `python -m unittest discover -s tests -v`
  `python -m compileall -q intelligence/collector`

An Actions run queued/in-progress/cancelled is **not PASS**. No live DTK response was obtained here, and no production PostgreSQL transaction was executed. Operator-approved live smoke and atomic D1 persisted receipt are later gates.

**Next:** VC-MOD-09 — one canonical D1 Discovery ingest transaction per verified page, preserving all QV query/hypothesis lineage, page sha, project ownership, and pending SRE-ready events. Then VC-MOD-10 switches Worker `completed` to require that persisted receipt (never provider done alone).
