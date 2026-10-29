# GA-MOD-07 — Sparse Frame Sampling, Thumbnail & Evidence Manifest

**Implementation:** group-a standalone `feature/group-a-full-module`.
**Activation:** explicit offline `GroupAService.sample_local_frames` only.
**QA:** source/branch static audit PASS, direct FFmpeg frame command smoke PASS;
committed Python + PG17 CI **PENDING/NOT VERIFIED**.
**Release:** NOT READY. No QDP/Group B/Analytics host hooks activated.

## 1. Modules and responsibilities

- `intelligence/group_a/extractors/frames.py`: deterministic planner,
  timebase-verified FFmpeg PNG extraction, tiny perceptual duplicate check,
  sparse-coverage truth semantics and manifest draft.
- `intelligence/group_a/extractors/frame_store.py`: immutable, atomic,
  content-addressed local PNG and `manifest.json` storage, not PostgreSQL.
- `intelligence/group_a/schemas/group-a-frame-manifest-v1.json`: strict
  JSON Schema 2020-12 wire format.
- `GroupAService.sample_local_frames(verified_media, probe, roots=...,
  scenes=..., config=..., store=..., project_id=..., run_id=...)`:
  explicit non-worker orchestration. `verified_media` must be a
  GA-MOD-05 `VerifiedFile` (byte SHA pinned), `probe` is
  GA-MOD-06 `ProbeInfo` (real stream index, timebase, duration).
  Without `store`, returns (manifest, PNG blob map) to a local host.
  With `FrameStore`, returns (manifest, immutable batch ref).
  In-memory PNG bytes must not be serialized to SQL.

## 2. Sampling policy, deterministic budget and limits

The planner uses strict Decimal timestamps, never wall-clock and never
a guessed FPS. Defaults are **12 requested samples** (configurable 1–48),
maximum PNG edge 512 pixels (64–720 allowed), early-focus count 3.
Candidate timestamps combine initial frame (0s), early hook positions,
all observed A1 technical scene midpoints and periodic slots across
the entire clip. A deterministic farthest-gap selection prioritizes
geographic *time coverage*, not fabricated semantic phases.
All selected target offsets are ordered, unique, bounded by real duration
(0 < duration <=600s), and never deliberately request exact EOF.

Sampling is sparse by design, with a bounded budget. A video with 100
scenes does not magically get 100 scene screenshots if the budget is 12.
This intentionally does **not** copy PSI's independent 8-frame limit,
nor claim 100% footage or OCR visibility. The stage does NOT classify CTA,
speech, visual meaning, music, subtitles or full screen-text intervals.

## 3. Verified actual source timestamps

One isolated FFmpeg call per selected target decodes from the start of
the source, using:
- `-nostdin -protocol_whitelist file,pipe -xerror -err_detect explode`;
- exact selected video stream `-map 0:<stream_index>`;
- `setpts=PTS-STARTPTS` then `select='gte(t,<target>)'`;
- `showinfo` to record the **actual selected frame PTS integer ticks**,
  verified against the original FFprobe `ProbeInfo.time_base`;
- bounded `scale` + `format=rgb24`, PNG output `image2pipe`
  `-frames:v 1 -fps_mode passthrough` and 60s timeout each.

The requested timestamp is not misrepresented as the observed timestamp.
The wire artifact includes both `requested_sec` and `observed_sec`,
plus exact `observed_pts_ticks` and `source_timebase`.
Unknown, ambiguous, inconsistent, negative or impossible PTS fails closed.
An EOF target producing no frame increments `not_observed` and produces
NO fake PNG/PTS; actual decoder errors still fail the entire extraction.

On Linux, FFmpeg receives an already GA-MOD-05-verified file descriptor
via `/proc/self/fd/<fd>` and `pass_fds`, preventing a pathname-swap
between verification and decode. On other platforms the validated local
filename is passed and file identity is checked again after processing.
Windows/macOS-specific race and path handling remain GA-MOD-23 QA gates.

**Performance caveat:** per-request full decode from the start maximizes
temporal provenance and determinism but is costly for long videos.
Strict 1–48 budget/60s per-frame timeout applies; GA-MOD-20/22 may add
a streaming single-pass optimized selector provided timestamp semantics
and integrity remain identical.

