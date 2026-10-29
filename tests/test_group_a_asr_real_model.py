"""GA10 optional REAL ru/en speech accuracy gate, not mocked ASR.

Requires a separately installed local faster-whisper and exact model SHA,
eSpeak and FFmpeg. CI without weights skips this test: it is NOT a passed
accuracy benchmark. Golden phrases use TTS, not a representative corpus.
"""
from __future__ import annotations
from array import array
from contextlib import contextmanager
from pathlib import Path
import os
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from intelligence.group_a.extractors.audio_decode import AudioProbe,DecodedAudio
from intelligence.group_a.extractors.asr import ASRConfig,transcribe
from intelligence.group_a.extractors.local_asr import (
    LocalFasterWhisper,LocalASRProvenance,
)


def _words(text):
    return re.findall(r"[^\W_]+",text.casefold(),flags=re.UNICODE)


def _wer(reference,hypothesis):
    ref=_words(reference);out=_words(hypothesis)
    prev=list(range(len(out)+1))
    for i,a in enumerate(ref,1):
        current=[i]+[0]*len(out)
        for j,b in enumerate(out,1):
            current[j]=min(prev[j]+1,current[j-1]+1,
                           prev[j-1]+int(a!=b))
        prev=current
    return prev[-1]/max(len(ref),1)


class ResourceLease:
    @contextmanager
    def acquire(self,*,kind,device):
        if kind!="asr" or device!="cpu":
            raise ValueError("local ASR only")
        yield


def _env_ready():
    return bool(all(os.environ.get(name) for name in (
        "CM_TEST_ASR_MODEL_DIR","CM_TEST_ASR_MODEL_SHA",
        "CM_TEST_ASR_MODEL_VERSION","CM_TEST_ASR_ENGINE_VERSION"
    )) and shutil.which("espeak") and shutil.which("ffmpeg"))


@unittest.skipUnless(_env_ready(),"real ASR model/FFmpeg/espeak not supplied; accuracy NOT VERIFIED")
class TestGA10RealCorpus(unittest.TestCase):
    def test_english_and_russian_generated_speech(self):
        info=LocalASRProvenance(
            os.environ["CM_TEST_ASR_MODEL_DIR"],
            os.environ["CM_TEST_ASR_MODEL_SHA"],
            os.environ["CM_TEST_ASR_MODEL_VERSION"],
            os.environ["CM_TEST_ASR_ENGINE_VERSION"],
        )
        backend=LocalFasterWhisper(info,lease=ResourceLease())
        examples=[
            ("en","Hello how are you today"),
            ("ru","Привет как дела сегодня"),
        ]
        with TemporaryDirectory(prefix="ga-asr-corpus-") as folder:
            for lang,utterance in examples:
                with self.subTest(language=lang):
                    wav=Path(folder)/(lang+".wav")
                    subprocess.run(["espeak","-v",lang,"-s","130","-w",str(wav),
                                    utterance],check=True,stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,timeout=25)
                    pcm=subprocess.run([
                        "ffmpeg","-nostdin","-hide_banner","-v","error",
                        "-i",str(wav),"-vn","-sn","-dn","-ac","1","-ar","16000",
                        "-f","s16le","-acodec","pcm_s16le","pipe:1",
                    ],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                       check=True,timeout=30).stdout
                    audio=DecodedAudio(AudioProbe(
                        "present",0,"pcm_s16le",1,16000,"0.000",
                        "1/16000",None),pcm,"b"*64)
                    observed=transcribe(audio,config=ASRConfig(
                        language=lang,source_policy="local_only",
                        timeout_sec=240,word_timestamps=True
                    ),backend=backend)
                    self.assertEqual(observed["speech_status"],"speech_detected")
                    self.assertEqual(observed["language"],lang)
                    hypothesis=" ".join(s["text"] for s in observed["segments"])
                    score=_wer(utterance,hypothesis)
                    self.assertLessEqual(score,0.80,
                        f"Real {lang} TTS WER too high: {score:.3f}")
                    self.assertTrue(all(s["end_sec"]>s["start_sec"]
                                        for s in observed["segments"]))


if __name__=="__main__":
    unittest.main()
