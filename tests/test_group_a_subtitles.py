"""GA-MOD-14 subtitle tri-state, calibrated visual and FFprobe QA."""
from __future__ import annotations

from array import array
from contextlib import contextmanager
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from copy import deepcopy
import json
import shutil
import subprocess
import sys
import unittest

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.extractors.asr import ASRConfig,Sidecar,transcribe,_build
from intelligence.group_a.extractors.audio_decode import AudioProbe,DecodedAudio
from intelligence.group_a.extractors.speech_metrics import measure_speech
from intelligence.group_a.extractors.technical import ProbeInfo
from intelligence.group_a.extractors.subtitle_probe import (
    parse_subtitle_probe,probe_subtitle_tracks,
)
from intelligence.group_a.extractors.subtitles import (
    SubtitleError,SubtitleConfig,SubtitleCalibration,DenseNegativeReview,
    detect_subtitles,
)
from intelligence.group_a.extractors.text_tracking import track_screen_text
from intelligence.group_a.extractors.subtitle_calibration import (
    GoldSubtitleCase,evaluate_heldout_subtitle_rule,
)
from intelligence.group_a.media_manifest import verify_file

from test_group_a_text_tracking import framespec,wrd,fixture

try:
    import jsonschema
except ImportError:
    jsonschema=None

SHA="b"*64
YES=lambda _:True


def audio(absent=False):
    if absent:
        return DecodedAudio(AudioProbe("absent",None,None,None,None,None,None,None),
                            b"",SHA)
    pcm=array("h",[500])*16000
    if sys.byteorder!="little":pcm.byteswap()
    return DecodedAudio(AudioProbe("present",1,"pcm_s16le",1,16000,
                                   "0.000","1/16000","1.000"),pcm.tobytes(),SHA)


def subtitle_asr(*,srt=False,kind=None,lang="en"):
    sample=audio()
    if srt:
        content=b"1\n00:00:00,100 --> 00:00:00,400\nHello world\n\n2\n00:00:00,550 --> 00:00:00,900\nHow are you"
        source=Sidecar("srt",SHA,content,sha256(content).hexdigest())
        return transcribe(sample,sidecar=source,video_duration_sec="10.000")
    rows=[
        {"start_sec":"0.100","end_sec":"0.350","text":"Hello world",
         "language":lang,"confidence":None,"words":None,
         "word_timing_status":"unknown"},
        {"start_sec":"0.550","end_sec":"0.900","text":"How are you",
         "language":lang,"confidence":None,"words":None,
         "word_timing_status":"unknown"},
    ]
    return _build(sample,ASRConfig(),source=kind or "local_faster_whisper",
                  status="partial",speech_status="speech_detected",
                  language=lang,lang_source="model_detected",
                  timing_origin="audio_stream_local_zero",
                  timing="segment_observed",segments=rows,
                  untimed_text=None,model={"engine":"faster-whisper",
                  "engine_version":"1.1","model_version":"v3",
                  "sha256":"a"*64},upstream=None,reason="fixture")


def speech_metrics(asr):
    probe=ProbeInfo(Decimal("10.000"),"9:16",0,"mpeg4","1/1000",
                    Decimal("0.000"),"stream")
    return measure_speech(audio(),asr,video_probe=probe)


def subtitle_streams(*,number=0,count=1,codec="subrip"):
    rows=[]
    if number:
        rows=[{"index":2+i,"codec_type":"subtitle",
               "codec_name":codec,"nb_read_packets":str(count),
               "tags":{"language":"eng"}} for i in range(number)]
    return parse_subtitle_probe(json.dumps({"streams":rows}).encode(),SHA)


def tracks(*,lower=True,noise=False,lang="en"):
    texts=("Hello world","How are you")
    ys=(130,130) if lower else (15,15)
    rows=[framespec("0.200",1,[wrd(texts[0],y=ys[0],width=210)]),
          framespec("0.700",2,[wrd(texts[1],y=ys[1],width=210)])]
    if noise:
        rows.insert(1,framespec("0.450",3,[wrd("BUY NOW",y=110,width=170)]))
        rows[-1]["sample_index"]=4
        rows[-1]["frame_ref"]="ga-frame:f004-"+f"{4:064x}"
        rows[-1]["frame_sha256"]=f"{4:064x}"
    ocr,manifest=fixture(rows)
    return track_screen_text(ocr,manifest,verify_current=YES)


def run(**kw):
    return detect_subtitles(source_media_sha256=SHA,verify_current=YES,**kw)


