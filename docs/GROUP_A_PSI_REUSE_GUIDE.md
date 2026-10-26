# GA-MOD-08 — Shared Media Understanding / PSI Read-only Bridge

**Implementation:** COMPLETE (standalone, opt-in). **Release:** NOT READY.
**QA:** GitHub source/branch static audit PASS; committed Python and
disposable PostgreSQL17 tests QUEUED / NOT YET VERIFIED.

## 1. Observed PSI main contracts, not assumed schemas

Inspected actual `main` sources:
- `intelligence/project_sources/transcript_normalizer.py` writes
  `transcript/v1`, includes `video_sha256`, normalization/version,
  SRT or ASR provenance, ms timestamps, optional confidence, timing
  coverage and explicit speech/processing statuses.
- `intelligence/project_sources/vision_engine.py` writes
  `vision-prepare/v1`, NOT literally `visual-preparation/v1`.
  Preparation is local, at most 8 sparse sampled frames with
  `all_frames_analyzed=false`; Tesseract OCR is one sampled instant
  (`coverage_kind=single_frame`) with **no calibrated confidence**.
- PSI paid `visual-observations/v1` is a distinct explicit Sonnet
  analysis stage. GA-MOD-08 **does not run, request or silently reuse**
  that LLM step as verified local OCR/visual truth.
- PSI project-source versions and acquisition IDs are **UUIDs**. Discovery
  `videos.id` is a BIGINT. They are never numerically equated, coerced
  or joined by coincidental URL/native ID alone.

PSI owns `source_processing_outputs`,
`project_video_acquisition_runs`, `project_transcript_runs`,
`project_vision_runs`, `project_source_versions` and
`project_media_assets`. Group A does not clone any of these tables
or import PSI private implementation code.

## 2. Explicit lineage requirement

`PSIAssetBinding` is host supplied and must be authenticated from
the user's project context:
```text
project_id (32-char project ID)
Discovery video_id (positive BIGINT)
Discovery/PSI exact same original SHA-256, original media byte_size
PSI source_id UUID + independent PSI source_version_id UUID
PSI acquisition_job_id UUID
```

`PostgresPSIReader(conn,binding,kind,observed_version)` first checks
the version/port contract, then queries the **current Discovery** video
through `sre_project_videos`, `videos`, `media_assets`, `sources`,
and active `source_policy_versions`, requiring matching project/video,
media SHA and live analysis permissions.

Only then does it read PSI outputs with project/source/version-scoped joins,
pinning acquisition's original `video_sha256`, PSI media asset SHA and
size, exact acquisition UUID, job stage/state, current PSI source
`can_analyze` + non-tombstoned status, source-version policy snapshot,
asset rights snapshot, and the exact transcript/vision run config SHA.
The linked PSI job must be `completed` or `partial` and output schema
must match the observed PSI public version.

No PSI table is migrated, created, modified or inserted. Incomplete
SQL peer schema or unavailable port fails closed; no provider or
network is invoked. The read must execute in a project-scoped
database transaction managed by the host. Current D1 and PSI rights
must be revalidated before each independent reuse decision.

## 3. Version negotiation and semantic translation

| Actual peer output | Group A port contract | Accepted evidence |
|---|---|---|
| `transcript/v1` | `transcript/v1` | Verified timed SRT/ASR segments only |
| `vision-prepare/v1` | `visual-preparation/v1` | Sparse sampled frames and observed one-frame OCR text only |
| `visual-observations/v1` | **Not auto-negotiated** | No paid Sonnet inference reused as confirmed facts |

For transcript the adapter recomputes canonical SHA-256 of PSI
`output`, checks `video_sha256`, exact normalizer/model/version/config
when requested, non-overlapping monotonic timings and real segment count.
Integer millisecond boundaries convert losslessly to GA decimal seconds
(`1301ms`→`1.301`) and vice versa. A TXT-only transcript is
**not** timed ASR. Unknown timings or invalid confidence are rejected,
not imputed.

For vision, the adapter only accepts the locally prepared schema with
`trust_level=local_frame_sample`,
`all_frames_analyzed=false`, at most eight frames and original image
SHA/byte metadata. OCR `status=ok` and real observed text create
**single-frame-only** evidence with `confidence=null`; no OCR
persistence between sampled frames, no scene-duration text assertions.

