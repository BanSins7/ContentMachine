# GB-MOD-09 — OpenRouter Model Provider & Capability Probe (v1)

**Branch:** `feature/group-b-full-module`. **Owned code:** `intelligence/group_b/providers/{protocols,openrouter}.py`. **Expected Group B model port:** `group-b-model/v1`. **No paid activation, no provider default, no keys in source, no changes to main/005/055/056.**

## Verified OpenRouter contract (26 October 2026)

OpenRouter documents `GET https://openrouter.ai/api/v1/models` exposing exact `id` slug, `architecture.input_modalities`, `architecture.output_modalities`, `context_length`, `top_provider.max_completion_tokens`, `supported_parameters` and `pricing`. Pricing `prompt`/`completion` are returned as **USD per token** (other image/request units remain separate). The current Chat Completions endpoint is `POST https://openrouter.ai/api/v1/chat/completions`, with `response_format: {type:json_schema,json_schema:{name,strict:true,schema}}`. OpenRouter's guidance notes that structured-outputs support differs by **endpoint** and `provider.require_parameters=true` is necessary to avoid silent parameter dropping.

Documentation references:

- https://openrouter.ai/docs/api/api-reference/models/get-models
- https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request
- https://openrouter.ai/blog/insights/confidence-thresholds-for-model-escalation-routing/

**No claim of an actually contacted or verified production model.** Metadata is authoritative only if obtained through a trusted HTTPS OpenRouter request, with an exact catalog slug. This stage uses solely mocked HTTP in automated tests, not real model credentials. Do not hardcode a presumed Opus 5.5 video model: `image`, `video`, `text` capabilities are independent metadata. An image-capable model is *not* automatically a raw-video decoder.

## Adapter design / security

`OpenRouterProvider(transport=None,api_key=None)` is **inert by default** and implements `ModelPort` structural protocol: `capability()`, `quote(...)`, `invoke_approved(...)`. `capability()` reports declared metadata only after an **explicit** `fetch_catalog(model=exact_slug)` using an injected transport and explicit key; this is a non-billed metadata GET, not an availability/budget guarantee. `OpenRouterTransport` uses stdlib `urllib.request` with fixed allowlisted URLs, strict timeouts, bounded responses and no printing/logging of secrets. All errors are sanitized to stable reason codes, including `rate_limited` (429), `timeout`, `upstream_temporary`, `network_unavailable`, `unauthorized` and malformed payload. No server-provided error body or sensitive prompt string is returned.

**Crucial paid boundary:** `OpenRouterProvider.invoke_approved`, `quote`, and `OpenRouterTransport.post_json` raise `ExecutionNotApproved` unconditionally in GB-MOD-09. This explicitly prevents even a fabricated `approved_attempt` mapping from activating a POST. Only GB-MOD-10 and GB-MOD-11, using durable project-specific quote/approval/attempt ledger and fresh revocation checks, may add a distinct approval-enforcing transport and enable paid execution. A model port returning `available` from catalog is **not a spend approval**.

`parse_model_catalog(raw,requested_model=...)` requires an exact lower-case `author/slug`, unambiguous per-model limits, positive context and completion capacity, trusted input/output modalities, `supported_parameters`, and finite non-negative Decimal prompt/completion/image/request prices. Missing fields, unknown target, duplicate slugs, negative/NaN prices or changed catalog shape **fail closed**; other, unrelated catalog entries can be ignored when probing a specified model. Catalog SHA-256 is pinned to canonical JSON; it is a content hash, **not** a digital signature or proof of freshness. A subsequent failed fetch invalidates any cached metadata.

`probe_model(catalog,model,packet,output_tokens)` reads only the exact chosen descriptor and verified `ContextPacket` digest. It refuses text-only models for image refs, missing `structured_outputs`, output/context limit overflow, unknown models, unverified/mutated packets or changed delivery flags. Catalog prices are evidence only; GB-MOD-10 is responsible for live quote snapshots, pricing version, per-image/caching/input estimation and user limits. Stage08 `estimated_token_ceiling` is a **planning number**, not a provider tokenizer guarantee.

## Multimodal request planning

`prepare_chat_request(...)` produces an **UNSENT** OpenRouter chat-completions dictionary with `stream:false`, `provider.require_parameters:true`, a strict JSON Schema object, exact requested model slug and max output tokens. An explicit trusted caller must supply each `VerifiedImage(ref_sha256,mime_type,content)` for every selected `unresolved_image_ref`. Ref SHA must match the pinned Stage08 entry, **actual bytes SHA** must match its `content_sha256`, MIME must be PNG/JPEG/WEBP with matching header, and image data is emitted as bounded base64 data URLs — no file paths, arbitrary external URLs or unverifiable images. No attachments or partial coverage => `unresolved_image_references`, not a fake image-seen inference. It does not attach videos, synthesize frames or assume a named model processes raw video automatically.

OCR/ASR text from Stage08 remains in JSON `untrusted_text` fields in a **user data message**, separate from a static privileged instruction telling the model to ignore instructions inside observations. Hostile OCR cannot create a system/tool message. The schema compiler deliberately supports only an auditable strict subset (object/required/all properties/additionalProperties:false, array/items, primitive types and enums, bounded depth), refusing unsupported schema features instead of pretending they are enforced.

`decode_chat_response(...)` is an independent pure decoder: one non-streaming completed choice, `finish_reason=stop`, no refusal, valid JSON, schema type/required/enum validation, matching model/response identifiers, and nonnegative prompt/completion usage fields. Incomplete `finish_reason=length`, malformed JSON, unexpected extra/missing fields, mismatched identities and unavailable usage are rejected, not silently saved as B1–B8 success.

### Limits of this stage

- Strict output support may vary per provider endpoint. `require_parameters` requests enforcement; it is **not** proof every endpoint will behave correctly. Decoder repeats local schema validation; do not trust a single provider hint.
- Catalogue metadata can change at any time, especially prices and max context sizes; fresh quoting and model selection must happen before actual paid calls.
- Source byte/permission revalidation remains with GB-MOD-06/07/08; context model input currently contains only ref metadata until explicit media attachments are supplied.
- Provider receipts, refunds, retries, 429 backoff, idempotent billable attempt recovery, actual model selection policy, tokens and analytics lineage are GB-MOD-10/11/21/22 tasks, not claimed implemented here.
- Public code does not send any live chat request, even with an API key. The implementation is deliberately **non-operational for paid inference** until future spend authorization.

## QA

`tests/test_group_b_provider.py` uses fake catalog and injected urllib HTTP opener; it tests exact slug, missing/incomplete metadata/price and output limits, text/image/JSON flags, 429/503/timeouts (no secret leakage), invalid response shapes/JSON/refusal/length, Unicode hostile OCR, requested strict routing, missing or corrupted image bytes, and explicit zero-POST denial. CI runs on Linux/Windows and in the Group B PG17 workflow, without any real OpenRouter tokens/credentials.

```bash
python -m unittest discover -s tests -p 'test_group_b_provider.py' -v
```

**QA truth:** Code/tests committed != proven passed. Record actual completed runner outcomes before claiming PASS. Joint real QDP→A→B, live model availability, paid quote/approval and E2E are **NOT RUN/BLOCKED** until corresponding branches/consent exist.

**Next:** GB-MOD-10 — Cost Manager, quote/approval and immutable spend ledger.
