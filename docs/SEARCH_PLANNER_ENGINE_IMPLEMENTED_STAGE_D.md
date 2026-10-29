# Search Planner — Stage D: OpenRouter + AI Query synthesis (verified implementation)

**23.10.2026.** Implementation guide for the code introduced by PR #25 (exact PR number verified after opening). This is a new implementation on top of Stage C, not a claim of measured TikTok recall improvement. **No live paid OpenRouter calls were sent during development or CI.**

## Scope and architecture

Approved immutable RTB → full HypothesisContext → direction batches → verified/versioned approved model + prompt + raw schema → full-request quote (not byte-only Stage A quote) → distinct owner-approved Search Planner consent → read-only live preflight → reserve atomically → durable dispatch intent → ONE OpenRouter chat completion → raw model-derived trace → strict schema and cross-references + lexical QA → actual cost settlement → candidates and coverage → transactionally completed run and outbox.

Source files:
- `ai_query/prompts.py`: pinned system instructions, immutable context JSON, strict JSON-Schema subset, SHA-256 prompt/schema, endpoint options and conservative serialized request upper bound.
- `ai_query/transport.py`: single OpenRouter HTTP POST; require_parameters, no fallback, no streaming or retries; safe errors; response parser with usage and precise USD, injected sender for no-cost QA.
- `ai_query/quality.py`: exact hypothesis/facet/angle/policy contract; duplicate and junk term checks; method-level trace diagnostics; not semantic ranking.
- `ai_query/experiments.py`: offline method ablation A0→A4 and audit metrics; **not** search recall benchmark.
- `ai_query/live_worker.py`: trusted server-side explicit paid opt-in; read-only preflight, recheck model/tariffs, exact request hash and per-batch approved reserve; Stage B reserve and durable dispatch marker; successful Stage C atomic output or unknown/known failure with correct cost semantics.
- Updated `generation_engine/cost_estimator.py` and `budget_manager.py`: real-version configurations automatically quote the exact JSON messages+schema envelope; older fake configs keep previous Stage C flow. Config hash mismatch **fails closed**.
- `tests/test_search_planner_stage_d.py`: schema/pin/lineage/deterministic request/transport/usage/quality tests.
- `tests/test_taxonomy_deep_db.py`: end-to-end disposable PostgreSQL with two RTB Human Reviews, quote+consent, mock OpenRouter three-batch completion, outbox, unknown cost, malformed billed response.

## Current live prompt — grounded query synthesis v2 (29.10.2026)

The live SP4 prompt no longer requests ThinkQE, MuGI, Jina or adaptations of those methods.
It now asks only for grounded semantic understanding, bounded search-intent facets and
short keyword/hashtag queries. It preserves approved RTB include/exclude boundaries,
does not ask for pseudo-video sketches or lexical query expansion, and explicitly
sets the legacy required `sketches` and `lexical_hints` fields to empty arrays.
The raw `ai-query-raw/v1` response shape stays unchanged for SP5–SP7 compatibility.

Active pins: `prompt_version=ai-query-v2`,
`strategy_version=grounded-query-synthesis-v2`. The prompt SHA-256 and JSON Schema
SHA-256 have also changed. Existing approved configs, quotes and request hashes
are not silently migrated; register a fresh generation config, plan and quote
before further paid dispatch.

The following subsection records **historical v1 prompting only**. It is not
part of the current live model instructions.

## Historical v1 implementation — not sent to the live model

### ThinkQE-inspired facet exploration (one LLM completion)

Original ThinkQE runs iterative BM25 retrieval + generated hypothetical passages + feedback. There is NO video retrieval or external corpus at AI Query stage. Content Machine v1 uses a single LLM pass per batch to derive `semantic_profile`, up to six non-overlapping `facets` per hypothesis, one bounded missed-facet reflection, each `facet_key/angle/intent/anchor_terms`. Provenance: model-derived; never a human-approved RTB fact. Each final query has a validated `facet_key` to the same hypothesis. Actual feedback-driven facet adaptation is future Collector/SRE work, not mislabeled as existing ThinkQE.

### MuGI-inspired synthetic video content sketches

Original MuGI generates multiple pseudo-documents to augment BM25 query text. We instead generate up to two **short hypothetical video sketches per facet** in the *same* completion. Each sketch hints at creator language/situations, but **never** becomes a real video, search record or evidence. It remains only in `sqg_attempts.raw_result`. Platform query text is short and independent.

### Jina-inspired controlled lexical expansion

Original Jina LLM query expansion appends additional information for embedding retrieval. Here up to four `lexical_hints` per facet enrich natural creator/format language for **separate keyword/hashtag strings**; allowed languages/modes are pinned by RTB search policy. Hints are not embeddings, verified trends or existing hashtags. They remain audit traces and do not directly become Collector tasks.

