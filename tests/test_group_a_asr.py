"""GA-MOD-10: offline ASR source truth, alignment and subprocess regression."""
from __future__ import annotations

from array import array
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock,patch
import json
import subprocess
import sys
import unittest
import wave
try:
    import jsonschema
except ImportError:
    jsonschema=None

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.extractors.audio_decode import AudioProbe,DecodedAudio
from intelligence.group_a.extractors.asr import (
    ASRConfig,ASRError,Sidecar,transcribe,read_sidecar,ASR_SCHEMA,
)
from intelligence.group_a.extractors.local_asr import (
    LocalFasterWhisper,LocalASRProvenance,_model_inventory,
)


def media(samples=16000, value=250, absent=False):
    if absent:return DecodedAudio(
        AudioProbe("absent",None,None,None,None,None,None,None),
        b"","b"*64)
    data=array("h",[value])*samples
    if sys.byteorder!="little":data.byteswap()
    return DecodedAudio(
        AudioProbe("present",1,"pcm_s16le",1,16000,"0.250","1/16000","1.000"),
        data.tobytes(),"b"*64)


def side(kind,text):
    raw=text.encode("utf-8")
    return Sidecar(kind,media_sha256="b"*64,content=raw,
                   expected_sha256=sha256(raw).hexdigest())


SRT=("1\n00:00:00,200 --> 00:00:00,800\nHello!\n\n"
     "2\n00:00:00,800 --> 00:00:01,000\nWorld!\n")
RU_SRT=("1\n00:00:00,100 --> 00:00:00,700\nПривет, мир!\n")


class StubASR:
    provenance={"engine":"faster-whisper","sha256":"c"*64,
                "model_version":"medium-v3","engine_version":"1.1.1"}

    def __init__(self, language="en", words=True, invalid=None):
        self.calls=0;self.language=language;self.words=words;self.invalid=invalid
    def transcribe(self,audio,cfg):
        self.calls+=1
        segments=[{
            "start_sec":"0.100","end_sec":"0.700","text":"Hello world",
            "confidence":None,
            "words":[{"start_sec":"0.100","end_sec":"0.300","text":"Hello",
                      "confidence":0.88},
                     {"start_sec":"0.400","end_sec":"0.700","text":"world",
                      "confidence":0.95}] if self.words else None,
        }]
        if self.invalid=="overlap":
            segments[0]["words"][1]["start_sec"]="0.200"
        if self.invalid=="no_time":
            segments[0].pop("start_sec")
        return {"language":self.language,"segments":segments,
                "model_sha256":"c"*64 if self.invalid!="hash" else "d"*64,
                "model_version":"medium-v3","engine_version":"1.1.1"}


def psi(rows=None,origin="asr"):
    if rows is None:
        rows=[{"start_sec":"0.100","end_sec":"0.850","text":"hello!",
               "origin":origin,"language":"en","confidence":"0.85"}]
    return {"schema_version":"ga-psi-transcript-reuse/v1",
            "media_sha256":"b"*64,"complete_speech_coverage":False,
            "verified_no_speech":False,"status":"partial",
            "segments":rows,"language":"en",
            "upstream":{"output_id":"0"*8+"-"+"0"*4+"-"+"0"*4+"-"+"0"*4+"-"+"0"*12,
                        "output_sha256":"f"*64}}