**PSI job `completed` is NOT Group A coverage `complete`.**
Reused observations are marked Group A `partial` when evidence
exists or `unavailable` otherwise, with:
- `visual_temporal_coverage=unknown`
- `ocr_temporal_coverage=unknown`
- `complete_speech_coverage=false`
- `verified_no_speech=false`

Without a genuine VAD/ASR and temporal tracking stage, these are the
only defensible truth states.

## 4. Cache and runtime

`PSIReuseCache` is optional in-memory memoization keyed by:
project/video, original SHA/bytes, PSI source/version/acquisition UUIDs,
stream kind, upstream output SHA, configuration SHA, model version and
observed peer version. It returns defensive copies and validates
upstream provenance/coverage at write time.

Every cache read requires `verify_current(binding)` from an
authoritative current-rights checker. It invalidates the cache on
revocation. Merely finding a matching digest is never authorization.
This is not a second permanent ASR/OCR data catalog. The existing
`ga_cache_refs` remains a future runtime integration surface.

`GroupAService.reuse_psi(...)` requires the explicit
`GASettings(allow_psi_reuse=True)` option; **default is disabled**.
Reader objects must bind exactly the requested `PSIAssetBinding`.
When outputs are absent/incompatible/revoked, the decision is
`state=unavailable`. A later authorized local ASR/OCR stage may then
process independently, but GA-MOD-08 NEVER launches fallback itself.

Three frozen JSON Schemas:
- `schemas/ga-psi-transcript-reuse-v1.json`
- `schemas/ga-psi-vision-reuse-v1.json`
- `schemas/ga-psi-reuse-decision-v1.json`

## 5. Explicit host example

```python
from intelligence.group_a import GroupAService, GASettings, PSIAssetBinding
from intelligence.group_a.adapters.psi_reuse import PostgresPSIReader

service = GroupAService(settings=GASettings(allow_psi_reuse=True))
binding = PSIAssetBinding(
    project_id=verified_project_id,
    video_id=verified_discovery_video_id,
    media_sha256=verified_original_sha,
    media_byte_size=verified_original_size,
    psi_source_id=verified_psi_source_uuid,
    psi_source_version_id=verified_psi_version_uuid,
    psi_acquisition_job_id=verified_psi_acquisition_uuid,
)
# conn must be transaction-scoped, from the authorized host database.
decision = service.reuse_psi(
    binding,
    transcript_reader=PostgresPSIReader(
        conn, binding, kind="transcript", observed_version="transcript/v1"),
    vision_reader=PostgresPSIReader(
        conn, binding, kind="vision", observed_version="vision-prepare/v1"),
)
```

These variables are **explicit host inputs**, not generated identities.
No automatic producer, worker, paid Sonnet request or Group B publication.

## 6. Tests and independent QA ledger

`tests/test_group_a_psi_reuse.py` covers:
- PSI UUID distinct from Discovery video BIGINT, and foreign project;
- exact ms→s→ms conversion, unknown/invalid timestamps, overlapping ASR;
- PSI output digest mismatch, source byte/hash mismatch, PSI source/version/
  acquisition mismatch, stale/current rights and denied D1 lookup;
- model/config versions, confidence calibration and normalized stream
  schemas;
- no-speech/untimed text not falsely treated as verified absence;
- fake PostgreSQL public read-only query constraints;
- sparse OCR never upgraded to full coverage and no Sonnet fallback;
- in-memory cache reauthorization, adversarial payload and mutation.

Read-only GitHub source audit covered independent module diff,
current D1 project/video join, actual PSI version mapping,
acquisition hash/size, config and rights checks, no peer imports or
paid provider calls, canonical timestamps, conservative coverage and
new tests/schemas: **STATIC PASS**.

**Python and disposable PG17 GitHub Actions: QUEUED / NOT EXECUTED.**
These are not claimed as PASS. Live PSI–D1 data fixtures and host
transaction/authorization orchestration must be validated before
merging into production. This branch does not change `main`.

**Next:** GA-MOD-09 — offline audio decoding and VAD; PSI reuse must
not preempt properly verified local speech/audio processing.
