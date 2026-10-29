# GA-MOD-15 — Audio Event Detection: music / sound_effect / other

**Branch:** `feature/group-a-full-module`.
**Implementation:** COMPLETE in code, offline/opt-in, standalone.
**QA:** GitHub source/scope static audit **21/21 PASS**; committed
`tests/test_group_a_audio_events.py` and PostgreSQL17 CI **QUEUED /
NOT EXECUTED**. Genuine licensed YAMNet/PANNs ONNX inference,
real-music accuracy and latency benchmarks **NOT VERIFIED**.
**Release:** NOT READY. No GA15 direct SQL write; atomic writer is GA18.

## 1. Input, windows and classifier contract

`extractors/audio_events.py` accepts only GA09 `DecodedAudio`,
already produced from a GA05 SHA/rights-verified local media source.
It reads 16 kHz mono PCM s16le in memory, never raw URL, arbitrary media
paths or transcript text. The caller MUST supply an authoritative
`verify_current(context)` returning literal `True` for source media SHA,
PCM SHA and selected audio stream. SHA integrity by itself is NOT a
permission check.

`AudioEventConfig` supports PCM window lengths 250/500/1000 ms, hops
125/250/500 ms (never larger than a window), bounded 3000 windows,
up to 600s wall-clock budget and configurable thresholds:
music 0.750, sound_effect 0.800, other 0.850. The threshold policy
version and exact model + class map provenance are SHA-bound to every
result. The final partial window can be zero-padded **for model input**,
but the evidence end time is clipped to the actual decoded PCM sample
count, never extended by the padding. Audio-local timestamps are exactly
three-decimal strings derived from integer 16 kHz sample indices.

`AudioEventClassifier` is a pure injected offline Protocol with
`provenance` and `predict(window_pcm,sample_rate=16000)` returning
all three labels' finite 0–1 scores. Classes are **multi-label**:
music and sound_effect may overlap each other **and** VAD/ASR speech.
No label is suppressed when speech exists. Windows with invalid scores,
missing outputs or inference errors are `failed`, not no-music.
Consecutive positive windows are unioned by label; gaps/failures split
events. `min_positive_windows` is explicit; no temporal interpolation
is implied between unobserved windows.

## 2. Real optional local ONNX implementation

`extractors/onnx_audio_events.py` provides
`LocalONNXAudioClassifier` for an **explicitly installed,
host-authorized** local ONNX YAMNet/PANNs graph. It requires:
- absolute, non-symlink `.onnx` file under 384 MiB with host-pinned
  full SHA-256, exact `model_version` and ONNX engine declaration;
- `pinned_labelmap`: explicit 0-based unique class indexes for
  `music`, `sound_effect` and `other`, pinned by canonical SHA;
  no guessed AudioSet classes or invented taxonomy grouping;
- pre-approved model license/usage verification from the trusted host;
  `verify_classifier` callback again verifies exact model+map license
  binding at every GA15 invocation;
- an explicit host CPU resource lease
  `lease.acquire(kind="audio_events",device="cpu")`;
- separately provisioned ONNX Runtime + NumPy and an ONNX model whose
  waveform input is either float32 `[samples]` or `[1,samples]`
  and output is scores `[classes]` or `[frames,classes]`.
  Incompatible signatures/providers fail closed. Raw 0–1 ONNX scores
  must already be normalized; no guessed sigmoid or fake probabilities.

ONNX Runtime imports lazily; the engine loads model bytes from the
SAME buffer whose hash was checked, avoiding path-swap between
verification and inference. No runtime model download, internet,
inference API billing, CUDA-to-CPU fallback or weight file in git.

**Safety limit:** ONNX predict currently runs in-process under the
host resource lease; the GA15 wall-clock budget is checked between
windows but cannot forcibly interrupt a hung single ONNX call.
A hard subprocess watchdog, peak memory budgets and validated
hardware compatibility are required in GA20/GA22/GA23 before
production activation. No production weights/accuracy are claimed.

## 3. Truth semantics

| Observation | music_presence / sound_effect_presence | A003 event rows |
|---|---|---|
| Verified **no audio stream** | `false / false` | Empty |
| Verified exact **zero-valued PCM** | `false / false` | Empty |
| PCM exists, classifier not installed | `unknown / unknown` | Empty |
| Classifier failed, never observed positives | `unknown / unknown` | Empty |
| Nonzero PCM, no label above threshold | `unknown / unknown` | Empty |
| Authorized classifier observes music window | `true / unknown` (unless SFX observed too) | Positive music candidate with proven clock |
| Music and SFX scores both positive | `true / true` | Two overlapping candidate event rows |