class GA14Truth(unittest.TestCase):
    def test_embedded_packets_prove_true_even_without_asr_or_ocr(self):
        p=subtitle_streams(number=1,count=2)
        got=run(embedded_probe=p)
        self.assertEqual(got["has_subtitles"],"true")
        self.assertEqual(got["proof_kind"],"embedded_subtitle_packets")
        self.assertEqual(got["embedded_packet_track_count"],1)
        self.assertEqual(got["a003_has_subtitles_projection"],"true")
        self.assertFalse(got["screen_text_intervals_created"])
        self.assertFalse(got["subtitle_style_classified"])
        self.assertEqual(got["observation_sha256"],canonical_sha256({
            k:v for k,v in got.items() if k!="observation_sha256"}))

    def test_empty_track_and_no_track_not_false(self):
        for number,count in ((1,0),(1,None),(0,1)):
            raw=json.dumps({"streams":[{"index":2,"codec_type":"subtitle",
                        "codec_name":"mov_text",
                        "nb_read_packets":str(count) if count is not None else "N/A"}]
                         if number else []}).encode()
            got=run(embedded_probe=parse_subtitle_probe(raw,SHA))
            self.assertEqual(got["has_subtitles"],"unknown")
            self.assertIsNone(got["caption_source"])
            self.assertEqual(got["a003_has_subtitles_projection"],"unknown")

    def test_verified_timed_subtitles_prove_true_even_if_audio_absent(self):
        captions=subtitle_asr(srt=True)
        result=run(transcript=captions,embedded_probe=subtitle_streams())
        self.assertEqual(result["has_subtitles"],"true")
        self.assertEqual(result["caption_source"],"sidecar_srt")
        self.assertEqual(result["proof_kind"],"verified_timed_captions")
        no_audio=audio(absent=True)
        content=b"1\n00:00:00,100 --> 00:00:00,400\nVisible captions"
        side=Sidecar("srt",SHA,content,sha256(content).hexdigest())
        independent=transcribe(no_audio,sidecar=side,video_duration_sec="10.000")
        got=run(transcript=independent)
        self.assertEqual(got["has_subtitles"],"true")
        self.assertEqual(independent["speech_status"],"no_audio_stream")

    def test_txt_and_asr_speech_alone_not_proof(self):
        content=b"HELLO WORLD"
        side=Sidecar("txt",SHA,content,sha256(content).hexdigest())
        txt=transcribe(audio(),sidecar=side,video_duration_sec="10.000")
        got=run(transcript=txt)
        self.assertEqual(got["has_subtitles"],"unknown")
        self.assertIn("untimed_text_is_not_subtitle_evidence",got["reason_codes"])
        got=run(transcript=subtitle_asr())
        self.assertEqual(got["has_subtitles"],"unknown")

    def test_title_only_top_region_and_sparse_blank_stay_unknown(self):
        asr=subtitle_asr()
        title=tracks(lower=False)
        out=run(tracking=title,transcript=asr,
                speech_metrics=speech_metrics(asr))
        self.assertEqual(out["has_subtitles"],"unknown")
        self.assertEqual(out["visual"]["state"],"insufficient_correlated_evidence")
        self.assertEqual(out["visual"]["candidate_count"],0)
        # one OCR sample is never enough to establish a subtitle sequence
        rows=[framespec("0.200",1,[wrd("Hello world",y=130,width=210)])]
        ocr,man=fixture(rows)
        single=track_screen_text(ocr,man,verify_current=YES)
        out=run(tracking=single,transcript=asr,speech_metrics=speech_metrics(asr))
        self.assertEqual(out["has_subtitles"],"unknown")

    def test_burned_in_multiframe_ocr_plus_asr_is_candidate_not_true_without_gold(self):
        asr=subtitle_asr()
        visual=tracks()
        result=run(embedded_probe=subtitle_streams(),tracking=visual,
                   transcript=asr,speech_metrics=speech_metrics(asr))
        self.assertEqual(result["has_subtitles"],"unknown")
        self.assertEqual(result["visual"]["state"],"corroborated_candidate")
        self.assertEqual(result["visual"]["distinct_frames"],2)
        self.assertEqual(result["visual"]["distinct_asr_segments"],2)
        self.assertEqual(result["visual"]["visual_match_ratio"],"1.000")
        self.assertIn("ocr_asr_correlated_but_visual_precision_not_calibrated",
                      result["reason_codes"])
        self.assertEqual(result["a003_has_subtitles_projection"],"unknown")

    def test_visual_positive_needs_authoritative_heldout_calibration(self):
        asr=subtitle_asr()
        v=tracks()
        valid=SubtitleCalibration(
            "heldout-rus-eng-v1","d"*64,"group-a-subtitle-ocr-asr-rule-v1",
            ("en","ru"),"97.5",150,"0.850")
        kw=dict(transcript=asr,tracking=v,speech_metrics=speech_metrics(asr),
                calibration=valid)
        with self.assertRaises(SubtitleError) as ctx:
            run(**kw)
        self.assertEqual(ctx.exception.code,"subtitle_calibration_unverified")
        with self.assertRaises(SubtitleError):
            run(**kw,verify_calibration=lambda _:"true")
        got=run(**kw,verify_calibration=YES)
        self.assertEqual(got["has_subtitles"],"true")
        self.assertEqual(got["proof_kind"],"calibrated_ocr_asr")
        calibration=SubtitleCalibration("heldout-rus-eng-v2","d"*64,
           "group-a-subtitle-ocr-asr-rule-v1",("ru",),"97.5",150,"0.850")
        got=run(transcript=asr,tracking=v,speech_metrics=speech_metrics(asr),
                calibration=calibration,verify_calibration=YES)
        self.assertEqual(got["has_subtitles"],"unknown")

    def test_overlay_and_word_highlighting_do_not_autopromote(self):
        asr=subtitle_asr()
        rows=[
            framespec("0.200",1,[wrd("Hello",y=130,width=110)]),
            framespec("0.700",2,[wrd("How",y=130,width=100)]),
        ]
        ocr,man=fixture(rows)
        visual=track_screen_text(ocr,man,verify_current=YES)
        out=run(tracking=visual,transcript=asr,speech_metrics=speech_metrics(asr))
        self.assertEqual(out["visual"]["state"],"corroborated_candidate")
        self.assertEqual(out["has_subtitles"],"unknown")

    def test_missing_audio_or_unproven_video_clock_stays_unknown_for_visual(self):
        visual=tracks()
        got=run(tracking=visual)
        self.assertEqual(got["has_subtitles"],"unknown")
        self.assertEqual(got["visual"]["state"],"ocr_only_not_audible_speech_proof")
        got=run(tracking=visual,transcript=subtitle_asr())
        self.assertEqual(got["visual"]["state"],"asr_video_clock_unverified")
        self.assertEqual(got["has_subtitles"],"unknown")

    def test_false_requires_full_video_dense_review_and_checked_empty_embedded(self):
        empty=subtitle_streams()
        att=DenseNegativeReview(SHA,"f"*64,"0.000","10.000",300,
                                True,True,True,True)
        with self.assertRaises(SubtitleError):
            run(dense_review=att,embedded_probe=empty,video_duration_sec="10.000")
        actual=run(dense_review=att,verify_dense_review=YES,
                   embedded_probe=empty,video_duration_sec="10.000")
        self.assertEqual(actual["has_subtitles"],"false")
        self.assertEqual(actual["proof_kind"],"certified_dense_negative_review")
        self.assertEqual(actual["a003_has_subtitles_projection"],"false")
        self.assertEqual(actual["reason_codes"],[])
        for duration in ("1.000","9.000","11.000"):
            with self.assertRaises(SubtitleError):
                run(dense_review=att,verify_dense_review=YES,
                    embedded_probe=empty,video_duration_sec=duration)
        tiny=DenseNegativeReview(SHA,"f"*64,"0.000","10.000",2,
                                 True,True,True,True)
        with self.assertRaises(SubtitleError):
            run(dense_review=tiny,verify_dense_review=YES,
                embedded_probe=empty,video_duration_sec="10.000")

    def test_no_fake_false_on_unverified_sparse_ocr_or_missing_track_probe(self):
        att=DenseNegativeReview(SHA,"f"*64,"0.000","10.000",300,
                                True,True,True,True)
        with self.assertRaises(SubtitleError):
            run(dense_review=att,verify_dense_review=YES,video_duration_sec="10.000")
        with self.assertRaises(SubtitleError):
            run(dense_review=att,verify_dense_review=YES,
                embedded_probe=subtitle_streams(number=1,count=0),
                video_duration_sec="10.000")

    def test_negative_vs_positive_conflict_rejected(self):
        att=DenseNegativeReview(SHA,"f"*64,"0.000","10.000",300,
                                True,True,True,True)
        with self.assertRaises(SubtitleError):
            run(dense_review=att,verify_dense_review=YES,
                embedded_probe=subtitle_streams(number=1,count=2),
                video_duration_sec="10.000")

    def test_tampered_sha_foreign_media_and_revoked_rights_fail(self):
        p=subtitle_streams(number=1,count=2)
        forged=deepcopy(p);forged["tracks"][0]["packet_count"]=0
        with self.assertRaises(SubtitleError) as ctx:
            run(embedded_probe=forged)
        self.assertEqual(ctx.exception.code,"embedded_probe_hash_mismatch")
        forged=deepcopy(p);forged["source_media_sha256"]="c"*64
        forged["observation_sha256"]=canonical_sha256({
            k:v for k,v in forged.items() if k!="observation_sha256"})
        with self.assertRaises(SubtitleError):
            run(embedded_probe=forged)
        transcript=subtitle_asr(srt=True)
        with self.assertRaises(SubtitleError):
            detect_subtitles(source_media_sha256=SHA,
                             verify_current=lambda _:False,transcript=transcript)
        with self.assertRaises(SubtitleError):
            detect_subtitles(source_media_sha256=SHA,
                             verify_current=lambda c:c.get("type")!="transcript",
                             transcript=transcript)

    def test_unrelated_metrics_cannot_be_injected_as_subtitle_provenance(self):
        speech=subtitle_asr()
        evidence=speech_metrics(speech)
        with self.assertRaises(SubtitleError) as ctx:
            run(speech_metrics=evidence,transcript=speech)
        self.assertEqual(ctx.exception.code,
                         "speech_metrics_without_audible_tracking")
        with self.assertRaises(SubtitleError):
            run(speech_metrics=evidence,transcript=subtitle_asr(srt=True),
                tracking=tracks())

    def test_invalid_calibration_and_dense_claims_rejected(self):
        with self.assertRaises(SubtitleError):
            SubtitleCalibration("bad","d"*64,"group-a-subtitle-ocr-asr-rule-v1",
                                ("ru",),"80.0",2,"0.90")
        with self.assertRaises(SubtitleError):
            DenseNegativeReview(SHA,"f"*64,"0.000","0.000",1,
                                True,True,True,True)
        with self.assertRaises(SubtitleError):
            DenseNegativeReview(SHA,"f"*64,"0.000","10.000",300,
                                True,False,True,True)
        with self.assertRaises(SubtitleError):
            SubtitleConfig(min_word_overlap="nan")