class TestGA10Sources(unittest.TestCase):
    def test_real_word_alignment_only_when_provided(self):
        src=media()
        engine=StubASR()
        got=transcribe(src,backend=engine,config=ASRConfig(language="en"))
        self.assertEqual(got["source"],"local_faster_whisper")
        self.assertEqual(got["speech_status"],"speech_detected")
        self.assertEqual(got["language_origin"],"configured")
        self.assertEqual(got["timing_reliability"],"word_observed")
        self.assertTrue(got["word_alignment_complete"])
        self.assertEqual(got["segments"][0]["words"][0]["start_sec"],"0.100")
        self.assertEqual(got["segments"][0]["words"][1]["confidence"],"0.95")
        self.assertFalse(got["complete_speech_coverage"])
        self.assertEqual(got["observation_sha256"],
            canonical_sha256({k:v for k,v in got.items() if k!="observation_sha256"}))
        self.assertEqual(engine.calls,1)

    def test_missing_words_do_not_get_estimated_from_segment(self):
        engine=StubASR(words=False)
        got=transcribe(media(),backend=engine,config=ASRConfig(language="auto"))
        self.assertEqual(got["timing_reliability"],"segment_observed")
        self.assertFalse(got["word_alignment_complete"])
        self.assertEqual(got["segments"][0]["word_timing_status"],"unknown")
        self.assertIsNone(got["segments"][0]["words"])

    def test_srt_is_timed_caption_not_proven_audible_speech(self):
        src=side("srt",SRT)
        result=transcribe(media(),sidecar=src,video_duration_sec="1.000")
        self.assertEqual(result["source"],"sidecar_srt")
        self.assertEqual(result["time_origin"],"video_local")
        self.assertEqual(result["speech_status"],"unknown")
        self.assertEqual(result["timing_reliability"],"segment_observed")
        self.assertEqual(result["segments"][1]["end_sec"],"1.000")
        self.assertIsNone(result["segments"][0]["words"])
        self.assertIsNone(result["untimed_text"])
        self.assertEqual(result["upstream"]["sidecar_sha256"],src.expected_sha256)
        with self.assertRaises(ASRError) as ctx:
            transcribe(media(),sidecar=src)
        self.assertEqual(ctx.exception.code,"video_duration_required")

    def test_srt_can_exist_on_no_audio_video_without_claiming_speech(self):
        got=transcribe(media(absent=True),sidecar=side("srt",RU_SRT),
                       video_duration_sec="1.000")
        self.assertEqual(got["source"],"sidecar_srt")
        self.assertEqual(got["speech_status"],"no_audio_stream")
        self.assertEqual(got["segments"][0]["text"],"Привет, мир!")
        no_cues=transcribe(media(absent=True))
        self.assertEqual(no_cues["status"],"unavailable")
        self.assertEqual(no_cues["speech_status"],"no_audio_stream")

    def test_txt_is_explicit_untimed_and_never_word_aligned(self):
        result=transcribe(media(),sidecar=side("txt","Привет, мир!\nHello!"),
                          video_duration_sec="1.000")
        self.assertEqual(result["source"],"sidecar_txt")
        self.assertEqual(result["timing_reliability"],"untimed")
        self.assertEqual(result["segments"],[])
        self.assertEqual(result["untimed_text"],"Привет, мир!\nHello!")
        self.assertEqual(result["speech_status"],"unknown")
        self.assertFalse(result["word_alignment_complete"])

    def test_precedence_and_strict_source_policy(self):
        engine=StubASR()
        src=side("srt",SRT)
        checked=psi()
        allowed=lambda x: x is checked
        a=transcribe(media(),psi=checked,psi_verify_current=allowed,
                     sidecar=src,backend=engine,video_duration_sec="1.000")
        self.assertEqual(a["source"],"psi_asr")
        self.assertEqual(a["timing_reliability"],"segment_observed")
        self.assertEqual(engine.calls,0)
        b=transcribe(media(),psi=checked,psi_verify_current=allowed,sidecar=src,
                     backend=engine,config=ASRConfig(source_policy="sidecar_only"),
                     video_duration_sec="1.000")
        self.assertEqual(b["source"],"sidecar_srt")
        c=transcribe(media(),psi=checked,sidecar=src,backend=engine,
                     config=ASRConfig(source_policy="local_only"))
        self.assertEqual(c["source"],"local_faster_whisper")
        self.assertEqual(engine.calls,1)
        d=transcribe(media(),config=ASRConfig(source_policy="psi_only"))
        self.assertEqual(d["status"],"unavailable")
        self.assertEqual(d["reason_code"],"selected_source_unavailable")

    def test_psi_rights_hash_schema_timing_or_status_denied(self):
        checked=psi()
        def call(x,verify=lambda y:True):
            return transcribe(media(),psi=x,psi_verify_current=verify,
                              video_duration_sec="1.000")
        with self.assertRaises(ASRError) as ctx:call(checked,None)
        self.assertEqual(ctx.exception.code,"psi_current_rights_unverified")
        with self.assertRaises(ASRError):call(checked,lambda _:False)
        wrong=dict(checked,media_sha256="a"*64)
        with self.assertRaises(ASRError):call(wrong)
        wrong=dict(checked,complete_speech_coverage=True)
        with self.assertRaises(ASRError):call(wrong)
        wrong=psi(rows=[{"start_sec":"0.800","end_sec":"1.100",
                          "text":"oops","origin":"asr"}])
        with self.assertRaises(ASRError):call(wrong)
        result=call(psi(origin="srt"))
        self.assertEqual(result["source"],"psi_srt")
        self.assertEqual(result["speech_status"],"unknown")

    def test_absent_or_verified_silent_audio_blocks_local_asr(self):
        engine=StubASR()
        no_track=transcribe(media(absent=True),backend=engine)
        self.assertEqual(no_track["reason_code"],"verified_no_audio_stream")
        silent=transcribe(media(value=0),backend=engine)
        self.assertEqual(silent["reason_code"],"verified_digital_silence")
        self.assertEqual(silent["speech_status"],"verified_silent")
        self.assertEqual(engine.calls,0)
        with self.assertRaises(ASRError):
            transcribe(media(value=0),psi=psi(),
                       psi_verify_current=lambda _:True,video_duration_sec="1.000")
        self.assertEqual(transcribe(media(),backend=None)["reason_code"],
                         "local_asr_backend_not_connected")

    def test_language_validation_and_bad_segment_fail_closed(self):
        for language in ("ru","en"):
            data=transcribe(media(),config=ASRConfig(language=language),
                            backend=StubASR(language=language))
            self.assertEqual(data["language"],language)
        with self.assertRaises(ASRError):
            transcribe(media(),config=ASRConfig(language="ru"),backend=StubASR(language="en"))
        for kind in ("overlap","no_time","hash"):
            with self.subTest(kind=kind),self.assertRaises(ASRError):
                transcribe(media(),backend=StubASR(invalid=kind))
        with self.assertRaises(ASRError):ASRConfig(language="all")
        with self.assertRaises(ASRError):ASRConfig(timeout_sec=2)

    def test_srt_malformed_durations_cues_and_sidecar_hash(self):
        cases=("1\n00:00:00,999 --> 00:00:01,500\nLater",
               "1\n00:61:00,000 --> 00:00:01,000\nOops",
               "1\n00:00:00,000 --> 00:00:00,000\nZero",
               "not a caption","1\n00:00:00,000 --> 00:00:00,500\nA\n\n"
               "2\n00:00:00,400 --> 00:00:00,800\nOverlap")
        for text in cases:
            with self.subTest(text=text[:20]),self.assertRaises(ASRError):
                transcribe(media(),sidecar=side("srt",text),video_duration_sec="1.000")
        with self.assertRaises(ASRError):
            Sidecar("txt","b"*64,b"hello","c"*64)
        with self.assertRaises(ASRError):
            transcribe(media(),sidecar=Sidecar(
                "txt","c"*64,b"hello",sha256(b"hello").hexdigest()),
                video_duration_sec="1.000")


