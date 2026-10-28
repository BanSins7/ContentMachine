# GB-MOD-17 — B4 Visual Content: source-first, fail-closed four SQL fields

**Branch:** `feature/group-b-full-module`. **Original schema:** `intelligence/migrations/005_content_features_b.sql` (**unchanged**). **Implementation:** `intelligence/group_b/blocks/{b4_visual,b4_schema,b4_storage}.py`, `prompts/b4_visual_v1.txt`. **Tests:** `tests/test_group_b_b4.py`, `tests/test_group_b_b4_db.py`. No model calls, cost reservation or automatic SQL persistence occur during visual classification.

## 1. Exactly four columns, unknown is not FALSE

Original SQL005 defines `content_b_visual_content(analysis_id,ai_generated,person_visible,product_visible,logo_visible)`. The four component fields, in that exact order:

| Original B4 column | Stored type | When allowed |
|---|---|---|
| `ai_generated` | **nullable** BOOLEAN | `TRUE` only from host-attested signed generation provenance; `FALSE` only from independent **full-chain verified non-AI** origin; otherwise `NULL` — never judge AI source based on aesthetic |
| `person_visible` | NOT NULL BOOLEAN | `TRUE` when source SHA-verified delivered original visual ref supports it; `FALSE` **only** with separate authenticated exhaustive original-media absence proof |
| `product_visible` | NOT NULL BOOLEAN | Same positive/negative rules |
| `logo_visible` | NOT NULL BOOLEAN | Same positive/negative rules |

**Visibility is about actual pixels, not words or metadata**: a creator mentioning a product in subtitles does not make it visually present. A single confirmed frame can prove *existence* at some point in the publication, but failing to detect something in ten sampled frames does not prove it is *absent across the whole video*. This asymmetry drives B4's strict conservative design.

`B4VisualProposal` may contain `None` for any unproven visible field and has explicit `classification_status=inferred|needs_review|unknown`; `db_fields()` is **deliberately unavailable** until all three visible flags are genuinely boolean with full authenticated visual pass, no open review reasons, and a verified source/delivery/model receipt. In contrast, a SQL row with `ai_generated=NULL` is legitimate if the other three independent properties are fully established. No speculative `false` is inserted solely to satisfy a NOT NULL column.

## 2. Strict model contract and evidence handling

`B4_OUTPUT_SCHEMA` describes *only* the three observable visible properties, each with exact fields `assessment=present|absent|unknown`, `confidence=high|medium|low`, `evidence_refs` list of original sampled frame or slide SHAs. **AI origin is not a property of a vision model response.** Any model output containing `ai_generated`, style metadata, UGC, people's identities, probabilities or free-form hidden keys is rejected.

`validate_b4_visual` checks:
1. Stage06 verified `group-b-media-evidence/v1` manifest and Stage08 **B4** context packet with identical project, B/QDP run, video, exact GA result id+SHA, source policy version, source manifest SHA, original content kind, and both UNSENT markers. All selected/omitted observation refs are reaccounted (so recomputing the JSON hash after hiding evidence does not work). Original pixel evidence must retain full content/asset SHA, original asset ID, video-absolute frame time or exact 1-based slide ordinal. A mixed carousel can have an actual video slide with verified sampled frame, not a synthetic photo.
2. A trust-domain-owned `receipt_verifier.verify_b4_response(response,packet,delivered_image_refs)` returning literal True. The host must independently verify **actual** attached PNG/JPEG/WEBP bytes and request SHA, approved provider attempt, response ID/model/usage and current source rights. An opaque Stage08 ref is not an actual vision delivery. B4 supplies no production receipt implementation and cannot itself authorize a paid execution.
3. For **present**, at least one real delivered sampled-frame/slide proof must be cited for the feature; repeated refs or refs absent from the actual packet are rejected. `confidence=low` always requires review.
4. For **absent**, the model supplies no positive ref; its negative assessment is only a candidate. The trusted `absence_verifier.verify_whole_media_absence(manifest,feature,delivered_refs)` must separately authenticate an **exhaustive ORIGINAL-source** inspection, not the finite Stage08 sample, and all visual source selection must be complete. Otherwise value remains `None` and the review ledger records `<feature>_absence_unverified`. Without a trusted absence port all three visible fields may remain unpersistable rather than fabricating false values.
5. Temporal/slide coverage is conservative: a normal video's selected verified frames should include more than one distinct measured timestamp, an early sample in the first 5 sec, and a sample in its last quarter; a carousel should include every original 1-based slide (and at least two). Missing original frames/slides or low confidence produce `needs_review`, **even if the LLM JSON claims high confidence**.
6. `origin_verifier.verify_ai_origin(manifest)` is a completely independent host-owned provenance resolver returning a verified, SHA-pinned `signed_generation_provenance` (generated=true) or `full_chain_verified_non_ai` (generated=false) claim. A missing attestor or no conclusive evidence yields NULL. Mismatched or fraudulent attestation is rejected. Visual style, polished graphics, impossible images, any LLM pronouncement or absence of C2PA metadata **cannot** establish either generated or not-generated.

