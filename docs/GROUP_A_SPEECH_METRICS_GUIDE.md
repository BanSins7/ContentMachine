# GA-MOD-11 — Speech Metrics: words, active WPM, first speech, coverage

**Branch:** \`feature/group-a-full-module\`
**Implementation:** COMPLETE (pure, offline; host worker and SQL writer remain off).
**QA:** 15-point GitHub source/scope static audit PASS; independent numeric
golden calculations PASS; committed Python/PG17 GitHub Actions QUEUED /
NOT EXECUTED. Real Silero/faster-whisper model accuracy NOT VERIFIED.

## 1. Scope and input contract

\`extractors/speech_metrics.py\` owns \`measure_speech()\`,
\`union_intervals()\`, \`count_observed_words()\`,
\`SpeechMetricsConfig\` and \`PSITimelineBinding\`. It consumes:

- \`DecodedAudio\` from GA09: authoritative media/PCM SHA and true
  observed no-audio/digital-silence state, not user-labelled speech.
- Frozen \`ga-asr-transcript/v1\` from GA10: observed timed utterances
  and source provenance (\`local_faster_whisper\`, \`psi_asr\`, SRT/TXT
  captions or no source), exact canonical observation SHA.
- Optional \`ga-vad-observation/v1\` from GA09: candidate intervals,
  SHA-pinned same PCM, verified time origin and duration.
- Optional GA06 \`ProbeInfo\` for exact VIDEO duration and FFprobe
  video start time. GA10 audio stream start comes from actual FFprobe
  \`DecodedAudio.probe.start_time_sec\`.
- Optional \`PSITimelineBinding\`, supplied **only** by an authoritative
  host that has validated the active PSI acquisition/source version and
  the original video. It must match exact GA media SHA and PSI
  upstream output SHA; default absent (unknown clock mapping).

The module has **no PostgreSQL writes, Group B publication, downloaded
models or paid external calls**. It cannot generate ASR text or words
from waveform energy or VAD candidates.

## 2. Deterministic word count

\`spoken_words\` is computed only from audible ASR origins:
\`local_faster_whisper\` or current-authorized \`psi_asr\`.
SRT and PSI/SRT timed captions and TXT sidecars **never** count as
spoken words, even if they visually contain Russian/English language.
Unknown transcription is \`null\`, not zero.

Count policy: \`unicode_nfc_alnum_apostrophe_v1\` normalizes text to
Unicode NFC, counts each ordered alphanumeric lexical unit and keeps
English apostrophe contractions as one token. Repetitions are counted,
hyphenated tokens count as separate lexical units, digits count as
tokens by explicit convention; Russian, English and mixed utterances
are supported without guessing corrections.

For example \`Привет, мир! Hello can't stop\` contains 5 tokens.
This counts observed transcript lexical units, not a claim that
the recognizer has perfect real-world accuracy. An audible ASR source
with text consisting only of punctuation produces \`null\`, **not**
\`spoken_words=0\`.

The source media SHA, ASR observation canonical digest and PCM SHA must
match the current decoded audio. Any forged/transplanted observation,
contradiction of proven no-audio/silence or transcript timeline outside
decoded media is rejected.

## 3. Active speech duration and WPM

Both ASR utterance intervals and VAD candidate intervals use exact
Decimal times and sorted interval-union, eliminating duplicate
overlapping duration. \`duration_source\` is configurable:

- \`asr_only\`: use union of actual timed audible ASR utterance ranges.
- \`prefer_vad\` (default): use VAD candidate union **only if**
  temporally correlated with the real ASR transcript. At least 50%
  of total ASR active duration and 50% of candidate VAD duration must
  overlap; otherwise fall back to observed ASR union and report
  \`vad_asr_consistency=mismatched_with_asr\`.
- \`vad_only\`: require validated, correlated VAD candidates alongside
  audible ASR; without them, speech duration/WPM remain \`null\`.

VAD alone is not proof of speech; its candidate duration may be
reported separately as \`vad_candidate_duration_sec\` for diagnostics,
but cannot generate spoken words, \`speech_duration_sec\` or WPM if
no ASR speech was observed. A music/noise interval unrelated to
audible ASR cannot inflate or deflate WPM by silent acceptance.

\`speech_wpm = spoken_words × 60 / active_speech_sec\`

Decimal arithmetic with HALF_UP rounding at two decimal places.
For very short active duration (under 250 ms by default) or a
non-credible rate over 3000 WPM, \`speech_wpm=null\` and a reason
in \`wpm_status\` is emitted, rather than a misleading numerical zero.
Speech duration uses exactly three decimal places.

**Numeric gold:** 5 observed words across two intervals of 1 second
each gives active duration 2.000s and WPM 150.00.
With separately observed, ASR-correlated VAD totaling 2.100s,
WPM is 142.86. Neither formula divides by total video duration,
includes transcript segment gaps, nor double counts overlaps.

## 4. Proven video timeline and coverage

\`first_speech_sec\` comes from the first observed AUDIO-ASR segment,
translated to verified video-local time.

For local ASR:
\`\`\`text
video-local time =
    audio-local ASR time
    + (FFprobe audio_stream_start - FFprobe video_stream_start)
\`\`\`
Both source start offsets must exist and refer to the exact same
validated media. FFprobe may have negative encoder delays; offsets
are signed and **must not** be clamped to zero.

For PSI, explicit authenticated \`PSITimelineBinding\` must contain
the current original media SHA, PSI output SHA and observed video
offset; **no mapping is invented** from a matching URL, arbitrary
PSI source UUID or project title.

If alignment is absent: speech count, ASR-local duration and
ASR-local WPM may still be known, but \`first_speech_sec=null\`,
\`speech_coverage_pct=null\`, \`coverage_state=unknown\`.
\`SpeechMetricsConfig(require_video_clock=True)\` instead fails closed.

When authenticated video duration exists and timed spans translate
fully within video bounds:

\`speech_coverage_pct = 100 × active_speech_sec / video_duration_sec\`

Result is rounded to two decimal places. Out-of-video intervals and
coverage above 100% are **rejected**, not clipped or silently reduced.
This is the share of the video covered by measured/candidate active
speech, not 100% model observation coverage.

For the 10s example with ASR duration 2s, coverage = 20.00%.
If FFprobe audio starts at 0.250s and video starts at 0,
the observed first speech segment at audio-local 0.500s yields
video-local \`first_speech_sec=0.750\`.

## 5. True absence and unknown semantics

- Verified **no audio stream**: \`spoken_words=0\`,
  \`speech_duration_sec=0.000\`, \`speech_wpm=null\`,
  \`first_speech_sec=null\`. With authoritative video duration,
  \`speech_coverage_pct=0.00\`.
- Verified **decoded exact-zero PCM**: same zero semantics for that
  single selected audio stream, with distinct reason code/state.
- Audio exists, model failed/missing, or no words observed:
  unknown \`spoken_words=null\`, not fake zero. VAD \`no_candidates\`
  or unavailable is NOT proof of silence.
- Caption-only SRT/TXT on videos with audio: no spoken words inferred.
  Captions on videos without audio are preserved independently,
  but speech metrics correctly reflect proven absence.
- A \`speech_detected\` transcript with only punctuation yields
  unknown word count (no observable lexical tokens).
- Verified absence is local to the selected stream; it does not
  establish complete GA image/OCR/music/CTA processing.

Output carries \`complete_speech_coverage=false\` unconditionally,
and a canonical \`observation_sha256\` bound to method/config,
video source, ASR/VAD digests, active basis and derived values.

## 6. A003 compatibility with actual \`003_video_features_a.sql\`

This stage looked up A003 **in main**, rather than assuming columns.
\`video_feature_a_analyses\` stores \`spoken_words\`,
\`speech_duration_sec\` and \`speech_status\`, while view
\`video_feature_a_summary\` DERIVES \`speech_wpm\` and
\`speech_coverage_pct\`. The view derives
\`first_speech_sec\` from the minimum start time of
\`video_feature_a_speech_segments\`.

Therefore \`a003_speech_projection\` contains only the true
stored A003 speech columns:
\`\`\`json
{"speech_status":"partial","spoken_words":5,
 "speech_duration_sec":"2.000"}
\`\`\`
and \`a003_speech_segments_projection\` contains the
video-local timed rows:
\`\`\`json
[
 {"start_sec":"0.750","end_sec":"1.750",
  "spoken_text":"Привет, мир!","method_name":"ga11-local_faster_whisper",
  "confidence_pct":null},
 {"start_sec":"2.250","end_sec":"3.250",
  "spoken_text":"Hello can't stop","method_name":"ga11-local_faster_whisper",
  "confidence_pct":null}
]
\`\`\`
These illustrative values correspond to the numeric gold fixture,
not to arbitrary real videos. Uncalibrated model confidence is never
written as a fake calibrated \`confidence_pct\`.

With unverified video clock the segments projection is empty, so
A003 cannot silently derive a fabricated first-speech timestamp.
\`speech_status=partial\` for observed speech or verified absence,
\`unavailable\` when unknown. **The module does not call A003 SQL**;
atomic insertion, versioned immutable snapshots and cross-stream
quality gates are GA-MOD-18 responsibilities.

## 7. Tests and current independent QA

\`tests/test_group_a_speech_metrics.py\` includes:
- Russian/English/mixed text, contractions, repeated words,
  hyphenated words, normalized Unicode and numerals;
- gold WPM 150.00, 142.86 and video coverage 20.00%, 21.00%;
- overlapping/contiguous/gapped interval union;
- uncorrelated music-like VAD windows rejected from WPM;
- absent video clock and PSI clock mapping, trusted audio offset,
  negative/out-of-bounds/unknown time handling;
- SRT/TXT captions never spoken, VAD candidates without ASR unknown;
- no-audio and zero-PCM exact zero, without fake zero-WPM;
- malformed SHA/PCM/ASR/VAD data, outside-video intervals,
  sub-250ms unstable WPM;
- actual A003 stored column and segment projection, canonical
  deterministic hashes, strict frozen 2020-12 JSON Schema and
  negative forged coverage/absence cases.

Independent numeric calculation was checked locally:
\`5×60/2.000=150.00\`, \`5×60/2.100=142.86\`,
\`100×2/10=20.00\`, \`100×2.1/10=21.00\`.
**NUMERIC FORMULA CHECK PASS** (not a Python suite).

GitHub source/branch static audit **PASS (15 checks)**.
The container cannot clone github.com due DNS restriction;
the GitHub Actions workflow on this feature branch remains
**QUEUED / NOT EXECUTED**. Consequently, committed Python test
pass and PG17 compatibility are **NOT VERIFIED**; no fake green
test verdict. Actual Silero/faster-whisper multilingual accuracy
also remains pending.

Reproduction:
\`\`\`bash
python -m compileall -q intelligence/group_a tests/test_group_a_*.py
python -m unittest discover -s tests -p test_group_a_speech_metrics.py -v
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
\`\`\`

**Next:** GA-MOD-12 OCR (multilingual frame text and geometry), not
an inference of full-screen-text temporal coverage from sparse frames.
