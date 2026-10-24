# SRE Production V1 — полный план реализации

> Статус: SPEC / IMPLEMENTATION NOT STARTED. Ветка: feature/sre-production-v1-20261023. Основание: main 4e51de5fc59f52d83e4fa71492d2d767689cd019 от 2026-10-23. Этот файл — источник истины для последующих этапов разработки SRE; при реализации изменять статусы в отдельном tracker, а спецификацию версионировать. Ни одна paid operation не разрешена настоящим документом.

## 0. Итоговая цель и критерий завершения

Создать автономный Semantic Relevance Engine (SRE) Content Machine, работающий по цепочке:
approved Research Taxonomy Builder deep publication -> Collector/D1 canonical video + provenance -> rights-gated content snapshot -> embedding ranking (optional) -> structured LLM classification -> evidence verification -> immutable per-hypothesis results -> human review / gold labels -> experiments -> outbox to Analytics A/B/C.

Полностью готовый SRE должен:
1. Обрабатывать реальные/fixture видео D1 по неизменяемой human-approved Taxonomy с полной трассировкой до исходной гипотезы и фактов.
2. Определять match/no_match/uncertain, поддерживать несколько match для одного видео, отличать not_evaluated от no_match и технические ошибки от семантической неопределённости.
3. Сохранять проверенное evidence (цитата и привязка к точной версии источника), model and prompt hashes, usage/cost, run lineage и человеческие исправления.
4. Работать с fake adapters end-to-end без внешнего доступа и с real OpenRouter adapters строго после отдельной сметы и согласия.
5. Сохранять задания после рестарта, не повторять неоднозначные платные запросы и блокировать операцию при отзыве прав / истечении бюджета.
6. Предоставлять localhost API и интерфейс SRE Studio с историей, фильтрами, разметкой, метриками, затратами.
7. Передавать результаты Analytics по явным, идемпотентным событиям, не присваивая себе функции Performance C и Features A/B.

Release Gate: новый чистый PostgreSQL 17, миграции, Python 3.12 unittest, Node 22 UI tests, Windows smoke, offline full E2E, restart/rights/budget chaos, reproducible benchmark, controlled opt-in live paid smoke и review PR. Не считать модуль production complete по наличию одних таблиц/контрактов.

## 1. Текущее состояние репозитория и запрет дублирования

Проверено на base SHA:
- main: intelligence/relevance/contracts.py, pipeline.py, presets.py, repository.py, storage.py, experiments.py, builder.py, fact_review.py.
- RTB: intelligence/taxonomy_builder/sre_bridge.py, deep_repository.py. Глубокая публикация создаёт approved tb_deep_publications, tb_taxonomy_publications и атомарно обновляет sre_taxonomy_versions/categories, tb_sre_taxonomy_links.
- SRE migration group: 006_discovery_sre_workspace.sql ... 012_tb_sre_taxonomy_integration.sql, включая immutable taxonomies, human labels, rights/readable view, stage execution journal.
- D1: intelligence/discovery/service.py canonical videos/discoveries/source_video_metadata/source_policy_versions, D1 rights.
- Search Planner: Stage A+B в main; прочитать существующие budget_manager.py/repository.py для паттернов, НЕ переиспользовать их quote/consent как SRE-разрешение.
- UI: app.py ThreadingHTTPServer; source files in site/; PostgreSQL migrator sorts file names lexicographically and verifies file checksum.
- Другие ветки: open draft PR #20 feature/video-collector-dev-20261023; PR #10 preview; PR #13 DTK; PR #6 historical SRE draft. Не merge/cherry-pick все эти ветки автоматически.

Текущий relevance/pipeline.py использует injected model_adapters (без реальных API, runtime, DB or credentials), embedding только ранжирует по умолчанию, LLM returns exact categories + source-backed evidence. Preserve behavior as compatibility baseline.

## 2. Ownership & scope