class GA14CalibrationGate(unittest.TestCase):
    def cases(self):
        result=[]
        for lang in ("en","ru"):
            result.extend(GoldSubtitleCase(f"gold-{lang}-true-{i:03d}",lang,"true",True,"0.980")
                          for i in range(100))
            result.extend(GoldSubtitleCase(f"gold-{lang}-false-{i:03d}",lang,"false",True,"0.050")
                          for i in range(40))
        return result

    def test_fixed_threshold_precision_requires_authorized_heldout_labels(self):
        cases=self.cases()
        with self.assertRaises(SubtitleError):
            evaluate_heldout_subtitle_rule(cases,decision_threshold="0.850",
                calibration_id="heldout-offline-en-ru-v1",verify_gold_source=None)
        auth=[]
        def authorized(context):
            auth.append(context);return True
        calibration,report=evaluate_heldout_subtitle_rule(cases,
            decision_threshold="0.850",
            calibration_id="heldout-offline-en-ru-v1",verify_gold_source=authorized)
        self.assertEqual(calibration.language_scope,("en","ru"))
        self.assertGreaterEqual(Decimal(calibration.min_precision_pct),Decimal("95"))
        self.assertEqual(report["metrics"]["en"]["false_positive"],0)
        self.assertEqual(report["metrics"]["ru"]["true_positive"],100)
        self.assertEqual(auth[0]["split"],"heldout")
        self.assertEqual(auth[0]["dataset_sha256"],calibration.dataset_sha256)
        c2,report2=evaluate_heldout_subtitle_rule(list(reversed(cases)),
            decision_threshold="0.850",
            calibration_id="heldout-offline-en-ru-v1",verify_gold_source=YES)
        self.assertEqual(c2.dataset_sha256,calibration.dataset_sha256)
        self.assertEqual(report2["observation_sha256"],report["observation_sha256"])

    def test_rejects_false_positives_unbalanced_cohort_or_tuned_claim(self):
        cases=self.cases()
        forged=list(cases)
        for i,item in enumerate(forged):
            if item.language=="ru" and item.human_label=="false":
                forged[i]=GoldSubtitleCase(item.case_id,"ru","false",True,"0.990")
        with self.assertRaises(SubtitleError) as ctx:
            evaluate_heldout_subtitle_rule(forged,decision_threshold="0.850",
                calibration_id="heldout-offline-en-ru-v1",verify_gold_source=YES)
        self.assertEqual(ctx.exception.code,"subtitle_gold_precision_below_gate")
        with self.assertRaises(SubtitleError):
            evaluate_heldout_subtitle_rule(cases[:120],
                decision_threshold="0.850",
                calibration_id="heldout-offline-en-ru-v1",verify_gold_source=YES)
        with self.assertRaises(SubtitleError):
            evaluate_heldout_subtitle_rule(cases+cases[:1],
                decision_threshold="0.850",
                calibration_id="heldout-offline-en-ru-v1",verify_gold_source=YES)
        with self.assertRaises(SubtitleError):
            evaluate_heldout_subtitle_rule(cases,decision_threshold="NaN",
                calibration_id="heldout-offline-en-ru-v1",verify_gold_source=YES)
        with self.assertRaises(SubtitleError):
            GoldSubtitleCase("too-short","fr","true",True,"0.900")