`unknown_b4(manifest,reason)` represents the no-visual-pass path with three nullable unknown values and zero SQL writes. Nothing from title/caption/ASR-only content is treated as a visual pass. `prepare_b4_request` emits a **strict, hash-pinned, UNSENT** OpenRouter-compatible JSON-schema request with the trusted versioned prompt and actual SHA-verified media attachments, using the Stage09 capability checker, no API key/POST or price authorization. Real model delivery and source proofs remain future integration responsibilities.

## 3. Opt-in PostgreSQL 17 one-shot persistence

`persist_b4_visual(conn,analysis_id,manifest,proposal,admission_verifier)` requires:
- An already-created original `content_b_analyses` parent in `pending` or `partial`, same video/source/policy/content type and locked `FOR UPDATE`. No new analysis, source, or policy version is created by B4.
- Parent `method_details` SHA pins for **exact** Group B project, run, source media-evidence manifest and B4 proposal, set by trusted host before calling this adapter.
- Fresh host-owned `admission_verifier.verify_visual_persistence()` checking current QDP/GA/media-source rights/bytes and authenticated actual remote model delivery receipts.
- `proposal.can_persist` and exactly four SQL005 typed values; AI origin may be NULL, visibility may **never** be NULL.
- One-shot transaction: existing B4 component row must not be overwritten; a revised analysis requires a distinct `analysis_key`. After inserting exactly `ai_generated,person_visible,product_visible,logo_visible`, read back the row and leave the parent analysis **partial** (never falsely `completed`). A refusal/SQL conflict/view mismatch must be rolled back by the **caller-owned transaction**.

The standalone adapter is not a public endpoint and supplies **no live permission/grant** implementation. Tests use only fake host verifiers and disposable source/media fixtures. Production system **must not** accept an attacker-supplied callback as valid authorization.

## 4. QA coverage and remaining work

`tests/test_group_b_b4.py` asserts strict 3-only model fields and exact four SQL columns, source-grounded TRUEs, unknown AI NULL, both independently verified AI-origin Boolean directions, impossible AI-style claim, sampled-frame-only absent is not FALSE, exhaustive full-media absence gate, low confidence/unknown review, title-only no visual pass, hidden/forged context evidence even if envelope SHA recomputed, QDP/GA/policy/source swap, video/carousel/mixed slide, real PNG attachment hashes, deterministic UNSENT request and denied fake provider receipts. `tests/test_group_b_b4_db.py` exercises original four-field PG17 writes, true AI origin vs unknown NULL, one-shot replay refusal, changed pins/failed admission, NULL/partial rejection, and parent state; it **skips without a safe disposable test DSN**.

```bash
python -m unittest discover -s tests -p 'test_group_b_b4.py' -v
CM_TEST_PG_DSN=postgresql://.../cm_group_b_test python -m unittest discover -s tests -p 'test_group_b_b4_db.py' -v
```

Both tests are wired into the Group B Windows/Linux and PostgreSQL17-hosted workflow. **Source and tests committed do NOT mean tests passed.** Real Group A/QDP integration, full-video independent absence attestation, signed origin provenance, authenticated OpenRouter model/delivery receipts and approved paid execution are **NOT RUN/BLOCKED_EXTERNAL**. No real B4 prediction has been persisted and no production LLM charges occur in this stage. Original SQL005 and main remain unchanged.

**Next:** `GB-MOD-18` — B6 Subtitle style, original three subtitle types and seven permitted features with source-first validation.