### Linked structured generation

Each hypothesis JSON must contain exactly `semantic_profile,facets,queries,no_viable_reason`; each facet requires `facet_key/angle/intent/anchor_terms/sketches/lexical_hints`, and each query requires matching facet_key, angle, language, query_mode, research_stance, rationale. Python checks all shape constraints **after** the provider schema, because strict modes vary by endpoint. 0 queries only with meaningful `no_viable_reason`. IDs and source facts attached by server on output. No tool-specific collector commands.

## Financial safety

1. Approved `sqg_config_versions` must pin actual prompt SHA-256 and schema SHA-256, strategy version and verified OpenRouter catalog snapshot.
2. Quote must contain `estimator_version=search-query-live-request-envelope-v1`, exact request SHA-256 **per batch**, frozen model/rates and response budget. Legacy quote must not authorize real HTTP.
3. Each preflight checks consent not revoked/expired, quote/model match, actual payload and SHA, payload size under 1 MB, token-proxy upper/context size, and per-batch cost reserve. Immediately before reservation, real transport checks credential availability without sending a request or exposing the key.
4. Actual request is guarded by explicit `explicit_paid_execution=True`, plus trusted owner authorization required of the calling server. There is **no HTTP endpoint/UI** in this stage that users can trigger accidentally.
5. `reserve_batch` locks Generation→Consent→Batch; `record_dispatch_intent` is committed **before** network. Exactly one POST per attempt, without retries.
6. If HTTP outcome/usage unknown, reserve is retained, status uncertain and no re-dispatch. Malformed body with provider-confirmed billed cost is settled as known billed failure, not an automatic free retry.
7. OutputManager only writes complete candidates/outbox after all approved hypotheses have recorded coverage. Stage C outbox consumer / Query Validation is still a separate stage.

Conservative 2× UTF-8 payload bytes + fixed overhead is a **safety proxy, not a mathematical bound** on provider billable tokens, and the 20% margin cannot guarantee never exceeding actual charges. Future tier-based provider fees and live endpoint capability must be independently checked. Fail-closed if pinned quote and rechecked request cannot agree. Real billing reconciliation requires trusted OpenRouter evidence; no automatic reimbursement.

## Trusted invocation (do NOT call without real owner approval)

```python
from intelligence.search_planner.ai_query.live_worker import (
    preflight_live_batch, execute_one_paid_batch,
)
from intelligence.search_planner.ai_query.transport import OpenRouterQueryTransport
from intelligence.search_planner.ai_query.prompts import stage_d_fingerprints

# Configure a new Stage D model version with actual fingerprints:
# config["model"].update(stage_d_fingerprints())
# approved = register_config_version(...)
# run = create_planned_generation(...)
# quote = create_generation_quote(...)    # exact-request live envelope
# consent = authorize_generation_budget(...) # distinct human owner approval
safe_preview = preflight_live_batch(project_id, generation_id) # no money
# Trust-boundary: caller must independently validate owner/session/CSRF.
# This function is explicitly paid; no automatic scheduled execution.
# execute_one_paid_batch(project_id, generation_id,
#     transport=OpenRouterQueryTransport(),
#     explicit_paid_execution=True)
```

**Actual paid network integration tests are intentionally absent**: they require configured credentials, fresh provider capabilities, a new explicit budget authorization and controlled live environment. Integration tests use injected deterministic mock OpenRouter HTTP responses and disposable PostgreSQL 17 instead.

## QA commands / acceptance

```sh
python -m unittest discover -s tests -p 'test_search_planner_stage_d.py' -v
python -m unittest discover -s tests -p 'test_search_planner_engine_stage_c.py' -v
python -m unittest discover -s tests -p 'test_search_planner_engine_cost.py' -v
python -m compileall -q intelligence/search_planner
CM_TEST_PG_DSN=postgresql://...disposable... \
  python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
```

Unit coverage: 3 method trace checks, strict schema subset, request SHA reproducibility, nontruncated RTB contexts, malicious facet/language/text/repeated queries, fake HTTP shape, known charged malformed output, usage NaN/negative/unknown, provider fallback disabled and no search-tool imports. PostgreSQL integration: complete 3 batches, no user opt-in => no call, project isolation, outbox only on complete, billed-invalid known failure, uncertain cost reserved, never auto-retry. Linux/Windows CI result must be separately confirmed before marking PR ready.

## Still separate product stages

- No HTTP owner-authenticated approval UI or externally exposed endpoints yet; only trusted Python service.
- No real ThinkQE BM25+retrieval iteration / MuGI BM25 augmentation / Jina embeddings service (adaptations intentionally differ from papers).
- No live Collector feedback loop. Actual search relevance/recall/precision **not measured** in this stage.
- Query Validation relevance, embeddings, MMR, Tool Registry and Task Planner remain future work.