RTB владеет семантикой дерева, происхождением и human approval; SRE может читать, классифицировать, выдавать неавтоматические предложения на review, но НЕ менять RTB.
Search Planner владеет поисковыми запросами; SRE может связывать discovery с query lineage, но НЕ генерировать поисковые запросы.
Video Collector владеет поиском, provider-run metadata и ingestion delivery; SRE не выполняет scraping/download.
Discovery D1 владеет глобальными video IDs, source-policy, media rights, provenance и dataset rows; SRE владеет только project-video association, content snapshots и оценками релевантности.
SRE не выводит viral/breakout/engagement score и не выдаёт performance metrics.
Features A/B поставляют только явно разрешённые транскрипты/описания сцен, если/когда работающие producers появятся.
Performance C и downstream consumers используют approved/verified SRE classifications, с сохранением scope и rights.

В v1 проектные ограничения текущих contracts: niche_code=health_fitness, research_goal=maximize_views. Не расширять нишу неявно.

## 3. Раздельная ветка и интеграция

Имя: feature/sre-production-v1-20261023; branch point SHA указан выше. Разработка ТОЛЬКО в ней. Держать main чистым. Создать draft PR в main после первого production-code milestone, затем обновлять его; не объединять до Release Gate. Коммиты small, scoped и с тестами; желательно один milestone = один review checkpoint.
Перед каждым milestone проверить новый main SHA, текущие open PR и каталог migrations. Периодически синхронизировать main в SRE branch отдельным явно названным merge commit, без force-push и без перезаписи чужих изменений.
Если параллельные изменения затронули app.py, site/, migration numbering либо intelligence/discovery, сначала заново читать live versions и согласовать API; никогда не переписывать соседнюю subsystem целиком.
Merge стратегии: staged commit chain в одной feature branch; draft PR остаётся WIP; финальный merge только после согласованных checks. Для дальнейших независимых работ можно создать дочерние feature branches от актуальной SRE branch, но не создавать их без потребности.
Baseline status file: docs/SRE_IMPLEMENTATION_TRACKER_V1.md. При каждом этапе менять status TODO -> IN_PROGRESS -> DONE и записывать commit SHA, tests и blockers.

## 4. Точные доменные ID и границы

project_id: существующий canonical 32 hex from projects.py.
video_id: D1 videos.id BIGINT. Никакой второй video catalog.
taxonomy: tb_deep_publications/run_id/builder_publication_id и tb_sre_taxonomy_links.sre_taxonomy_version_id.
source hypothesis id: RTB tree node.id (строковый source_node_id), RTB kind=hypothesis.
category_key: UUID5 mapped by deep_repository.py, stored in sre_taxonomy_categories.category_key.
direction / branch: только context и derived grouping; классификация непосредственно по hypothesis leaf. Если RTB policy допускает неклассические листья, отмечать unsupported and fail closed.
classification_run_id: sre_classification_runs.id UUID, pinned taxonomy_version_id + pipeline_id + dataset snapshot.
content_version_id: sre_content_versions.id, pinned for evidence and downstream lineage.
provider_attempt_id: отдельная попытка в immutable ledger; уникальный dispatch identity.
discovery_id/source_request_id/search_query_id: nullable external lineage provided by D1 or collector, always verified, never trusted merely because included in client payload.

Одна пара (video_id, hypothesis category_key) получает отдельное semantic verdict within a run; одно видео может иметь 0..N matches; parents displayed by roll-up of leaf match statuses, never fake LLM verdict.

## 5. Contract V1: Collector/D1 -> SRE (не требовать готовность Collector)

Entry point: intake_from_discovery(project_id, video_id, discovery_id optional, source_id, source_policy_version_id, search_query_id optional, idempotency_key, requested_mode).
Server reads matching D1 rows in one repeatable-read DB transaction and validates project association/collecting run provenance, policy, rights and retention. No client-provided content/provenance is authoritative. Synthetic adapter must call the SAME service with fixtures backed by D1 test rows.
Required response: (project_id, video_id, source_id, source_policy_version_id, content_version_id, content_sha256, completeness, allowed_content_fields, expiry, lineage reference).
Supported completeness: metadata_only; text_enriched; multimodal_enriched. Actual old DB completeness enum is full/partial/metadata_only; implement explicit mapping in an additive migration or adapter; do not silently mix enums.
Fields: title, description, hashtags, transcript segments, scene text segments. No automatic download; transcript/scene only from trusted producer with media_asset rights and derived-content provenance. NO inference of full multimodal capability from existing feature tables.

