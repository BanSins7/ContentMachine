# GB-MOD-13 — B1 Hook, source-first multilabel semantic classification

**Branch:** `feature/group-b-full-module`. **Contract:** `group-b-b1-hook/v1` and original immutable `005_content_features_b.sql`. **Files:** `blocks/{b1_hook,b1_schema}.py`, `prompts/b1_hook_v1.txt`, `tests/test_group_b_b1.py`. No SQL migration or B1 persistence occurs here. The OpenRouter `POST` and `PaidWorker` remain **disabled**.

## Taxonomy and exact 005 columns

B1 is one overall hook plus **ordered, repeatable hook parts**, not a single primary type. Ten independent mechanisms, in canonical array order: `problem`, `promise`, `curiosity`, `myth_busting`, `mistake`, `surprise`, `story`, `challenge`, `comparison`, `proof`. Multiple mechanisms may coexist on the same opening and in multiple parts; there is no `primary_hook_type`. Delivery may combine `speech`, `on_screen_text`, `visual_action`, and `sound`, but each channel **must have independently authenticated matching evidence**.

The five original flags are `is_question`, `has_number`, `direct_address`, `has_negation`, and `is_command`. The strict model JSON schema represents each as one of `"true"`, `"false"`, `"unknown"`, then the B1 validator maps to SQL `TRUE`, `FALSE`, `NULL`. Missing measurements are not false. The B1 unknown helper returns an honest non-inferred proposal with nullable values; for a carousel, an unpositioned unknown proposal is **not directly INSERT-able** due to the 005 slide-index guard. Persistence/review belongs to GB-MOD-21/22.

`B1HookProposal.hook_db_fields()` maps exactly `start_sec`, `end_sec`, `slide_index`, `spoken_text`, `screen_text`, `visual_description`, mechanisms[], five nullable flags, delivery[], `classification_status` and `taxonomy_version='1.0'` for the original `content_b_hooks`. `parts_db_rows()` maps ordered 1-based `part_index`, start/end seconds or `slide_index`, optional verbatim text and mechanisms to `content_b_hook_parts`. The additional source refs live in the versioned proposal/audit manifest, **not in nonexistent 005 columns**. No DB write is attempted at this stage.

## Untrusted model output and honest authorization boundaries

1. Stage06 must authenticate the exact QDP/Group A/source/media bytes and evidence content; Stage07 must check current source policy/rights and B1 readiness; Stage08 prepares a `group-b-context/v1` packet with SHA-pinned evidence. B1 checks exact project, B run, video, GA SHA and GB06 manifest SHA again. It checks that **every B1-relevant source observation** is selected and truly present; a falsified `coverage_complete=true` bit or recomputed JSON digest cannot hide a source reference.
2. `prepare_b1_request` uses the Stage09 OpenRouter **strict** JSON schema `B1_OUTPUT_SCHEMA`, the checked-in immutable instructions `prompts/b1_hook_v1.txt`, the actual model catalog and explicitly byte-verified image attachments. Returns `B1PreparedRequest` containing a **not-sent** request body, exact request SHA and prompt SHA. No call to a provider, price reservation, network transport or model execution.
3. A candidate **cannot become inferred merely because it matches JSON Schema** or because a model-like dictionary claims success. `validate_b1_response` requires a `VerifiedChatResponse` and a **trusted host-owned** `receipt_verifier.verify_b1_response(response,packet,image_refs,audio_refs)` returning literal `True`. This verifier **must** authenticate the provider request/response against the durable approved/charged attempt and verify which media were actually delivered. No production provider receipt verifier is installed in GB13; CI exercises a fake verifier. Never accept an arbitrary client-supplied callback or `approved=true` as real authorization. GB09+GB10+GB11 still deny all paid dispatch.
4. Candidate `spoken_text`, `screen_text`, and each part's nonempty `verbatim_text` must be an **exact substring** of actual SHA-256-verified UTF-8 ASR/OCR source strings in the selected context and have a matching referenced source/time position. B1 **rehashes the actual Unicode text** against the stage06 pinned observation `content_sha256`. Consequently, substituting an invented quote and recalculating the context JSON hash does not work. No paraphrase is ever passed off as `verbatim`. ASR/OCR content is untrusted **data**, not system or developer instructions.
5. Visual descriptions require `visual_action` plus original sampled-frame/slide refs **actually delivered** (not merely included as opaque refs). Sound requires an actual audio ref **and a trusted receipt proving audio delivery**; Stage09 currently prepares image attachments, not raw audio. Unknown channel (or model claiming a channel with only an undelivered ref) fails closed.
6. Video uses **observed 0 ≤ start_ms < end_ms ≤ 5000** and SQL seconds derived exactly from those milliseconds. Each part must lie inside the hook and have chronological nondecreasing starts. Carousel uses **positive, verified slide_index**, `start_ms=end_ms=0` in JSON and NULL SQL times; each part refers to the hook slide. No invented carousel timestamps. The validator forbids gaps in part indexes, undeclared mechanisms, refs outside the selected source or part's time, empty unsupported parts, overlong text and duplicate labels, while allowing genuinely repeated parts/quotes in separate locations.
7. The result is a deterministic `group-b-b1-hook/v1` **proposal**, with project/run/media/GA SHA, source manifest SHA, provider request/response ID, refs, classification status and canonical output SHA. It is **not** automatically written to `content_b_analyses` or considered final business truth. Human review and full B1–B8 storage/approval are future stages.

## QA and real integration blockers

`tests/test_group_b_b1.py` includes strict Stage09-compatible schema, overlapping mechanisms and channels, correct nullable flags, ordered and repeated parts, exact Unicode ASR/OCR, fabricated quotes and modified source text despite rehashed envelope, fake missing/duplicate refs and undelivered image, out-of-first-5s timestamps, reversed parts, carousel slide validation, authenticated receipt denial and **UNSENT** image request planning with real byte-signature checks. It uses no live LLM/API key, paid charge or external HTTP.

Run:
```bash
python -m unittest discover -s tests -p 'test_group_b_b1.py' -v
python -m unittest discover -s tests -p 'test_group_b_provider.py' -v
```

Added to Group B Windows/Linux and PostgreSQL17-hosted workflow. **Test source committed is not proof of PASS; examine completed remote CI logs separately.** Production model invocation, request/receipt attestation and direct real A/QDP source catalog remain **NOT RUN / BLOCKED_EXTERNAL**. Any failure to retrieve A text/media/provider receipt must produce unknown or unavailable, never guessed `inferred`.

**Next:** `GB-MOD-14` — B2 Format classifier with video/carousel type-specific enums and source-grounded validation.
