# Group A / Stage A1 — первый исполняемый аналитический блок после Qualified Data

**Ветка:** `feature/group-a-technical-extraction-v1-20261023`, создана **от** `feature/qualified-data-pipeline-v1-20261023`, поскольку Group A потребляет `qdp_readable_enrichments` и `qdp_preparations`. Изменения не попадают в `main` до review + merge зависимости QDP.

**Реализовано:** локальное техническое измерение разрешённых видео через FFprobe и FFmpeg, очередь заданий, идемпотентность, права, запись в действующую схему A003, UI/API и тесты.

**Не выдаём за реализованное:** автоматический ASR/Whisper, OCR, измерение наличия субтитров, время CTA, музыку/SFX, полную обработку каруселей, семантический Group B, Score Group C. Для этих сигналов A003 сохраняет `NULL` / `unknown` / `not_run`; `analysis.state=partial`. Значение `scene_status=complete` означает полную обработку *технических границ сцен*, а не весь Group A.

## 1. Pipeline и безопасность

```text
SRE completed + approved taxonomy
    ↓
QDP Qualified Video Set → Enrichment → Analysis Preparation
    ↓
QDP item status ready/partial + A group ready
    ↓ [GET current rights, exact project/video/run, policy and media asset]
Explicit Group A Create Task (local POST)
    ↓ [transaction: immutable fingerprint, task, durable job]
Group A Worker leases ONLY group_a.analyze_video
    ↓ [lease_generation token; one external worker at most]
Allowed media path check (CM_GROUP_A_MEDIA_ROOTS)
    ↓ [local absolute path, within authorized directory, approved extension,
      <= 2 GiB, nonempty, existing]
FFprobe → duration + *display* aspect ratio (including sample aspect)
    ↓
FFmpeg select(scene) + showinfo → cut timestamps
    ↓
Boundary integrity / sorted / min-gap / ≤ 300 scenes / ≤ 600 seconds
    ↓ [fresh QDP media/source permissions rechecked AFTER FFmpeg]
DB transaction:
    video_feature_a_analyses (state=partial; scene_status=complete)
    video_feature_a_scenes (ordered intervals)
    group_a_tasks (completed; analysis_id)
    jobs (completed + fenced lock release)
    ↓
group_a_readable_results → UI / downstream consumers
```

No external HTTP, provider calls, media upload, payment, FFmpeg shell invocation, remote streaming, concurrent execution without DB lease, automatic creation for all qualified videos or snapshot write without a fresh rights gate. A task never promotes an unknown media kind or metadata-only media asset to an analyzable video.

## 2. Measured signals and exact A003 mapping

| Agreed Group A feature | Stage A1 actual output |
|---|---|
| 1 Duration | `duration_sec`, from video/format FFprobe duration |
| 2 Display aspect ratio | `aspect_ratio` after sample-aspect ratio compensation; raw width/height not stored |
| 3 Scene count | `video_feature_a_summary.scene_count` from measured scene rows |
| 4 Technical scene intervals | `video_feature_a_scenes` with contiguous start/end times |
| 5 First on-screen text | `NULL`, no OCR |
| 6 Spoken word count | `NULL`, no ASR |
| 7 Words per minute | `NULL`, no ASR/active speech measurement |
| 8 Subtitles | `unknown`, no observation |
| 9 Speech coverage | `NULL`, no speech segments |
| 10 CTA timing | `NULL`, requires independent evidence |
| 11 Timed transcript segments | None, speech_status=not_run |
| 12 Timed on-screen text | None, screen_text_status=not_run |
| 13 First spoken word | `NULL` |
| 14 Music/SFX windows | None, audio_status=not_run |

**Excluded by design:** avg_scene_sec, resolution columns, raw FPS. The underlying decoder of course observes geometry; only the agreed ratio is persisted.

## 3. Source rights and media provenance

`create_task()` joins `qdp_items`, `qdp_readable_enrichments`, and `qdp_preparations` for the same `project_id, qdp_run_id, video_id`, with group A ready, canonical `videos.content_kind IN ('video','slideshow_video')`, matching current source/policy/media asset, rights `can_analyze_media=true` and policy permission, active source, unexpired media, and `permission<>'metadata_only'`.

