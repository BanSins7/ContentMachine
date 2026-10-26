# Search Planner SP-00 — Verified Integration Map
Date: 2026-10-26 · Stage: SP-00 · **Audit/freeze, not a runtime implementation**

## 1. GitHub snapshot (read-only verified)
| Component | Ref | Head/status |
| --- | --- | --- |
| Main (A8/B9 incorporated) | `main` | `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` |
| Search Planner working branch (pre-SP-00) | `feature/search-planner-full-integration` | `61278c9af75e59b886674c59ab4111e7e45e56aa`; 2 documentation commits ahead of main, 0 behind |
| Video Collector PR #20 | `feature/video-collector-dev-20261023` | `a4f658de77fb82de2437474e60c2b917ae2a9811`; open/unmerged; 25 ahead and 145 behind current main |
| C Corrector PR #31 | `feature/rtb-c-corrector-v2` | `deff6c9883229b37c4ebba52a1bce8061ecdbec6`; open draft; only initial C-FIX-00 freeze |
| B9 PR #30 / A8 PR #29 | `main` | both merged; historical A/B files and SQL now present |
| Previous old-SP audit | `8d9bee17402019a26bb4eaa27df040beb06153b9` | superseded for base-state purposes |

GitHub source links:
- https://github.com/BanSins7/ContentMachine/tree/feature/search-planner-full-integration
- https://github.com/BanSins7/ContentMachine/pull/20
- https://github.com/BanSins7/ContentMachine/pull/31
- https://github.com/BanSins7/ContentMachine/actions/runs/40284202868

## 2. Actual SP1–SP12 source → interface → recipient

| Stage | Authoritative existing files/contracts | Recipient / release gap |
| --- | --- | --- |
| SP1 | `input_loader.py`: `load_approved_publication`, `read_published_snapshot`; read-only repeatable-read JOIN across approved C9 / two reviews / project | SP2; V1 only; no real C9 Deep V2 publication yet |
| SP2 | `context_builder.py`: `build_hypothesis_contexts`; `contracts.py` enforces 11-node-field RTB core and approved facts | SP3 via immutable `hypothesis-context/v1`; V2 not implemented |
| SP3 | `generation_engine/{run_manager,batch_manager,model_manager,cost_estimator,budget_manager,repository,worker,recovery_manager,contracts,provider_port}.py` | SP4; durable SQG run/quote/consent/ledger exists but no complete service worker / owner-facing HTTP flow |
| SP4 | `ai_query/{prompts,transport,quality,live_worker,experiments}.py`; `execute_one_paid_batch` | SP5; pinned OpenRouter method trace; explicit Python call, not managed GUI process |
| SP5 | `generation_engine/output_manager.py`: `get_candidates_bundle`, `settle_successful_batch` | SP6; `query_candidates/v1`, `sqg_candidates`, `sqg_outbox` |
| SP6 | `query_validation/embedding.py`: `SentenceTransformerEmbeddingProvider`, `HashingTestEmbeddings`; `selection.py` | SP7; real weights/operator revision packaging, model QA needed |
| SP7 | `query_validation/selection.py`: cosine gates, exact/semantic dedup, MMR, reasons, coverage | SP8; baseline policy/provenance; cross-hypothesis sharing open |
| SP8 | `query_validation/service.py`: `validate_generation_outbox`, `get_validated_bundle`, `pending_validated_outbox`, `ack_validated_outbox` | SP9/SP11; `validated_queries/v1`, `qv_runs/decisions/coverage/outbox` completed; no downstream consumer |
| SP9 | no SP production registry; reference-only Collector `registry.py`, `SourceDescriptor` | SP10 receives versioned capability *port*, no duplicate provider registry |
| SP10 | no SP production task planner; reference-only Collector `planner.py`, `build_preview` | SP11 SP-owned neutral task plan, not a duplicate executable Collector planner |
| SP11 | SP-owned durable handoff missing; current `qv_outbox` pending/ACK API only | SP-owned inbox / optional Collector adapter; must not require Collector package to import/run |
| SP12 | no measured feedback code | deferred: only true Collector/Discovery/SRE results, no synthetic recall |

