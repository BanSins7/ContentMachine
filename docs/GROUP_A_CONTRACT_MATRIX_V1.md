# Group A v1 — Contract Matrix & Truth Semantics (GA-MOD-01)

**Status: FROZEN wire shapes, implementation-only; NOT proof of runtime integration.**
Branch `feature/group-a-full-module`, main baseline `218f5f3`.
No database migration, host mounting, paid AI call or cross-branch merge is part of this stage.

## 1. Observed peer contract matrix (read-only, 2026-10-26)

| Peer | Source observed | GA expectation | Present in clean main? | Status / handoff |
|---|---|---|---|---|
| QDP | `feature/qualified-data-pipeline-v1-20261023`; inspected QDP `integrations/contracts.py`, QDPHandoffPointer; branch head at review `f429c3d` | `qdp-handoff/v1` fields `event_id, project_id, qdp_run_id, video_id, analysis_group, input_sha256` | No | **unavailable** until GA-MOD-04 consumer and authoritative QDP read |
| D1 video A003 | main `003_video_features_a.sql` | `video_feature_a_analyses` + scene/speech/OCR/audio tables; analysis ID BIGINT | Yes | Schema exists, new GA writer absent |
| D1 carousel A004 | main `004_carousel_feature_a.sql` | `carousel_feature_a_analyses/items`, image/video ordering, A003 video-slide link | Yes | Schema exists, carousel extractor absent |
| Group B | main `005_content_features_b.sql` | `content_b_analyses.video_a_analysis_id` OR `carousel_a_analysis_id`, consistent video/source/kind | Yes, schema | No Group B consumer adapter; CTA owned by B |
| PSI transcript | main `project_sources/transcript_normalizer.py` | JSON `transcript/v1` where present; ASR `speech_detected/no_speech/asr_error` distinguish cases | PSI code yes | **unavailable** until explicit SHA bridge; PSI IDs never equal D1 video IDs |
| PSI sparse vision | main `project_sources/vision_engine.py` / vision jobs | `vision-prepare/v1`, `visual-preparation/v1` checkpoint as negotiated | PSI code yes | **unavailable** until hash verified; 8-frame samples ≠ full OCR coverage |
| Analytics | `feature/analytics-engine-qualified-pipeline-20261023` `analytics/contracts.py`, `aggregate.py` | `analytics-qualified/v1` reads A003/A004 with `state=completed`, verified media, matching B A-FK | Peer branch | Existing reader ignores partial A; `image_post` blocked by B2 |
| SRE | SRE-owned branch | Relevant QDP readiness only; no GA import of SRE internals | External | SRE selection Embeddings + Opus 5.5 does not alter GA DTO |

**Reality check:** QDP is already evolving in a separate branch; even an exact name/version match is *necessary but not sufficient* for running the consumer. A connection must re-read the authoritative current preparation and permissions under the same project/video/run/media lineage. Calling `peer_state(expected, observed)` only classifies unavailable/incompatible/available for a stated version. It does NOT authorize execution.

## 2. Frozen schema files and Python validators

| Wire name | Schema path | Validator |
|---|---|---|
| `qdp-handoff/v1` | `intelligence/group_a/schemas/qdp-handoff-v1.json` | `qdp_handoff_v1` |
| `ga-media-manifest/v1` | `intelligence/group_a/schemas/media-manifest-v1.json` | `media_manifest_v1` |
| `group-a-stream-truth/v1` | `intelligence/group_a/schemas/stream-truth-v1.json` | `stream_truth_v1` |
| `group-a-to-b/v1` | `intelligence/group_a/schemas/group-a-to-b-v1.json` | `group_a_to_b_v1` |

Runtime validators are in `intelligence/group_a/contracts_v1.py` (no import of QDP, PSI, Group B, Analytics, PostgreSQL).
Wire JSON Schemas constrain shape and common fields; cross-field rules, canonical UUID and SHA lineage are **also** checked by Python. An external consumer MUST apply equivalent semantic checks rather than relying on JSON Schema shape alone.

QDP-owned Python pointer actually uses **`input_sha256`**, not the conceptual `preparation_input_sha256` from the earlier plan. This is a deliberate observed-contract decision; no unannounced aliases. The `event_payload_sha256` is optional on the pointer because the QDP `PreparationConsumer.receive` signature supplies it separately. Consumer implementation and ACK come in GA-MOD-04.

`project_id`: 32 lowercase hex **string**; `qdp_run_id/event_id`: lowercase canonical UUID; `video_id/media_asset_id/A003/A004 IDs`: positive INTEGER, never boolean; all hashes are 64 lowercase hex digits. Version strings are exact matches, never substring-coerced. Seconds are decimal **strings** at exactly 3 places; no floating point, fake timings, or slide swipe-duration guesses.

