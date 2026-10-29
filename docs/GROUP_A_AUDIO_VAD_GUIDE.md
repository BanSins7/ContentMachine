# GA-MOD-09 — Audio Decode + Voice Activity Detection

**Implementation:** COMPLETE in `feature/group-a-full-module` (isolated, offline).
**Runtime:** opt-in only; NO QDP worker activation, provider calls, ASR, OCR, paid
inference, music classification or language/word-count guesses.
**QA:** independent direct FFprobe/FFmpeg command smoke PASS; source/branch
static audit PASS; GitHub Python/PG17 suite QUEUED / NOT EXECUTED.
**Model QA:** genuine production Silero JIT weights are NOT supplied in this
branch, therefore live Silero accuracy/latency are NOT VERIFIED.

## 1. Architecture and safe input boundary

```text
GA-MOD-05 D1 authorized VerifiedFile (SHA-256 verified bytes)
    → GroupAService.inspect_local_audio(verified_media, roots=...)
    → audio_decode.decode_audio()
       → FFprobe stream enumeration
       → FFmpeg PCM, 16 kHz mono s16le, memory only
       → DecodedAudio (never put PCM in SQL or published API)
    → vad.observe_vad()
       → no audio / verified digital silence / model unavailable
         OR explicit local SHA-pinned Silero JIT candidate intervals
    → ga-audio-decode/v1 + ga-vad-observation/v1 evidence
    → future GA-MOD-10 ASR and GA-MOD-11 speech metrics
```

`audio_decode.py` uses `media_manifest.open_verified()` and only reads an
actual GA-MOD-05 verified local video. Linux FFmpeg/FFprobe receive the
already-open verified descriptor as `/proc/self/fd/<fd>`, passed explicitly
to both subprocesses; there is no path-reopen race between byte verification
and processing. Other hosts use the approved local path with verified inode,
size and time attributes before/after the call; platform-specific races remain
GA-MOD-23 QA. Source/rights must be independently rechecked at job execution
in the future GA-MOD-20 worker.

No HTTP/URL input, no automatic media fetch, no PSI/QDP private import,
no PyTorch Hub and no weight download are performed.

## 2. FFprobe and FFmpeg contract

- FFprobe must positively identify a valid video container. Zero audio
  streams => `audio_status=absent` with
  `verified_audio_stream_absence=true`, not a failed decoder.
- Exactly one audio track is accepted by default. If there are several,
  an explicit `audio_stream_index` is required. No language or priority
  is guessed from stream order. Invalid/unavailable codec or stream
  selection is rejected.
- Probed codec/channel count/sample rate, exact stream index, time base,
  duration and start-time offset are preserved as provenance. Encoder
  negative start offsets are retained, not silently clamped to zero.
- FFmpeg uses `-protocol_whitelist file,pipe`, `-xerror`,
  `-err_detect explode`, explicit `-map 0:<audio stream>`,
  disables video/subtitles/data (`-vn -sn -dn`), downmixes to **1 mono
  channel**, resamples to **16,000 Hz**, and emits **PCM s16le**
  to an in-memory pipe. Per-process time limits: FFprobe 30s, FFmpeg
  210s. PCM output is capped at approximately 19.2 MB / 600s,
  plus a small decoder tolerance.
- Audio stream present but zero PCM or corrupt decode => explicit error
  `audio_pcm_unobservable`/`audio_tool_failed`; **not** silence.
  Successful PCM with every exact integer sample equal to zero =>
  `audio_status=silent`, `digital_silence=true`.
  Nonzero PCM => `audio_status=decoded`. Low energy/noisy/mixed
  music cannot be called speech-free by a volume threshold.
- `DecodedAudio.wire()` includes media SHA, PCM SHA, decoded sample
  count, output rate/channel/format, input stream provenance and
  `time_origin=audio_stream_local_zero`. It NEVER serializes PCM.
  **Audio-local time is not assumed equal to video-local time**:
  ffprobe's stream start offset is pinned for later GA-MOD-10/11 mapping.
- Missing, corrupt, undecodeable or partial data must not be counted as
  observed acoustic silence or full speech coverage.

`ga-audio-decode/v1` is validated by a strict JSON Schema
`schemas/ga-audio-decode-v1.json`.

## 3. Explicit Silero JIT, CPU/GPU configuration and VAD semantics

`vad.py::LocalSileroJIT` accepts a preinstalled absolute `.jit` path,
expected full SHA-256, explicit pinned model version
(`silero-vad/<version>`) and device `cpu` or `cuda`. The runtime lazily
imports torch; CUDA is never silently substituted with CPU if the
requested device is unavailable. JIT model file is read, SHA verified
and loaded from **the same opened descriptor**, guarding model file
path replacement. Model recurrent state is reset per audio clip.