Metadata storage permission is NOT equivalent to permission to export content to third-party LLM. Add explicit SRE-processing policy and provider transmission restriction (capability, user consent, source terms, data residency if applicable); default deny if unknown. Before dispatch AND publication recheck current policy/expiry.
Retention policy covers input content, embeddings, prompt/output portions with source quotes, logs, review snapshots and downstream materialized views. On revocation: immediately hide in queries, stop queued work, issue deletion/tombstone workflow and downstream invalidation; retain only legally permitted minimal audit hashes/IDs.
Never store provider secrets or unrestricted raw media in SRE JSONB.

## 6. Contract V1: approved RTB -> SRE

Implement taxonomy_adapter.py, taxonomy_snapshot.py, taxonomy_mapping.py.
Read exact approved deep publication using tb_deep_publications JOIN tb_taxonomy_publications, two reviewed human gates, tb_sre_taxonomy_links and approved facts; project-scoped and immutable. Verify schema and linkage, project, IDs, parent topology, fact provenance, status, hash. Preserve all 11 RTB node fields id,parent_id,kind,relation,source_fact_ids,name,rationale,definition,include,exclude,examples. Combine with direction and branch context; never truncate required rules. Add hash of fully canonicalized final classifier payload, not merely old flat names.
Map source_node_id -> category_key using actual mirrored hierarchy.rules.hierarchy and tb_sre_taxonomy_links; do not regenerate UUID with a different seed.
Reject flat-only RTB for full SRE v1, unsupported/ambiguous mixed hierarchy, unapproved facts/reviews, cross-project links or duplicated node IDs. On new RTB version, old runs remain pinned; reclassification new run only.

## 7. Contract V1: classifier output

Inputs: run_id, video content_version_id/hash and rights snapshot, taxonomy_version_id/source publication checksum, leaf category list and ancestor context, pipeline config pinned, provider/model/prompt/output-schema IDs and hashes.
Output JSON: schema_version=sre-labels/v1, labels=array exactly one per requested category_key; each label has decision(match/no_match/uncertain), nonempty reason, evidence[]. Evidence source=title/description/hashtag/transcript/scene; quote exact per approved normalized match; optional verified segment index/time offsets.
Validation reuses contracts.validate_labels/validate_evidence; reject missing/duplicate keys; reject fabricated quote/timestamp/out-of-bounds; positive match MUST carry evidence. Include source + content_version_id in persisted association.
Technical outcomes: queued/reserved/dispatch_intent/completed/skipped/invalid_output/provider_error/uncertain_dispatch separate from semantic decision. A skipped hypothesis -> not_evaluated, never no_match. Provider timeouts/errors never semantic uncertain. Reject prompt injection in video data by separating untrusted content from instructions.
For no_match, require useful nonempty reason and sufficiently complete evaluation; weak metadata or information gaps should favor uncertain, not fabricated negative certainty.
Do not promise evidence substring check proves semantic entailment: add deterministic quote grounding + optional independent semantic audit/human review.

## 8. Semantic pipeline and batching