Worker repeats this check when claiming and **after extracting but before DB commit**. A revocation between read and write blocks persistence. A revoked result already stored is hidden by `group_a_readable_results` instead of returned as consumable evidence.

`CM_GROUP_A_MEDIA_ROOTS` is a **required explicit local directory allowlist**, paths separated by OS path separator (`;` on Windows, `:` on Linux). A symlink resolving outside that folder, non-video extension, missing file or oversized asset is rejected. Worker never treats a DB path as a URL or shell command.

Local CPU extraction uses a bounded FFprobe subprocess (30 seconds) followed by bounded FFmpeg scene detection (210 seconds); lease 300 seconds. Technical cuts do **not** represent semantic hook/problem/solution boundaries; those belong to Group B.

## 4. Idempotency and versioning

Migration `017_group_a_technical_extraction.sql` creates:
- `group_a_tasks` (QDP-scope FKs, source/media identity, fixed config/input_sha256, job_id, analysis_id, states)
- `group_a_readable_results` (live-gated view over A003 and QDP)

Repeated request with same QDP snapshot and scene parameters returns the existing task. Different parameters create separate tasks, never overwrite a completed analysis. Analysis key is `group-a:{task_id}`. Technical result A003 `analysis_version='A1'`, `method_name/method_version='group-a-local-video-v1'`, and `measurement_methods` records algorithm settings; speech/OCR/audio/CTA reason codes explain missing data.

Jobs use the existing PostgreSQL `jobs` table, their own type `group_a.analyze_video`; lease and `lease_generation` protect concurrent workers. FFmpeg executes **outside** the database transaction, while database writes are all-or-nothing. A failed/revoked media can be diagnosed and a new QDP snapshot or task created explicitly (not by mutating immutable observations).

## 5. HTTP & UI

GET `/api/projects/:pid/group-a/tasks?qdp_run_id=:uuid` — paginated list (max 100 returned).

GET `/api/projects/:pid/group-a/tasks/:uuid` — visible result + technical scene timeline if rights remain active. Local file paths are never returned in the HTTP response.

POST `/api/projects/:pid/group-a/tasks` with:
```json
{"qdp_run_id":"<canonical UUID>","video_id":123,
 "config":{"scene_threshold":"0.350","min_scene_gap_sec":"0.250"}}
```
Uses parent localhost Host/Origin and X-ContentMachine CSRF header. Explicit button "Измерить Group A" appears for Group A ready items inside the QDP view. The button starts CPU-only work and never calls paid models.

## 6. Setup and QA

Requires QDP schema 016, new schema 017, existing A003, PostgreSQL 17 and local FFmpeg + **FFprobe** on PATH. Set environment variable `CM_GROUP_A_MEDIA_ROOTS` to one or more approved folders containing local media assets.

```powershell
$env:CM_GROUP_A_MEDIA_ROOTS = 'C:\\Users\\YOU\\Downloads\\InstagramArchive'
python -m intelligence.migrate
python -m unittest discover -s tests -p "test_group_a_*.py" -v
python -m intelligence.group_a.worker --once
```

For PostgreSQL integration use **only a disposable test database** via `CM_TEST_PG_DSN`. The integration fixture stores an approved immutable SRE taxonomy and does not bypass DB immutability to clean it up. GitHub Actions workflow `.github/workflows/group-a-tests.yml` performs PostgreSQL 17 schema and behavior checks plus JS/Python syntax QA; FFmpeg command behavior can be exercised with a supplied local permitted media asset in manual end-to-end tests.

## 7. What remains for full Group A

A2 speech & subtitle: authorized audio stream, VAD, transcript, word counting and interval-union WPM with versioned offline ASR. A3 on-screen text OCR with times / sampling. A4 audio event classification. A5 carousel slide/image technical extraction. A6 downstream QA including per-platform sampling confidence, throughput and permissions/retention expiry deletion. Each must be separately permission-gated and must not report `complete` without actual evidence.