The `true` value is **classifier-positive candidate evidence**,
not a human-reviewed calibrated probability of actual music.
`class_probability_calibrated=false` and
`full_video_acoustic_coverage=false` are unconditional.
Raw `max_engine_score` and per-window scores are diagnostic
0..1 numbers, NOT calibrated confidence percentages.
A failed or unobserved window cannot prove a negative.
Other/sound_effect/music are independent labels; VAD "no candidates"
cannot prove any sound class absent.

`window_coverage=complete_selected_pcm` means successful overlap
window inference over **one selected decoded audio track**, NOT every
original media track/video frame nor proven classification accuracy.
Overlapping event rows are kept, not collapsed.

## 4. Exact A003 projection

The real `003_video_features_a.sql` stores
`video_feature_a_audio_events` with:
`event_type IN ('music','sound_effect','other')`,
`start_sec`, `end_sec`, `label`, `method_name`,
`confidence_pct` (nullable). The GA15 output has
`a003_audio_events_projection` matching these columns, but it is
**not persisted** in this stage.

Source events initially use `time_origin=audio_stream_local_zero`.
Only when the authoritative host supplies the corresponding
GA06 `ProbeInfo` video clock, both FFprobe stream start offsets exist,
and every mapped event fits inside the video duration, an A003 row is
constructed using:

```
video_local_start = audio_local_start + audio_stream_start - video_stream_start
video_local_end   = audio_local_end   + audio_stream_start - video_stream_start
```

Missing timebase gives an empty projection and
`a003_mapping_state=blocked_unverified_video_timebase`.
Out-of-video bounds are an error, never silently clipped.
All projected `confidence_pct=null` because raw classifier
scores have not been empirically calibrated to probabilities.
Real inserts are owned by GA18 atomic versioned snapshots.

## 5. Service usage (no automatic worker)

```python
from intelligence.group_a import (
    GroupAService, AudioEventConfig,
    LocalONNXAudioClassifier, pinned_labelmap,
)

groups, mapping_sha = pinned_labelmap(verified_host_label_groups)
engine = LocalONNXAudioClassifier(
    model_path=authorized_local_onnx_path,
    expected_sha256=trusted_onnx_model_sha,
    model_version=approved_model_version,
    engine="yamnet_onnx",
    label_groups=groups,
    expected_labelmap_sha256=mapping_sha,
    input_layout="samples",  # must match the inspected ONNX signature
    lease=authorized_host_cpu_lease,
    verify_license=authoritative_license_policy,
)
result = GroupAService().inspect_local_audio_events(
    decoded_audio, classifier=engine, video_probe=verified_video_probe,
    config=AudioEventConfig(), verify_current=current_media_rights,
    verify_classifier=current_model_and_license_rights,
)
# result["events"]: positive model-observed candidate intervals
# result["a003_audio_events_projection"]: mapped rows, NO DB write
```

All `verified_*` and `authorized_*` variables are trusted
**host inputs**: this example does not manufacture provenance,
model weights, license permission or source rights.

## 6. Tests and remaining verification

`tests/test_group_a_audio_events.py` covers:
music only, music+SFX overlaps, transient short SFX, background noise
below threshold, `other` label coexistence, digital silence,
verified no audio, missing classifier/model, failed windows,
malformed/out-of-range/NaN scores, missing/forged model SHA/license,
user-selected wrong device and resource lease,
short final padded window, known/unverified/out-of-bounds video
timebase, A003 projection with NULL confidence, deterministic hashes,
class index map/overlap collisions, strict JSON Schema positive and
negative fixtures. No paid endpoint, no bundled weight download.

At last source inspection 21/21 static assertions PASS, feature branch
ahead of main and behind 0 with Group A-only changes.
The GitHub workflow `Group A standalone (audio events and PG17)` is
QUEUED; no verified Python/PG17 PASS exists yet. No genuine model
inference, RU/EN/music noisy corpus scoring, transient onset accuracy
or audio vs ASR/VAD cross-model performance was executed.

**Next:** GA-MOD-16 photo carousel/image-post source verification and
ordered slide features; it must not inherit any audio timebase from GA15.
