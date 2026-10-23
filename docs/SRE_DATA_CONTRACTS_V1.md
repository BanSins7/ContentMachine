# SRE Data Contracts V1 — implementation reference

**Status:** S1 contract-only delivery. No production worker, database migrations, HTTP routes, external network calls, model invocations or expense authorization are implemented in S1.  
**Code:** intelligence/relevance/v1_contracts.py, v1_taxonomy.py, v1_runtime.py.  
**Tests:** tests/test_sre_contracts_v1.py.  
**Design owner:** feature/sre-production-v1-20261023.  
**Compatibility:** existing intelligence/relevance/contracts.py, pipeline.py, presets.py, repository.py, storage.py, experiments.py, and RTB's source schemas unchanged.

## Purpose

Freeze explicit provider-agnostic *shape* contracts for S2–S10 and enforce negative validation now. Every validator in these new modules is deterministic, pure standard-Python (except calls into existing pure validation modules), has zero PostgreSQL I/O, opens no HTTP connections, executes no LLM and costs nothing.

All validator functions return NEW normalized Python objects suitable for canonical JSON serialization. Unknown keys are rejected, required fields must be supplied explicitly. The code never invents data, grants rights or fills provenance from user-supplied guesses. Raw client JSON is not an authorized database snapshot.

Stable error interface: SREContractError(ValueError) with code, path and message. Known error codes include invalid_contract, unsupported_schema_version, snapshot_mismatch, invalid_model_output, rights_denied, rights_expired, illegal_state_transition, budget_exceeded. Exceptions must be mapped to safe HTTP errors by a later API; do not leak provider secrets or SQL details.

All UUID values are canonical lower-case hyphenated; project_id is existing canonical 32-hex; digest is 64 lower-case hexadecimal; integer fields reject bool; USD is an explicit decimal STRING with 0..8 fractional digits (never float, NaN, Infinity, exponent or negative values); timestamps are timezone-aware ISO 8601 converted to UTC for comparisons. SHA256 fingerprints use utf-8, sorted keys, compact JSON and ensure_ascii=False; fingerprint of a validated object is not an approval signature.

## Contract A: immutable RTB taxonomy

Name: sre-taxonomy/v1; function: validate_taxonomy_snapshot(payload).
Required:
- schema_version, project_id, publication_id (UUID), rtb_run_id (UUID)
- taxonomy_version (positive), sre_taxonomy_version_id (positive)
- tree_sha256 (canonical fingerprint of normalized RTB nodes)
- approved_facts: list of {id (UUID),kind:function|problem|audience|product_type,text,origin?,source_quote?}
- human_reviews: exact [1,2]
- nodes: complete canonical RTB tree with exactly the 11 fields:
  id,parent_id,kind,relation,source_fact_ids,name,rationale,definition,include,exclude,examples
- hypothesis_links: one {source_node_id,category_key} for each hypothesis and no extra categories.

Nodes are validated with the existing RTB deep_contracts.validate_tree: max 48; min two distinct directions; approved fact links; relation and ancestor constraints; no duplicate topics; direct direction -> hypothesis allowed; every direction must be researchable. category_key must equal UUID5(UUID(rtb_run_id), source_node_id), matching deep_repository.py's existing projection. No invented parent relevance predictions: only hypothesis leaves are model candidates and parents are derived summaries.

IMPORTANT: human_reviews, hashes, publication IDs and links in a transport payload are UNTRUSTED. S2 must load approved reviews, immutable deep publication, fact provenance and tb_sre_taxonomy_links under project scope from PostgreSQL and pass the SERVER-CONSTRUCTED envelope to this validator. S1 is shape validation, not database approval or authorization.

## Contract B: D1 -> SRE canonical content

Name: sre-content-input/v1; functions: validate_content_input(payload) and authorize_content_usage(...).
Required:
- schema_version, project_id, video_id, source_id, source_policy_version_id
- content_version_id, content_sha256
- completeness: metadata_only|partial|full, directly compatible with existing sre_content_versions.completeness values
- rights: booleans can_store_metadata, can_semantic_classify, can_external_llm, can_analyze_media
- data: title (nullable), description (nullable), hashtags (list), optional transcript/scenes (nullable lists of {text,start_ms,end_ms})
- provenance: discovery_id, search_query_id, source_request_id (positive integer or null)
Optional: media_asset_id (positive integer|null), expires_at (offset-aware ISO time|null).