Modes:
A. strong_only, initially fake adapter; default baseline for quality.
B. fast_then_strong: fast evaluates all requested leaf hypotheses, strong only uncertain/flagged.
C. embedding_fast_strong: embeddings rank categories; allow_candidate_drop=false until signed benchmark. If enabled later, dropped become not_evaluated.
Embedding cache keyed content_hash + taxonomy/prompt normalization + provider/model/version + rights boundary, with cleanup. No pgvector in baseline: deterministic cosine Python over bounded approved Taxonomy; add pgvector later only after performance evidence.
Prevent invented embedding recall: calibration MUST be verified against frozen gold benchmark in DB including dataset_id, model_ref/version, taxonomy_sha, data/content modes and measured recall; user-provided number not trusted.
Long contexts: deterministic packing by token/byte budget, no silent cutting of definitions/exclusions or transcript evidence; reject oversized tasks or split along explicit segment boundaries while preserving evidence pointer.
Adaptive batching: max hypotheses / request and max payload tokens enforced, each batch exact inputs + canonical input hash; classify complete expected ID set or fail batch. Strong second stage receives independent original evidence and rules to reduce anchoring.
No automatic paid retry for unknown dispatch outcome. Rate limit concurrency globally/per project, quota, circuit breaker, backoff only for verified requests never sent.

## 9. Run lifecycle and financial consent

Run (target): draft/awaiting_authorization -> queued -> running -> completed|partial; paused_budget, paused_rights, uncertain, failed, cancelled. Add SQL check transitions via service and guards; existing run statuses in schema must be extended additively (preserve backwards tests). Zero paid calls during preview, quote, approval or offline fixtures.
Each run pins: project, publication, approved taxonomy, dataset items/content versions, pipeline config, model catalog snapshot+prices, prompt/schema digests, mode, budget, limits and idem_key.
Quote created server-side from pinned full request/batch shapes, verifies fresh real model catalog and supports Decimal USD precision >= 8 dp. Disclose lower/expected/high estimates and maximum cap; pricing can change, fail closed on stale quote. Include embedding calls as paid operations when relevant.
Separate SRE quote/consent; neither RTB nor Search Planner consent authorizes SRE. Decimal ledger invariant: 0 <= spent + reserved <= authorized, under row lock. Handle unknown usage as uncertain, never zero.
Attempt states: reserved -> dispatch_intent -> completed | failed_known | uncertain; before dispatch reserved -> abandoned_before_dispatch. Durable dispatch intent committed BEFORE paid network POST. Workers claim with lease/fencing; no automatic replay of dispatch_intent if worker crashes; retain reservation and reconcile by provider evidence or manual admin. If provider lacks lookup, manual controlled resolution required. Append-only receipt with usage/cost and charge even when invalid JSON returned.
Cancellation stops future calls but does not pretend to abort a request already in flight or refund provider charge.
Auto-update: coordinate paid SRE critical section with auto_update busy guard; don't restart active paid workers; after restart reconcile lease journal.

## 10. PostgreSQL implementation, additive only

Before coding next migration determine max existing filename again; provisional name: 016_sre_runtime.sql. NEVER edit 001–015 checksummed existing migrations.
Existing: sre_taxonomy_versions, sre_taxonomy_categories, sre_classification_runs, sre_classification_results, sre_stage_executions, sre_content_versions, sre_human_labels, sre_experiments, sre_project_videos, sre_pipeline_configs.
Proposed new tables / extensions:
- sre_hypothesis_links: mapping project/publication/taxonomy/category/source_node + ancestry SHA; unique project+taxonomy+category and run context.
- sre_source_links: project, video, discovery/source_request/search_query provenance verified against D1.
- sre_run_items: run_id, project_id, video_id, content_version_id, immutable input digest, state/lease generation/last_error; unique run+video.
- sre_batches: run_id, video_id, stage, candidate set, request sha256, expected IDs, state.
- sre_provider_attempts: stage/batch, dispatch identity, state, reserved/actual USD NUMERIC(20,8), provider request ID, timestamps, usage, receipt digest, reason.
- sre_quotes, sre_consents, sre_budget_events: pinned price/estimate, TTL, user authorization, spent/reserved, immutable audit trail.
- sre_embedding_cache: model/text hash, provenance and expires_at; no cross-rights cache leakage.
- sre_review_queue: reason/priority/status, unique active review target, project scope.
- sre_quality_snapshots: benchmark and candidate/quality metrics with lineage.
- sre_events, sre_outbox: event idempotency, delivery state, downstream rights revocation.
- Add run control columns/statuses and stage execution associations/precision as needed.

