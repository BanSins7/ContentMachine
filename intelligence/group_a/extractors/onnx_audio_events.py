"""Optional fully local ONNX YAMNet/PANNs score adapter for GA15.

No model, label map, license or ONNX Runtime is bundled. The deployer must
pin exact bytes, supply an approved class-index map, and lease CPU resources.
A model's raw scores are not calibrated probabilities. No hidden sigmoid,
threshold adaptation, provider fallback, HTTP or model hub.
"""
from __future__ import annotations

from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
import re

from ..contracts_v1 import canonical_sha256
from .audio_events import AudioEventError, LABELS, SAMPLE_RATE, _decimal

MAX_MODEL_BYTES=384*1024*1024
MAX_CLASSES=4096
MAX_OUTPUT_ROWS=512
SHA=re.compile(r"^[a-f0-9]{64}$")
VERSION=re.compile(r"^[a-zA-Z0-9._+-]{1,80}$")


def pinned_labelmap(groups):
    if type(groups) is not dict or set(groups)!=set(LABELS):
        raise AudioEventError("audio_event_labelmap_invalid")
    clean={}
    indices=set()
    for label in LABELS:
        items=groups[label]
        if type(items) not in (tuple,list) or not 1<=len(items)<=256:
            raise AudioEventError("audio_event_labelmap_invalid")
        if any(type(i) is not int or not 0<=i<MAX_CLASSES for i in items):
            raise AudioEventError("audio_event_labelmap_invalid")
        if len(set(items))!=len(items):
            raise AudioEventError("audio_event_labelmap_invalid")
        if any(i in indices for i in items):
            raise AudioEventError("audio_event_labelmap_conflict")
        indices.update(items)
        clean[label]=tuple(sorted(items))
    return clean,canonical_sha256({k:list(clean[k]) for k in LABELS})


class LocalONNXAudioClassifier:
    """Uses ONNX Runtime CPU with an explicitly preinstalled model.

    input_layout='samples' → rank 1 float32 tensor
    input_layout='batch_samples' → rank 2 [1,N] float32 tensor
    model emits [classes] or [time_frames,classes] normalized 0..1
    scores. For multi-frame output this adapter takes max over rows;
    it does not claim class calibration or actual within-window event
    boundaries. Resource lease must come from the trusted host.
    """

    def __init__(self, *, model_path,expected_sha256,model_version,
                 engine,label_groups,input_layout,lease,
                 expected_labelmap_sha256,verify_license):
        if (engine not in ("yamnet_onnx","panns_onnx")
            or type(model_version) is not str or VERSION.fullmatch(model_version) is None
            or type(expected_sha256) is not str or SHA.fullmatch(expected_sha256) is None
            or type(expected_labelmap_sha256) is not str or SHA.fullmatch(expected_labelmap_sha256) is None
            or input_layout not in ("samples","batch_samples")):
            raise AudioEventError("audio_event_model_config_invalid")
        if lease is None or not callable(getattr(lease,"acquire",None)):
            raise AudioEventError("audio_event_resource_lease_required")
        group, digest=pinned_labelmap(label_groups)
        if digest!=expected_labelmap_sha256:
            raise AudioEventError("audio_event_labelmap_hash_mismatch")
        path=Path(model_path)
        if not path.is_absolute() or not path.is_file() or path.is_symlink() or not 0<path.stat().st_size<=MAX_MODEL_BYTES:
            raise AudioEventError("audio_event_model_file_invalid")
        # One byte sequence is SHA-pinned AND fed directly to ONNX Runtime;
        # no path replacement between the check and graph load.
        try:
            contents=path.read_bytes()
        except OSError as exc:
            raise AudioEventError("audio_event_model_file_unreadable") from exc
        if sha256(contents).hexdigest()!=expected_sha256:
            raise AudioEventError("audio_event_model_hash_mismatch")
        context={"model_sha256":expected_sha256,
                 "labelmap_sha256":digest,"model_version":model_version,
                 "engine":engine,"device":"cpu","license_authorization":"host_verified"}
        if not callable(verify_license) or verify_license(context) is not True:
            raise AudioEventError("audio_event_model_license_unverified")
        try:
            import onnxruntime as ort
            import numpy as np
        except ImportError as exc:
            raise AudioEventError("onnxruntime_not_installed") from exc
        try:
            session=ort.InferenceSession(contents,providers=["CPUExecutionProvider"])
            if session.get_providers()!=["CPUExecutionProvider"]:
                raise ValueError("unexpected ONNX provider")
            inputs=session.get_inputs()
            outputs=session.get_outputs()
            if len(inputs)!=1 or len(outputs)!=1 or inputs[0].type!="tensor(float)":
                raise ValueError("unsupported ONNX tensor signature")
            expected_rank=1 if input_layout=="samples" else 2
            if len(inputs[0].shape)!=expected_rank:
                raise ValueError("unsupported ONNX waveform shape")
        except Exception as exc:
            raise AudioEventError("audio_event_model_signature_incompatible") from exc
        self._session=session
        self._np=np
        self._groups=group
        self._layout=input_layout
        self._input=inputs[0].name
        self._output=outputs[0].name
        self._lease=lease
        self._provenance={
            "engine":engine,
            "engine_version":str(ort.__version__),
            "model_version":model_version,
            "model_sha256":expected_sha256,
            "labelmap_sha256":digest,
            "device":"cpu","license_authorization":"host_verified",
        }
        if VERSION.fullmatch(self._provenance["engine_version"]) is None:
            raise AudioEventError("audio_event_onnx_version_invalid")

    @property
    def provenance(self):
        return dict(self._provenance)

    def predict(self,window_pcm:bytes,*,sample_rate:int):
        if sample_rate!=SAMPLE_RATE or type(window_pcm) is not bytes or not window_pcm or len(window_pcm)%2:
            raise AudioEventError("audio_event_pcm_input_invalid")
        np=self._np
        samples=np.frombuffer(window_pcm,dtype="<i2").astype(np.float32)/32768.0
        data=samples if self._layout=="samples" else samples.reshape(1,-1)
        try:
            lock=self._lease.acquire(kind="audio_events",device="cpu")
            with lock:
                out=self._session.run([self._output],{self._input:data})[0]
        except Exception as exc:
            raise AudioEventError("audio_event_onnx_inference_failed") from exc
        if not isinstance(out,np.ndarray) or out.dtype.kind!="f":
            raise AudioEventError("audio_event_onnx_output_invalid")
        if out.ndim==1:
            out=out.reshape(1,-1)
        if (out.ndim!=2 or not 1<=out.shape[0]<=MAX_OUTPUT_ROWS
            or not 1<=out.shape[1]<=MAX_CLASSES or not np.isfinite(out).all()
            or (out<0).any() or (out>1).any()):
            raise AudioEventError("audio_event_onnx_output_invalid")
        if max(max(x) for x in self._groups.values())>=out.shape[1]:
            raise AudioEventError("audio_event_onnx_labelmap_out_of_bounds")
        return {label:float(out[:,self._groups[label]].max()) for label in LABELS}
