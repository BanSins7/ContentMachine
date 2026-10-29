"""GA15 deterministic offline music/SFX event and A003 regression tests."""
from __future__ import annotations

from array import array
from contextlib import contextmanager
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import json
import math
import unittest

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.extractors.audio_decode import AudioProbe,DecodedAudio
from intelligence.group_a.extractors.audio_events import (
    AudioEventError,AudioEventConfig,observe_audio_events,_events,
)
from intelligence.group_a.extractors.technical import ProbeInfo
from intelligence.group_a.extractors.onnx_audio_events import (
    LocalONNXAudioClassifier,pinned_labelmap,
)

try:
    import jsonschema
except ImportError:
    jsonschema=None


def audio(*,seconds=2,value=1500,absent=False,start="0.250"):
    if absent:
        return DecodedAudio(AudioProbe("absent",None,None,None,None,None,None,None),
                            b"","b"*64)
    count=seconds*16000
    samples=array("h",[value])*count
    if sys.byteorder!="little":samples.byteswap()
    return DecodedAudio(AudioProbe("present",1,"pcm_s16le",1,16000,start,
                 "1/16000",f"{seconds}.000"),samples.tobytes(),"b"*64)


def video(*,duration="5.000",start="0.000"):
    return ProbeInfo(Decimal(duration),"9:16",0,"mpeg4","1/1000",
          Decimal(start) if start is not None else None,"stream")


class Classifier:
    provenance={"engine":"injected_offline","engine_version":"test-1",
        "model_version":"pinned-test-1","model_sha256":"c"*64,
        "labelmap_sha256":"d"*64,"device":"cpu",
        "license_authorization":"host_verified"}

    def __init__(self,values):
        self.values=values
        self.calls=0
        self.sizes=[]
    def predict(self,window_pcm,*,sample_rate):
        assert sample_rate==16000
        self.sizes.append(len(window_pcm))
        i=self.calls;self.calls+=1
        value=self.values[i % len(self.values)]
        if isinstance(value,Exception):raise value
        return value


def result(src=None,*,classifier=None,config=None,probe=None,media_rights=lambda _:True,
           model_rights=lambda _:True):
    return observe_audio_events(audio() if src is None else src,classifier=classifier,
        config=config,video_probe=probe,verify_current=media_rights,
        verify_classifier=model_rights)


def score(music=0,sfx=0,other=0):
    return {"music":music,"sound_effect":sfx,"other":other}