## 3. Content kinds / canonical output table

| `content_kind` | Manifest item sequence | A003/A004 primary result | Required stream names |
|---|---|---|---|
| `video` | exactly 1 video | A003 video analysis | technical, speech, screen_text, audio, subtitles |
| `slideshow_video` | exactly 1 video (encoded as video) | A003 video analysis | same as video |
| `image_post` | exactly 1 image | A004 carousel analysis | slides, screen_text |
| `photo_carousel` | 2..100 ordered images | A004 carousel analysis | slides, screen_text |
| `mixed_carousel` | 2..100 images/videos, at least one of each | A004 with A003 per video slide | slides, technical, speech, screen_text, audio, subtitles |

Image slides have **no duration_sec**. Every manifest has exact positive asset IDs, distinct stable slide index 1..N, SHA-256 of actual asset (to be verified by GA-MOD-05), byte size, and opaque local path reference (never raw filesystem path in consumer DTO). Manifest digest = SHA-256 of canonical JSON excluding `manifest_sha256` itself. A future mutable media revision must create another manifest/hash and GA run, not silently update the old output.

## 4. Truth semantics / observed zero vs missing

| Status / field | Allowed interpretation | Forbidden interpretation |
|---|---|---|
| `not_run`, `reason_code` nonnull | No attempt/observation | 0 words, no subtitles, no music |
| `unavailable`, reason e.g. `asr_error` | Attempt not measurable / dependency unavailable | `no_speech` |
| `partial`, reason required | Bounded incomplete coverage, evidence_count may be nonnull | Full observation coverage |
| `complete` + `evidence_count > 0` | Has pinned evidence hashes for this stream; `verified_absence=false` | A guessed measurement from title/URL |
| `complete` + `evidence_count=0` + `verified_absence=true` | Genuine measured absence, **with separate pinned negative-observation proof** | Inference from no stored segments alone |
| `has_subtitles=unknown` (A003) | Detection not proven | Subtitles absent |
| `CTA` | Group B semantic inference, append-only B analysis and read projection | Group A inferring CTA, changing A003 `cta_start_sec`, or circular B→A writer |

Status `completed` in `group-a-to-b/v1` requires *all applicable streams complete*, each with proof record(s); otherwise `partial`. This prevents A1 scene-only work being passed to Analytics as a complete Group A result. A3/A4 OCR/VAD/ASR stages must preserve exact origins, clocks and confidence; no pseudo-confidence for FFprobe measurements. Downstream Group B/Analytics must verify the same `project_id + qdp_run_id + video_id + manifest_sha256 + qdp_input_sha256` and that evidence asset hashes occur in the verified manifest. The schemas do not assert those hashes have been read from bytes yet: GA-MOD-05 will implement file verification.

## 5. Compatibility and safety rules

- `video_a_analysis_id` XOR `carousel_a_analysis_id` must match kind; never confuse IDs, coerce numeric text to integers, or treat PSI `source_version_id` as D1 video ID.
- Cross-project, foreign-video, mismatching QDP SHA, mismatching media manifest, unknown versions and unrecognized fields **reject** rather than silently fall back.
- `group-a-to-b/v1` carries measurement/evidence pointers only. It does not require semantic B output or classification, and has no CTA field.
- JSON canonical SHA uses sorted keys, UTF-8, no nonfinite numbers, no `default=str` conversion. All counts nonnegative exact integers, decimals fixed width.
- Current A1 `TechnicalMeasurement.to_dict()` and `parse_config()` remain unchanged. A003 partial/unknown semantics preserved. No applied migrations rewritten.
- A001..A004 and Analytics release compatibility **remain unverified at runtime** until GA-MOD-18..24, despite the documented schema mapping.

## 6. Test command, evidence and limitations

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_*.py
python -m unittest discover -s tests -p "test_group_a_*.py" -v
```

`test_group_a_contracts_v1.py` covers canonical/noncanonical IDs, invalid versions,
5 content kinds, index order, digest drift, duplicate asset ID, cross-project/video,
wrong evidence SHA, missing pinned proof, `no_speech` vs `asr_error`, and
`complete` vs `partial`. A real JSON Schema conformance pass uses optional
`jsonschema` (skips if absent); the pure-Python validator tests are mandatory.

**QA != code completion.** See `GROUP_A_IMPLEMENTATION_TRACKER.md` for explicit
PASS/FAIL/QUEUED/NOT RUN statuses; a queued GitHub job is neither success nor
test failure. The GA module is not integration-ready yet.