Metadata-only cannot include actual media-derived segments. Actual transcript/scene requires media asset and can_analyze_media=true; title/description/hashtags are bounded. authorize_content_usage rejects no explicit semantic permission or expiration, and external_llm=true additionally requires explicit provider-transfer permission.

IMPORTANT: caller-supplied rights booleans do NOT establish permission. S3 must query current D1 source/source_policy version, media asset, terms, user consent and retention BEFORE provider dispatch AND before persisting/publicizing a result; reconcile raw source policy and derived SRE booleans with default-deny. D1's existing can_store_metadata is NOT license to transmit data to external AI providers. Retention covers content, prompt/output quotes, embeddings, cached traces and downstream materializations. Database joins must verify ownership and origin IDs. This V1 contract does not scrape/download, transcribe or fabricate scenes.

## Contract C: structured classifier labels

Name: sre-labels/v1; function: validate_model_output(payload, expected_category_keys, content).
Required: schema_version and labels. labels[] have exactly category_key,decision,reason,evidence. Each expected category exactly once. decision only match|no_match|uncertain (provider is forbidden from emitting not_evaluated). Match MUST have source-grounded evidence verified against the exact content snapshot via existing relevance.contracts.validate_labels. Evidence sources supported: title, description, hashtag, transcript, scene; segment/timestamp validation inherited from existing contracts.py. Reasons required even for negatives. Missing or fabricated quote, ID mismatch or malformed JSON -> invalid_model_output (not a semantic no_match/uncertain).

The evidence substring/timestamp check proves structural grounding, NOT semantic entailment; S8 must independently evaluate evidence and human disagreements.

System-only not_evaluated means intentionally unexamined pair, never negative. Provider failures, budget/rights blocks, unknown dispatch state are technical statuses outside semantic decisions.

Embedding results: validate_retrieval_scores(scores, expected_keys) requires exactly expected UUIDs, finite cosine score between -1 and 1, descending score with UUID tie-break. Candidate drop remains disabled unless an independently verified immutable gold benchmark authorizes it in S7.

## Contract D: pipeline snapshot

Name: sre-pipeline-snapshot/v1; function: validate_pipeline_snapshot(payload).
Required:
- schema_version, project_id, pipeline_config_id, taxonomy_version_id
- config: existing relevance.contracts.validate_pipeline shape
- models: exactly one entry per active stage model_ref. Model contains provider, model_id, model_version, catalog_snapshot_id, catalog_sha256, prompt_sha256, output_schema_sha256, input_usd_per_million, output_usd_per_million, capability.

Capability must match stage (embedding_retrieval or llm_classifier). Prices are strings with fixed decimal precision. The actual model catalog snapshot and transport support are not verified by shape validation: S5/S6 must resolve authenticated catalog data and preserve response schema/prompt/model hashes. No hardcoded live model assumptions.

## Contract E: planned run

Name: sre-run-plan/v1; function: validate_run_plan(payload).
Required:
- schema_version, project_id, run_id, publication_id
- taxonomy_version_id, taxonomy_sha256, pipeline_config_id, pipeline_sha256
- idempotency_key, state (MUST start awaiting_authorization)
- items[]: {video_id,content_version_id,content_sha256,hypothesis_keys[]}

Each video occurs once per run; each item has >=1 unique canonical hypothesis UUID, no extra data accepted. Complete DB run pinning must be implemented S4; this interface does not enqueue paid calls. Empty dataset intentionally rejected; show explicit no-data state before creating a run.

Run state transitions: awaiting_authorization -> queued/cancelled/failed; queued -> running/paused_*/cancelled/failed; running -> completed/partial/paused_*/uncertain/failed/cancelled; uncertain -> resolution paths; terminal completed/partial/failed/cancelled never restart in place. Real SQL state may require additive migrations: never modify previously checksummed SQL.

## Contract F: quote + explicit SRE consent + ledger