class GA14ProbeUnit(unittest.TestCase):
    def test_parser_counts_real_subtitle_packets_and_unavailable_not_absent(self):
        parsed=subtitle_streams(number=2,count=3)
        self.assertEqual(parsed["tracks"][0]["packet_count"],3)
        self.assertEqual(parsed["status"],"inspected")
        no=subtitle_streams()
        self.assertEqual(no["tracks"],[])
        for body in (b"{bad",b"[]",b'{"streams":[{"codec_type":"audio"}]}'):
            with self.assertRaises(SubtitleError):
                parse_subtitle_probe(body,SHA)
        zero=subtitle_streams(number=1,count=0)
        self.assertEqual(run(embedded_probe=zero)["has_subtitles"],"unknown")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "real FFmpeg/FFprobe required")
class GA14RealSubtitlePackets(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def build(self,with_subtitles):
        path=self.root/("subtitles" if with_subtitles else "plain")
        path=path.with_suffix(".mkv")
        cmd=[shutil.which("ffmpeg"),"-nostdin","-hide_banner","-v","error",
             "-y","-f","lavfi","-i","color=c=black:s=128x96:r=10:d=2"]
        if with_subtitles:
            cue=self.root/"sub.srt"
            cue.write_text("1\n00:00:00,200 --> 00:00:01,300\nHello test\n","utf-8")
            cmd+=["-i",str(cue)]
        cmd+=["-c:v","mpeg4"]
        if with_subtitles:
            cmd+=["-c:s","srt"]
        cmd+=[str(path)]
        subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE,timeout=40)
        digest=sha256(path.read_bytes()).hexdigest()
        return verify_file(str(path),media_kind="video",sha256=digest,
                           roots=[str(self.root)])

    def test_actual_embedded_srt_and_plain_video(self):
        embedded=self.build(True)
        result=probe_subtitle_tracks(embedded,roots=[str(self.root)])
        self.assertEqual(result["status"],"inspected")
        self.assertTrue(result["tracks"])
        self.assertGreater(result["tracks"][0]["packet_count"],0)
        self.assertEqual(result["source_media_sha256"],embedded.sha256)
        self.assertEqual(detect_subtitles(source_media_sha256=embedded.sha256,
           embedded_probe=result,verify_current=YES)["has_subtitles"],"true")
        plain=self.build(False)
        empty=probe_subtitle_tracks(plain,roots=[str(self.root)])
        self.assertEqual(empty["status"],"inspected")
        self.assertEqual(empty["tracks"],[])
        self.assertEqual(detect_subtitles(source_media_sha256=plain.sha256,
           embedded_probe=empty,verify_current=YES)["has_subtitles"],"unknown")


@unittest.skipUnless(jsonschema is not None,"jsonschema is a test dependency")
class GA14SchemaTests(unittest.TestCase):
    def test_true_unknown_and_certified_false_schema(self):
        p=Path(__file__).resolve().parents[1]/"intelligence"/"group_a"/"schemas"/"ga-subtitle-presence-v1.json"
        schema=json.loads(p.read_text("utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        att=DenseNegativeReview(SHA,"f"*64,"0.000","10.000",300,True,True,True,True)
        rows=[
            run(),
            run(embedded_probe=subtitle_streams(number=1,count=1)),
            run(embedded_probe=subtitle_streams(),transcript=subtitle_asr(srt=True)),
            run(embedded_probe=subtitle_streams(),dense_review=att,
                verify_dense_review=YES,video_duration_sec="10.000"),
        ]
        for row in rows:
            jsonschema.validate(row,schema)
        forged=dict(rows[0],has_subtitles="false")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,schema)
        forged=dict(rows[-1],proof_kind="none")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,schema)


if __name__=="__main__":
    unittest.main()
