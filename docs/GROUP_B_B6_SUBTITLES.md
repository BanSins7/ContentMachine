# Group B — B6 Subtitle Style (GB-MOD-18)

**Branch:** `feature/group-b-full-module`. **Status:** code and synthetic QA committed; real Group A subtitle detection and signed full-video inspection NOT authenticated. No paid calls, automatic scanning, production writes or schema 005 changes.

## Contract and source of truth
- Works **only** with `video` or `slideshow_video` backed by one pinned original video asset with measured positive duration. A `photo_carousel`, `mixed_carousel` or static image is not a video subtitle source; screen text on a slide is *not* a subtitle.
- Exact SQL005 `content_b_subtitles`: `subtitle_type` = `none|standard|dynamic`, plus seven fields `position` = top/center/bottom, `font_size` = small/medium/large, `font_style` = regular/bold/extra_bold, `text_color` = `#RRGGBB`, nullable `highlight_color` = `#RRGGBB`, `background_style` = none/outline/shadow/box, `display_mode` = line/phrase/word_by_word. Exactly all seven must be NULL for measured `none`; otherwise all except optional highlight_color required.
- Full-video inspection means the **actual original media**, not only selected sampled frames. Absence of OCR, sparse frame predictions, missing audio or `context.coverage_complete` does not prove `none`. The detector must authenticate the full temporal scope and distinguish captions from titles, static on-screen annotations and text slides.
- Positive `standard` and `dynamic` require SHA-pinned genuine A OCR and linked original video frame refs, independent caption/temporal inspection and measured style. `dynamic` requires a measured word-by-word display mode or multiple distinct dynamic frames proving an animation/change; `standard` rejects such evidence. Motion evidence does not come from a model's unsupported guess.
- The Group B B6 context packet is **UNSENT**. It must match exact project, run, QDP, GA-result SHA, media SHA, policy, original A media-asset ID, timestamp, OCR-to-frame linkage, all selected/omitted refs and source-derived coverage counters. OCR text is untrusted bytes and hash checked before use. A locally recomputed digest is *integrity*, **not** attestation.
- The injected `detector_verifier.verify_b6_scan(manifest,packet,scan)` must independently verify the signature/identity of the actual inspected source, rights, full original-video coverage, caption-versus-static-text, font/color/position/background/display-mode measurements, and that the inspector evidence hash identifies authentic immutable inspection output. There is **no default implementation** of that trust boundary. Missing/false/exception -> B6 unavailable.
- Low confidence/partial inspection/unknown produces `needs_review` and is not SQL-persistable, even with a locally signed-looking scan. Call `unknown_b6` for source unavailable/OCR unavailable without inferring `none`.

## Execution and persistence
1. Obtain exact current QDP+GA frozen version and permitted original video (stage 05–08); construct the B6 context. A future connector must produce a trusted `group-b-b6-subtitles/v1` scan satisfying exactly the validator's explicit keys and including the independent inspector SHA.
2. `validate_b6_subtitles(manifest,packet,scan,detector_verifier)` creates immutable hash-pinned `B6SubtitleProposal`. It has no network, OCR or model execution. Only `can_persist` proposals may call `db_fields()`.
3. The host must freshly authorize source rights, media bytes, QDP/A/current policy, detector identity and complete inspection, create or reuse an exact-pinned *pending/partial* SQL005 parent with `group_b_project_id`, `group_b_run_id`, `group_b_media_evidence_manifest_sha256` and `group_b_b6_subtitle_proposal_sha256` in `method_details`.
4. `persist_b6_subtitles` is opt-in with an **independent** `admission_verifier.verify_subtitle_persistence(analysis_id,manifest,proposal)`, one-shot INSERT of precisely eight SQL005 B6 columns, parent `partial` state and readback inside the caller's existing transaction. No overwrite, no implied B1–B8 completion.

## QA
- Synthetic pure suite: `python -m unittest discover -s tests -p "test_group_b_b6.py" -v`; fake inspector, no decoding, real OCR or signed provenance.
- Disposable PostgreSQL 17 suite: `CM_TEST_PG_DSN=... python -m unittest discover -s tests -p "test_group_b_b6_db.py" -v`; refuses non-test DB. This is still a fake host/detector, not real end-to-end producer evidence.
- Windows/Linux and PostgreSQL17 workflow: `.github/workflows/group-b-module.yml`; CI must be observed PASS before claiming green. Paid POST not enabled.

**Hard blockers:** real Group A subtitles producer, full-original-video detector with independently verifiable signature, OCR/image frame byte evidence, actual admissible rights/GA+QDP handshake, and live source video QA. Until then B6 cannot truthfully declare whole-video `none` or definitive style in production.