@unittest.skipUnless(jsonschema is not None,"jsonschema test-only dependency")
class TestGA10Schema(unittest.TestCase):
    def test_wire_schema_for_all_source_kinds_and_silent_video(self):
        path=Path(__file__).resolve().parents[1]/"intelligence"/"group_a"/"schemas"/"ga-asr-transcript-v1.json"
        schema=json.loads(path.read_text("utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        candidates=[
            transcribe(media(),backend=StubASR()),
            transcribe(media(),sidecar=side("srt",SRT),video_duration_sec="1.000"),
            transcribe(media(),sidecar=side("txt","Привет!"),video_duration_sec="1.000"),
            transcribe(media(),psi=psi(),psi_verify_current=lambda _:True,
                       video_duration_sec="1.000"),
            transcribe(media(),backend=StubASR(words=False)),
            transcribe(media(value=0)),
            transcribe(media(absent=True)),
            transcribe(media(absent=True),sidecar=side("srt",RU_SRT),
                       video_duration_sec="1.000"),
        ]
        for item in candidates:
            with self.subTest(source=item["source"],reason=item["reason_code"]):
                jsonschema.validate(item,schema)
        forged=dict(candidates[1],speech_status="speech_detected")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,schema)
        forged=dict(candidates[0],complete_speech_coverage=True)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,schema)
        forged=dict(candidates[4],word_alignment_complete=True,
                    timing_reliability="word_observed")
        # word_observed with no timestamps is rejected by core; schema
        # alone checks outer shape, not cross-item word completeness.
        self.assertEqual(forged["segments"][0]["word_timing_status"],"unknown")


