# GB-MOD-08 — Context Builder / Budgetable Manifests (v1)

**Owner:** `intelligence/group_b/context/{builder,sampling}.py`, `feature/group-b-full-module`. **Schema:** `group-b-context/v1`; sampler `group-b-stratified/v1`. There is no network, provider request, paid model call or SQL write in this stage.

## Contract

A packet is constructed from **an exact byte-verified** `MediaEvidenceManifest` (`group-b-media-evidence/v1`) plus a `ReadinessReport` whose run/project/manifest SHA and **block-specific READY SHA** match. It is never built from QDP text descriptions, an arbitrary filename, an unpinned Group A latest result, or a caller-supplied `ready=true` string.

`build_context_packet(manifest,readiness,block=...,limits=...,text_reader=...,config=...)` is **pure data packing** against an already-authenticated stage06/07 snapshot. `build_current_context_packet(conn,project_id,b_run_id,ga_result_id,ga_result_sha256,block,expected_manifest_sha256,media_roots,observation_catalog, ...)` is the stronger opt-in entry point: in an **authorized caller-owned DB transaction**, it first rereads QDP inbox and receipt, QDP revocation/policy, pinned A version and media rights via stage07, then rehashes the source manifest and all proof bytes via stage06, checks the manifest SHA has not changed, and only then packs bytes/references. This is **not a paid execution authorization**; the future stage09/10/11 worker must repeat the checks again at actual dispatch.

The output `ContextPacket` records `packet_sha256` over canonical JSON data, an exact encoded UTF-8 byte count, and an **estimated** token ceiling. `delivery_state='not_sent'` and `media_delivery_state='opaque_refs_not_attached'` are explicit. Frames/slides remain opaque SHA-pinned **references**; they are not pixels or provider attachments. The package never falsely asserts that any model saw the bytes. Stage09 must define the real model/media attachment contract, model-specific token accounting and provider receipt.

## Source-first context format

Each entry references the verified original `media_asset_id`, immutable A `ref_sha256`, `content_sha256`, `asset_sha256`, optional **measured** absolute `start_ms/end_ms` or ordered carousel `slide_index`, plus `carrier` and `content_in_packet`.

- Video sampled frames and carousel slides: `carrier=unresolved_image_ref`. No image pixel content, URL, filesystem path or invented slide timestamp is emitted.
- Scenes/audio events: `carrier=verified_observation_ref` (only evidence pointers; no unsupported natural-language inference).
- Transcript/OCR spans: optional `carrier=untrusted_utf8_data`, **only if** an injected authenticated `text_reader.read_verified_bytes(observation)` returns bytes matching the exact original `content_sha256` and valid UTF-8. No arbitrary text supplied by the caller is believed. If the authenticated source is not available, the observation is explicitly omitted with a reason.
- **Never** interpolate `untrusted_text` into a system/developer message, a tool instruction or SQL. Treat it as untrusted observed data in a structured JSON field; instructions embedded in a transcript or OCR are **not instructions to ChatGPT or a future provider**. Control characters and hostile strings are JSON-escaped, not executed. File paths, secrets, raw images, storage objects and model credentials are not included.

Every source observation is either in `entries` or in `omitted` with an explanatory `reason`. Unsupported-for-block evidence is reported as `irrelevant_to_block`, not silently discarded. Examples of omissions are `frame_sampling_cap`, `slide_sampling_cap`, `image_reference_cap`, `observation_cap`, `text_segment_cap`, `text_total_cap`, `trusted_text_reader_missing`, `source_text_unavailable`, `serialized_byte_or_token_cap`.

The `coverage` manifest reports source/relevant/selected/omitted counts, selected/available frame and slide refs, and observed video time buckets with no **selected sampled frame**. Gaps are not fabricated scenes, not evidence of nonexistence, and not all possible model-visible coverage; the report only describes the context selection. A `coverage_complete` value is true only when **all block-relevant proof refs** were selected; `not_sent` remains true regardless.

## Limits / estimates

