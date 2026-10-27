# Group B — GB-MOD-21 Unified Evidence & Schema Validator

**Scope:** Independent *second-line* schema/source sanity gate before stage22 atomic write. The earlier B1–B8 domain validators remain authoritative for their specialized semantic detector/receipt contracts. This stage does **not** send OpenRouter requests, decode media, create DB rows, replace Group A/QDP, authenticate rights, or mark the overall B analysis `completed` autonomously.

## Single evaluation API
`intelligence/group_b/validation/schema.py` provides `validate_block(block,manifest,candidate,packet,trusted_attestor,request_proof)` and `validate_bundle(manifest,candidates,packets,request_proofs,trusted_attestor)`. It accepts **typed existing domain proposals**, not arbitrary model output dictionaries. All accepted blocks require both a deterministic validation and an **independent live trusted attestor**.

- **`accepted`:** valid exact type/digest/version/current A/QDP/source pins, SQL005 enums/placements, original citations/ASR and frame-bound OCR integrity, and `trusted_attestor.verify_unified_block(block,manifest,candidate,packet,request_proof) is True`. Host is responsible for verifying the authentic paid attempt, model slug/version, trusted prompt SHA/schema and provider result, actual byte-delivered images/audio, full-media detectors/timelines, rights and current source snapshot. **A proposal SHA and in-memory `authenticated_*` flag cannot independently satisfy this gate.**
- **`needs_review`:** an authentic typed but unknown/partial/review proposal whose refs/positions/source hash still pass structural verification. It remains review-only and is never silently converted to negative `false`, `none`, an invented category ID, or finished B.
- **`quarantined`:** wrong proposal class/schema/digest/pins, forged frame, unverified original video time/slide, tampered ASR or OCR SHA/text, unknown enum, impossible metrics, inferred without true independent authentication or missing/denied attestor. No SQL write permitted.

`validate_bundle` reports all eight statuses, including missing blocks. `can_complete` is TRUE only if every block is `accepted` **and** a separately trusted `verify_unified_completion(manifest,reports,candidates)` returns exactly `True`. Stage21 is read-only; stage22 will implement safe transactional writes. For photo/mixed carousels B5/B6 video-only gaps remain unresolved by default (full completion blocked until explicit per-kind not-applicable semantics are defined), not a fake `none` row.

## Checks performed
- **Original evidence:** nested B4 feature citation lists plus regular claim references, duplicate/cross-publication A reference detection, original media asset/SHA/timebase and duration, original slide index, OCR-original-frame identity and timing.
- **Payload integrity:** strict per-block top-level allowed/required field set from the actual B1–B8 proposal contracts; arbitrary model-added or omitted fields are quarantined, even when the attacker recalculates an ordinary SHA digest. Except for truly optional B3 delivery-ref metadata, missing expected keys are forbidden.
- **Stage08 packet:** exact source/policy/QDP/A/media manifest hash pins; content-is-untrusted, `not_sent` contract; strict ref-carrier/content shape; byte-backed ASR/OCR content SHA-256; complete selected vs omitted accounting and independently recomputed counts. A packet's self-proclaimed `coverage_complete` cannot hide omissions.
- **B1 hook:** only SQL005 mechanisms/deliveries/nullable flags; hook inferred needs provider receipt; source-backed quotes.
- **B2 format:** approved video/carousel enum list only; correct modality and original media evidence, unknown not inferred.
- **B3 story:** SQL005 role/phase/boundary enum; source frame refs and original scene bounds, sampled plan digest. 'Other' role remains review-gated.
- **B4 visual:** three nullable/confirmed visibility booleans, AI-generation origin remains nullable and separately attested; no guessed absence from sparse frames.
- **B5 editing:** deterministic `B5Metrics` editing enum, nonnegative verified cuts and finite SQL005 quantitative values, **external fresh Group A shot-timeline attestor** required. No model request fabricated.
- **B6 subtitles:** video-only original complete detector, exact 3 types and 7 style fields; `subtitle_type=none` is an attested scan decision, not no OCR or classifier failure. The `B6SubtitleProposal` is verified independently.
- **B7 CTA:** 12 types, 9 destinations, exact channel enum, sequential original occurrences, finite original-duration-bounded video seconds vs source slide placement, recognized service and HTTPS URL. Zero events are never assumed `false` without an independently attested whole-original scan; `partial` remains review-only.
- **B8 topic:** category/topic/optional subtopic *existing stable SQL005 IDs*, approved registry snapshot and human-review-only unknown/merged-parent cases. Current registry revalidation/locks are applied by B8 storage stage20. No automatic new slugs.

## Test plan and limitations
- `tests/test_group_b_validation.py`: unknown/partial vs quarantine, stale publication, invented citations, prompt-injection OCR alteration, missing media, bad enums, implausible timestamp, malicious body, missing independent attestation and non-complete bundle. All external receipts are **synthetic test doubles**, never security evidence.
- Added `test_group_b_validation.py` to standalone Linux/Windows and disposable PG17 CI. **Not locally executed here, no PASS asserted**, because no mounted clone is available and Actions runs remain queued. Re-run under local environment to establish actual QA.
- No new migration and no edits to 005, `main` or peer branches.
- Integration MUST wire the actual independently authenticated signed Group A shot/video/slide proof, QDP/outbox/version/current rights and real provider receipt/budget gate. A test stub returning `True` is only valid within fixtures.

Commands:
```bash
python -m unittest discover -s tests -p "test_group_b_validation.py" -v
```
