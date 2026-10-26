# GB-MOD-06 — Source-first Media Evidence Manifest v1

**Owned by** `intelligence/group_b/context/`. **Peer versions:** QDP `qdp-handoff/v1`, Group A `group-a-to-b/v1`, GA media `ga-media-manifest/v1`. **Manifest version:** `group-b-media-evidence/v1`.

## Purpose and limits

Input is **one exact verified A result** via `read_exact_a(conn,b_run_id,ga_result_id,expected_sha256)`, never the latest A result. `group_b/context/manifest.py::build_media_evidence_manifest` re-reads the corresponding Group B inbox+GA run, exact ordered GA media pins and D1 media/source/policy rows. It requires explicit allowlisted local roots and an injected **authoritative** `observation_catalog.resolve_a_proof(kind,ref_sha256)` adapter. A catalog cannot be synthesized from filenames or titles. Without real GA outputs/catalog, the function is fail-closed and no successful analysis is asserted.

Before use all upstream evidence requires exactly matching `project_id`, canonical `video_id`, `source_id`, media asset ID and source SHA. The builder requires current source status, `can_analyze_media`, active source policy+permission, unexpired non-metadata media, exact GA slide order and checked `ga_media_refs.byte_size`. It rehashes each file in bounded chunks to match current D1 and GA SHA256; verifies file identity (size, inode/mtime before/after read), rejects symlink paths, arbitrary/relative paths and any file outside explicit roots. It checks **container signatures**, not FFmpeg/decoded media truth; images include PNG/JPEG/WEBP/GIF/BMP, videos require MP4/MOV `ftyp` or WebM/MKV EBML. Exotic image formats without a verified signature are intentionally unavailable.

Every frozen A `evidence` tuple (`kind,ref_sha256,asset_sha256`) is carried forward from `GroupAExactPin.evidence_refs`. For each proof, an injected catalog must return a registered observation record with exact matching IDs, kind, source asset SHA, **content file path** and expected SHA. The content bytes (OCR/transcripts included) are independently SHA-verified; **the bytes and paths are never copied into the manifest**. The manifest stores only IDs, normalized timing, source SHA and content SHA. Unknown catalog objects, missing references or duplicate proof references fail closed. Empty full-A proof collections are not treated as measured zero.

### Observation kinds

| A proof kind | Group B observation | Timing |
|---|---|---|
| `scene` | `scene` | Observed video interval |
| `transcript` | `transcript_segment` | Observed speech segment interval |
| `screen_text` | `ocr_span` | Text interval must reference one hash-pinned sampled frame |
| `frame` | `sampled_frame` | Measured single point on video timeline |
| `audio` | `audio_event` | Measured audio interval |
| `slide` | `slide_ref` | **Ordered slide index only, no invented timestamp** |

Observations explicitly declare `timebase: video_absolute | clip_relative | none`. `clip_relative` requires known `clip_origin_ms` and `clip_duration_ms` within the measured parent media duration. The validator converts offsets to **absolute media coordinates**, denies missing/out-of-bounds origins and never treats a slide index as video seconds. Video proof intervals require measured duration from authoritative A003/A004; missing duration is unavailable. OCR requires one actual sampled-frame proof on the same source asset and a frame point inside its interval.

### Catalog integration contract

`resolve_a_proof(kind,ref_sha256)` is an injected **trusted catalog** method; it returns a mapping with required:
`reference_sha256, kind, project_id, video_id, source_id, media_asset_id, asset_sha256, content_sha256, local_path, timebase`, plus optional:
`start_ms,end_ms,clip_origin_ms,clip_duration_ms,slide_index,frame_ref_sha256`.

These fields must be resolved from authoritative Group A/D1 observations with access authorization. The current Group A branch is **not yet a completed GA-MOD-18 producer/catalog**; an actual production catalog implementation and joint GA/QDP/B test remain external dependencies. A caller-supplied mapping should **never** be treated as authenticated catalog output in a network API.

The builder emits a frozen `MediaEvidenceManifest` with `schema_version`, exact project/QDP/GA/B/version/hash pins, ordered byte-verified source media list, checked observation references, `observed_counts`, and `manifest_sha256` over canonical JSON of the path-free payload. No raw paths, transcripts, OCR body text, audio bytes, access keys, provider calls, or fabricated observations are exported. The hash is a reproducibility pin, **not a digital signature or proof of who authenticated the caller**.

### Security and execution gates

- File signature inspection is not decoding/FFprobe, copyright/right-to-use attestation or protection against external mutation after the read. Symlink paths are denied; shared filesystem directory mutation during reads still requires operational trust in configured roots. Restrict directory write permissions and rehash at time of use.
- Reading current D1 rows does not lock QDP invalidations or guarantee future permission. Stage GB-MOD-07 must revalidate QDP eligibility and source/media rights immediately before execution, while GB-MOD-10 must separately gate paid calls.
- The manifest is read-only/in-memory. Durable versioned context snapshots and model budgets belong to GB-MOD-08/10/22. There is no new database migration in this stage.
- Only already-created, measured A proof IDs can be assembled. `not_run`, `partial`, unknown duration, missing slide image, inconsistent source SHA or insufficient OCR frame provenance must return a stable blocker.

## QA

`tests/test_group_b_evidence.py` uses temporary local media and catalog mocks, including corrupt file/hash, missing image, wrong container signature, arbitrary/external/symlink paths, duplicate frame IDs, OCR frame mismatch, source/project mismatch, missing proof, slide-order/timestamp contradictions and clip-relative offsets. It builds and hashes one example manifest without network/FFmpeg. CI runs the suite on **Windows/Linux**, plus the PG17 service job for consistency.

```bash
python -m unittest discover -s tests -p 'test_group_b_evidence.py' -v
```

These tests are **not** evidence of live Group A 054/055 + QDP + B cross-branch producer E2E, persistent proof catalog, actual FFmpeg decode, or paid model execution. Track those as **BLOCKED/NOT RUN** until real checks produce results.

**Next:** GB-MOD-07 — readiness/dependency matrix and point-of-use revocation checks for B1–B8.
