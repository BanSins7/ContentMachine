"""GA20 deterministic local-only DAG and logical resource admission policies.

No cloud/paid jobs, model inference, QDP request, storage mutation or
background activation occurs by importing this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re

from .contracts_v1 import canonical_sha256

STEP_KINDS=frozenset((
    "technical","frames","audio_decode","vad","asr","speech_metrics",
    "ocr","text_tracking","subtitles","audio_events","slides",
    "mixed_slides","assembly",
))
CACHE_KINDS=frozenset(("frames","probe","transcript","vision","audio","ocr"))
RETRY_SAFE=STEP_KINDS  # Only explicitly registered local executors, no provider calls.
STREAM_REFS={
    "technical":"probe","frames":"frames","audio_decode":"audio",
    "asr":"transcript","ocr":"ocr",
}
POOL_LOCK_A=61320
POOL_LOCK_B=1
SHA=re.compile(r"^[a-f0-9]{64}$")
KEY=re.compile(r"^[a-z][a-z0-9_:-]{0,119}$")
WORKER=re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
ERROR=re.compile(r"^[a-z][a-z0-9_]{1,79}$")


class RuntimeDenied(RuntimeError):
    def __init__(self, code):
        self.code=code
        super().__init__("GA20 runtime denied: "+code)


@dataclass(frozen=True,slots=True)
class ResourceBudget:
    cpu_slots: int=4
    gpu_slots: int=0
    max_concurrent_jobs: int=2
    lease_seconds: int=60

    def __post_init__(self):
        for field,bounds in (
            ("cpu_slots",(1,64)),("gpu_slots",(0,8)),
            ("max_concurrent_jobs",(1,64)),("lease_seconds",(5,300))
        ):
            value=getattr(self,field)
            if type(value) is not int or not bounds[0]<=value<=bounds[1]:
                raise RuntimeDenied("invalid_resource_budget")


@dataclass(frozen=True,slots=True)
class Stage:
    key: str
    kind: str
    depends_on: tuple[str,...]=()
    cpu_slots: int=1
    gpu_slots: int=0
    priority: int=0
    max_runtime_sec: int=600
    allow_retry: bool=True

    def __post_init__(self):
        if type(self.key) is not str or not KEY.fullmatch(self.key):
            raise RuntimeDenied("invalid_stage_key")
        if self.kind not in STEP_KINDS:
            raise RuntimeDenied("unknown_stage_kind")
        if (type(self.depends_on) is not tuple
            or any(type(x) is not str or not KEY.fullmatch(x) for x in self.depends_on)
            or len(self.depends_on)!=len(set(self.depends_on))):
            raise RuntimeDenied("invalid_stage_dependencies")
        if (type(self.cpu_slots) is not int or not 1<=self.cpu_slots<=64
            or type(self.gpu_slots) is not int or not 0<=self.gpu_slots<=8
            or type(self.priority) is not int or not -20<=self.priority<=20
            or type(self.max_runtime_sec) is not int or
            not 1<=self.max_runtime_sec<=3600
            or type(self.allow_retry) is not bool):
            raise RuntimeDenied("invalid_stage_resource_profile")
        if self.gpu_slots and self.kind not in ("asr","ocr","audio_events"):
            raise RuntimeDenied("gpu_not_justified_for_stage")


def plan_for_kind(content_kind):
    """Explicit deterministic DAG; initial:v1 already persisted by GA04.

    For mixed carousels video stages are batch operations over ALL verified
    video slide assets; stage inputs retain exact GA05 manifest identity.
    """
    if content_kind in ("video","slideshow_video"):
        first="technical"
        items=[Stage("initial:v1",first)]
    elif content_kind=="mixed_carousel":
        items=[Stage("initial:v1","mixed_slides")]
        items.append(Stage("technical:v1","technical",("initial:v1",)))
    elif content_kind in ("image_post","photo_carousel"):
        items=[Stage("initial:v1","slides")]
        items.append(Stage("assembly:v1","assembly",("initial:v1",)))
        return tuple(items)
    else:
        raise RuntimeDenied("unsupported_content_kind")

    technical="initial:v1" if content_kind in ("video","slideshow_video") else "technical:v1"
    items.extend((
        Stage("frames:v1","frames",(technical,),cpu_slots=2,priority=3),
        Stage("audio_decode:v1","audio_decode",(technical,),priority=4),
        Stage("subtitles:v1","subtitles",(technical,)),
        Stage("vad:v1","vad",("audio_decode:v1",)),
        Stage("asr:v1","asr",("vad:v1",),cpu_slots=2,max_runtime_sec=1200),
        Stage("speech_metrics:v1","speech_metrics",("asr:v1",)),
        Stage("ocr:v1","ocr",("frames:v1",),cpu_slots=2,max_runtime_sec=1200),
        Stage("text_tracking:v1","text_tracking",("ocr:v1",)),
        Stage("audio_events:v1","audio_events",("audio_decode:v1",)),
    ))
    deps=(technical,"speech_metrics:v1","text_tracking:v1",
          "subtitles:v1","audio_events:v1")
    if content_kind=="mixed_carousel":
        deps+=("initial:v1",)
    items.append(Stage("assembly:v1","assembly",deps,
                       cpu_slots=1,max_runtime_sec=900,allow_retry=False))
    return tuple(items)


def verify_plan(stages):
    if type(stages) not in (list,tuple) or not 1<=len(stages)<=40:
        raise RuntimeDenied("invalid_dag_size")
    stages=tuple(stages)
    if any(not isinstance(s,Stage) for s in stages):
        raise RuntimeDenied("invalid_dag_stage")
    by={s.key:s for s in stages}
    if len(by)!=len(stages) or "initial:v1" not in by:
        raise RuntimeDenied("invalid_dag_root_or_duplicate")
    if by["initial:v1"].depends_on:
        raise RuntimeDenied("dag_initial_has_dependency")
    for stage in stages:
        if stage.key in stage.depends_on or any(x not in by for x in stage.depends_on):
            raise RuntimeDenied("dag_unknown_or_self_dependency")
    visiting=set()
    visited=set()

    def visit(key):
        if key in visiting:
            raise RuntimeDenied("dag_cycle")
        if key in visited:
            return
        visiting.add(key)
        for upstream in by[key].depends_on:
            visit(upstream)
        visiting.remove(key)
        visited.add(key)

    for key in by:
        visit(key)
    return stages


def step_input_sha(manifest_sha,stage):
    if type(manifest_sha) is not str or SHA.fullmatch(manifest_sha) is None:
        raise RuntimeDenied("invalid_manifest_digest")
    if not isinstance(stage,Stage):
        raise RuntimeDenied("invalid_stage")
    if stage.key=="initial:v1":
        return manifest_sha
    return canonical_sha256({
        "schema_version":"ga20-input/v1",
        "manifest_sha256":manifest_sha,"stage_key":stage.key,
        "stage_kind":stage.kind,"depends_on":list(stage.depends_on),
    })


def safe_worker_id(worker):
    if type(worker) is not str or not WORKER.fullmatch(worker):
        raise RuntimeDenied("invalid_worker_id")
    return worker


def safe_sha(value):
    if type(value) is not str or SHA.fullmatch(value) is None:
        raise RuntimeDenied("invalid_output_sha")
    return value


def safe_error(value):
    if type(value) is not str or ERROR.fullmatch(value) is None:
        raise RuntimeDenied("invalid_error_code")
    return value


def retry_seconds(attempt):
    if type(attempt) is not int or attempt<1:
        raise RuntimeDenied("invalid_attempt")
    return min(300,2**min(attempt,8))