`ContextLimits` enforces positive bounded values, defaulting to:

| Limit | Default | Meaning |
|---|---:|---|
| `max_serialized_bytes` | 2,000,000 | **Hard** UTF-8 JSON wire payload cap; also bounded by `GroupBConfig.max_context_bytes` |
| `max_estimated_tokens` | 500,000 | **Conservative planning ceiling**, not provider-certified billing/tokens |
| `max_observations` | 80 | Hard included references cap; also bounded by `GroupBConfig.max_media_refs` |
| `max_image_refs` | 16 | Total image/slide references, not attached images |
| `max_frame_refs` | 12 | Hard sampled frame selection cap |
| `max_slide_refs` | 20 | Hard carousel slide selection cap |
| `max_text_bytes` | 64,000 | Total included plain UTF-8 text |
| `max_text_segment_bytes` | 8,000 | Per transcript/OCR item; no silent clipping of measured text |
| `image_token_reservation` | 1024 | Configurable **placeholder reserve per image ref**, not validated provider vision usage |

The estimate is `exact_encoded_wire_bytes + selected_image_refs × image_token_reservation`: it intentionally upper-estimates plain byte-token units for planning but **is not a universal upper bound for actual image tokens**. True provider cost/vision token counts must be measured/requoted by future `GB-MOD-09/10` before paid dispatch. Never use this estimate alone for billing or approval.

Candidate additions are rejected when the final envelope **including selected/omitted manifests** would breach either cap; the builder never hides omitted references to fit a budget. If even the omission manifest cannot fit, it raises typed `context_unavailable: manifest_accounting_exceeds_budget` rather than returning a misleading `complete` packet. Text exceeding a segment/global byte cap is omitted whole (not truncated into fake verbatim excerpts). Integrity mismatch of any actually read text **blocks** context creation.

### Sampling and long content

`context/sampling.py::select_diverse` is deterministic max-min distance on original measured timestamps or original slide indices. It retains first/last, prefers an observed early 5s hook frame when capacity allows, and fills largest chronological gaps; it does **not** generate new frames, reinterpret slide order or invent timestamps. Example 10-minute video with 100 measured frame refs: at most 12 selected, 88 omitted, unobserved ten-second windows recorded. Example 100-slide carousel: at most 20 ordered references, 80 excluded with explicit reasons. This makes long context tractable while exposing **coverage loss** instead of claiming a complete visual scan. Actual B3 sampling schedule and semantic interval selection belong to `GB-MOD-15/16`.

## Security and dependencies

Source byte hashing, media permission, and provider policy are maintained by stage06/07 and must be rerun at point of actual use; an in-memory `ContextPacket` or its SHA is a reproducibility reference **not** an authorization grant or an authenticated model receipt. The packet's `packet_sha256` is an ordinary SHA of canonical JSON, **not** a digital signature. Caller must enforce project authorization and restrict access to transcript/OCR strings; never persist or log raw packet contents without a separate approved retention policy.

This implementation requires a real Group A completed-result producer (`GA-MOD-18`), source byte/proof catalog and current QDP dispatch not yet demonstrated across independent branches. Unit fixtures do **not** prove live A→B operation. Image byte attachments, provider capability, token quoting, cost reservation and model spending belong to future stages. No new migration or change to `005_content_features_b.sql` is needed here.

## QA

Run without network, paid provider or database:

```bash
python -m unittest discover -s tests -p 'test_group_b_context.py' -v
```

Tests cover 10-minute, 100-frame video, 100-slide carousel, deterministic first/last/early-frame selection, UTF-8/Unicode and hostile OCR instructions as **untrusted** data, corrupted content digest, missing text reader, text truncation refusal, image/serialized-byte/token caps, omitted evidence accounting, changed manifest, blocked readiness, and fresh point-of-use gating. Windows/Linux and PG17-hosted runs are added to the Group B standalone CI workflow. **No CI/Python PASS is claimed before the remote job actually finishes.**

**Next stage:** `GB-MOD-09` Model Provider and capability probe, with no default paid activation.
