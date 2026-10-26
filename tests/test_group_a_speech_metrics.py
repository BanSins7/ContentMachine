"""GA-MOD-11 exact numeric regression and truth/lineage checks (no DB)."""
from __future__ import annotations

from array import array
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import json
import sys
import unittest

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.extractors.audio_decode import AudioProbe, DecodedAudio
from intelligence.group_a.extractors.asr import (
    ASRConfig, Sidecar, _build, transcribe,
)
from intelligence.group_a.extractors.technical import ProbeInfo
from intelligence.group_a.extractors.speech_metrics import (
    SpeechMetricsConfig, SpeechMetricsError, PSITimelineBinding,
    union_intervals, count_observed_words, measure_speech,
)


SHA = "b"*64


def audio(*, samples=16000*10, value=800, start="0.250", absent=False):
    if absent:
        return DecodedAudio(
            AudioProbe("absent",None,None,None,None,None,None,None),b"",SHA)
    pcm=array("h",[value])*samples
    if sys.byteorder!="little":
        pcm.byteswap()
    return DecodedAudio(
        AudioProbe("present",1,"pcm_s16le",1,16000,start,"1/16000","10.000"),
        pcm.tobytes(),SHA,
    )


def video(duration="10.000",start="0.000"):
    return ProbeInfo(Decimal(duration),"9:16",0,"mpeg4","1/1000",
                     Decimal(start) if start is not None else None,"stream")


def asr(src,*,source="local_faster_whisper",texts=None):
    lines=texts or [("0.500","1.500","Привет, мир!"),
                     ("2.000","3.000","Hello can't stop")]
    rows=[{"start_sec":a,"end_sec":b,"text":text,
           "language":"ru" if i==0 else "en",
           "confidence":None,"words":None,"word_timing_status":"unknown"}
          for i,(a,b,text) in enumerate(lines)]
    if source=="local_faster_whisper":
        time_origin="audio_stream_local_zero"
        model={"engine":"faster-whisper","engine_version":"1.1.1",
               "model_version":"medium-v3","sha256":"c"*64}
        upstream=None
    elif source=="psi_asr":
        time_origin="psi_source_local_ms"
        model=None
        upstream={"output_sha256":"f"*64,"output_id":"8"*36}
    else:
        raise ValueError(source)
    return _build(src,ASRConfig(),source=source,status="partial",
                  speech_status="speech_detected",language="unknown",
                  lang_source="model_detected" if source=="local_faster_whisper" else "psi",
                  timing_origin=time_origin,timing="segment_observed",
                  segments=rows,untimed_text=None,upstream=upstream,
                  model=model,reason="fixture_observed")


def side(kind,text):
    data=text.encode("utf-8")
    return Sidecar(kind,SHA,data,sha256(data).hexdigest())


SRT="1\n00:00:00,500 --> 00:00:01,500\nFive bright stars\n"


def vad(src,spans,*,state="speech_candidates"):
    fields={
        "schema_version":"ga-vad-observation/v1",
        "method_version":"group-a-vad-silero-jit-v1",
        "source_media_sha256":SHA,
        "pcm_sha256":sha256(src.pcm).hexdigest() if src.pcm else None,
        "time_origin":"audio_stream_local_zero",
        "state":state,"status":"partial","reason_code":"candidate_speech_not_asr",
        "verified_no_speech":False,"speech_status":"not_run",
        "sampling_rate":16000,"source_duration_ms":src.duration_ms,
        "processed_windows":313,"model":{"name":"silero_vad","version":"silero-vad/test",
            "sha256":"c"*64,"device":"cpu"},
        "config":{"speech_threshold":"0.55","release_threshold":"0.40",
                  "min_speech_ms":120,"min_silence_ms":160,"pad_ms":64,
                  "merge_gap_ms":120,"device":"cpu","window_samples":512,
                  "sample_rate":16000},
        "intervals":[{"start_ms":a,"end_ms":b,
                      "start_sec":f"{Decimal(a)/1000:.3f}",
                      "end_sec":f"{Decimal(b)/1000:.3f}"} for a,b in spans],
        "active_speech_ms":sum(b-a for a,b in spans),
        "speech_interval_coverage":"candidate_only" if spans else "unknown",
    }
    if not spans:
        fields.update(state="no_candidates",reason_code="vad_no_candidates")
    return {**fields,"observation_sha256":canonical_sha256(fields)}


