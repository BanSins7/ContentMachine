"""GA-MOD-10 local faster-whisper subprocess provider (no downloads).

Host installs faster-whisper & verified model files, supplies a resource lease
and grants CPU/GPU. A worker subprocess has a bounded wall clock; child
returns only bounded JSON of observed segments/optional word times. PCM stays
in private temporary WAV and is deleted on every failure/timeout.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import wave

from ..contracts_v1 import canonical_sha256
from .asr import ASRConfig, ASRError
from .audio_decode import DecodedAudio

MODEL_FILES = 96
MODEL_MAX_FILE = 6 * 1024**3
MAX_JSON = 8 * 1024 * 1024
MODEL_VERSION = re.compile(r"^[a-zA-Z0-9._-]{1,70}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
SAFE_FILE = re.compile(r"^[a-zA-Z0-9._/-]{1,250}$")


def _model_inventory(path):
    """Hash every model file, including config/tokenizer, not only model.bin."""
    if not isinstance(path,Path) or not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise ASRError("asr_model_path_invalid")
    files={}
    root=path.resolve(strict=True)
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ASRError("asr_model_symlink_forbidden")
        if p.is_dir():
            continue
        if not p.is_file():
            raise ASRError("asr_model_file_invalid")
        relative=p.relative_to(root).as_posix()
        if not SAFE_FILE.fullmatch(relative) or len(files)>=MODEL_FILES:
            raise ASRError("asr_model_directory_invalid")
        if p.stat().st_size<1 or p.stat().st_size>MODEL_MAX_FILE:
            raise ASRError("asr_model_size_invalid")
        digest=sha256()
        with p.open("rb") as stream:
            for blob in iter(lambda:stream.read(1024*1024),b""):
                digest.update(blob)
        files[relative]=digest.hexdigest()
    if "model.bin" not in files or "config.json" not in files:
        raise ASRError("asr_model_files_missing")
    return canonical_sha256(files)


@dataclass(frozen=True,slots=True)
class LocalASRProvenance:
    model_dir: str
    model_sha256: str
    model_version: str
    engine_version: str

    def __post_init__(self):
        if (type(self.model_dir) is not str or not Path(self.model_dir).is_absolute()
            or type(self.model_sha256) is not str or not SHA.fullmatch(self.model_sha256)
            or type(self.model_version) is not str or not MODEL_VERSION.fullmatch(self.model_version)
            or type(self.engine_version) is not str or not MODEL_VERSION.fullmatch(self.engine_version)):
            raise ASRError("invalid_asr_model_provenance")


class LocalFasterWhisper:
    """ASRBackend: injected resource lease is mandatory for a local run.

    Lease implements acquire(kind='asr',device='cpu'|'cuda') -> context manager.
    Caller owns lease accounting and hardware resource budgets. The module
    cannot bypass lease if no usable resource is granted.
    """

    def __init__(self, provenance:LocalASRProvenance,*,lease,runner=subprocess.run):
        if not isinstance(provenance,LocalASRProvenance):
            raise ASRError("invalid_asr_model_provenance")
        if lease is None or not callable(getattr(lease,"acquire",None)):
            raise ASRError("asr_resource_lease_required")
        if not callable(runner):
            raise ASRError("invalid_asr_runner")
        self._info=provenance
        self._lease=lease
        self._runner=runner

    @property
    def provenance(self):
        return {"engine":"faster-whisper","engine_version":self._info.engine_version,
                "model_version":self._info.model_version,"sha256":self._info.model_sha256}

    def transcribe(self,audio:DecodedAudio,config:ASRConfig):
        if not isinstance(audio,DecodedAudio) or not isinstance(config,ASRConfig):
            raise ASRError("invalid_asr_input")
        if audio.probe.state!="present" or not audio.pcm:
            raise ASRError("asr_pcm_unavailable")
        path=Path(self._info.model_dir)
        if _model_inventory(path)!=self._info.model_sha256:
            raise ASRError("asr_model_digest_mismatch")
        if audio.sample_count>600*16000:
            raise ASRError("asr_audio_limit")
        try:
            lease=self._lease.acquire(kind="asr",device=config.device)
        except Exception as exc:
            raise ASRError("asr_resource_unavailable") from exc
        if not hasattr(lease,"__enter__") or not hasattr(lease,"__exit__"):
            raise ASRError("asr_resource_unavailable")
        try:
            with lease:
                with tempfile.TemporaryDirectory(prefix="ga-asr-") as folder:
                    wavpath=Path(folder)/"audio.wav"
                    outfile=Path(folder)/"transcript.json"
                    with wave.open(str(wavpath),"wb") as stream:
                        stream.setnchannels(1)
                        stream.setsampwidth(2)
                        stream.setframerate(16000)
                        stream.writeframes(audio.pcm)
                    cfg={"language":config.language,"device":config.device,
                         "compute_type":config.compute_type,
                         "beam_size":config.beam_size,
                         "word_timestamps":config.word_timestamps}
                    cmd=[sys.executable,"-m","intelligence.market_intelligence._copied.group_a.extractors.asr_worker",
                         self._info.model_dir,str(wavpath),str(outfile),
                         json.dumps(cfg,sort_keys=True,separators=(",",":")),
                         self._info.model_sha256,self._info.model_version]
                    env={**os.environ,
                         "HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1",
                         "HF_DATASETS_OFFLINE":"1","HF_HUB_DISABLE_TELEMETRY":"1",
                         "PYTHONNOUSERSITE":"1"}
                    try:
                        result=self._runner(cmd,stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            timeout=config.timeout_sec,check=False,env=env)
                    except subprocess.TimeoutExpired as exc:
                        raise ASRError("asr_wallclock_timeout") from exc
                    except OSError as exc:
                        raise ASRError("asr_subprocess_unavailable") from exc
                    if type(result.returncode) is not int or result.returncode!=0:
                        raise ASRError("asr_worker_failed")
                    if not outfile.is_file() or outfile.is_symlink() or not 0<outfile.stat().st_size<=MAX_JSON:
                        raise ASRError("asr_worker_output_missing")
                    try:
                        data=json.loads(outfile.read_text("utf-8"),
                            parse_constant=lambda x:(_ for _ in ()).throw(ValueError(x)))
                    except (ValueError,UnicodeError,OverflowError) as exc:
                        raise ASRError("asr_worker_bad_json") from exc
                    if type(data) is not dict or set(data)!={"language","segments","model_sha256","model_version","engine_version"}:
                        raise ASRError("asr_worker_bad_response")
                    if (data["model_sha256"]!=self._info.model_sha256
                        or data["model_version"]!=self._info.model_version
                        or data["engine_version"]!=self._info.engine_version):
                        raise ASRError("asr_worker_model_mismatch")
                    return data
        except ASRError:
            raise
        except Exception as exc:
            raise ASRError("asr_resource_or_execution_failed") from exc
