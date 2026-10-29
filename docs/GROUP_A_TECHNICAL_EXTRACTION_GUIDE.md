# GA-MOD-06 — A1 Technical Extractor / FFprobe + FFmpeg

**Implementation:** COMPLETE in `feature/group-a-full-module`. **Release:** NOT READY.
**QA:** static source/branch audit PASS; manually executed synthetic FFmpeg
command smoke PASS; committed Python/PG17 CI QUEUED/NOT EXECUTED.

## Architecture

`intelligence/group_a/extractors/technical.py` is the canonical offline
technical extractor. `intelligence/group_a/extractor.py` retains legacy
`authorized_file` and re-exports `measure`, `inspect_probe`,
`parse_cuts`, tool helpers, old constants and `TechnicalMeasurement`.
No A003/A004 schema change or public forbidden FPS/width/height field.

`GroupAService.inspect_local_video(path, config, roots=...)` is an
explicit local-only entrypoint. New strict extraction requires authorized
absolute roots, rejects symlink/path escapes using the GA-MOD-05 resolver
and compares the file identity before and after processing. File descriptor
verification and current QDP rights must still be enforced in later
worker stages; stat-before/after by itself is not an atomic guarantee
against adversarial pathname replacement.

## Probe and technical timeline

- FFprobe selects exactly one primary video stream (ignoring attached
  cover art), reads codec, video index, timebase, start_time,
  stream/container duration and geometry with sample aspect ratio.
- Display aspect = reduced width×SAR / height. Example: 720×576,
  SAR 16:15 => 4:3. Duration must be positive and <=600.000s.
  A concrete invalid stream duration cannot silently fall back to a
  shorter container duration. Container duration is used only when the
  stream duration is genuinely absent/N/A.
- FFmpeg forces `setpts=PTS-STARTPTS` before scene detection, so source
  start_time of 3s maps technical PTS to local 0..duration.
  `eq(n,0)+gt(scene,threshold)` retains the first frame even for static
  videos. `showinfo` generates observed candidate cut times; they are
  sorted, millisecond-quantized, min-gap debounced and constrained to
  exact 0 and duration endpoints, with at most 300 scenes.
- FFmpeg runs with `-nostdin`, `-protocol_whitelist file,pipe`, explicit
  video stream mapping, `-an -sn -dn`, `-xerror -err_detect explode`,
  `-progress pipe:1`, and a 210-second timeout (FFprobe 30 seconds).
  Selected output frames must be nonzero in strict mode. `selected_frames`
  is **not** the total decoded frame count or an OCR coverage claim.
- `TechnicalEvidence` captures exact threshold/gap, method version
  `group-a-ffmpeg-technical-v2`, observed video timebase/start offset,
  codec/stream identity, optional actual FFmpeg/FFprobe versions and
  legacy `TechnicalMeasurement` (method version
  `group-a-local-video-v1`). It reports `analysis_state=partial`,
  `content_semantics=unknown`; speech/OCR/audio remain not_run,
  subtitles unknown and CTA null.
- FFmpeg output size is checked after capture (not a streaming output
  quota); memory, codec matrix and Windows race hardening remain further
  release/performance work.

## Reproduction

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_*.py
python -m unittest discover -s tests -p test_group_a_core.py -v
python -m unittest discover -s tests -p test_group_a_technical.py -v
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
```

`tests/test_group_a_technical.py` checks old API exports,
SAR/timebase, start offsets, audio-only, attached cover, unsupported
codec, duration limits, 300-scene bound, no selected-frame progress,
timeouts, missing binaries, root/symlink restrictions, video mutations,
and actual FFmpeg-generated red-blue, static, gradual and offset clips.

## Verification evidence

The local runtime was used to execute real FFprobe and FFmpeg commands
with synthetic 2-second files:
- Red→blue source with `-output_ts_offset 3`: FFprobe reported stream
  start_time=3.000000 and duration=2.000000; normalized FFmpeg
  `showinfo` reported PTS `0,1` and progress `frame=2`.
- Static-red source: `eq(n,0)+gt(scene,0.350)` emitted one frame at
  local PTS 0, without inventing a scene transition.
- Strict `-xerror -err_detect explode` flags were accepted by installed
  FFmpeg on a generated local clip.

These are **direct CLI checks**, not assertions that the committed Python
unit suite passed. GitHub Actions remained queued; no PG17 or Windows
validation was completed. The workflow now installs/requires FFmpeg
before running the complete Group A tests, so future CI cannot skip the
real-video suite merely because FFmpeg is missing.

**Next:** GA-MOD-07 frame sampling/evidence; sparse sampling must never
claim full video or OCR coverage.
