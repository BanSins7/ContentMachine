# Group B — GB-MOD-19 B7 CTA occurrences (source-first)

**Code status:** implemented in `feature/group-b-full-module`; offline unit and disposable PG17 tests authored, not independently observed green. No production paid inference, media decoding, copy of SQL005, HTTP route or external peer write.

## Frozen SQL005 contract
- `content_b_cta_analyses`: `analysis_status` = `not_run|partial|complete|failed`; `taxonomy_version` = `1.0`; model version after verified provider response.
- `content_b_ctas`: one record per *occurrence*, `occurrence_index` starting at 1, with repeats retained in source order. Exactly 12 `cta_type`: follow, like, comment, share, save, profile_visit, link_click, app_install, purchase, sign_up, dm, other. Exactly 9 `destination_type`: current_post, profile, link_in_bio, website, app_store, comments, dm, other, unknown.
- `delivery` is 1 or 2 unique ordered labels from `speech|on_screen_text`; each detected channel requires its own verbatim supporting evidence. Technical video timestamp is original `start_ms/end_ms` (stored as seconds to three decimals), `slide_index` SQL NULL. Photo/mixed carousel `slide_index` is original ordered slide number, timestamps SQL NULL. An actual unchanged slide screen quote must be independently attested; image pixels or model output alone cannot authenticate a screen quote.
- Mixed carousels can have speech CTAs only on a genuine original **video slide** with source-linked ASR, video-absolute original-slide timestamps, authenticated frame/media and the correct slide index. Image-only carousels must never acquire fabricated speech. Video CTA times may not exceed the verified quoted ASR/OCR text interval. A quote supporting two distinct CTA goals can yield two separate semantic goal occurrences, but a duplicated identical goal at the same source position and refs is rejected.
- Only exact contiguous source utterances/screen text are stored, and `destination_url`, `destination_handle`, service, application name, keyword and other description must literally occur in independently verified utterance/screen quote. URLs must be literal HTTPS domain URLs, never model-invented. No guessing usernames/links from semantically vague CTAs.
- **Truth table for `content_b_cta_summary.has_cta`:** `complete` + N>=1 -> TRUE; `complete` + N=0 -> FALSE; `partial` + any N -> NULL; `not_run` -> NULL. Completeness is not model's word: an independent trusted verifier must attest a scan of the **entire original video** or all actual carousel slides. Zero OCR rows or finite image samples are not a negative proof.
- `low` confidence, destination `unknown`, omitted refs, partial model coverage and missing independent full scan force `partial` with `review_reasons`. Detected occurrences can be preserved for review without pretending complete.
- SQL005 trigger enforces source media rights, immutable completed parent, video-vs-carousel placement. Additional module checks enforce exact QDP run/GA version/result SHA/source media SHA and policy version, hash-verified selected OCR/ASR text, actual OCR-to-original-frame link, full selected/omitted evidence accounting, and authenticated delivery receipt for all selected image attachments.

## Stage19 files and boundaries
- `blocks/b7_cta.py`: deterministic validator, `B7CTAProposal`, `unknown_b7`, independently hash-pinned `prepare_b7_request` (never sends).
- `blocks/b7_schema.py`, `prompts/b7_cta_v1.txt`: strict OpenRouter-compatible structured JSON, optional fields as empty string. Untrusted OCR/ASR cannot change system instructions.
- `blocks/b7_storage.py`: optional one-shot, host-gated append of 1 analysis + 0..N ordered rows in the **caller-owned** transaction; checks precreated `pending|partial` `content_b_analyses`, project/run/media/CTA proposal SHA pins, SQL summary and complete readback. Does not set overall B1–B8 state `completed`, does not create any analysis or silently overwrite.
- `tests/test_group_b_b7.py`, `tests/test_group_b_b7_db.py`: synthetic provider/full-original-scan/slide-text admission doubles; no real signed detector or paid request. Wired to Ubuntu/Windows plus disposable PostgreSQL17.

## Trust-boundary TODO before real production use
1. Implement external `receipt_verifier.verify_b7_response(response,packet,delivered_image_refs)` binding the actual provider output, paid attempt budget, original prompt hash, and delivered image attachments.
2. Implement independent `full_scan_verifier.verify_b7_full_scan(manifest,packet,response)`: fresh rights, actual full original video or slide list; absence decisions must withstand uninspected spans/frames. Model's `coverage_claim='complete'` alone is never accepted.
3. Implement independent `slide_quote_verifier.verify_b7_slide_quote(manifest,slide,quote)` tied to source-authenticated OCR of original image carousel slides (the existing stage06 image-only slide proof is **not** text-OCR evidence).
4. Implement independent `admission_verifier.verify_cta_persistence(analysis_id,manifest,proposal)` with current QDP, GA, media-byte, project/source authorization, source revocation and valid provider/whole-scan receipts. Missing verifier defaults to hard-deny. Do not use tests' fake verifier in production.
5. End-to-end run of disposable PG17 plus CI and real peer producer; stage21/22 will add unified cross-B quality and transactionality.

## QA commands
```bash
python -m unittest discover -s tests -p 'test_group_b_b7.py' -v
CM_TEST_PG_DSN='postgresql://.../cm_group_b_test' python -m unittest discover -s tests -p 'test_group_b_b7_db.py' -v
```
CI uses `.github/workflows/group-b-module.yml`. The PG17 test refuses non-test DB names. CI status must be recorded as actually observed PASS/FAIL/QUEUED, not inferred from pushes.