class GA11GoldMetrics(unittest.TestCase):
    def test_ru_en_repeats_contractions_hyphens_numbers(self):
        lines=[
            {"text":"Привет, мир! Hello can't stop. Hello!"},
            {"text":"по-русски 123 don't _ignore_ café c\u0061fe\u0301"},
        ]
        self.assertEqual(count_observed_words(lines),13)
        self.assertEqual(count_observed_words([{"text":"— … ! 🎯"}]),0)
        self.assertEqual(count_observed_words([{"text":"да да да"}]),3)

    def test_local_measured_gold_wpm_coverage_first_speech(self):
        src=audio()
        result=measure_speech(src,asr(src),video_probe=video(),
                             config=SpeechMetricsConfig(duration_source="asr_only"))
        self.assertEqual(result["spoken_words"],5)
        self.assertEqual(result["speech_duration_sec"],"2.000")
        self.assertEqual(result["speech_wpm"],"150.00")
        self.assertEqual(result["speech_coverage_pct"],"20.00")
        self.assertEqual(result["first_speech_sec"],"0.750")
        self.assertEqual(result["video_clock_offset_sec"],"0.250")
        self.assertEqual(result["active_duration_basis"],"observed_asr_utterance_union")
        self.assertEqual(result["clock_status"],"ffprobe_stream_origins")
        self.assertEqual(result["coverage_state"],"known_derived_partial")
        self.assertFalse(result["complete_speech_coverage"])
        self.assertEqual(result["a003_speech_projection"]["spoken_words"],5)
        self.assertEqual(result["observation_sha256"],
            canonical_sha256({k:v for k,v in result.items()
                              if k!="observation_sha256"}))

    def test_vad_candidate_duration_is_distinct_and_explicit(self):
        src=audio()
        candidate=vad(src,[(500,1200),(1400,2800)])
        out=measure_speech(src,asr(src),video_probe=video(),vad=candidate)
        self.assertEqual(out["spoken_words"],5)
        self.assertEqual(out["speech_duration_sec"],"2.100")
        self.assertEqual(out["speech_wpm"],"142.86")
        self.assertEqual(out["speech_coverage_pct"],"21.00")
        self.assertEqual(out["first_speech_sec"],"0.750")
        self.assertEqual(out["vad_candidate_duration_sec"],"2.100")
        self.assertEqual(out["active_duration_basis"],
                         "vad_candidates_correlated_with_asr")
        self.assertEqual(out["vad_asr_consistency"],"correlated_with_asr")
        self.assertEqual(measure_speech(
            src,asr(src),vad=candidate,video_probe=video(),
            config=SpeechMetricsConfig(duration_source="asr_only")
        )["speech_duration_sec"],"2.000")

    def test_unrelated_vad_music_like_window_is_not_wpm_denominator(self):
        src=audio()
        unrelated=vad(src,[(6500,8000)])
        result=measure_speech(src,asr(src),video_probe=video(),vad=unrelated)
        self.assertEqual(result["spoken_words"],5)
        self.assertEqual(result["speech_duration_sec"],"2.000")
        self.assertEqual(result["speech_wpm"],"150.00")
        self.assertEqual(result["vad_asr_consistency"],"mismatched_with_asr")
        self.assertEqual(result["vad_candidate_duration_sec"],"1.500")
        result=measure_speech(src,asr(src),video_probe=video(),
            vad=unrelated,config=SpeechMetricsConfig(duration_source="vad_only"))
        self.assertIsNone(result["speech_wpm"])
        self.assertEqual(result["wpm_status"],"vad_evidence_unavailable")

    def test_interval_union_gold_overlaps_contiguous_and_bounds(self):
        d=Decimal
        self.assertEqual(union_intervals([
            (d("0"),d("0.900")),(d("0.500"),d("1.500")),
            (d("1.500"),d("2.000")),(d("3"),d("4"))
        ],max_duration=d("5")),((d("0"),d("2.000")),(d("3"),d("4"))))
        with self.assertRaises(SpeechMetricsError):
            union_intervals([(d("0"),d("6"))],max_duration=d("5"))
        with self.assertRaises(SpeechMetricsError):
            union_intervals([(d("2"),d("2"))],max_duration=d("5"))
        with self.assertRaises(SpeechMetricsError):
            union_intervals([(d("0"),d("-1"))],max_duration=d("5"))

    def test_without_video_clock_duration_and_wpm_can_be_measured(self):
        src=audio()
        measured=measure_speech(src,asr(src))
        self.assertEqual(measured["spoken_words"],5)
        self.assertEqual(measured["speech_wpm"],"150.00")
        self.assertIsNone(measured["first_speech_sec"])
        self.assertIsNone(measured["speech_coverage_pct"])
        self.assertEqual(measured["coverage_state"],"unknown")
        with self.assertRaises(SpeechMetricsError) as ctx:
            measure_speech(src,asr(src),config=SpeechMetricsConfig(
                require_video_clock=True))
        self.assertEqual(ctx.exception.code,"video_clock_required")

    def test_missing_audio_video_offset_preserves_unknown_first_speech(self):
        src=audio(start=None)
        measured=measure_speech(src,asr(src),video_probe=video())
        self.assertIsNone(measured["first_speech_sec"])
        self.assertIsNone(measured["speech_coverage_pct"])
        self.assertEqual(measured["speech_duration_sec"],"2.000")
        measured=measure_speech(audio(),asr(audio()),video_probe=video(start=None))
        self.assertIsNone(measured["first_speech_sec"])

    def test_psi_requires_explicit_acquisition_clock(self):
        src=audio()
        transcript=asr(src,source="psi_asr")
        without=measure_speech(src,transcript,video_probe=video())
        self.assertEqual(without["spoken_words"],5)
        self.assertIsNone(without["speech_coverage_pct"])
        binding=PSITimelineBinding(SHA,"f"*64,"0.100")
        output=measure_speech(src,transcript,video_probe=video(),
                              psi_timeline=binding)
        self.assertEqual(output["first_speech_sec"],"0.600")
        self.assertEqual(output["speech_coverage_pct"],"20.00")
        self.assertEqual(output["clock_status"],"verified_psi_acquisition")
        with self.assertRaises(SpeechMetricsError):
            measure_speech(src,transcript,video_probe=video(),
                           psi_timeline=PSITimelineBinding(SHA,"e"*64,"0.100"))
        with self.assertRaises(SpeechMetricsError):
            PSITimelineBinding(SHA,"f"*64,"NaN")

    def test_caption_only_ru_en_and_txt_never_count_as_spoken_words(self):
        src=audio()
        for kind,text in (("srt",SRT),("txt","Русский English 123")):
            transcript=transcribe(src,sidecar=side(kind,text),video_duration_sec="10.000")
            metrics=measure_speech(src,transcript,video_probe=video())
            self.assertIsNone(metrics["spoken_words"])
            self.assertIsNone(metrics["speech_duration_sec"])
            self.assertIsNone(metrics["speech_wpm"])
            self.assertIsNone(metrics["first_speech_sec"])
            self.assertEqual(metrics["speech_status"],"unknown")

    def test_verified_audio_absence_or_zero_pcm_only_true_zero(self):
        none=audio(absent=True)
        quiet=audio(value=0)
        for src in (none,quiet):
            output=measure_speech(src,transcribe(src),video_probe=video())
            self.assertEqual(output["spoken_words"],0)
            self.assertEqual(output["speech_duration_sec"],"0.000")
            self.assertIsNone(output["speech_wpm"])
            self.assertEqual(output["speech_coverage_pct"],"0.00")
            self.assertIsNone(output["first_speech_sec"])
            self.assertEqual(output["speech_status"],"verified_absence")
            self.assertEqual(output["coverage_state"],"verified_zero")
        # Even on a silent video, SRT means captions, NOT audible words.
        trans=transcribe(none,sidecar=side("srt",SRT),video_duration_sec="10.000")
        result=measure_speech(none,trans,video_probe=video())
        self.assertEqual(result["spoken_words"],0)
        self.assertEqual(result["transcript_source"],"sidecar_srt")

    def test_only_vad_candidates_no_asr_cannot_infer_words_or_wpm(self):
        src=audio()
        candidate=vad(src,[(500,1200),(1400,2800)])
        result=measure_speech(src,transcribe(src),vad=candidate,video_probe=video())
        self.assertIsNone(result["spoken_words"])
        self.assertIsNone(result["speech_duration_sec"])
        self.assertIsNone(result["speech_wpm"])
        self.assertIsNone(result["first_speech_sec"])
        self.assertIsNone(result["speech_coverage_pct"])
        self.assertEqual(result["vad_candidate_duration_sec"],"2.100")
        result=measure_speech(src,asr(src),config=SpeechMetricsConfig(
            duration_source="vad_only"))
        self.assertEqual(result["spoken_words"],5)
        self.assertIsNone(result["speech_duration_sec"])
        self.assertEqual(result["wpm_status"],"vad_evidence_unavailable")

    def test_low_duration_has_known_words_but_unstable_wpm(self):
        src=audio()
        short=asr(src,texts=[("0.100","0.200","слово")])
        metrics=measure_speech(src,short,video_probe=video(),
            config=SpeechMetricsConfig(duration_source="asr_only"))
        self.assertEqual(metrics["spoken_words"],1)
        self.assertEqual(metrics["speech_duration_sec"],"0.100")
        self.assertIsNone(metrics["speech_wpm"])
        self.assertEqual(metrics["wpm_status"],
                         "insufficient_active_duration_for_stable_wpm")

    def test_speech_past_video_end_is_rejected_not_clipped(self):
        src=audio()
        with self.assertRaises(SpeechMetricsError) as ctx:
            measure_speech(src,asr(src),video_probe=video(duration="2.000"))
        self.assertEqual(ctx.exception.code,"speech_interval_outside_video")
        with self.assertRaises(SpeechMetricsError):
            measure_speech(src,asr(src),video_probe=video(start="5.000"))

    def test_source_and_vad_hash_tampering_fail_closed(self):
        src=audio();transcript=asr(src)
        altered=dict(transcript,source_media_sha256="e"*64)
        with self.assertRaises(SpeechMetricsError):
            measure_speech(src,altered)
        altered=dict(transcript,source_pcm_sha256="e"*64)
        altered["observation_sha256"]=canonical_sha256({
            k:v for k,v in altered.items() if k!="observation_sha256"})
        with self.assertRaises(SpeechMetricsError):
            measure_speech(src,altered)
        candidate=vad(src,[(500,1200),(1400,2800)])
        tampered=dict(candidate,active_speech_ms=100)
        with self.assertRaises(SpeechMetricsError):
            measure_speech(src,transcript,vad=tampered)
        tampered=dict(candidate,active_speech_ms=100)
        tampered["observation_sha256"]=canonical_sha256({
            k:v for k,v in tampered.items() if k!="observation_sha256"})
        with self.assertRaises(SpeechMetricsError):
            measure_speech(src,transcript,vad=tampered)

    def test_vad_interval_forgeries_clocks_and_contradicting_silence(self):
        src=audio();transcript=asr(src)
        candidate=vad(src,[(500,1200),(1400,2800)])
        modified=dict(candidate,intervals=[{**candidate["intervals"][0],
            "end_ms":11000}])
        modified["observation_sha256"]=canonical_sha256({
            k:v for k,v in modified.items() if k!="observation_sha256"})
        with self.assertRaises(SpeechMetricsError):
            measure_speech(src,transcript,vad=modified)
        impossible=asr(src,texts=[("0.000","0.800","hello")])
        quiet=audio(value=0)
        impossible["source_pcm_sha256"]=sha256(quiet.pcm).hexdigest()
        impossible["observation_sha256"]=canonical_sha256({
            k:v for k,v in impossible.items() if k!="observation_sha256"})
        with self.assertRaises(SpeechMetricsError):
            measure_speech(quiet,impossible)

    def test_repeated_runs_are_deterministic_and_projection_is_partial(self):
        src=audio()
        a=measure_speech(src,asr(src),video_probe=video())
        b=measure_speech(src,asr(src),video_probe=video())
        self.assertEqual(a,b)
        self.assertEqual(a["schema_version"],"ga-speech-metrics/v1")
        self.assertNotIn("analysis_state_complete",a)
        self.assertNotIn("cta",json.dumps(a))
        self.assertNotIn("screen_text",json.dumps(a))


if __name__=="__main__":
    unittest.main()