class GA15Gold(unittest.TestCase):
    def test_music_only_timeline_score_and_a003_projection(self):
        classifier=Classifier([score(.95,.04,.02)])
        row=result(classifier=classifier,probe=video())
        self.assertEqual(row["state"],"classified_partial")
        self.assertEqual(row["music_presence"],"true")
        self.assertEqual(row["sound_effect_presence"],"unknown")
        self.assertEqual(row["other_presence"],"unknown")
        self.assertEqual(row["counts"]["observed"],4)
        self.assertEqual(classifier.sizes,[32000]*4)
        self.assertEqual(len(row["events"]),1)
        event=row["events"][0]
        self.assertEqual(event["event_type"],"music")
        self.assertEqual(event["start_sec"],"0.000")
        self.assertEqual(event["end_sec"],"2.000")
        self.assertEqual(event["source_window_indices"],[0,1,2,3])
        self.assertEqual(event["max_engine_score"],"0.950")
        self.assertEqual(event["evidence_level"],"uncalibrated_local_model_candidate")
        self.assertIsNone(event["confidence_pct"])
        self.assertEqual(row["a003_mapping_state"],"verified_audio_video_timebase")
        self.assertEqual(row["video_clock_offset_sec"],"0.250")
        self.assertEqual(row["a003_audio_events_projection"][0]["start_sec"],"0.250")
        self.assertEqual(row["a003_audio_events_projection"][0]["end_sec"],"2.250")
        self.assertIsNone(row["a003_audio_events_projection"][0]["confidence_pct"])
        self.assertFalse(row["class_probability_calibrated"])
        self.assertFalse(row["full_video_acoustic_coverage"])
        self.assertEqual(row["observation_sha256"],canonical_sha256({
            k:v for k,v in row.items() if k!="observation_sha256"}))

    def test_simultaneous_music_and_sfx_overlap_allowed(self):
        detector=Classifier([score(.91,.96,.01)])
        row=result(classifier=detector,probe=video())
        self.assertEqual(len(row["events"]),2)
        types={x["event_type"] for x in row["events"]}
        self.assertEqual(types,{"music","sound_effect"})
        self.assertEqual(row["music_presence"],"true")
        self.assertEqual(row["sound_effect_presence"],"true")
        self.assertEqual({x["start_sec"] for x in row["events"]},{"0.000"})
        self.assertEqual({x["end_sec"] for x in row["events"]},{"2.000"})
        self.assertEqual(len(row["a003_audio_events_projection"]),2)

    def test_transient_sfx_window_and_background_noise_not_assumed_sfx(self):
        values=[score(.02,.04,.2),score(.02,.99,.1),
                score(.02,.05,.2),score(.02,.06,.1)]
        row=result(classifier=Classifier(values))
        self.assertEqual(row["sound_effect_presence"],"true")
        effect=row["events"][0]
        self.assertEqual(effect["start_sec"],"0.500")
        self.assertEqual(effect["end_sec"],"1.500")
        self.assertEqual(row["a003_audio_events_projection"],[])
        self.assertEqual(row["a003_mapping_state"],
                         "blocked_unverified_video_timebase")
        self.assertEqual(row["music_presence"],"unknown")
        none=result(classifier=Classifier([score(.02,.05,.1)]))
        self.assertEqual(none["events"],[])
        self.assertEqual(none["music_presence"],"unknown")
        self.assertEqual(none["sound_effect_presence"],"unknown")
        self.assertEqual(none["window_coverage"],"complete_selected_pcm")

    def test_other_label_is_separate_and_can_overlap_music(self):
        row=result(classifier=Classifier([score(.91,.01,.97)]))
        self.assertEqual({x["event_type"] for x in row["events"]},
                         {"music","other"})
        self.assertEqual(row["other_presence"],"true")

    def test_verified_no_audio_and_digital_silence_have_no_false_events(self):
        detector=Classifier([score(1,1,1)])
        for src,state in ((audio(absent=True),"no_audio_stream"),
                          (audio(value=0),"digital_silence")):
            got=result(src,classifier=detector,probe=video())
            self.assertEqual(got["state"],state)
            self.assertTrue(got["audio_absence_verified"])
            self.assertEqual(got["music_presence"],"false")
            self.assertEqual(got["sound_effect_presence"],"false")
            self.assertEqual(got["other_presence"],"false")
            self.assertEqual(got["events"],[])
            self.assertEqual(got["windows"],[])
            self.assertEqual(got["counts"]["attempted"],0)
        self.assertEqual(detector.calls,0)

    def test_unknown_and_missing_model_are_not_silence(self):
        output=result(probe=video())
        self.assertEqual(output["state"],"classifier_unavailable")
        self.assertFalse(output["audio_absence_verified"])
        self.assertEqual(output["music_presence"],"unknown")
        self.assertEqual(output["sound_effect_presence"],"unknown")
        self.assertIsNone(output["classifier_model"])
        absent_pcm=DecodedAudio(AudioProbe(
            "present",1,"aac",1,16000,None,"1/16000",None),b"","b"*64)
        got=result(absent_pcm,classifier=Classifier([score(1,1,1)]))
        self.assertEqual(got["state"],"unavailable")
        self.assertEqual(got["music_presence"],"unknown")

    def test_window_failure_and_model_error_cannot_prove_absence(self):
        cls=Classifier([score(.99,.02,.01),RuntimeError("bad input"),
                        score(.01,.02,.01),score(.9,.01,.01)])
        got=result(classifier=cls)
        self.assertEqual(got["state"],"partial_failure")
        self.assertEqual(got["counts"]["failed"],1)
        self.assertEqual(got["music_presence"],"true")
        self.assertEqual(len(got["events"]),2)
        self.assertEqual(got["events"][0]["end_sec"],"1.000")
        self.assertEqual(got["events"][1]["start_sec"],"1.500")
        self.assertEqual(got["window_coverage"],"partial_with_failures")
        for bad in (score(float("nan"),0,0),score(-0.1,0,0),
                    score(1.01,0,0),{"music":0.9}):
            got=result(classifier=Classifier([bad]))
            self.assertEqual(got["counts"]["failed"],4)
            self.assertEqual(got["music_presence"],"unknown")
            self.assertEqual(got["events"],[])

    def test_current_media_and_model_license_authorization_required(self):
        cls=Classifier([score(.9,0,0)])
        for rights in (None,lambda _:False,lambda _:"True"):
            with self.assertRaises(AudioEventError):
                result(classifier=cls,media_rights=rights)
        with self.assertRaises(AudioEventError) as err:
            result(classifier=cls,model_rights=None)
        self.assertEqual(err.exception.code,"audio_event_classifier_host_unverified")
        with self.assertRaises(AudioEventError):
            result(classifier=cls,model_rights=lambda _:False)
        self.assertEqual(cls.calls,0)
        checked=[]
        got=result(classifier=cls,model_rights=lambda x: checked.append(x) or True)
        self.assertEqual(checked[0]["model_sha256"],"c"*64)
        self.assertEqual(checked[0]["labelmap_sha256"],"d"*64)
        self.assertEqual(got["music_presence"],"true")

    def test_model_identity_and_device_rejection(self):
        for p in ({"engine":"injected_offline"},
                  {**Classifier.provenance,"model_sha256":"f"},
                  {**Classifier.provenance,"license_authorization":"unknown"},
                  {**Classifier.provenance,"device":"cuda"}):
            cls=Classifier([score(1,1,1)])
            cls.provenance=p
            with self.assertRaises(AudioEventError):
                result(classifier=cls)
        with self.assertRaises(AudioEventError):
            AudioEventConfig(device="cuda")
        with self.assertRaises(AudioEventError):
            AudioEventConfig(hop_ms=500,window_ms=250)
        with self.assertRaises(AudioEventError):
            AudioEventConfig(music_threshold="NaN")
        with self.assertRaises(AudioEventError):
            AudioEventConfig(min_positive_windows=True)

    def test_no_video_clock_or_untrusted_bounds_do_not_fabricate_projection(self):
        missing=result(classifier=Classifier([score(1,0,0)]),
                       probe=video(start=None))
        self.assertEqual(missing["music_presence"],"true")
        self.assertEqual(missing["a003_audio_events_projection"],[])
        self.assertEqual(missing["a003_mapping_state"],
                         "blocked_unverified_video_timebase")
        with self.assertRaises(AudioEventError) as err:
            result(classifier=Classifier([score(1,0,0)]),
                   probe=video(duration="1.500"))
        self.assertEqual(err.exception.code,"audio_event_outside_video_timebase")

    def test_short_final_window_is_padded_but_time_clipped(self):
        cls=Classifier([score(0,.99,0)])
        src=audio(seconds=1)
        res=result(src,classifier=cls,probe=video())
        self.assertEqual(len(res["windows"]),2)
        self.assertEqual(cls.sizes,[32000,32000])
        self.assertEqual(res["events"][0]["end_sec"],"1.000")
        self.assertEqual(res["a003_audio_events_projection"][0]["end_sec"],"1.250")
        self.assertEqual(res["events"][0]["source_end_sample"],16000)

    def test_degraded_no_model_and_results_are_deterministic(self):
        a=result(classifier=Classifier([score(.95,.99,.2)]),probe=video())
        b=result(classifier=Classifier([score(.95,.99,.2)]),probe=video())
        self.assertEqual(a,b)
        self.assertNotIn("pcm",a)
        self.assertFalse(a["class_probability_calibrated"])
        self.assertNotIn("music_mood",a)

    def test_labelmap_pin_and_disjoint_class_indices(self):
        groups={"music":[20,21],"sound_effect":[8,9],"other":[0,3]}
        parsed,digest=pinned_labelmap(groups)
        self.assertEqual(parsed["music"],(20,21))
        self.assertEqual(digest,canonical_sha256({
            "music":[20,21],"sound_effect":[8,9],"other":[0,3]}))
        with self.assertRaises(AudioEventError):
            pinned_labelmap({"music":[1],"sound_effect":[1],"other":[2]})
        with self.assertRaises(AudioEventError):
            pinned_labelmap({"music":[-1],"sound_effect":[3],"other":[2]})
        with TemporaryDirectory() as folder:
            p=Path(folder)/"model.onnx"
            p.write_bytes(b"not a real model")
            with self.assertRaises(AudioEventError) as error:
                LocalONNXAudioClassifier(model_path=str(p),
                    expected_sha256="0"*64,model_version="test",
                    engine="yamnet_onnx",label_groups=groups,
                    input_layout="samples",lease=object(),
                    expected_labelmap_sha256=digest,verify_license=lambda _:True)
            self.assertEqual(error.exception.code,"audio_event_resource_lease_required")


@unittest.skipUnless(jsonschema is not None,"jsonschema is test-only")
class GA15WireSchema(unittest.TestCase):
    def test_silent_unknown_positive_partial_multi_event(self):
        loc=Path(__file__).resolve().parents[1]/"intelligence"/"group_a"/"schemas"/"ga-audio-events-v1.json"
        schema=json.loads(loc.read_text("utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        examples=[
            result(audio(absent=True)),
            result(audio(value=0)),
            result(),
            result(classifier=Classifier([score(.9,.9,.0)]),probe=video()),
            result(classifier=Classifier([score(.91,.02,0),ValueError("boom")])),
        ]
        for row in examples:
            with self.subTest(state=row["state"]):
                jsonschema.validate(row,schema)
        forged=dict(examples[2],music_presence="false")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,schema)
        forged=dict(examples[3],class_probability_calibrated=True)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(forged,schema)


if __name__=="__main__":
    unittest.main()
