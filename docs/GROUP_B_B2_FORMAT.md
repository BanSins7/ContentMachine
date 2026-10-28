# GB-MOD-14 — B2 Format classifier: exact, evidence-linked video/carousel taxonomy

**Branch:** `feature/group-b-full-module`. **Owner:** `intelligence/group_b/blocks/{b2_format,b2_schema}.py`, `prompts/b2_format_v1.txt`. **Schema:** `group-b-b2-format/v1`, taxonomy `1.0`. **SQL source of truth:** `intelligence/migrations/005_content_features_b.sql` (**UNCHANGED**). Stage14 creates only reviewable proposals and UNSENT plans, not model requests, payments or database writes.

## 1. Original SQL 005 is the compatibility boundary

The `content_b_formats` table has **five** component columns: `video_primary`, `video_secondary`, `carousel_format`, `classification_status` and `taxonomy_version`. `B2FormatProposal.db_fields()` returns exactly these fields, with optional classifications mapped to SQL NULL.

Video `content_kind in ('video','slideshow_video')` accepts exactly one **required primary** from the following 12 formats, plus at most one **distinct optional secondary**:

| Video formats (12) |
|---|
| `talking_head`, `dialogue_interview`, `skit`, `demonstration`, `product_showcase`, `screen_recording`, `pov`, `reaction`, `voiceover_montage`, `animation_motion_graphics`, `slideshow_video`, `vlog_bts` |

`photo_carousel` / `mixed_carousel` accepts only one required `carousel_format` from these 8:

| Carousel formats (8) |
|---|
| `explainer`, `listicle`, `step_by_step`, `comparison`, `storytelling`, `case_study`, `product_catalog`, `visual_gallery` |

The B2 model cannot combine video and carousel formats; `video_secondary` cannot repeat `video_primary`. There is **no** `UGC`, `people`, `layout`, `text_density`, `image_post` or invented additional label in this version. Such properties require a separately approved contract, not undocumented columns or speculative guesses. A carousel containing a video slide is still a carousel publication (not automatically `slideshow_video`).

## 2. Strict JSON proposal and review policy

`B2_OUTPUT_SCHEMA` is compatible with the audited GB-MOD-09 strict JSON subset (object/required fields/arrays/string enums, `additionalProperties:false`); its **exact keys** are `video_primary`, `video_secondary`, `carousel_format`, `confidence` (`high`, `medium`, `low`) and `evidence_refs` (1–40 actual original visual observation SHAs). A JSON empty string denotes a missing optional format; the validator turns it into `None` for the 005 row. It enforces all cross-field rules *independently* because this schema subset does not support `oneOf`.

Valid response labels alone are **not proof** of truth. `validate_b2_response(manifest,packet,response,receipt_verifier,delivered_image_refs)` checks:
- Exact, digest-verified stage06 media manifest; project, B run, publication, GA result SHA, content kind and stage08 `group-b-context/v1` packet SHA, with `delivery_state=not_sent`.
- Every selected image/slide ref matches a real pinned `sampled_frame` (video) or `slide_ref` (carousel), including asset/content SHA, media ID, measured time or slide index, carrier and actual boolean that image pixels are not in the context packet.
- **Full inclusion/omission accounting** against the actual stage06 observation manifest: no missing, duplicate or invented omitted refs and no forged `coverage_complete`, available image counts, selected/omitted totals. A successfully recomputed JSON digest cannot hide deleted source evidence.
- **Actual media delivery:** all selected visual refs require a literal matching tuple of *attested delivered* images. An opaque Stage08 ref is never treated as an image received by the model. Stage09 must build real base64 attachments from SHA-verified PNG/JPEG/WEBP bytes. `receipt_verifier.verify_b2_response(response,packet,delivered_image_refs)` must be a **trusted host-owned verifier** checking actual provider request ID, model response, authenticated approval/charge and attachment SHA receipt. Arbitrary user `verify=true` is not a production authorization; GB14 ships no production verifier.
- `evidence_refs` must be an explicit valid unique subset of selected delivered visual refs. No model-created timestamps, missing image URL placeholders or untrusted OCR text can act as image evidence.

`classification_status` is computed **by the validator**, not dictated by the model:

| Status | Rule |
|---|---|
| `inferred` | Verified provider/actual media delivery; valid content-kind labels; `high` or `medium` confidence; all selected visual evidence cited; complete visual-selection accounting; and sufficient temporal/slide coverage |
| `needs_review` | Any otherwise valid provisional candidate with `low` confidence, unselected source visuals, incomplete Stage08 context, selective evidence citation, weak first/end video time coverage, fewer than two measured frame times, or incomplete slide coverage |
| `unknown` | No independently authenticated model result / unable to classify. `unknown_b2` yields NULL labels and a typed reason; **never** a guessed `talking_head`, `visual_gallery` or silent negative |

For a sufficiently covered video, selected sampled frames must include at least two distinct measured timestamps, an early sample within the first 5 seconds and an observed sample in the final quarter of measured duration. For a carousel, at least two distinct observed slide indices covering the original media-slide set are needed for `inferred`. These are **conservative completeness heuristics**, not proof that the semantic label is correct, and they may yield `needs_review` on real data. Semantic confirmation still needs domain review and downstream GB21/22 validation. No raw video inference or unverified image understanding is claimed.

## 3. Unsent model request and execution denial

`prepare_b2_request(catalog,model,packet,max_output_tokens,attachments)` constructs `B2PreparedRequest` with:
- Checked-in trusted static `prompts/b2_format_v1.txt` SHA, selected exact model from the GB09 catalog with image-input/strict-json support.
- Strict schema plus `provider.require_parameters=true`, `stream=false` and only **actual matching SHA-verified** image payloads; all original OCR/video descriptions remain untrusted JSON data.
- Canonical `request_sha256`, with a final byte-cap check **after** adding the trusted prompt. It does **not** send, quote, reserve, dispatch, persist or spend anything.

Stage09 `OpenRouterTransport.post_json` and GB11 `PaidWorker.run_once` are still intentionally **DISABLED**; reserve/approval in GB10 is not remote-dispatch authorization. Stage14 does not fabricate successful live classifications or pretend that real provider receipts exist. No migration is introduced or modified in this stage.

## 4. QA and pending integration

`tests/test_group_b_b2.py` uses only synthetic SHA-pinned manifests, fake authenticated receipts, and offline image bytes. Cases include: 12/8 enum sets, one primary plus distinct optional secondary, video vs carousel crossover, unsupported `image_post`, high-confidence full evidence, low-confidence review, sparse/early-only frames, missing carousel slides, selective proof citations, forged count/hidden/duplicate omissions, swapped GA/source/media hashes, missing delivery/receipt, genuine image-byte SHA mismatch, strict OpenRouter JSON schema and UNSENT deterministic request with no actual HTTP. Tests are added to Group B CI Windows/Linux and PostgreSQL-hosted jobs.

```bash
python -m unittest discover -s tests -p 'test_group_b_b2.py' -v
python -m unittest discover -s tests -p 'test_group_b_provider.py' -v
```

**QA truth:** Adding tests to a workflow is not a PASS. Actual CI/Python/PG17 status must be observed before claiming green. Real QDP→A→GB media catalog, receipt verification, budgeted paid dispatch and canonical B persistence are **NOT RUN / BLOCKED_EXTERNAL**; reviewable model result fixtures are not a demonstration of working remote semantics.

**Next:** `GB-MOD-15` — B3 Story sampling and deterministic observation scheduling.