All SP implementation to live in `intelligence/search_planner/`, with narrow additive `app.py` mount and `site/` UI in later stages. No SP-00 runtime edits.

## 3. Verified upstream ownership and C9 caveat

**Existing C9 V1:** `tb_deep_publications` joined to `tb_taxonomy_publications`, `tb_runs`, `tb_deep_runs`, `tb_fact_review_submissions`; `tb_deep_reviews` must contain reviews 1 and 2; tree is strict 11 fields and `taxonomy_sha256=sha256(nodes)` using RTB canonical JSON. A validated publication ID is the taxonomy publication UUID, not a run ID. `hypothesis-context/v1` preserves hierarchy, profile subset, approved facts and search policy.

**Merged A/B:** A `project_context_snapshots` (`project-context/v1`) and video output `video-intelligence/v1`; B9 `knowledge_approved_snapshots` (`knowledge-snapshot/v1`), `knowledge_c_handoffs` (`knowledge-c-handoff/v1`), facts have SHA-256 `fact_id`. The B9 → flat C path in migration 053 / `knowledge_c_generation_runs` is NOT proof that approved Deep C9 V2 is supported by SP.

**C Corrector draft PR #31:** `research_contracts_v2.py` proposes SHA→UUID5 adapter for B9 facts and original A observation evidence references. Deep V2 14-node schema, C9 lifecycle and publication are *not yet implemented*. Stage SP-02 must dispatch on real immutable publication schema/version only; do not auto-accept enriched nodes in V1 or forge V2 data.

## 4. Exact canonical identity
`intelligence/taxonomy_builder/pipeline_contracts.py`:
```python
canonical(value) = json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False)
sha256(value) = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
```
Reusing the existing helper is mandatory. Preserve `candidate_id` UUID across every projection. Any new schema gets explicit version + immutable SHA of canonical *actual* data.

## 5. Actual UI/API and workers
- `app.py`: local `Handler.do_GET/do_POST`; current owner boundary uses Host/Origin plus `X-ContentMachine: 1` on mutation. Routes delegate to `intelligence.project_sources.*_http`, `intelligence.discovery.http_api`, and RTB. No Search Planner management route was found.
- `site/index.html`: script-based local app, `site/app.js`, existing taxonomy/project scripts and `style.css`; new SP UI must use this shell without replacing it.
- `generation_engine/worker.py` provides `process_one_batch` with mock provider and `drain_generation`; `ai_query/live_worker.py` provides owner-gated `execute_one_paid_batch`; QV consumer is an explicit function, NOT background daemon.
- SP-01 onward must provide supervised operation lifecycle and fail-closed no-Collector mode; no network work in GET.

## 6. Boundaries frozen at SP-00
1. Branch is a self-contained **ContentMachine submodule**, not a second app; it may rely on main's project/RTB/B9 common infrastructure.
2. No `intelligence.collector` imports required for SP core, migrations, app start or tests.
3. One real authority per provider capability: Collector registry if attached, otherwise unavailable, never fabricated ready.
4. QV source truth is database `qv_runs` + `qv_decisions`, never caller JSON.
5. SP-owned inbox ACK != Collector ACK. `qv_outbox` may be ACKed only atomically after SP inbox durable insert; downstream delivery has separate receipt.
6. Zero viable selected queries is durable `no_work`; unsupported partial matches remain visible.
7. External source search and execution authorization remain Collector-owned; OpenRouter generation authorization is separate.
8. SP12 true measured feedback is dependency gated and not release-blocking for standalone SP core.

## 7. Stage gates
- SP-00 outputs: this map, `SEARCH_PLANNER_INTERFACE_MATRIX.md`, `SEARCH_PLANNER_SP00_BASELINE_QA.md`, `SEARCH_PLANNER_MODULE_ADR.md`.
- SP-01: standalone public facade/ports/DTO only once these boundaries are pinned.
- Full release: SP pipeline tested end-to-end without Collector; optional Collector mock adapter independently tested; consumer contract actual Collector PR reevaluated before real integration.
