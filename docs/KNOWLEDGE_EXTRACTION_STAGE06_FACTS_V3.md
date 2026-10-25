# Content Machine B — Stage 06 · B3 MiMo Fact Extraction CONTRACTS

Status: **offline request preparation, strict facts-v3 schema, source-lineage validation, immutable unsent request registry**. This stage deliberately makes ZERO paid OpenRouter calls. Execution and cost ledger are Stage07; Python normalization Stage08, independent quote/source grounding Stage09, dedup/conflicts Stage10, QA Stage11, human review Stage12, KnowledgeSnapshot/C adapter Stage13.

## Model and source boundary

Model ID: `xiaomi/mimo-v2.6-flash`. Do not reuse A12 `facts-context-v1` as the B contract: that route chooses a bounded set of preapproved A Evidence and runs RTB, while B reads complete, immutable B1 text inventory from Stage04 **Full Context EvidencePack** or Stage05 **Multi-Pack**. Never run both extraction paths automatically, and never infer product capabilities from competitor/other material.

Source pack → strict model request:
- Full: `evidence-pack/v1` verified with SHA, original source refs and all byte offsets; pinned B1/Context/model snapshot.
- Multi: `evidence-multi-pack/v1`, pack must be exactly present in its `evidence-multi-pack-plan/v1`, with parent B1 bundle + catalog SHA. Every original source fragment and exact Unicode span is covered by Stage05 proof. One MiMo request per part.
- No video data, video-derived transcripts, ASR/OCR/Vision, or scraped social handles in MiMo. Video stays pinned separately in B1/B9 and may be consumed only downstream by C as intended.

Request builder `fact_contracts.prepare_request(manifest,payload,snapshot,multi_plan=...)`:
- `facts-v3` strict JSON Schema with atomic proposals only: `kind`, `text`, `source_chunk_id`, exact `source_quote`, `quote_start`, `quote_end`.
- Fact kinds: function, problem, audience, product_type, constraint, metric, positioning, external_claim. No A Evidence IDs or B Fact IDs may be minted by MiMo.
- Supported OpenRouter parameter `structured_outputs` must be observed from pinned model capabilities; require actual `response_format.json_schema`, provider `require_parameters=true`, fallback disabled and non-streaming usage requested. An endpoint advertising only `json_object` is **not enough** for this contract.
- Exactly two chat messages: system contract and user-source JSONL as **untrusted data**. Content that resembles `{"role":"system"...}` remains within the user text, never privileged.
- Deterministic SHA for schema, prompt, original payload, exact serialized request and observed model catalog version. The request is generated in-process, **not** persisted as raw text.
- Existing `serialize_openrouter_stage` and `reservation_cost` are reused, with full final request messages factored into the conservative byte-derived token reservation. Unknown prices/model window, unsupported schema, unexpected model pin, wrong output ceiling or too-big final request fail closed.
- Any generated request remains **not_dispatched** and requires a **new** explicit Stage07 provider-cost consent. A Stage05 budget cap is a planning estimate only and not delegated consent.

Server-side simulated proposal validator `validate_candidate_response(response,manifest,payload)`:
- Validates strict `facts-v3` structural schema, response size, known `chunk_id` or `fragment_id`, zero-based Unicode character offsets within the pinned row and case-sensitive exact quote.
- Rejects unknown or forged chunk IDs, mismatched quotes, fabricated source/evidence/subject IDs, invalid types, extra fields, truncated JSON and oversized batches.
- Subject, source role, original source/version/output, locator, optional exact-match A Evidence IDs and trust label are derived exclusively from the **verified pack line**, not from model text. Even a matching citation is still labeled `grounding_status=pending_b5`, `human_review_status=unreviewed`. These are model-proposed claims, not knowledge.
- Empty fact arrays are valid (if no source-grounded claim is present); reaching 80 claims sets `candidate_limit_reached=True` for downstream B7 coverage assessment, never proof of complete extraction. No creative semantic rewriting or hard-coded industry goal.
- Stage07 will capture raw paid attempt states and outputs (including failure/unknown costs); Stage08 quarantines/normalizes, Stage09 re-verifies against stored original A source rather than only this model-visible pack. None are implemented in Stage06.

## Persistence and APIs

Migration **045_ke_fact_request_batches.sql** adds only:
- `knowledge_fact_request_batches`: exactly one Full Pack or Multi Plan, project/bundle/model snapshot FK, immutable prompt/schema/request-plan hashes, exact request count and USD estimate.
- `knowledge_fact_request_parts`: per-pack request SHA, bytes, original pack payload/manifest SHA, quotation and chunk count. All immutable; no raw source text, model reply or key.
- Original SQL 001–044 untouched.

Python `FactRequestRepository`:
- `prepare(project_id,full_pack_id=...)` OR `prepare(project_id,multi_plan_id=...)` verifies historical pack bytes, **current** approved A Context and verified latest model catalog in the same read/commit lineage, builds each request, stores only immutable fingerprints; repeated identical requests are idempotent. Returns no raw text or provider credentials.
- `materialize(project_id,batch_id)`: historical *read-only* replay of all original packs, catalog, prompt/schema, request hashes and immutable SQL membership; returns unsent requests in memory for authorized Stage07 use.
- `inspect_response(project_id,batch_id,part_index,simulated_response)`: purely local validation/inspection of an independently provided synthetic JSON draft. No provider invocation or publication.
- All queries scoped by `project_id`. There is **no HTTP endpoint, job runner, provider call, retry, automatic paid dispatch or implicit cost consent** in Stage06.

## Technical limitations / honest guarantees

- The exact number of provider tokens remains uncertain; the existing RTB provider cost preflight uses conservative UTF-8 byte factors, not a certified remote tokenizer. Stage07 must verify the **actual** final serialized request, current usage/prices/authorized reservation atomically immediately before external dispatch, and reconcile uncertain outcomes with no automatic paid retry.
- Provider support for strict structured outputs is catalog-dependent. The implementation intentionally blocks MiMo until the catalog confirms this capability; no unsupported model capability is assumed from the model name. Tests use synthetic provider metadata only.
- The source content may be previously truncated or merely `model_reported_unverified` at A. Stage06 cannot fix source-extraction completeness or independently verify truth or semantic entailment.
- `facts-v3` transport limits per-part proposal count to 80. This must feed into Stage07–11 completeness decisions; 80 is **not** a semantic completeness guarantee.
- Source rights processing gates remain intentionally outside Stage06 as previously directed; preserve technical project isolation and independent explicit consent for provider spending.

## QA

```bash
python -m unittest discover -s tests -p 'test_ke_stage06_*.py' -v
python -m unittest discover -s tests -v
python -m scripts.psi_release_gate --strict-ci
```

Tests use actual Stage04/05 immutable reconstructed source packs and a synthetic verified MiMo catalog. Negative tests cover foreign projects, fake chunk/source/evidence/subject IDs, wrong quote/Unicode range, corrupt payload, unsupported JSON transport or model pin, injection as data, 0-fact return, full/multi separate request SHA, no raw research text in SQL, DB idempotence/immutability, historical replay after profile revision, refusal of unauthorized paid execution, and all A stage regression tests.

**Next Stage07 (only on explicit user instruction):** paid MiMo attempt orchestration, consent ledger, idempotency, uncertain provider bill handling, explicit budgets and retry policy; no merging until human approval.
