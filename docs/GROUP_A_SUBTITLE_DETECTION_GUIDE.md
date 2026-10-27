# GA-MOD-14 — Subtitle detection: TRUE / FALSE / UNKNOWN

**Branch:** `feature/group-a-full-module` • **Implementation:** COMPLETE
**QA:** independent real FFmpeg→FFprobe MKV/SRT CLI smoke PASS;
static source/scope audit PASS; committed Python/PG17 Actions PENDING/NOT RUN.
**Real human-reviewed multilingual visual calibration:** NOT VERIFIED.
**A003 write:** disabled (GA18 owns immutable assembly).

## 1. Goal and evidence contract

The output `ga-subtitle-presence/v1` maps to the actual A003
`video_feature_a_analyses.has_subtitles` `true|false|unknown`.
Group A measures **presence** only; Group B B6 owns caption visual style.
No inference about language of all video speakers, subtitle style, or
continuous burned-in text intervals is permitted.

- `extractors/subtitle_probe.py`: local verified-video FFprobe audit with
  `-select_streams s -count_packets`; returns `ga-subtitle-stream-probe/v1`.
  Confirmed subtitle **packets** (not stream header alone) prove an available
  embedded subtitle/caption track. Unknown codec/failed tool => unavailable,
  never false. Only GA05 authorized SHA-pinned video files are accepted;
  Linux passes the verified file descriptor to FFprobe.
- `extractors/subtitles.py`: source-bound, current-rights-checked
  `detect_subtitles`, `SubtitleConfig`, `SubtitleCalibration`,
  `DenseNegativeReview`. Reads GA13 sparse OCR tracks, GA10 ASR/SRT
  transcript and optional GA11 video-clock mapping; returns immutable
  SHA-hashed evidence and exactly one A003 enum projection.
- `extractors/subtitle_calibration.py`: optional, **separately authenticated**
  held-out gold evaluator for English and Russian with preselected visual
  threshold, positive/negative human annotations and per-language
  95% Wilson lower precision bound. Its tests use synthetic gold cases,
  NOT real-world performance evidence.
- Strict `schemas/ga-subtitle-presence-v1.json`, package exports,
  `GroupAService.inspect_local_subtitle_tracks()`,
  `measure_subtitle_presence()` and `evaluate_subtitle_gold()`.

## 2. Truth matrix

| Condition | A003 `has_subtitles` | Authority |
|---|---|---|
| GA05-verified embedded subtitle track with packet_count > 0 | `true` | Source-pinned FFprobe evidence |
| GA10-authorized SRT/PSI-SRT with observed cue times and pinned upstream | `true` | Current-rights-verified timed caption source |
| GA13 lower-region OCR matching verified ASR speech | `unknown` default | Corroborated *candidate*, not calibrated subtitle proof |
| Same visual candidate + independent trusted RU/EN gold evaluation and language match + fixed threshold | `true` *only when explicitly enabled* | Externally verified calibration |
| No subtitle streams, sparse OCR blank frames | `unknown` | Never proof of video-wide absence |
| ASR speech present, no OCR / no video clock | `unknown` | Speech alone does not imply captions |
| Big intro title, decorative lettering or CTA overlay | `unknown` | OCR text is not necessarily subtitles |
| Video without audio but with timed SRT | `true` | Visual / selectable captions need not accompany audible speech |
| Explicit trusted full-duration dense negative review, 100% interval attestations, subtitle streams and sidecars checked, no contrary evidence | `false` | Requires host-authenticated independent full review |
| Missing FFprobe, unknown subtitle packet count, corrupted/partial evidence | `unknown` | Never infer false |

An empty subtitle track packet count **does not** prove absence of
burned-in captions. Sparse frame sampling never proves complete
`has_subtitles=false`. No final `false` can be generated from GA12/13
OCR alone.

## 3. Visual matching and calibration boundary

The matcher uses observed GA13 line text and original normalized frame
position. It looks for text in the configurable lower image region
(default bottom edge of line at or below 52% image height), compares
case-folded normalized RU/EN tokens against actual GA10 audible-ASR
utterances near the **trusted GA11 video-local time**, and records
the matching `frame_ref`, ASR segment index and token-overlap ratio.
Default candidate rule: at least two distinct sampled frames and
two distinct ASR segments, lexical overlap ≥ 0.65 and within 0.6s of
a source-bound utterance. No fake ASR/video offset is generated.