## 4. Duplicate detection and honest coverage

Each PNG is checked by Pillow for valid PNG bytes and dimensions.
Stored SHA-256 is calculated from actual encoded PNG bytes.
A 9x8 grayscale difference-hash and 1x1 RGB mean vector are both used;
visually near-identical samples are suppressed only when
Hamming-distance AND color-distance thresholds both pass. Flat red and
flat blue do not collapse merely because both have flat grayscale dHash.
Duplicate skips retain no fake image evidence.

The manifest includes:
- `source_media_sha256`, source byte size, observed duration, selected
  stream index/timebase and exact config;
- each retained frame's sample index, requested second, observed PTS
  ticks/second, SHA, byte length, image dimensions, perceptual hash and
  opaque `ga-frame:fNNN-<sha256>` reference;
- `coverage.type=sparse_observations`, `requested`, `retained`,
  `duplicates_suppressed`, `not_observed`, observed PTS span;
- **`visual_temporal_coverage=unknown`,
  `ocr_temporal_coverage=unknown` and
  `unsampled_intervals_present=true`**.

The observed first/last span is **not** temporal observation coverage:
nothing between sparse timestamps is asserted. This is also NOT a new
A003 duration/FPS/raw-resolution measurement or a completed Group A
result. QDP media-asset/project/run ownership is held in the upstream
manifest/inbox and explicit batch project+run namespace, not inferred
from a filename or frame perceptual similarity.

## 5. Local atomic evidence store

`FrameStore(root)` requires an explicit local absolute root with no
symlink in configured root ancestry; `commit(project_id,run_id,
manifest,png_blobs)` validates the canonical manifest digest, individual
PNG SHA, length, dimensions, perceptual-hash evidence, ordered frame
indices and sparse-coverage counts. Project must be a 32-hex ID and run
a canonical UUID. No relative path or user-provided filename is written.

The writer creates an isolated same-parent staging directory with
restricted file permissions, writes each PNG and a sorted-JSON manifest,
`fsync`s files/directories, then publishes by atomic directory rename
into `<root>/<project>/<run>/<manifest_sha256>/`.
A failure during write removes staged content and leaves no published
manifest. A replay of the same batch verifies **all** existing file
digests; any modification is an immutable conflict, never a silent
overwrite. `read_png` checks both membership and encoded SHA before
returning image bytes. No frame bytes are saved in SQL.

The store is a local integrity mechanism, NOT a cryptographic signature
or cross-project authorization API. Host callers must verify current
project/QDP policy and expected source manifest before sharing references
with other modules.

## 6. Real FFmpeg smoke, tests and remaining QA

The direct, independent FFmpeg command was executed on a disposable
160x90 10fps 2-second red→blue clip through a verified Linux file
descriptor. Observed requests and actual frame PTS:
`0.000→0.000`, `0.250→0.300`, `0.750→0.800`,
`1.200→1.200`, `1.700→1.700`. All five commands returned
valid PNG frames (128x72), and red/blue RGB means differed.
**Direct CLI smoke PASS.** This alone is NOT a pass of committed Python
module tests.

`tests/test_group_a_frames.py` covers pure planning, timebase
correction and errors, red/blue vs static/black dedup, source SHA,
invalid decoder result, changed media, schema validity, atomic staging
cleanup/rollback, idempotent manifest reuse, digest tampering, bad
perceptual hashes, project/run traversal and real FFmpeg on synthetic
abrupt-change/low-light clips.

`group-a-module.yml` installs pinned test-only
`Pillow==11.1.0`, `jsonschema==4.23.0`,
`psycopg[binary]==3.2.6`, and requires FFmpeg/FFprobe.
The entire `test_group_a_*.py` suite is discovered. GitHub Actions
has been queued/pending; Python/PG17 **NOT YET PASSED**.
Windows/macOS race hardening, end-to-end QDP/Group B integration,
and optimized sampling performance remain open.

**Next:** GA-MOD-08 — PSI transcript/visual-preparation bridge and
cache reuse pinned to exact source asset SHA/version; never equate sparse
frame samples with a full OCR transcript.
