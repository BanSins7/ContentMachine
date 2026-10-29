# GA-MOD-10 — Local ASR, source selection, language and word provenance

**Branch:** feature/group-a-full-module. **Implementation:** code committed.
**Release:** NOT READY. **QA:** source/branch static audit PASS (16 checks).
Committed Python+PG17 CI QUEUED/NOT RUN, real faster-whisper model
and human/multilingual speech accuracy NOT VERIFIED.

## 1. Architectural boundary

A1 technical measurements, GA05 verified media, GA09 DecodedAudio PCM,
and optionally GA08 PSI normalized transcript are the inputs. This stage
does NOT install a QDP worker, a paid provider, ASR model weights or new
PostgreSQL schema. It remains a pure/explicit offline service.

- \`extractors/asr.py\`:
  \`ASRConfig\`, \`Sidecar\`, \`ASRBackend\` protocol and \`transcribe\`;
  validates timestamps/word evidence and chooses one source.
- \`extractors/local_asr.py\`:
  \`LocalASRProvenance\` and \`LocalFasterWhisper\` concrete provider;
  model-file inventory SHA (including config), resource lease required,
  in-memory 16k mono PCM converted to private temporary WAV.
- \`extractors/asr_worker.py\`:
  child process imports faster-whisper **only inside the child**, starts
  from an absolute local model directory with
  \`WhisperModel(...,local_files_only=True)\`; loads no HuggingFace ID,
  downloads nothing, transcribes with optional \`word_timestamps=True\`.
- \`schemas/ga-asr-transcript-v1.json\`:
  strict versioned wire contract; \`complete_speech_coverage=false\`
  and optional word timestamps as observed, never fabricated.
- \`GroupAService.transcribe_local_audio(...)\`:
  explicit offline entrypoint. No live queue/HTTP or Group B side effects.

\`DecodedAudio.pcm\` is never serialized in the transcript wire, logged
or persisted to PostgreSQL. The subprocess returns bounded JSON, not audio.
The parent deletes temporary audio/output files on success and exceptions.

## 2. Source policy matrix

| Policy | First source | Next | Last |
|---|---|---|---|
| \`prefer_verified\` (default) | Current-rights-verified PSI | SHA-bound SRT/TXT | Local faster-whisper if connected |
| \`psi_only\` | PSI | unavailable | unavailable |
| \`sidecar_only\` | SRT/TXT | unavailable | unavailable |
| \`local_only\` | Local faster-whisper | unavailable | unavailable |

PSI must be normalized by GA-MOD-08 (actual \`transcript/v1\`, media
SHA and immutable output evidence pinned) **and** reauthorized through
the host-supplied \`psi_verify_current\` callback on every reuse. An
ordinary dict or matching model name is not independently trusted.
Results with mismatched media hash, source kind or current rights fail
closed. Runtime should supply an authoritative GA08 reader, not a
test-only \`lambda:True\`.

SRT is video-local **caption observation** with real cue boundaries.
It is not automatically confirmed audible speech or word timestamps.
TXT is explicitly \`untimed\`: words cannot be assigned fake
positions. Even when the video has no audio stream, an SRT may be
present and is preserved as captions, **not audible speech**.

For PSI/SRT paths \`video_duration_sec\` must come from the actual
verified video timeline, not from the audio stream sample count. The
audio clock may start at a positive/negative encoder offset, so the
result carries \`time_origin=audio_stream_local_zero\`, \`video_local\`,
or \`psi_source_local_ms\` explicitly. No automatic cross-clock
alignment is attempted. GA11 is responsible for consistent WPM/speech
coverage and stream-origin mapping.

## 3. Local model execution and availability

Host prepares a complete local faster-whisper CTranslate2 model
directory. The canonical inventory SHA-256 hashes **all** local
model files (including \`model.bin\`, tokenizer/config files), not
merely the path or version name. Parent verifies the full directory
against \`LocalASRProvenance.model_sha256\` before launch; child repeats
the same verification before loading any model. Symlink files,
missing/oversized model files or changed bytes are rejected.
No model files are committed to Group A.

A local host-supplied lease object is mandatory:
\`lease.acquire(kind='asr',device='cpu'|'cuda')\` returns a context manager
controlling CPU/GPU concurrency/budget. No lease => no local ASR.
The subprocess runs with \`stdin/stdout/stderr\` suppressed, a strict
10–900s wall-clock timeout (default 240s), temporary WAV/output files
and HuggingFace offline environment flags. The worker does not
transcribe when verified audio is absent or exact PCM digital silence.

\`ASRConfig\` supports \`language=auto|ru|en\`, device \`cpu|cuda\`,
\`compute_type=int8|float16|float32\`, beam size 1–8,
and opt-in word timestamps. The returned model/engine versions and
model inventory hash must match the explicitly configured source;
an error cannot silently switch provider, device, or language.
A resource lease is released after failures and timeouts.

No general claim is made about subprocess peak memory, GPU OOM,
model accuracy, or user-side provider availability. Full stress,
resource performance and Windows compatibility are GA22/GA23 gates.

## 4. Transcript and word alignment rules

Source output is immutable and SHA-hashed using
\`ga-asr-transcript/v1\`, including config, upstream/model provenance
and source media/PCM digests. It contains:
- \`source\` and status \`partial|unavailable\`;
- \`speech_status\` exactly \`speech_detected\`, \`unknown\`,
  \`verified_silent\`, or \`no_audio_stream\`;
- detected or configured language and its origin;
- ordered timed segments, each with actual start/end text and language;
- optional real word start/end/confidence. If any word lacks model
  timing, retain the timed segment but set word alignment \`unknown\`.
- separate \`timing_reliability=none|untimed|segment_observed|word_observed\`;
  \`word_alignment_complete\` true only if all observed segments
  contain valid word timings, never because words were split from text.
- \`complete_speech_coverage=false\`; no word counts, WPM,
  video-wide absence or SRT-to-speech classification.

Word positions are never distributed proportionally across a segment.
Invalid time ordering, overlap, out-of-bounds cues, nonfinite
confidence, invalid language, forged model hash and malformed
model outputs are rejected, not silently normalized.

Speech candidates from GA09 VAD alone are **not** an ASR result.
No VAD candidates does not establish proven no-speech. Confirmed
no-audio or exact-zero decoded PCM can skip local ASR but must not
discard independent verified caption observations.

## 5. QA matrix and reproducible commands

\`tests/test_group_a_asr.py\` covers RU/EN language, real
model-provider boundary with fake injected worker and resource lease,
segment and optional word times, wrong model/config, silence/no audio,
SRT vs TXT, captions on video without audio, independent timebase,
misaligned PSI/rights revocation, conflicting source precedence,
bad SRT, malformed intervals, sidecar SHA, subprocess timeout/
nonzero exit/no JSON, exact wire schema and no automatic network calls.

\`tests/test_group_a_asr_real_model.py\` is a **separate opt-in actual
model accuracy gate**, not a fake-model PASS. It generates local
English and Russian eSpeak TTS phrases, converts them with FFmpeg
to 16k mono PCM and runs the installed faster-whisper. It calculates
a minimal word-error rate and verifies source language and timing.
It skips when the host has not explicitly provided all:
\`\`\`
CM_TEST_ASR_MODEL_DIR
CM_TEST_ASR_MODEL_SHA
CM_TEST_ASR_MODEL_VERSION
CM_TEST_ASR_ENGINE_VERSION
\`\`\`
and installed faster-whisper, eSpeak, FFmpeg. A TTS WER threshold is
a smoke/golden regression, NOT validation of accuracy on representative
real-world noisy multilingual human speech.

The independent local shell was used to generate English/Russian eSpeak
audio and resample each using actual FFmpeg. Observed 16k mono samples:
RU **33,983**, EN **33,137**, both nonempty. **TTS/FFmpeg INPUT SMOKE PASS**.
This is NOT a faster-whisper inference test: package/weights were absent.

Static GitHub source audit (16 checks) **PASS**, branch isolated
from \`main\` (ahead, not behind). GitHub Actions Python/PG17 remained
\`queued\`: **PYTHON UNIT, ASR SUBPROCESS and PG17 NOT EXECUTED**.
Opt-in real ru/en ASR model accuracy **NOT RUN**.

Reproduction:
\`\`\`bash
python -m compileall -q intelligence/group_a tests/test_group_a_*.py
python -m unittest discover -s tests -p test_group_a_asr.py -v
python -m unittest discover -s tests -p test_group_a_asr_real_model.py -v
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
\`\`\`

Next: **GA-MOD-11**, speech word count, active-time WPM,
first-speech timing and coverage, using only validated ASR/VAD
intervals and respecting distinct audio/video time origins.