All composite FKs project-scoped, sensible unique indexes, CHECKs, partial indexes for current/active labels, immutable triggers for approved content and price quotes, append-only audit. Use SELECT FOR UPDATE SKIP LOCKED for queue, explicit isolation/fencing and transaction-scope locking. Never hold SQL transaction during network request.
Migration tests: fresh DB, upgrade existing populated DB, repeated migrate no-op, immutable old file checksum, concurrent workers, rights revocation and foreign-project references.

## 11. Worker and orchestration

Modules: run_manager.py; execution_manager.py; worker.py; recovery_manager.py; attempt_repository.py; events.py; observability.py.
Create run -> materialize immutable dataset items -> quote/consent -> enqueue -> claim -> rights check -> preflight-> reserve -> commit dispatch intent -> provider call -> output verification -> settlement + classification results + stage journal + outbox in one transaction where possible.
If batch fails known with zero/verified charge, store failure + correct charged amount. If ambiguous, mark uncertain and retain reserve. A run may be partial; item-level failures visible.
Complete run ONLY when all planned leaves either classified (match/no_match/uncertain) or explicitly not_evaluated/failed with counted unresolved items; never write synthetic negatives. Worker launch wired to app lifecycle similar D1 but avoid interfering with D1 locks or paid Taxonomy jobs.
No browser in-memory state as canonical status. Avoid launching paid tasks on app startup without valid consent. Build recovery test injecting crashes at each commit/network boundary.

## 12. Model registry and OpenRouter integration

Reuse existing catalog fetch/verification patterns, not unverified manually entered model names. Pin model_id/version/pricing snapshot/context length, structured-output transport availability, capability, prompt hash and schema hash. Default real paid adapter disabled unless explicit authorization. Model aliases are stage-local and pinned.
Use provider HTTPS, short explicit timeout choices, no redirected credential leakage, restrictive response byte limits, redaction of secrets and limited diagnostic raw output. One POST per paid attempt, no silent retries by HTTP client.
Test with fake adapter and stub HTTP server, invalid JSON, timeout-before-dispatch, timeout-after-dispatch, pricing change, missing usage, unexpected model response, malformed citations. Real paid smoke only with user-approved small limit and logged receipt.

## 13. API and UI

Backend: intelligence/relevance/http_api.py; route integration into app.py with unchanged localhost Host/Origin, X-ContentMachine mutation guard; no untrusted user project_lookup injection.
Routes under /api/projects/{project_id}/sre:
GET state; GET taxonomy; GET pipeline; POST pipeline/draft; POST pipeline/approve;
POST dataset/preview; POST quotes; POST consents; POST consents/revoke;
POST runs; GET runs; GET runs/{id}; POST runs/{id}/cancel; GET runs/{id}/results;
GET review-queue; POST human-labels; GET experiments; POST experiments (explicit benchmark).
GETs must NEVER spend money or change state; responses paginated, filterable, sanitized, with exact totals.
Frontend: site/relevance.js, relevance-studio.js, relevance-run.js, relevance-review.js, relevance-experiments.js, relevance.css; add static routes explicitly to app.py, check site/index.html / navigation and Node js tests.
UI shows publication checksum/version, model+pinned quote, expected/high cost and authorized cap, cost spent/reserved/uncertain, item states, source completeness, evidence quotes/segment timestamps, semantically uncertain vs provider failure, verification failures, human changes, historical run diff, benchmark metrics. No inert controls or unauthorized automatic spend.

## 14. Human review, gold benchmark and experiments

