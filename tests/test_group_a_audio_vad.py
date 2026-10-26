"""GA-MOD-09: fake injection, local audio truth + pinned VAD candidate tests."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from intelligence.group_a.contracts_v1 import canonical_sha256
from intelligence.group_a.media_manifest import verify_file
from intelligence.group_a.extractors.audio_decode import (
    AUDIO_SCHEMA, SAMPLE_RATE, AudioProbe, AudioDecodeError, DecodedAudio,
    decode_audio, parse_audio_probe, pcm_int16,
)
from intelligence.group_a.extractors.vad import (
    VAD_SCHEMA, VADConfig, ModelProvenance, VADError, LocalSileroJIT,
    observe_vad, detect_intervals, _union_spans,
)

MP4 = bytes.fromhex("000000186674797069736f6d")+bytes(128)
SHA_MODEL = "a"*64


def fixture_root(folder):
    path=Path(folder)/"synthetic.mp4"
    path.write_bytes(MP4)
    verified=verify_file(str(path),media_kind="video",
                         sha256=sha256(MP4).hexdigest(),roots=[str(folder)])
    return verified


def probe(*, audio=True, tracks=1, codec="aac", channels=2, rate="44100"):
    streams=[{"index":0,"codec_type":"video","codec_name":"mpeg4"}]
    if audio:
        streams.extend({
            "index":i+1,"codec_type":"audio","codec_name":codec,
            "channels":channels,"sample_rate":rate,"time_base":"1/44100",
            "start_time":"0.250","duration":"2.000",
        } for i in range(tracks))
    return json.dumps({"streams":streams,"format":{"duration":"2.000"}}).encode("utf-8")


def fake_runner(probed,pcm):
    logs=[]
    def run(cmd,**kw):
        logs.append((cmd,kw))
        if "-show_entries" in cmd:
            return SimpleNamespace(returncode=0,stdout=probed,stderr=b"")
        return SimpleNamespace(returncode=0,stdout=pcm,stderr=b"")
    return run,logs


def pcm(value,count=16000):
    from array import array
    import sys
    v=array("h",[value])*count
    if sys.byteorder != "little":
        v.byteswap()
    return v.tobytes()


def audio(data,*,present=True):
    return DecodedAudio(
        AudioProbe("present",1,"pcm_s16le",1,16000,
                   "0.000","1/16000","1.000") if present else
        AudioProbe("absent",None,None,None,None,None,None,None),
        data,sha256(MP4).hexdigest(),
    )


class PredictModel:
    provenance=ModelProvenance("silero_vad","silero-vad/jit-fixture-v1",SHA_MODEL,"cpu")
    def __init__(self,values):
        self.values=values
        self.calls=0
    def probabilities(self,pcm_bytes):
        self.calls+=1
        return self.values


class AudioDecodeTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file=fixture_root(self.temp.name)

    def test_no_audio_stream_is_proven_absent_without_guessing_speech(self):
        runner,calls=fake_runner(probe(audio=False),b"")
        observed=decode_audio(self.file,roots=[self.temp.name],runner=runner,
                              ffprobe=sys.executable,ffmpeg=sys.executable)
        self.assertEqual(observed.probe.state,"absent")
        result=observed.wire()
        self.assertEqual(result["audio_status"],"absent")
        self.assertTrue(result["verified_audio_stream_absence"])
        self.assertEqual(result["speech_status"],"not_run")
        self.assertIsNone(result["digital_silence"])
        self.assertIsNone(result["pcm_sha256"])
        self.assertEqual(observed.sample_count,0)
        self.assertEqual(len(calls),1)
        self.assertEqual(result["observation_sha256"],
            canonical_sha256({k:v for k,v in result.items()
                              if k!="observation_sha256"}))

    def test_exact_decoded_digital_silence_not_absent_audio(self):
        runner,calls=fake_runner(probe(),pcm(0))
        decoded=decode_audio(self.file,roots=[self.temp.name],runner=runner,
                             ffprobe=sys.executable,ffmpeg=sys.executable)
        wire=decoded.wire()
        self.assertEqual(wire["audio_status"],"silent")
        self.assertEqual(wire["digital_silence"],True)
        self.assertEqual(wire["output_sample_rate"],16000)
        self.assertEqual(wire["source_sample_rate"],44100)
        self.assertEqual(wire["source_start_sec"],"0.250")
        self.assertFalse(wire["verified_audio_stream_absence"])
        self.assertEqual(wire["decoded_duration_ms"],1000)
        command=calls[1][0]
        self.assertIn("-ac",command)
        self.assertEqual(command[command.index("-ac")+1],"1")
        self.assertEqual(command[command.index("-ar")+1],"16000")
        self.assertEqual(command[command.index("-map")+1],"0:1")
        self.assertEqual(command[command.index("-protocol_whitelist")+1],"file,pipe")
        self.assertNotIn("http",str(calls))
        self.assertNotIn("spoken_words",wire)
        self.assertNotIn("audio_pcm",str(wire))

    def test_present_nonzero_pcm_and_channel_conversion(self):
        runner,_=fake_runner(probe(rate="48000",channels=6),pcm(12000))
        decoded=decode_audio(self.file,roots=[self.temp.name],runner=runner,
                             ffprobe=sys.executable,ffmpeg=sys.executable)
        self.assertEqual(decoded.probe.source_channels,6)
        self.assertEqual(decoded.probe.source_sample_rate,48000)
        self.assertEqual(decoded.wire()["audio_status"],"decoded")
        self.assertFalse(decoded.wire()["digital_silence"])
        self.assertEqual(len(pcm_int16(decoded.pcm)),16000)

    def test_multitrack_is_never_guessed(self):
        with self.assertRaises(AudioDecodeError) as error:
            parse_audio_probe(probe(tracks=2).decode())
        self.assertEqual(error.exception.code,"multiple_audio_streams")
        p=parse_audio_probe(probe(tracks=2).decode(),chosen_audio_stream_index=2)
        self.assertEqual(p.stream_index,2)
        with self.assertRaises(AudioDecodeError):
            parse_audio_probe(probe(tracks=2).decode(),
                              chosen_audio_stream_index=19)
        runner,calls=fake_runner(probe(tracks=2),pcm(100))
        decoded=decode_audio(self.file,roots=[self.temp.name],runner=runner,
            ffprobe=sys.executable,ffmpeg=sys.executable,audio_stream_index=2)
        self.assertEqual(decoded.probe.stream_index,2)
        self.assertEqual(calls[1][0][calls[1][0].index("-map")+1],"0:2")

    def test_malformed_missing_corrupt_codec_duration_and_timeout(self):
        for raw in ("nope","{}","[]",probe(codec="attack").decode(),
                    probe(rate="0").decode(),probe(channels=0).decode()):
            with self.subTest(raw=raw[:30]),self.assertRaises(AudioDecodeError):
                parse_audio_probe(raw)
        with self.assertRaises(AudioDecodeError) as ctx:
            parse_audio_probe(json.dumps({"streams":[{"codec_type":"audio","index":0}]}))
        self.assertEqual(ctx.exception.code,"not_a_video_container")
        runner,_=fake_runner(probe(),b"")
        with self.assertRaises(AudioDecodeError) as ctx:
            decode_audio(self.file,roots=[self.temp.name],runner=runner,
                         ffprobe=sys.executable,ffmpeg=sys.executable)
        self.assertEqual(ctx.exception.code,"audio_pcm_unobservable")
        def broken(cmd,**kw):
            if "-show_entries" in cmd:
                return SimpleNamespace(returncode=0,stdout=probe(),stderr=b"")
            return SimpleNamespace(returncode=1,stdout=b"",
                                   stderr=b"/secret/path/corrupt")
        with self.assertRaises(AudioDecodeError) as ctx:
            decode_audio(self.file,roots=[self.temp.name],runner=broken,
                         ffprobe=sys.executable,ffmpeg=sys.executable)
        self.assertEqual(ctx.exception.code,"audio_tool_failed")
        self.assertNotIn("/secret",str(ctx.exception))
        def timeouts(cmd,**kw):
            raise subprocess.TimeoutExpired(cmd,kw["timeout"])
        with self.assertRaises(AudioDecodeError) as ctx:
            decode_audio(self.file,roots=[self.temp.name],runner=timeouts,
                         ffprobe=sys.executable,ffmpeg=sys.executable)
        self.assertEqual(ctx.exception.code,"audio_tool_unavailable")

    def test_unverified_path_and_media_mutation_fail_closed(self):
        runner,_=fake_runner(probe(),pcm(1000))
        bad=Path(self.file.path)
        bad.write_bytes(MP4+b"changed")
        with self.assertRaises(Exception):
            decode_audio(self.file,roots=[self.temp.name],runner=runner,
                         ffprobe=sys.executable,ffmpeg=sys.executable)


class VADTests(unittest.TestCase):
    def test_hysteresis_overlap_and_union(self):
        cfg=VADConfig(min_speech_ms=120,min_silence_ms=96,
                      pad_ms=80,merge_gap_ms=140)
        values=[0.1]*5+[0.9]*9+[0.1]*4+[0.8]*10+[0.1]*7
        interval=detect_intervals(values,total_samples=len(values)*512,
                                   config=cfg)
        self.assertGreaterEqual(len(interval),1)
        self.assertTrue(all(0<=a<b<=len(values)*32 for a,b in interval))
        self.assertEqual(_union_spans([(120,240),(240,320),(300,400)],
                    length_ms=500),[(120,400)])
        self.assertEqual(_union_spans([(0,100),(150,200)],
                    length_ms=500,merge_gap_ms=50),[(0,200)])

    def test_silence_absence_missing_model_are_distinct(self):
        absent=audio(b"",present=False)
        no_track=observe_vad(absent)
        self.assertEqual(no_track["state"],"no_audio_stream")
        self.assertTrue(no_track["verified_no_speech"])
        self.assertEqual(no_track["status"],"unavailable")
        silence=observe_vad(audio(pcm(0)))
        self.assertEqual(silence["state"],"digital_silence")
        self.assertEqual(silence["status"],"complete")
        self.assertTrue(silence["verified_no_speech"])
        unavailable=observe_vad(audio(pcm(300)))
        self.assertEqual(unavailable["state"],"model_unavailable")
        self.assertEqual(unavailable["status"],"unavailable")
        self.assertFalse(unavailable["verified_no_speech"])

    def test_nonempty_silero_candidate_and_no_candidate_never_asr(self):
        p=[0.1]*4+[0.85]*9+[0.1]*19
        model=PredictModel(p)
        result=observe_vad(audio(pcm(200)),model=model,
            config=VADConfig(min_silence_ms=96))
        self.assertEqual(result["schema_version"],VAD_SCHEMA)
        self.assertEqual(result["status"],"partial")
        self.assertEqual(result["state"],"speech_candidates")
        self.assertEqual(result["speech_status"],"not_run")
        self.assertEqual(model.calls,1)
        self.assertGreater(result["active_speech_ms"],0)
        self.assertFalse(result["verified_no_speech"])
        self.assertEqual(result["processed_windows"],32)
        silent_candidate=observe_vad(audio(pcm(200)),
            model=PredictModel([0.01]*32))
        self.assertEqual(silent_candidate["state"],"no_candidates")
        self.assertFalse(silent_candidate["verified_no_speech"])
        self.assertEqual(silent_candidate["status"],"partial")
        self.assertIsNone(silent_candidate["model"].get("language"))
        self.assertEqual(silent_candidate["observation_sha256"],
            canonical_sha256({k:v for k,v in silent_candidate.items()
                             if k!="observation_sha256"}))

    def test_partial_windows_and_repeated_runs_hash_stable(self):
        source=audio(pcm(250,512*3+1))
        probabilities=[0.98]*4
        cfg=VADConfig(min_speech_ms=32)
        first=observe_vad(source,model=PredictModel(probabilities),config=cfg)
        second=observe_vad(source,model=PredictModel(probabilities),config=cfg)
        self.assertEqual(first["observation_sha256"],second["observation_sha256"])
        self.assertEqual(first["source_duration_ms"],96)
        self.assertLessEqual(first["active_speech_ms"],96)
        with self.assertRaises(VADError):
            observe_vad(source,model=PredictModel([0.9]*3),config=cfg)

    def test_invalid_model_probabilities_thresholds_gpu_and_versions(self):
        data=audio(pcm(400))
        for value in (-0.1,1.1,float("nan"),True):
            with self.subTest(value=value):
                with self.assertRaises(VADError):
                    observe_vad(data,model=PredictModel([value]*32))
        with self.assertRaises(VADError):
            VADConfig(speech_threshold="NaN")
        with self.assertRaises(VADError):
            VADConfig(release_threshold="0.9",speech_threshold="0.5")
        with self.assertRaises(VADError):
            observe_vad(data,model=PredictModel([0.7]*32),
                        config=VADConfig(device="cuda"))
        with self.assertRaises(VADError):
            ModelProvenance("untrusted","fake","x"*64,"cpu")
        with self.assertRaises(VADError):
            LocalSileroJIT("relative.jit",expected_sha256=SHA_MODEL,
                           version="silero-vad/v5")

    def test_music_like_or_multilingual_audio_cannot_guess_words(self):
        # An injected model sees only normalized PCM; its output does not
        # depend on a declared language or a music/speech narrative label.
        model=PredictModel([0.95]*32)
        a=observe_vad(audio(pcm(1000)),model=model,config=VADConfig())
        self.assertTrue(a["intervals"])
        self.assertNotIn("language",a)
        self.assertNotIn("spoken_words",a)
        self.assertNotIn("speech_wpm",a)
        self.assertEqual(a["speech_status"],"not_run")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg/FFprobe required for local media tests")
class RealAudioFFmpeg(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def make_video(self,kind):
        path=self.root/(kind+".mkv")
        cmd=[shutil.which("ffmpeg"),"-nostdin","-hide_banner",
             "-loglevel","error","-y",
             "-f","lavfi","-i","color=c=black:s=128x96:r=10:d=1"]
        if kind!="no_track":
            source=("anullsrc=r=44100:cl=stereo:d=1" if kind=="silence"
                    else "sine=frequency=440:sample_rate=48000:duration=1")
            cmd+=["-f","lavfi","-i",source]
        cmd+=["-c:v","mpeg4"]
        if kind!="no_track":
            cmd+=["-c:a","pcm_s16le"]
        cmd+=[str(path)]
        subprocess.run(cmd,check=True,capture_output=True,timeout=30)
        body=path.read_bytes()
        return verify_file(str(path),media_kind="video",
                           sha256=sha256(body).hexdigest(),roots=[self.temp.name])

    def test_absent_audio_video_vs_silence_audio_video(self):
        no_track=self.make_video("no_track")
        absent=decode_audio(no_track,roots=[self.temp.name])
        self.assertEqual(absent.wire()["audio_status"],"absent")
        silent=self.make_video("silence")
        got=decode_audio(silent,roots=[self.temp.name])
        self.assertEqual(got.probe.state,"present")
        self.assertEqual(got.wire()["audio_status"],"silent")
        self.assertEqual(got.wire()["speech_status"],"not_run")
        self.assertTrue(observe_vad(got)["verified_no_speech"])

    def test_tone_audio_present_without_asr_or_music_claims(self):
        media=self.make_video("tone")
        decoded=decode_audio(media,roots=[self.temp.name])
        self.assertEqual(decoded.wire()["audio_status"],"decoded")
        self.assertGreater(decoded.sample_count,1000)
        self.assertGreater(decoded.duration_ms,0)
        self.assertIsNone(decoded.wire().get("spoken_words"))
        self.assertEqual(observe_vad(decoded)["state"],"model_unavailable")


if __name__=="__main__":
    unittest.main()