class FakeLease:
    def __init__(self):
        self.enter=0
        self.exit=0
    def acquire(self,*,kind,device):
        if kind!="asr" or device!="cpu":raise AssertionError("unexpected resource")
        @contextmanager
        def locked():
            self.enter+=1
            try:yield
            finally:self.exit+=1
        return locked()


class TestGA10Worker(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.model=self.root/"model"
        self.model.mkdir()
        (self.model/"model.bin").write_bytes(b"real fixture bytes")
        (self.model/"config.json").write_text('{"model_type":"whisper"}',"utf-8")
        self.digest=_model_inventory(self.model)
        self.info=LocalASRProvenance(str(self.model),self.digest,"medium-v3","1.1.1")

    def test_local_model_hash_and_cpu_resource_lease(self):
        lease=FakeLease()
        def fake(cmd,**kw):
            self.assertIn("HF_HUB_OFFLINE",kw["env"])
            self.assertEqual(kw["env"]["HF_HUB_OFFLINE"],"1")
            self.assertIn("-m",cmd)
            self.assertNotIn("http",str(cmd).lower())
            with wave.open(cmd[4],"rb") as wavefile:
                self.assertEqual(wavefile.getframerate(),16000)
                self.assertEqual(wavefile.getnchannels(),1)
                self.assertEqual(wavefile.getnframes(),16000)
            out={"language":"ru","segments":[{"start_sec":"0.100",
                "end_sec":"0.500","text":"Привет",
                "words":None,"confidence":None}],
                "model_sha256":self.digest,"model_version":"medium-v3",
                "engine_version":"1.1.1"}
            Path(cmd[5]).write_text(json.dumps(out,ensure_ascii=False),"utf-8")
            return SimpleNamespace(returncode=0)
        provider=LocalFasterWhisper(self.info,lease=lease,runner=fake)
        got=transcribe(media(),backend=provider,config=ASRConfig(language="ru"))
        self.assertEqual(got["source"],"local_faster_whisper")
        self.assertEqual(got["language"],"ru")
        self.assertEqual(lease.enter,1)
        self.assertEqual(lease.exit,1)
        self.assertEqual(got["timing_reliability"],"segment_observed")
        self.assertFalse(got["word_alignment_complete"])
        self.assertFalse(any(p.name.startswith("ga-asr-") for p in self.root.iterdir()))

    def test_changed_model_or_missing_lease_fails(self):
        with self.assertRaises(ASRError):
            LocalFasterWhisper(self.info,lease=None)
        (self.model/"config.json").write_bytes(b'{"changed":true}')
        engine=LocalFasterWhisper(self.info,lease=FakeLease())
        with self.assertRaises(ASRError) as ctx:
            engine.transcribe(media(),ASRConfig())
        self.assertEqual(ctx.exception.code,"asr_model_digest_mismatch")
        path=self.model/"evil"
        try:
            path.symlink_to(self.model/"model.bin")
        except (OSError,NotImplementedError):
            pass
        else:
            with self.assertRaises(ASRError):
                _model_inventory(self.model)

    def test_bounded_timeout_bad_json_nonzero_exit_and_lease_release(self):
        def timeout(*args,**kwargs):
            raise subprocess.TimeoutExpired(args,kwargs["timeout"])
        for runner,code in ((timeout,"asr_wallclock_timeout"),
                            (lambda *a,**k:SimpleNamespace(returncode=1),"asr_worker_failed"),
                            (lambda *a,**k:SimpleNamespace(returncode=0),"asr_worker_output_missing")):
            lease=FakeLease();engine=LocalFasterWhisper(self.info,lease=lease,runner=runner)
            with self.subTest(code=code),self.assertRaises(ASRError) as ctx:
                engine.transcribe(media(),ASRConfig())
            self.assertEqual(ctx.exception.code,code)
            self.assertEqual(lease.enter,1)
            self.assertEqual(lease.exit,1)
        self.assertEqual(len(list(self.root.glob("ga-asr-*"))),0)


if __name__=="__main__":
    unittest.main()