Name: sre-cost-quote/v1; function: validate_cost_quote(payload).
Required: project_id,run_id,quote_id,model_catalog_sha256,request_sha256,issued_at,expires_at,low_usd,expected_usd,high_usd, schema_version. Must satisfy 0 <= low <= expected <= high and high > 0; expiry after issue. Quote alone NEVER authorizes paid execution.

Name: sre-spending-consent/v1; function: validate_spending_consent(payload, quote).
Required: schema_version,project_id,run_id,quote_id,consent_id,authorized_usd,approved_at,expires_at,revoked_at. Exact quote/run/project binding; authorization must cover quote high; approval before quote expires; explicit revocation supported. Consent itself doesn't establish that current time, user approval, policy or SQL ledger allows a provider call.

validate_spending_position(authorized_usd,spent_usd,reserved_usd) enforces 0 <= spent + reserved <= authorized, using fixed-precision Decimal. S5 must enforce this atomically in PostgreSQL and must reserve BEFORE durable dispatch intent. Unknown request/usage keeps reservation, no paid retry. Attempt transitions:
reserved -> dispatch_intent | abandoned_before_dispatch
dispatch_intent -> completed | failed_known | uncertain
uncertain -> completed | failed_known (only after reconciliation)
terminal attempts -> none.
A quote and its upper estimate are not proof that an upstream provider can enforce a hard dollar cap; S5 must set explicit max output and conservative per-attempt reserve.

## Contract G: Analytics outbox event

Name: sre-analytics-event/v1; function: validate_analytics_event(payload).
Shared required: schema_version,event_id(UUID),event_type,project_id,run_id,publication_id,taxonomy_version_id,occurred_at.
Supported events: sre.classification_item_completed, sre.classification_run_completed, sre.classification_invalidated, sre.rights_revoked.
For item completion require video_id,content_version_id,source_policy_version_id,result_sha256,decisions.
decisions = {match: UUID[],no_match: UUID[],uncertain: UUID[],not_evaluated: UUID[]}, nonempty union, disjoint membership. Run completion carries no item decisions. Invalidation/rights revocation requires a reason. S10 must add transactional outbox and delivery/ack/replay; this contract itself does not publish an event.

Downstream never sums multi-label memberships as distinct videos. A revoked source invalidates active availability and downstream materializations; no evidence payload should be reused after rights expire.

## Integration responsibilities by milestone

| Milestone | Contract consumer | Database / external enforcement |
|---|---|---|
| S2 | validate_taxonomy_snapshot | trusted RTB approved deep publication loader, human reviews and mirrored links |
| S3 | validate_content_input; authorize_content_usage | source metadata/media policy and current rights, content snapshot writer |
| S4 | validate_run_plan; validate_model_output; validate_transition | run queue, evidence persistence, recovery, fake adapter |
| S5 | validate_cost_quote; validate_spending_consent; validate_spending_position | authenticated catalog, separate SRE consent, transactional payment ledger |
| S6 | validate_pipeline_snapshot; validate_model_output | actual verified provider, structured output, usage receipts |
| S7 | validate_retrieval_scores | real embeddings and calibrated candidate recall |
| S8 | model outputs, experiment baseline | immutable human gold, semantic grounding review |
| S9 | all endpoints | localhost authorization, safe API errors and UI |
| S10 | validate_analytics_event | D1 Collector integration and idempotent outbox consumer |
| S11 | all | tests for migration/Windows/cancellation/rights/financial chaos |

## S1 regressions and intended non-goals

Run: python -m unittest tests.test_sre_contracts_v1 -v (or python -m unittest discover -s tests -v). Tests include valid and invalid RTB tree links, noncanonical IDs, absent reviews, checksum mismatch, expired/external rights, media permissions, fabricated LLM evidence, missing and duplicate labels, NaN embeddings, model capability mismatch, duplicate run items, fractional money, quote expiry, unauthorized consent, cost cap, illegal transitions and overlapping event labels.

S1 deliberately has:
- no SQL migrations or views;
- no provider API adapter or paid request;
- no changed existing relevance.contracts/pipeline;
- no product UI/API, Collector integration or provider-specific model assumptions;
- no fake claim of live E2E.

All work beyond shape validation depends on separately implemented project/rights/model/budget/transaction authorization in subsequent milestones.