This produces `visual.state=corroborated_candidate`, **NOT** an
automatic true. A verified `SubtitleCalibration` is mandatory for
visual-only positive outcomes. The evaluator consumes an
independently human-annotated, authenticated held-out gold set,
uses a **preselected** threshold, requires RU and EN positive/negative
examples, and verifies a lower confidence bound for precision of at
least 95% *separately for both languages*. An externally
authenticated `verify_gold_source` callback is necessary. A second
trusted `verify_calibration` callback is necessary when using the
calibrated model at decision time. The transcript's language must lie
inside the calibration scope; the per-video match ratio must exceed
the fixed threshold.

**No authentic human-reviewed corpus or achieved production precision
is bundled or claimed.** Test fixtures exercise the gate but do not
certify visual subtitle classification in production. Consequently,
uncalibrated burned-in candidates remain `unknown` by default.
Word-by-word animations, near-top titles and mixed-language captions
require human gold QC before promotion.

## 4. FALSE is a strict, separate proof

`DenseNegativeReview` is a verified statement produced outside the
sparse GA07/GA13 pipeline. It pins original media SHA, independently
authenticated review SHA, start=0.000, end equal to verified video
duration, full interval coverage, checks of embedded tracks,
visual frames and sidecars, and adequate frame-audit count (at
least 2 per second). The host must explicitly verify every assertion
through `verify_dense_review`, not merely supply arbitrary JSON.
Subtitle probe must have **inspected** the media and found no
subtitle tracks. Timed SRT, embedded packets, or verified correlated
visual candidates contradict a dense negative claim and trigger
`SubtitleError` rather than emitting a false label.

This stage does **not** implement full-frame dense scanning.
Therefore an actual `false` result requires an existing external,
source-authenticated complete review; it is not auto-enabled for
ordinary QDP/GA12 sparse processing.

## 5. Contracts / ownership / future integration

`a003_has_subtitles_projection` contains the single validated enum,
with `screen_text_intervals_created=false` and
`subtitle_style_classified=false`. Group A neither fabricates
`video_feature_a_screen_text` ranges (A003 requires `end_sec>start_sec`)
nor assigns B6 style. GA18 will atomically publish A003 from immutable
snapshots after cross-stream quality gates. GA20 will wire explicit
production worker/resource admission. An unchecked project authorization
callback or reused PSI/GA13 evidence with wrong SHA is rejected.

### Explicit opt-in example

```python
from intelligence.group_a import GroupAService, SubtitleConfig

service = GroupAService()
embedded = service.inspect_local_subtitle_tracks(
    verified_video, roots=authorized_media_roots
)
evidence = service.measure_subtitle_presence(
    source_media_sha256=verified_media_sha,
    verify_current=current_authoritative_project_rights,
    embedded_probe=embedded,
    transcript=verified_ga10_transcript_or_none,
    tracking=verified_ga13_tracking_or_none,
    speech_metrics=verified_ga11_metrics_or_none,
    config=SubtitleConfig(),
)
# evidence["has_subtitles"] is "true", "false", or "unknown".
# No SQL writes or inference of B6 subtitle style.
```

## 6. QA and reproducibility

`tests/test_group_a_subtitles.py` tests real generated MKV + soft SRT,
plain MKV, multiple/empty subtitle streams, captions without audio,
timed SRT vs untimed TXT, RU/EN visual candidates, top-title-only,
animated one-word highlights, missing audio/video clock,
lineage mismatch, rights revocation, calibration precision and threshold,
Gold held-out distribution, valid and invalid dense-negative proof,
negative/positive contradiction, deterministic SHA and strict
2020-12 JSON Schema.

Independent direct host FFmpeg generated 2-second `yes.mkv` and
`no.mkv`; FFprobe 2026-10-27 `-count_packets` returned
`subrip` / `nb_read_packets=1` for the first and empty subtitle
streams for the second. **DIRECT FFMPEG/FFPROBE COMMAND SMOKE PASS.**
This checks command behavior, not committed Python package imports or
Python unit tests.

GitHub static audit passed for module isolation/truth constraints.
CI `Group A standalone (subtitle truth and PG17)` was PENDING/QUEUED
at last inspection; previous workflow was concurrency-cancelled.
Python unit, PostgreSQL17, real-world visual gold precision,
Windows/Linux E2E **NOT VERIFIED**. Do not report queued tests as PASS.

**Next:** GA-MOD-15 — sound/music and SFX event evidence, retaining
speech/musical overlap without guessed complete audio coverage.