Existing storage.record_human_label appends and supersedes current; preserve audit and original model result. Human match/no_match/uncertain stored with reviewer, reason, content/taxonomy lineage. Multi-review conflicts and adjudication policy, sampling from high-confidence predictions too, not only uncertain cases.
Gold sampling must cover platforms/languages, metadata vs transcript, hard negatives/overlapping hypotheses, multi-label video, missing content, duplicate creator/repost families. Split train/dev/test without leakage; freeze gold snapshot and record digest.
Calculate precision, recall, F1, coverage, abstention, candidate recall, evidence structural validity, price/video, latency p50,p95, human disagreement, error rate; metrics separately per mode/language. Unknown gold excluded, not_evaluated penalizes positive recall as existing experiments.py.
Recall gating for candidate pruning enforced on signed immutable benchmark, not fabricated config.
Feedback to RTB is proposal only: no automatic mutation of approved taxonomy or AI facts.

## 15. Downstream consumer contract

Transactional outbox event types: sre.classification_run_completed, sre.classification_item_completed, sre.classification_invalidated, sre.rights_revoked.
Event references project_id, video_id, run_id, taxonomy_version_id, RTB publication, content_version_id, output hash, leaf match/uncertain/not_evaluated keys, rights version, schema_version.
Analytics consumes only authorized valid current results and is idempotent by event_id. Parent roll-up computed deterministically; avoid double counting unique video when multi-label.
No claims that A/B/C extraction is implemented by SRE. Outbox can be initially tested with fake analytics consumer; real consumer integration can be adapted when its pipeline is ready.
On rights revocation, invalidate/hide related outbox-derived caches/results and delete restricted content per retention policy.

## 16. Milestone plan (strict sequence and Definition of Done)

S0 BRANCH/BASELINE — branch from exact main SHA, docs + tracker only, note open PRs; no code or paid call. DONE only when branch and docs readable.
S1 CONTRACTS/ARCH — V1 RTB/content/output/run/payment/outbox contracts, compatibility matrix, formal statuses, error taxonomy, tests for invalid input.
S2 TAXONOMY ADAPTER — approved deep loader, 11 fields, ancestry, stable mapping source node -> UUID, provenance/review/hash gate, multi-project tests.
S3 D1 INTAKE + RIGHTS — canonical project-video, source/provenance link, metadata-only content snapshot, read/right revocation and expiry, fixture input; no collector dependency.
S4 OFFLINE END-TO-END RUNTIME — additive migration, dataset snapshot, run/items/batches, fake embedding/LLM, evidence + results, idempotency/concurrency/restart tests. MANDATORY first functional E2E.
S5 FINANCIAL GATES — fresh prices, SRE-only quote/consent, Decimal ledger, reserve/dispatch/settle/uncertain; chaos and parallel worker tests.
S6 REAL MODEL PROVIDER — pinned OpenRouter LLM adapters/strict output validation, cost settlement, opt-in controlled smoke; no implicit retry.
S7 EMBEDDINGS + CASCADE — ranking/caching, strong baseline, fast/strong route, calibration; pruning disabled unless benchmark approved.
S8 HUMAN REVIEW & EVALUATION — queue/corrections, immutable gold labels, comparison, metrics, feedback proposals.
S9 HTTP + SRE STUDIO — localhost routes, management, run details, review, experiments, cost, UI tests, redaction/auth.
S10 COLLECTOR/DOWNSTREAM — stable D1-backed event intake from PR #20 or subsequently merged collector; verified outbox to analytics fixture, no unauthorized media calls.
S11 HARDENING — fresh and upgraded DB migrations, cross-platform smoke, restart/lease/rights/retention/payment chaos, observability, source documentation.
S12 RELEASE — final draft PR review and CI, baseline reconciliation, docs status update, merge only with approval. Record resulting main merge SHA.

Milestone checkpoint template:
- Status: TODO/IN_PROGRESS/BLOCKED/DONE
- Base SHA / code SHA / PR
- Changed files, API/contracts, migration
- Commands and actual test results
- Verified behavior and unresolved risks
- Next milestone dependency

## 17. Explicit test matrix