At 16 kHz the model consumes **512-sample windows**. Config supports:
hysteresis start/release thresholds (default 0.55/0.40), minimum speech
120ms, minimum silence 160ms, 64ms padding and 120ms merge gap.
Unfinished windows are zero-padded only for model input and their
candidate intervals are clipped to the actual decoded sample duration.
Intervals are sorted and unioned (no overlaps); output timestamps are
in exact integer milliseconds and reversible `x.xxx` seconds.

A VAD model identifies **candidate speech activity**, not words,
language, calibrated certainty or complete ASR coverage. Specifically:

| Input state | VAD result | Speech absence claim |
|---|---|---|
| Verified no audio stream | `no_audio_stream` | Verified: no audio track |
| Decoded exact-zero PCM | `digital_silence` | Verified: decoded samples contain no signal |
| Nonzero PCM, no model | `model_unavailable` | Unknown |
| Model returns intervals | `speech_candidates`, `partial` | No complete ASR claim |
| Model returns no intervals | `no_candidates`, `partial` | **Unknown**, not verified absent |
| Model fails/times out | Error/unavailable | Unknown |

The wire `speech_status` remains `not_run` until real transcription
is performed in GA-MOD-10. `active_speech_ms` represents the union of
**VAD candidates**, not final speech duration or WPM (GA-MOD-11).
A model could mistake music/SFX for speech; actual evaluation requires
a multilingual ground-truth corpus. No language or word labels are
fabricated in GA-MOD-09.

Config/model hashes, selected device and union intervals are part of
the deterministic `ga-vad-observation/v1` hash-bound evidence format
(`schemas/ga-vad-observation-v1.json`).

## 4. Deployment example (no automatic activation)

```python
from intelligence.group_a import (
    GroupAService, VADConfig, LocalSileroJIT
)

vad = LocalSileroJIT(
    "/srv/contentmachine/models/silero-vad-pinned.jit",
    expected_sha256=verified_model_sha256,
    version="silero-vad/host-pinned-v1",
    device="cpu",
)
service = GroupAService()  # worker/QDP integration remains disabled

decoded, vad_evidence = service.inspect_local_audio(
    verified_media,    # GA05 VerifiedFile, not an arbitrary URL/path
    roots=["/srv/contentmachine/media"],
    model=vad,
    config=VADConfig(device="cpu"),
)

audio_metadata = decoded.wire()  # immutable metadata, NO PCM
# decoded.pcm is memory only, for future approved local ASR.
```

All variables referring to media/model SHA are host-provided authoritative
data, not fabricated defaults. The Silero weights are **not committed**
or downloaded by Group A. Without a local model, the service returns
an explicit `model_unavailable` state for nonzero PCM.

## 5. Tests, independent smoke and open quality gates

`tests/test_group_a_audio_vad.py` covers:
- missing audio track vs decoded silence vs nonzero acoustic signal;
- stereo/six-channel to 16k mono resampling and explicit track mapping;
- multi-track without selector refusal; broken codec, timeout, absent PCM,
  nonzero exit, mutated source file and invalid metadata;
- VAD hysteresis, padding, gap union/overlap, model provenance, CPU/CUDA
  device mismatch, out-of-range/nonfinite probabilities, short PCM
  trailing window, deterministic repeated observation hash;
- explicit model unavailable, low-energy/no-candidate cannot assert
  no speech, and no guessed multilingual/language/ASR results;
- frozen audio/VAD JSON Schema positive and negative fixtures;
- real local FFmpeg-generated 1s `no_track.mkv`, `silence.mkv`
  and `tone.mkv` fixtures.

The current independent local runtime executed FFmpeg/FFprobe commands
on three synthetic 128x96 MKV videos. Direct checks observed:
- `no_track.mkv`: video stream only, audio absent.
- `silence.mkv`: audio `pcm_s16le`, stereo 44100 Hz → mono
  16000 Hz, exactly **16,000 samples** with sample peak **0**.
- `tone.mkv`: audio `pcm_s16le`, mono 48000 Hz → mono
  16000 Hz, exactly **16,000 samples** with nonzero sample peak
  **4095**.
- Descriptor-path `/proc/self/fd/<fd>` was independently tested
  for FFprobe and FFmpeg; both returned exit code 0 and FFmpeg produced
  32,000 bytes PCM.

**DIRECT FFMPEG/FFPROBE COMMAND SMOKE: PASS.**
This is not a claim that the committed Python module, PyTorch/Silero model
or PostgreSQL tests passed.

Workflow `.github/workflows/group-a-module.yml` runs all
`test_group_a_*.py` tests on Python 3.11/PG17, asserts installed PCM
decoder/encoder and `aresample`, but its GitHub Actions runners remain
**QUEUED / NOT EXECUTED** at the latest check. Torch/Silero weights are
optional and not installed in CI; deterministic fake-model fixtures test
the adapter boundary without network. A real pinned Silero model
accuracy/CPU/GPU performance test must be completed before release.

**Next:** GA-MOD-10 — offline ASR pipeline, exact timed words and
language provenance; GA-MOD-11 — speech count, active-time WPM and
speech coverage, respecting overlaps and verified silence semantics.