Unit: RTB all field preservation/ancestry, missing fact/review, counterfeit source node, consent version, duplicate source, fake prompt injection, no_match vs uncertain, exact JSON keys, evidence quote/times, multi-label, sorting/calibration.
PostgreSQL: project FKs, immutable approved taxonomy, separate roles/scope, dataset pinning, stale policy, deletion, unique run pairs, finance invariant under 2 concurrent workers, lease fencing, idempotency, migrations, read rights.
Worker chaos: crash prior to reserve, after reserve before dispatch, after dispatch before response, after response before settlement, cancelled run, price/rights revocation midflight, missing usage, restart.
Provider: invalid/too large output, HTTP4xx/5xx, unknown charge, rate-limit before/after request, schema mismatch, long context, embedding partial response, model-id mismatch.
UI/API: GET read-only, Host+Origin, CSRF-style header, malicious paths, nonexisting/other project, huge payload, unsafe characters, no secret leakage, pagination.
Offline full E2E: approved RTB -> D1 fixture videos -> snapshots -> fake SRE run -> evidence -> human correction -> outbox fake analytics -> replay and revision -> rights revocation.
CI: .github/workflows/tests.yml already Python 3.12, PG17, Node22. Add new JS syntax and behavior checks; run Windows smoke workflow. Claims of pass require observed result on exact SHA, not assumptions.

## 18. Risk register / non-negotiable blockers

R1 Collector PR #20 draft and PR #10/#13 independent; integrate via stable D1 adapter, not premature platform coupling.
R2 PR #6 draft stale relative to main; do not blindly merge.
R3 Main can advance rapidly (#19 merged during planning); check SHA and migration availability before each commit and UI integration.
R4 Current D1 permissions (can_store_metadata etc.) do not alone define permitted external LLM transfer; explicit restriction default deny, real paid smoke blocked without permission.
R5 Timestamp verification proves bounds and quote presence, not semantic entailment; evaluation needed.
R6 Automatic updater can interrupt paid stages; add busy guard and crash-recovery semantics.
R7 Price/usage uncertain: retain reserve, no implicit refund or retry.
R8 Pending Feature A/B media extraction: metadata-only SRE still useful; multimedia enrichment behind rights/availability.
R9 Pipeline extraction versioning: no migration editing after production, stale runs pinned, old results not rewritten.
R10 Separate concurrent PRs editing app.py / site routing: never clobber unrelated changes.
R11 Model catalog version, prompt hashes and transport support must be verified; do not hardcode live models without validated catalog snapshots.
R12 Data retention/revocation applies to prompts, caches, evidence quotes, logs, and downstream data, not only sre_content_versions.

## 19. Implementation work rules

- At the start of each milestone read live main and branch HEAD, verify pending PRs and migration list.
- Only write to feature/sre-production-v1-20261023 until authorized final merge.
- Small atomic commits, code + regression tests + docs per milestone.
- No LLM API paid calls, external scraping or cloud reservations without explicit separate consent; fake adapters are the default.
- Preserve existing Python and DB APIs unless intentionally versioned; maintain legacy tests.
- No forked copies of RTB builder, Discovery, Search Planner budgets or D1 videos.
- Report exactly what was completed, what tests ran, which remain blocked and the commit SHA.
- Maintain docs/SRE_IMPLEMENTATION_TRACKER_V1.md after every milestone.
- Do not mark done based solely on conceptual design or unexecuted tests.

## 20. First implementation action after approving this plan

Implement S1 (contracts) and S2 (Taxonomy Adapter) first, then S3 and offline S4. Do NOT begin OpenRouter adapter, embeddings optimization or UI before offline service and persistence are passing.
First practical work units:
1. Compile crosswalk for RTB deep tree, flat categories and category_keys, including full 11-node-field snapshot.
2. Add provider-agnostic typed contracts for input, labels, errors and run statuses.
3. Test version/hash/project/review gates and reject malformed or flat-only publication.
4. Add metadata-only D1-to-SRE fixture adapter with explicit source rights.
5. Run end-to-end with injected fake LLM and real PostgreSQL, including journal/results/replay.

This order keeps SRE independent of incomplete Collector implementation and provides reproducible evidence that the foundation works before spending money.
